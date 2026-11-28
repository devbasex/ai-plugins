"""承認の記録: ステップのイベントの値から組み、今の PR と今の中身を照らし、承認の回答から読む（#3702 の決定 11・13）。

記録のファイルは作らない。材料は工程「ドキュメントレビュー」のステップの出力（`reviewed`・`design_units`。`stages.carried_values`）で、
記録の SSOT は承認の回答の事象の payload のキー `design_target` である。この層は事象の下書きを返すだけで書かない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters.git import Git, GitError
from ndf.core.plan import units_decide as ud
from ndf.core.plan import runner as plan_runner
from ndf.core.plan import units_files
from ndf.progress import folding, invoke, model, run_view, stages

RECORD_VERSION = 1
# 承認の回答の payload の、記録のキー
TARGET_KEY = "design_target"
# 再承認の待ちを返す結果の、承認資料（`{"path", "sha256"}`）のキー
MATERIAL_KEY = "reapproval_material"
# 回答のコマンドで、回答した者が見た承認資料を固定する引数
MATERIAL_FLAG = "--material-sha256"
# 記録を組む工程（定義の id）と承認ゲートの区分・単位を流す工程の定義（ファイル名が id）
REVIEW_STAGE_ID, GATE = "document-review", "design"
IMPLEMENT_DEFINITION = PurePosixPath("workflow/stages/implement.json")
_RESPLIT = "変更を求めて実装計画を分け直す"
# `approval_stale` で止まったら、工程の先頭から流し直すステップ
STALE_STEPS = (*stages.CARRIED_STEPS.values(), "docs-ci", "design-mvv", "design-merge-approved")


def _stage_by_id(snap: stages.Snapshot, stage_id: str) -> Optional[stages.StageDef]:
    return next((s for s in snap.stages if s.id == stage_id), None)


def _keys_of(output: str) -> list[str]:
    """結果の `outputs` のキー `output` が必ず持つキー（SSOT は結果のスキーマ）。"""
    return contracts.load_json("schemas/result.schema.json")["$defs"]["outputs"]["properties"][output]["required"]


def required(snap: stages.Snapshot) -> bool:
    """工程「ドキュメントレビュー」が、そのモードで設計承認ゲートのステップを持つか。"""
    stage = _stage_by_id(snap, REVIEW_STAGE_ID)
    return stage is not None and stage.gate_index(GATE) is not None


def applies(snap: stages.Snapshot, stage: stages.StageDef) -> bool:
    """工程 `stage` が、承認の記録を組んで照らす工程か。"""
    return stage.id == REVIEW_STAGE_ID and required(snap)


def from_steps(stage: stages.StageDef, done: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """工程の済んだステップの出力から記録を組む（`build`）。"""
    held = stages.carried_values(stage, done)
    return build(held.get("reviewed"), held.get("design_units"))


def build(reviewed: Optional[Mapping[str, Any]], design_units: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """2 つの事象の値から記録を組む。値が欠けるか、確かめたコミットがレビューした HEAD と違えば `model.Refused`。"""
    lack = [] if reviewed else ["reviewed"]
    lack += [f"reviewed.{k}" for k in _keys_of("reviewed") if reviewed and not reviewed.get(k)]
    lack += [] if design_units else ["design_units"]
    lack += [f"design_units.{k}" for k in _keys_of("design_units") if design_units and k not in design_units]
    if lack or reviewed is None or design_units is None:
        raise model.Refused(f"承認の記録を組む値が無い: {', '.join(lack)}")
    if design_units["head"] != reviewed["head"]:
        raise model.Refused(
            f"単位を確かめたコミット（design_units.head {design_units['head']}）がレビューした HEAD（{reviewed['head']}）と違う"
        )
    return _record(reviewed, design_units)


def _record(review: Mapping[str, Any], design_units: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": RECORD_VERSION,
        "review": {k: review[k] for k in _keys_of("reviewed")},
        "sprint_branch": design_units["branch"],
        "targets": {k: design_units[k] for k in ("issues", "handling", "omitted")},
        "limits": design_units["limits"],
        ud.CRITERIA_KEY: design_units[ud.CRITERIA_KEY],
        "units": design_units["units"],
    }


def stale(record: Mapping[str, Any], repo: str, pr: int, head: str, base: str) -> list[str]:
    """今の PR と記録の違い（キーごとに 1 行）。"""
    now = {"repo": repo, "pr": pr, "head": head, "base": base}
    approved = record["review"]
    return [f"{k} が違う（記録 {approved[k]}、今 {value}）" for k, value in now.items() if approved[k] != value]


def reviewed_pr(stage: stages.StageDef, done: Mapping[str, Mapping[str, Any]]) -> Optional[int]:
    """工程の済んだステップがレビューした PR の番号。まだ無ければ None。"""
    held = stages.carried_values(stage, done).get("reviewed")
    return held["pr"] if held else None


def moved(record: Mapping[str, Any], repo: Any, pr: Any, head: Any, base: Any) -> Optional[str]:
    """`stale` の違いを 1 つの理由にする。違いが無ければ None。"""
    found = stale(record, repo, pr, head, base)
    return f"承認の対象がレビューの結果と違う: {' / '.join(found)}" if found else None


def mvv_extra(
    v: run_view.View, stage: stages.StageDef, done: Mapping[str, Mapping[str, Any]], held: Mapping[str, Any]
) -> tuple[dict[str, Any], list[model.EventDraft]]:
    """MVV 判定 `held` で人の承認を省く回答に添える値と、省かずにレビューへ戻す事象（どちらかが空）。

    記録を組めないか、判定が読んだ PR の承認の対象（`target`）が記録と 1 つでも違うか欠ければ戻す。
    """
    if not applies(v.snap, stage):
        return {}, []
    target = held["target"] if isinstance(held.get("target"), Mapping) else {}
    try:
        record = from_steps(stage, done)
    except model.Refused as exc:
        return {}, reset_drafts(v, stage, exc.reason)
    why = moved(record, *(target.get(k) for k in ("repo", "pr", "head", "base")))
    return ({}, reset_drafts(v, stage, why)) if why else ({TARGET_KEY: record}, [])


def stale_step_drafts(v: run_view.View, stage: stages.StageDef, step: str, kind: Optional[str], why: str) -> list[model.EventDraft]:
    """ステップ `step` が `approval_stale` で止まったときの、工程の先頭へ戻す事象（当たらなければ空）。"""
    if kind != plan_runner.APPROVAL_STALE or step not in STALE_STEPS or not applies(v.snap, stage):
        return []
    return reset_drafts(v, stage, why)


def narrowed(record: Mapping[str, Any], drop: Sequence[int], root: Path) -> dict[str, Any]:
    """課題を外す回答の記録。承認の記録の単位が持つファイルを記録の HEAD から読んで `without_issues` に渡す。"""
    head = record["review"]["head"]
    if not _fetched(Git(root), head):
        raise model.Refused(f"記録の HEAD {head} が手元に無く、取得もできないので課題を外せない")
    # 実装計画を省いた課題の単位は `file` が空。記録に無いファイル（古い単位のファイルなど）は読まない
    paths = sorted({u["file"] for u in record["units"] if u["file"]})
    try:
        # 本文はまとめて読む（git の起動をファイル数に比例させない）
        texts = units_files.Tree(root, head).read_all(paths)
        files = [units_files.parse_text(path, texts[path]) for path in paths]
    except (units_files.UnitsFileError, GitError) as exc:
        raise model.Refused(f"記録の HEAD の単位のファイルを読めないので課題を外せない: {exc}") from None
    return without_issues(record, drop, files)


def reset_drafts(v: run_view.View, stage: stages.StageDef, why: str) -> list[model.EventDraft]:
    """工程の先頭から設計承認ゲートまでのステップを未了に戻す事象。"""
    at = stage.gate_index(GATE)
    if at is None:
        raise model.Refused(f"工程 {stage.name} は設計承認ゲートを持たない")
    generation = folding.step_progress(v, stage.name).generation
    return [
        model.EventDraft(
            run_view.STEP_EVENT,
            run_view.ACTOR,
            stage.name,
            payload={"step": st.id, "status": stages.RESET, "report": None, "generation": generation, "reason": why},
            rev=v.rev,
        )
        for st in stage.steps[: at + 1]
    ]


def _latest_answer_event(v: run_view.View, stage_id: str, kind: Optional[str] = None) -> Optional[model.Event]:
    """工程の最新の回答の事象。`kind` を渡せば、その作業種別の待ちへの回答だけを読む。"""
    stage = _stage_by_id(v.snap, stage_id)
    if stage is None:
        return None
    for ev in reversed(v.row_events(stage.name)):
        if ev.draft.kind == run_view.ANSWERED and (kind is None or run_view.event_payload(ev).get("kind") == kind):
            return ev
    return None


def _latest_answer(v: run_view.View, stage_id: str, kind: Optional[str] = None) -> Optional[dict[str, Any]]:
    """`_latest_answer_event` の payload。"""
    ev = _latest_answer_event(v, stage_id, kind)
    return None if ev is None else run_view.event_payload(ev)


def _target_of(payload: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """回答が承認で記録を持てば、その記録。回答として数えない MVV 判定の回答は読まない。"""
    if payload is None or payload.get("answer") != invoke.APPROVED or model.mvv_answer_void(dict(payload), GATE):
        return None
    held = payload.get(TARGET_KEY)
    return dict(held) if isinstance(held, dict) else None


def approved_of(v: run_view.View) -> Optional[dict[str, Any]]:
    """工程「ドキュメントレビュー」の承認ゲートの最新の回答が承認なら、その記録。後の変更要求があれば None。"""
    return _target_of(_latest_answer(v, REVIEW_STAGE_ID, invoke.LISTEN))


def approval_seq_of(v: run_view.View) -> Optional[int]:
    """`approved_of` が読む回答の事象の番号（承認の識別子）。同じ HEAD でも、承認し直せば変わる。回答が無ければ None。"""
    ev = _latest_answer_event(v, REVIEW_STAGE_ID, invoke.LISTEN)
    return None if ev is None else ev.seq


def reapproved_of(v: run_view.View) -> Optional[dict[str, Any]]:
    """工程「実装」の最新の回答が承認なら、その記録（変換の再承認）。"""
    return _target_of(_latest_answer(v, IMPLEMENT_DEFINITION.stem))


class Compared(NamedTuple):
    """承認の記録との比較の結果。`targets` は違いが無いときの、比較が `tree` から読んだ対象（`units_files.check_collected` に渡す）。"""

    differences: list[str]
    targets: Optional[units_files.Targets]


def mismatches(record: Mapping[str, Any], tree: units_files.Tree, context: Mapping[str, Any]) -> Compared:
    """承認の記録と今の中身の違い（設計書の決定 11 の表の 4 つの比較）。`tree` はスプリントの作業場所の HEAD。

    スプリントの課題は全体を比べ、ほかの比較は記録の `targets.issues` に絞る。
    """
    targets = record["targets"]
    issues = targets["issues"]
    now_issues = sorted(set(context["issues"]))
    found = [] if now_issues == sorted(issues) else [f"スプリントの課題が違う（承認 {sorted(issues)}、今 {now_issues}）"]
    head = record["review"]["head"]
    if not _fetched(Git(tree.root), head):
        return Compared([*found, f"記録の HEAD {head} が手元に無く、取得もできないので照らせない"], None)
    listing = tree.listing()
    differing, parsed = _file_mismatches(units_files.Tree(tree.root, head), tree, listing, targets)
    found += differing
    differing, handled = _handling_mismatches(targets["handling"], tree, listing, issues)
    found += differing
    excluded = {int(n) for n, h in targets["handling"].items() if h["handling"] in units_files.EXCLUDED_HANDLINGS}
    held = context["plan_omitted"] if "plan_omitted" in context else []
    omitted = sorted((set(held) & set(issues)) - excluded)
    if omitted != sorted(targets["omitted"]):
        found.append(f"実装計画を省いた課題が違う（承認 {sorted(targets['omitted'])}、今 {omitted}）")
    if found:
        return Compared(found, None)
    try:
        return Compared([], units_files.targets_from(tree, listing, issues, targets["omitted"], handled, parsed))
    except units_files.UnitsFileError as exc:
        return Compared([str(exc)], None)


def _fetched(g: Git, head: str) -> bool:
    """コミットが手元にあるか。無ければ設定のリモートから 1 回だけ取得する。"""
    for last in (False, True):
        try:
            g.rev_parse(head)
            return True
        except GitError:
            # リモート名は取得が要るときだけ求める（`Git.remote()` は git を起動する）
            if last or g.fetch(g.remote(), head).returncode != 0:
                break
    return False


def _file_mismatches(
    approved: units_files.Tree,
    now: units_files.Tree,
    listing: Mapping[str, Optional[tuple[str, str, str]]],
    targets: Mapping[str, Any],
) -> tuple[list[str], dict[str, ud.UnitFile]]:
    """単位のファイルの違いと、`now` から読んで解析した単位のファイル。`listing` は `now.listing()`。

    ファイルの増減は記録の全課題で見る。重複と中身は、記録を組むときに読む課題（実装計画を省かず、独立した単位を持つ課題）の
    ファイルだけを見る。"""
    issues = targets["issues"]
    handled = targets["handling"]
    unread = {int(n) for n, h in handled.items() if h["handling"] in units_files.EXCLUDED_HANDLINGS} | set(targets["omitted"])
    read = [n for n in issues if n not in unread]
    parsed: dict[str, ud.UnitFile] = {}
    try:
        old_listing = approved.listing()
        old_names, new_names = sorted(old_listing), sorted(listing)
        old_read = set(units_files.find(approved, read, old_names).values())
        new_read = set(units_files.find(now, read, new_names).values())
    except units_files.UnitsFileError as exc:
        return [str(exc)], parsed
    before, after = units_files.paths_naming(old_names, issues), units_files.paths_naming(new_names, issues)
    found = [f"{path}: 承認した単位のファイルが無い" for path in sorted(before - after)]
    found += [f"{path}: 承認の時点に無い単位のファイルがある" for path in sorted(after - before)]
    common = sorted(old_read & new_read)
    try:
        # 読み取りは側ごとに 1 回にまとめる（git の起動をファイル数に比例させない）
        old, new = approved.read_all(common, old_listing), now.read_all(common, listing)
    except units_files.UnitsFileError as exc:
        return [*found, str(exc)], parsed
    for path in common:
        try:
            was = units_files.parse_text(path, old[path])
            parsed[path] = units_files.parse_text(path, new[path])
            if units_files.differs(was, parsed[path]):
                found.append(f"{path}: 承認した単位のファイルと中身が違う")
        except units_files.UnitsFileError as exc:
            found.append(str(exc))
    return found, parsed


def _handling_mismatches(
    approved: Mapping[str, Any],
    tree: units_files.Tree,
    listing: Mapping[str, Optional[tuple[str, str, str]]],
    issues: Sequence[int],
) -> tuple[list[str], dict[int, tuple[str, Optional[int]]]]:
    """扱いと統合先の違いと、`tree` から読んだ扱い。"""
    try:
        now = units_files.handlings(tree, issues, listing)
    except units_files.UnitsFileError as exc:
        return [str(exc)], {}
    found = []
    for n in sorted(issues):
        was = (approved[str(n)]["handling"], approved[str(n)]["into"])
        if now[n] != was:
            found.append(f"#{n} の扱いか統合先が違う（承認 {was[0]} {was[1]}、今 {now[n][0]} {now[n][1]}）")
    return found, now


def without_issues(record: Mapping[str, Any], drop: Sequence[int], files: Sequence[ud.UnitFile]) -> dict[str, Any]:
    """課題を外す回答の記録を、残る課題へ絞る。`files` は記録の HEAD の単位のファイル。外せない形なら `model.Refused`。"""
    dropped = set(drop)
    targets = record["targets"]
    approved = {u["file"] for u in record["units"]}
    files = [f for f in files if f.path in approved]
    for f in files:
        if set(f.issues) & dropped and set(f.issues) - dropped:
            raise model.Refused(f"{f.path} が外す課題と残す課題の両方を持つ。{_RESPLIT}")
    for n, held in targets["handling"].items():
        into = held["into"]
        if into in targets["issues"] and (int(n) in dropped) != (into in dropped):
            raise model.Refused(f"統合する課題 #{n} と統合先 #{into} の片方だけを外せない。{_RESPLIT}")
    kept = [n for n in targets["issues"] if n not in dropped]
    omitted = [n for n in targets["omitted"] if n not in dropped]
    left = [*(f for f in files if not set(f.issues) & dropped), *map(ud.single, omitted)]
    criteria = {k: count for k, count in record[ud.CRITERIA_KEY].items() if int(k) not in dropped}
    judgement = ud.judge(left, ud.Limits(**record["limits"]), {int(k): count for k, count in criteria.items()}, propose=False)
    if judgement.violations:
        first = judgement.violations[0]
        raise model.Refused(f"残る単位が確かめを通らない（{first.rule} {first.unit}: {first.reason}）。{_RESPLIT}")
    report = units_files.Report(judgement, (), (), (), tuple(left), {}, {})
    return {
        **record,
        "targets": {
            "issues": kept,
            "handling": {k: held for k, held in targets["handling"].items() if int(k) not in dropped},
            "omitted": omitted,
        },
        ud.CRITERIA_KEY: criteria,
        "units": units_files.unit_items(report),
    }


def converted(
    repo: str, head: str, branch: str, context: Mapping[str, Any], limits: ud.Limits, report: units_files.Report
) -> dict[str, Any]:
    """変換の再承認の記録（`build` と同じ形）。`head` は単位のファイルを含むコミットで、設計 PR は無い。"""
    design_units = units_files.design_units_of(report, head, branch, context["issues"], limits)
    return _record({"repo": repo, "pr": None, "head": head, "base": branch}, design_units)
