"""承認の記録を組み、比べる（issues/issue-3702-design-detail.md の「承認の記録」、設計の決定 1・7・11）。

承認の記録は、設計承認ゲートの回答のイベントが payload のキー `design_target` に持つ値で、SSOT はイベントである。保存先の
`design-target.json` は承認資料を作るための作業用である。形は次のとおりで、`ndf progress` の外の値（GitHub の PR）は呼び手が
読んで渡す。

    {"version": 1, "review": {"repo", "pr", "head", "base"}, "sprint_branch", "targets": {"issues", "handling", "omitted"},
     "limits": {"target", "max"}, "criteria": {"<課題>": 数}, "units": [...]}

`ndf.progress.run`・`gate`・`units_invoke` が読むので、それらは読まない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.adapters.git import Git, GitError, mask_urls
from ndf.contracts import worker_paths
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import inputs_decide, units_files
from ndf.core.plan import units_decide as ud
from ndf.progress import folding, model, stages
from ndf.progress.run_view import ACTOR, ANSWERED, STEP_EVENT, View, event_payload

RECORD_VERSION = 1
# 保存先に `build` が書くファイル
TARGET_FILE = "design-target.json"
# `design-target.json` の記録の種別（種別の違うファイルは消さず、上書きもしない）
TARGET_KIND = "design-target"
# 回答の事象の payload が記録を持つキー
TARGET_KEY = "design_target"
# 承認ゲートの区分の語（コントラクト `workflow/progress.json` の gates の行。表に無ければ import の時点で `StopIteration`）
_GATE = next(gate for gate in model.table()["gates"] if gate == "design")
# 承認の回答を持つ工程: ドキュメントレビューは定義の id、実装は行のキー（工程の名前）で引く
_REVIEW_STAGE, _IMPLEMENT_ROW = "document-review", "実装"
_APPROVED = folding.answer_word("approved")
_REDO = "変更を求めて実装計画を分け直す"
# コミットの ID の形（理由の文に載せてよいかを確かめる）
_COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# NDF の保存先を持つディレクトリの名前。これより下の要素にリンクがあれば、保存先を読まない
_STORE_DIRS = (".git", identity.FIXED_WORDS["ndf_settings_dir"])


class RecordUnreadable(Exception):
    """保存先のファイルが無いか読めず、記録を組めない。理由はファイル名を持つ。呼び手は資料を出さず、`reset_drafts` を書く。"""


# --- 組む ---------------------------------------------------------------------------------------


def required(snap: stages.Snapshot) -> bool:
    """承認の記録が要るか。工程「ドキュメントレビュー」がそのモードで設計承認ゲートのステップを持つときだけ真。"""
    return any(s.id == _REVIEW_STAGE and s.gate_index(_GATE) is not None for s in snap.stages)


def build(record_dir: Path) -> dict[str, Any]:
    """レビューの記録の 3 つのファイルと `checked.json` から記録を組み、`design-target.json` に書く。"""
    head = _text(record_dir, units_files.REVIEWED_HEAD)
    base = _text(record_dir, plan_cli.REVIEWED_BASE)
    repo, _, number = _text(record_dir, plan_cli.REVIEWED_PR).rpartition("#")
    if not repo or not number.isdigit():
        raise RecordUnreadable(f"{record_dir / plan_cli.REVIEWED_PR}: <所有者>/<リポジトリ>#<番号> の形でない")
    checked = _checked(record_dir, head)
    try:
        record = _record(
            {"repo": repo, "pr": int(number), "head": head, "base": base},
            checked["branch"],
            {"issues": checked["issues"], "handling": checked["handling"], "omitted": checked["omitted"]},
            checked["limits"],
            checked[ud.CRITERIA_KEY],
            checked["units"],
        )
    except KeyError as exc:
        raise RecordUnreadable(f"{record_dir / plan_cli.CHECKED_FILE} にキー {exc} が無い") from None
    try:
        plan_files.discard_record(record_dir / TARGET_FILE, TARGET_KIND)
        plan_files.publish_record(record_dir / TARGET_FILE, TARGET_KIND, record)
    except plan_files.RecordRefused as exc:
        raise RecordUnreadable(str(exc)) from None
    return record


def _real_record_dir(record_dir: Path) -> Path:
    """保存先のパスのうち、`_STORE_DIRS` のディレクトリより上のリンクだけを解いた形。それより下のリンクは解かずに残し、開くときに断る。

    上を解くのは、ホームディレクトリがリンクの環境でも読めるようにするためである。該当するディレクトリが無いパスは、全部の要素を残す。
    """
    parts = record_dir.absolute().parts
    at = next((i for i in range(len(parts) - 1, -1, -1) if parts[i] in _STORE_DIRS), None)
    return record_dir if at is None else Path(*parts[:at]).resolve().joinpath(*parts[at:])


def _text(record_dir: Path, name: str) -> str:
    """保存先の直下の通常ファイルの中身。保存先と途中のディレクトリ、ファイルのリンクは辿らない（外の中身を理由の文に出さない）。"""
    path = record_dir / name
    try:
        with worker_paths.open_workdir(_real_record_dir(record_dir)) as wd:
            held = worker_paths.read_file(wd, name)
    except (worker_paths.EntryError, OSError) as exc:
        raise RecordUnreadable(f"{path} を読めない（{type(exc).__name__}）") from None
    found = "" if held is None else held.strip()
    if not found:
        raise RecordUnreadable(f"{path} が無いか空")
    return found


def _checked(record_dir: Path, head: str) -> dict[str, Any]:
    path = record_dir / plan_cli.CHECKED_FILE
    if not _COMMIT_ID.fullmatch(head):
        raise RecordUnreadable(f"{record_dir / units_files.REVIEWED_HEAD}: コミットの ID の形でない")
    try:
        found = json.loads(_text(record_dir, plan_cli.CHECKED_FILE))
    except json.JSONDecodeError:
        found = None
    if not isinstance(found, dict) or found.get(plan_files.RECORD_KIND_KEY) != plan_cli.CHECKED_KIND:
        raise RecordUnreadable(f"{path} は単位の確かめの記録でない")
    seen = found.get("head")
    if seen != head:
        shown = f"（{seen}）" if isinstance(seen, str) and _COMMIT_ID.fullmatch(seen) else ""
        raise RecordUnreadable(f"{path} が確かめたコミット{shown}はレビューした HEAD（{head}）と違う")
    return found


def _record(
    review: Mapping[str, Any],
    branch: str,
    targets: Mapping[str, Any],
    limits: Mapping[str, int],
    criteria: Mapping[str, int],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": RECORD_VERSION,
        "review": dict(review),
        "sprint_branch": branch,
        "targets": dict(targets),
        "limits": dict(limits),
        ud.CRITERIA_KEY: dict(criteria),
        "units": units,
    }


class Checked(NamedTuple):
    """`units_files.check` の結果と、確かめに渡した課題ごとの受け入れ条件の数（結果は数を持たない）。"""

    report: units_files.Report
    criteria: Mapping[int, int]


def converted(repo: str, head: str, branch: str, context: Mapping[str, Any], limits: ud.Limits, checked: Checked) -> dict[str, Any]:
    """この変更より前のスプリントを変換した分け方の再承認の記録。`head` は単位のファイルを含むコミットで、PR は無い。

    `checked` はそのコミットを確かめた結果である。
    """
    report = checked.report
    targets = {
        "issues": [int(n) for n in context["issues"]],
        "handling": {str(n): {"handling": handling, "into": into} for n, (handling, into) in report.handlings.items()},
        "omitted": [f.issues[0] for f in report.files if f.whole],
    }
    review = {"repo": repo, "pr": None, "head": head, "base": None}
    counts = {str(n): count for n, count in checked.criteria.items()}
    return _record(review, branch, targets, limits._asdict(), counts, units_files.unit_items(report))


# --- 回答 ---------------------------------------------------------------------------------------


def stale(record: Mapping[str, Any], repo: Optional[str], pr: Optional[int], head: Optional[str], base: Optional[str]) -> list[str]:
    """記録がレビューした対象と今の PR の違い。読めなかった値（None）も違いに数える。リポジトリ名は大文字と小文字を区別しない。"""
    held = record["review"]
    now = {"repo": repo, "pr": pr, "head": head, "base": base}

    def same(key: str) -> bool:
        if key == "repo" and repo is not None:
            return str(held[key]).casefold() == repo.casefold()
        return held[key] == now[key] and now[key] is not None

    return [f"{key}: 承認の記録は {held[key]}、今は {value}" for key, value in now.items() if not same(key)]


def reset_drafts(v: View, stage: stages.StageDef, why: str) -> list[model.EventDraft]:
    """工程の先頭から設計承認ゲートまでのステップを未了に戻すイベント（レビューから流し直す）。"""
    at = stage.gate_index(_GATE)
    steps = stage.steps if at is None else stage.steps[: at + 1]
    generation = folding.step_progress(v, stage.name).generation
    return [
        model.EventDraft(
            STEP_EVENT,
            ACTOR,
            stage.name,
            payload={"step": st.id, "status": stages.RESET, "report": None, "generation": generation, "reason": why},
            rev=v.rev,
        )
        for st in steps
    ]


def approved_of(v: View) -> Optional[dict[str, Any]]:
    """工程「ドキュメントレビュー」の設計承認ゲートの最新の回答が承認なら、その記録。

    ほかの回答か、記録が無ければ None。回答の後にゲートのステップが未了に戻っていれば（`reset_drafts`）、その承認は
    取り消されているので None。
    """
    stage = next((s for s in v.snap.stages if s.id == _REVIEW_STAGE), None)
    if stage is None:
        return None
    at = stage.gate_index(_GATE)
    gate_step = None if at is None else stage.steps[at].id

    def revoked(ev: model.Event) -> bool:
        payload = event_payload(ev)
        return ev.draft.kind == STEP_EVENT and payload.get("step") == gate_step and payload.get("status") == stages.RESET

    return _latest_target(v, stage.name, lambda p: p.get("gate") == _GATE, revoked)


def reapproved_of(v: View) -> Optional[dict[str, Any]]:
    """工程「実装」の最新の回答が承認なら、その記録（変換の再承認）。ほかの回答か、記録が無ければ None。"""
    return _latest_target(v, _IMPLEMENT_ROW, lambda _p: True)


def _latest_target(
    v: View, row: str, counts: Callable[[Mapping[str, Any]], bool], revoked: Callable[[model.Event], bool] = lambda _e: False
) -> Optional[dict[str, Any]]:
    """行 `row` の、`counts` が数える最新の回答が承認なら、その payload の記録。回答より後に `revoked` の事象があれば None。"""
    for ev in reversed(v.row_events(row)):
        if revoked(ev):
            return None
        payload = event_payload(ev)
        if ev.draft.kind == ANSWERED and counts(payload):
            held = payload.get(TARGET_KEY)
            return dict(held) if payload.get("answer") == _APPROVED and isinstance(held, dict) else None
    return None


# --- 今の中身との比較 -----------------------------------------------------------------------------


def mismatches(record: Mapping[str, Any], tree: units_files.Tree, context: Mapping[str, Any]) -> list[str]:
    """承認の記録と今の中身の違い（設計の決定 11 の表の 4 つの比較）。空なら承認した分け方のままである。

    `tree` は今の読み元（スプリントの作業場所）、`context` はスプリントの文脈。スプリントの課題は全体を比べ、ほかの比較は記録の
    課題に絞る（課題を外す回答の後も、文脈の `plan_omitted` と設計書の表には外した課題が残る）。
    """
    targets = record["targets"]
    issues = [int(n) for n in targets["issues"]]
    found: list[str] = []
    current = {int(n) for n in context["issues"]}
    if current != set(issues):
        found.append(f"スプリントの課題が違う: 承認の記録は {_listed(issues)}、今は {_listed(current)}")
    head = record["review"]["head"]
    unfetched = _fetch_missing(tree.root, head)
    if unfetched is not None:
        return [*found, f"承認の記録のコミット {head} が手元に無いので、単位のファイルを比べられない（{unfetched}）"]
    try:
        listing = tree.listing()
        found += _file_mismatches(units_files.Tree(tree.root, head), tree, listing, issues)
        found += _handling_mismatches(targets, tree, listing, issues)
    except units_files.UnitsFileError as exc:
        found.append(str(exc))
    found += _omitted_mismatches(targets, context, issues)
    return list(dict.fromkeys(found))


def _listed(issues: Collection[int]) -> str:
    return "・".join(f"#{n}" for n in sorted(issues)) or "無し"


def _fetch_missing(root: Path, commit: str) -> Optional[str]:
    """コミットが手元に無ければ取得を 1 回試す。手元にあれば None、無いままなら理由。"""
    git = Git(root)
    if git.commit_or_none(commit) is not None:
        return None
    try:
        done = git.fetch(git.remote(), commit)
    except GitError as exc:
        return f"取得できない: {exc}"
    if git.commit_or_none(commit) is not None:
        return None
    return f"取得できない: {mask_urls(done.stderr.strip())}"


def _file_mismatches(
    recorded: units_files.Tree,
    tree: units_files.Tree,
    now_listed: Mapping[str, Optional[tuple[str, str, str]]],
    issues: Sequence[int],
) -> list[str]:
    """単位のファイルの違い。パスが同じものは、JSON として読み、実際の行数を除いて比べる。一覧と本文は側ごとにまとめて読む。"""
    listed = recorded.listing()
    before = units_files.find(recorded, issues, sorted(listed))
    after = units_files.find(tree, issues, sorted(now_listed))
    pairs = dict.fromkeys((before.get(n), after.get(n)) for n in sorted(issues))
    same = [old for old, new in pairs if old is not None and old == new]
    old_docs = units_files.documents(recorded, same, listed)
    new_docs = units_files.documents(tree, same, now_listed)
    found = []
    for old, new in pairs:
        if old is None and new is not None:
            found.append(f"{new}: 承認の記録に無い単位のファイルがある")
        elif old is not None and new is None:
            found.append(f"{old}: 承認した単位のファイルが無い")
        elif old != new:
            found.append(f"{new}: 単位のファイルのパスが承認した {old} と違う")
        elif old is not None and units_files.differs(old_docs[old], new_docs[old]):
            found.append(f"{new}: 単位のファイルが承認した中身と違う")
    return found


def _handling_mismatches(
    targets: Mapping[str, Any],
    tree: units_files.Tree,
    listing: Mapping[str, Optional[tuple[str, str, str]]],
    issues: Sequence[int],
) -> list[str]:
    """課題ごとの扱いと統合先の違い。単位のファイルは `_file_mismatches` が読んだので、設計書だけを読む。"""
    now = units_files.handlings(tree, listing, issues)
    found = []
    for n in sorted(issues):
        held = targets["handling"][str(n)]
        before, after = (held["handling"], held["into"]), now[n]
        if before != after:
            found.append(f"#{n} の扱いが違う: 承認の記録は {_handled(before)}、今は {_handled(after)}")
    return found


def _handled(value: tuple[str, Optional[int]]) -> str:
    handling, into = value
    return handling if into is None else f"{handling}（#{into}）"


def _omitted_mismatches(targets: Mapping[str, Any], context: Mapping[str, Any], issues: Sequence[int]) -> list[str]:
    """実装計画を省いた課題の違い。独立した単位を持たない扱いの課題は、記録の省いた課題に入らないので除く。"""
    excluded = {int(n) for n, held in targets["handling"].items() if held["handling"] in units_files.EXCLUDED_HANDLINGS}
    declared = context["plan_omitted"] if "plan_omitted" in context else []
    now = {int(n) for n in declared} & set(issues) - excluded
    before = {int(n) for n in targets["omitted"]}
    if now == before:
        return []
    return [f"実装計画を省いた課題が違う: 承認の記録は {_listed(before)}、今は {_listed(now)}"]


# --- 課題を外す回答 -------------------------------------------------------------------------------


def without_issues(record: Mapping[str, Any], drop: Collection[int], files: Sequence[ud.UnitFile]) -> dict[str, Any]:
    """課題 `drop` を外した記録（`record` は書き換えない）。外せない形なら `model.Refused`。

    `files` は記録のコミットから読んだ単位のファイルで、実装計画を省いた課題の単位は記録から作り直す。残る単位の実行順と
    マージの順は判定し直す。
    """
    targets = record["targets"]
    dropped = {int(n) for n in drop}
    kept = [int(n) for n in targets["issues"] if int(n) not in dropped]
    _refuse_split_merge(targets, dropped)
    held = [f for f in files if not f.whole]
    for f in held:
        if set(f.issues) & dropped and set(f.issues) & set(kept):
            raise _refused(f"{f.path} が外す課題と残す課題の両方を持つ")
    remaining = [f for f in held if set(f.issues) & set(kept)]
    needed = {u["file"] for u in record["units"] if u["file"] and set(u["issues"]) & set(kept)}
    lacking = needed - {f.path for f in remaining}
    if lacking:
        raise _refused(f"残る単位のファイル {'・'.join(sorted(lacking))} を読めていない")
    omitted = [int(n) for n in targets["omitted"] if int(n) in kept]
    judged_files = [*remaining, *map(ud.single, omitted)]
    counts = record[ud.CRITERIA_KEY]
    criteria = {str(n): counts[str(n)] for n in kept if str(n) in counts}
    judgement = ud.judge(judged_files, ud.Limits(**record["limits"]), {int(n): c for n, c in criteria.items()}, propose=False)
    if judgement.violations:
        first = judgement.violations[0]
        raise _refused(f"残る単位が確かめを通らない（{first.rule}: {first.reason}）")
    report = units_files.Report(judgement, (), (), (), tuple(judged_files), {})
    narrowed = {
        "issues": kept,
        "handling": {str(n): targets["handling"][str(n)] for n in kept},
        "omitted": omitted,
    }
    units = units_files.unit_items(report)
    return _record(record["review"], record["sprint_branch"], narrowed, record["limits"], criteria, units)


def _refuse_split_merge(targets: Mapping[str, Any], dropped: set[int]) -> None:
    """扱いが「マージする」の課題と統合先の、片方だけを外す回答を断る。スプリントに無い統合先は見ない。"""
    issues = {int(n) for n in targets["issues"]}
    for number, held in targets["handling"].items():
        source, into = int(number), held["into"]
        if held["handling"] == inputs_decide.MERGE_HANDLING and into in issues and (source in dropped) != (into in dropped):
            raise _refused(f"#{source} は #{into} へマージする扱いなので、片方だけを外せない")


def _refused(why: str) -> model.Refused:
    return model.Refused(f"課題を外せない: {why}。{_REDO}")
