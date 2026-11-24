"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017・#3959）。

工程の定義で承認ゲート（設計・リリース）の直前に置く判定のステップの本体。進め方によらず、判定する PR のベースブランチの
承認済みの MVV があれば LLM のアダプター（`ndf.adapters.llm`）で判定を打ち（`mvv.mvv_validate_sprint`。同じ材料の「従う」は
記録を再利用する）、承認ゲートを省けるか（`ndf.progress.gate.checks.may_skip`）を出力の `mvv_gate` に書く。進め方（pace）と
承認ゲートの区分は省くかだけを決め、省けるのは fast・auto の設計承認ゲートだけである（リリース承認ゲートは #4105 まで省かない）。
承認ゲートのステップ（`ndf.progress.run`）はその結果だけを読み、省けるなら回答した者 `service:mvv` の承認で通す。

MVV とレッドラインのパスは PR のベースのコミットから 1 度だけ読み（worktree のファイルは読まない）、差分も同じコミットと head の組から読む。
判定の材料は、共通原則の本文（`contracts/data/ndf-common-principles.md`）・プロジェクト MVV の本文・PR の本文と差分。
レッドライン（必ず人の承認が要る操作）は共通原則とプロジェクト MVV の表から引き、材料の先に置く。
判定を打てないときは承認ゲートを省かず、理由を `why` に書いて人へ問う。失敗の原因は定型文と手元の診断の記録の識別子だけを
結果と PR に載せる（`failure_text`）。PR を読めた後の結果は、決めた終わり方でだけ判定の記録（`checks.note`）を PR のコメントへ載せる。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import contracts, identity
from ndf.adapters import llm, publish, run_log
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, Pull
from ndf.contracts import internal, limits
from ndf.domains.records import files, jsonfile, jsonl, mvv
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import definition
from ndf.progress.gate.checks import MvvFacts, judgeable, may_skip, note, refusals
from ndf.progress.model import MVV_GATE_STEPS, MVV_PACES, MVV_SKIP_GATES

# 判定の回答のスキーマ（contracts/schemas/mvv-verdict.schema.json）
VERDICT_SCHEMA = "mvv-verdict"
# 判定の 1 回の呼び出しの上限
JUDGE_LIMIT = "supervise.judge-step"
# 承認ゲートの区分（設計は作業場所のブランチの PR、リリースは文脈の pr を判定する）
DESIGN, RELEASE = "design", "release"
# 区分 → 判定のステップの id（`progress.model` の 1 か所の表）と、そのステップを持つ工程の文書の名前（`stages/<名前>.json`）
GATE_STEPS = MVV_GATE_STEPS
GATE_STAGES = {DESIGN: "design", RELEASE: "deploy"}
# 省けない区分（リリース）の判定が why に足す理由（#3959 の決定 3）
RELEASE_KEPT = "リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない"
# 判定の 1 回の呼び出し（質問・材料・回答のスキーマ・上限の秒 → 回答）。テストはスタブへ差し替える
Ask = Callable[[str, str, dict, float], llm.JudgeAnswer]


class GateError(Exception):
    """判定を打てない（PR が無い・LLM が回答しない）。承認ゲートは省かずに人へ問う。"""


def boundary_paths(root: Path, ref: str) -> tuple[str, ...]:
    """コミット `ref` の進め方の設定のレッドラインのパス（設定が無ければ空。形が壊れていれば `jsonfile.JsonFileError`）。

    worktree のファイルでなくベースのコミットから読む（PR が規則を緩めても、今回の変更にはベースの規則が効く。#3959）。
    """
    where = f"{ref}:{files.relpath('pace')}"
    (raw,) = Git(root).blobs(where)
    if raw is None:
        return ()
    try:
        decl = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise jsonfile.JsonFileError(f"{where} の進め方の設定を JSON として解けない: {exc}") from exc
    if not isinstance(decl, dict):
        raise jsonfile.JsonFileError(f"{where} の進め方の設定が JSON の object でない")
    held = decl["boundary_paths"] if isinstance(decl.get("boundary_paths"), list) else []
    return tuple(p for p in held if isinstance(p, str))


def snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]:
    """機械の検査の事実（PR の本文と変えたファイルを除く）と、プロジェクト MVV の本文。

    どれもベースの MVV `snap` とそのコミットから作り、worktree の MVV の設定・本文・進め方の設定は読まない（#3959）。
    """
    ref = inputs["mvv_ref"] if isinstance(inputs.get("mvv_ref"), str) else None
    paths = boundary_paths(root, snap.commit)
    approved = snap.state == md.State.APPROVED and snap.path is not None
    body = snap.path.read_text(encoding="utf-8") if approved and snap.path is not None else ""
    sha = snap.sha256 if approved else None
    return MvvFacts(snap.state, sha, sha, ref, str(inputs.get("mode")), boundary_paths=paths), body


def _common_dir(where: Path) -> str:
    return Git(where).run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()


def _same_repo(where: Path, root: Path) -> bool:
    """作業場所 `where` が `root` と同じリポジトリか。同じパスなら Git を呼ばず、違うパスのときだけ共通ディレクトリを比べる。"""
    try:
        if where.resolve() == root.resolve():
            return True
        return _common_dir(where) == _common_dir(root)
    except (GitError, OSError):
        return False


def release_pr(inputs: Mapping[str, Any]) -> int:
    """リリースの承認ゲートで判定する PR の番号（文脈の `pr`）。GitHub へつながない。無ければ `GateError`。"""
    if not isinstance(inputs.get("pr"), int):
        raise GateError("文脈にスプリントの PR の番号（pr）が無い")
    return int(inputs["pr"])


def pr_number(root: Path, gate: str, inputs: Mapping[str, Any], hub: GitHub) -> int:
    """判定する PR の番号。無い・決められなければ `GateError`。

    リリースは文脈の `pr`。設計は作業場所（入力の `worktree`。無ければ `root`）のブランチの開いた PR で、文脈の `design_pr` は
    読まない（プランの経路の入力に無いので、`NDF_STAGE_INPUT` と `--machine` の両方で同じ PR を選ぶため。#3959 の決定 5）。
    作業場所が判定するリポジトリの worktree でなければ、そのブランチ名で PR を探さない。マージ先が判定するリポジトリで、head も
    同じリポジトリのもの（フォークの同名ブランチを除く）を数え、1 件に決まらなければ止める（どれにもコメントを書かない）。
    """
    if gate == RELEASE:
        return release_pr(inputs)
    where = Path(inputs["worktree"]) if isinstance(inputs.get("worktree"), str) else root
    if not _same_repo(where, root):
        raise GateError(f"作業場所 {where} が判定するリポジトリの worktree でない")
    branch = Git(where).run("rev-parse", "--abbrev-ref", "HEAD").strip()
    opened = hub.open_pulls_of_head(branch, enough=2)
    if not opened:
        raise GateError(f"ブランチ {branch} の開いた設計の PR が無い")
    if len(opened) > 1:
        raise GateError(f"ブランチ {branch} の開いた PR が複数あり、判定する PR を 1 つに決められない")
    return opened[0].number


def _repo_of(where: Mapping[str, Any]) -> Optional[str]:
    root = where.get("root")
    if root is None:
        return None
    try:
        return mvv.repo_key(Path(root))
    except mvv.MvvError:  # noqa: NDF401 外部コマンド: メインディレクトリを解けなければ、診断の記録の repo を null にして書く
        return None


def failure_text(kind: str, exc: BaseException | str, where: Mapping[str, Any]) -> str:
    """失敗の原因を、PR のコメントと承認資料へ載せてよい文（「<定型文>（診断の記録 <id>）」）に変える（#3959）。

    例外の文は返す文に入れない。例外の文には手元のパス・社内の Git ホスト名・他のリポジトリへの参照が入りえ、伏せる処理は
    形の決まらない値を見分けないためである。全文は秘密の値だけを伏せて手元の診断の記録（`mvv.record_failure`）へ書く。
    `where` の `gate`・`pr`・`head` を記録に写し（無ければ null）、`root` があれば記録の `repo` を解く。記録に書けなくても例外を
    出さず「<定型文>（診断の記録に書けない）」を返す。GitHub も公開範囲の照会も呼ばない。
    """
    text = mm.STOP_REASONS[kind]
    row = {
        "at": jsonl.now(),
        "repo": _repo_of(where),
        **{k: where.get(k) for k in ("gate", "pr", "head")},
        "kind": kind,
        "cause": run_log.redact(str(exc)),
    }
    try:
        ident = mvv.record_failure(row)
    except (OSError, internal.InternalError):
        return f"{text}（診断の記録に書けない）"
    return f"{text}（診断の記録 {ident}）"


def judge_with(ask: Ask) -> mvv.Judge:
    """`mvv.mvv_validate_sprint` へ渡す判定の関数。回答の判定の語と、費用・判定の中身（理由・レッドライン・根拠）を返す。"""
    schema = contracts.load_json(contracts.schema_path(VERDICT_SCHEMA))

    def judge(inputs: Mapping[str, str]) -> tuple[str, Mapping[str, Any]]:
        got = ask(inputs["question"], inputs["material"], schema, limits.seconds(JUDGE_LIMIT))
        if not got.ok:
            raise GateError(f"MVV 判定の回答が無い: {got.cause}")
        return str(got.answer["verdict"]), {"usd": got.cost, **{k: got.answer[k] for k in md.VERDICT_DETAILS}}

    return judge


def _verdict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"verdict": row["verdict"], **{k: row[k] for k in md.VERDICT_DETAILS if k in row}}


class _Judging:
    """PR を読んだ後の 1 回の判定（`_judged`）。失敗の原因は `failure_text` の定型文と診断の記録の識別子だけを結果に入れる。"""

    def __init__(self, root: Path, gate_name: str, hub: GitHub, number: int, pull: Pull) -> None:
        self.root, self.gate, self.hub, self.number, self.pull = root, gate_name, hub, number, pull
        self.where = {"root": root, "gate": gate_name, "pr": number, "head": pull.head_sha}

    def text(self, kind: str, exc: BaseException | str) -> str:
        return failure_text(kind, exc, self.where)

    def stopped(self, kind: str, why: list[str], noted: bool = True) -> dict[str, Any]:
        """判定を持たずに終わる。`noted` なら判定の記録を PR のコメントへ書く（材料が今の PR に属さないときは書かない）。"""
        out = mm.stop_outcome(self.gate, kind, why, self.pull.head_sha, pr=self.number)
        return _recorded(self.hub, self.number, out, self.where) if noted else out


def _base_snapshot(j: _Judging, inputs: Mapping[str, Any]) -> tuple[str, mvv.Snapshot, MvvFacts, str]:
    """手順 (2): PR のマージ先を取得し、ベースの MVV を 1 度だけ読んで事実を作る（リモート・MVV・事実・MVV の本文）。"""
    g = Git(j.root)
    remote = g.remote()
    g.fetch_branch_or_raise(remote, j.pull.base_ref)
    snap = mvv.current(j.root, md.signals_ref(j.pull.base_ref, remote), allow_mismatch=True)
    facts, project = snapshot_facts(j.root, snap, inputs)
    return remote, snap, facts, project


def _diff(j: _Judging, remote: str, snap: mvv.Snapshot) -> tuple[Optional[list[dict[str, Any]]], Optional[dict[str, Any]]]:
    """手順 (4): `snap.commit` と head の組の差分と、読み終えた後に PR が動いていないかの確かめ。止まるなら（None, 結果）。"""
    try:
        rows = mvv.compare_files(j.hub, Git(j.root), remote, snap.commit, j.pull.head_sha)
    except (GitHubError, GitError, mm.DiffError) as exc:
        return None, j.stopped("diff_unreadable", ["変えたファイルを読めない: " + j.text("diff_unreadable", exc)])
    try:
        again = j.hub.pull(j.number)
    except GitHubError as exc:
        why = ["判定の材料を読んだ後に PR を読み直せない: " + j.text("pr_reread_failed", exc)]
        return None, j.stopped("pr_reread_failed", why, noted=False)
    if (again.head_sha, again.base_ref) != (j.pull.head_sha, j.pull.base_ref):
        moved = f"{j.pull.base_ref}@{j.pull.head_sha} → {again.base_ref}@{again.head_sha}"
        return None, j.stopped("pr_moved", [j.text("pr_moved", moved)], noted=False)
    return rows, None


def _read_records(j: _Judging, warnings: list[dict[str, Any]]) -> tuple[Optional[list[dict[str, Any]]], list[jsonl.Broken]]:
    """手順 (5): 記録を 1 回だけ読む（再利用にだけ使う）。読めなければ None で、判定は再利用を省いて LLM で打つ。

    保留（`review_rows`・`review_holds`）は読まない（#3959 の実装計画の単位 3 が足す）。
    """
    try:
        rows, broken = mvv.validation_rows(j.root)
    except (OSError, UnicodeDecodeError, internal.InternalError):
        return None, []
    warnings += jsonl.broken_items(mvv.record_path("mvv-validation"), broken)
    return rows, broken


def _judged(
    root: Path, gate_name: str, inputs: Mapping[str, Any], open_hub: Callable[[Path], GitHub], ask: Ask, warnings: list[dict[str, Any]]
) -> dict[str, Any]:
    # 手順 (1): 判定する PR と head を決める。ここまでの失敗は `gate` が受ける（書く先の PR が決まっていない）
    hub = open_hub(root)
    number = pr_number(root, gate_name, inputs, hub)
    j = _Judging(root, gate_name, hub, number, hub.pull(number))
    try:
        remote, snap, facts, project = _base_snapshot(j, inputs)
    except (GitError, mvv.MvvError, jsonfile.JsonFileError, identity.IdentityError, OSError) as exc:
        return j.stopped("base_unreadable", ["ベースの MVV を読めない: " + j.text("base_unreadable", exc)])
    # 手順 (3): 検査 1 だけが判定を止める。承認済みの MVV が無いリポジトリの出力と PR を今と同じにする（コメントを書かない）
    unjudgeable = judgeable(facts)
    if unjudgeable is not None:
        return j.stopped("mvv_unapproved", [unjudgeable], noted=False)
    rows, stop = _diff(j, remote, snap)
    if rows is None:
        assert stop is not None
        return stop
    facts = mvv.pr_facts(hub, number, facts, rows, j.pull)
    held, broken = _read_records(j, warnings)
    # 手順 (6): 差分の全体が材料に載らない PR は、省いた部分を判定できないため LLM を呼ばずに人へ問う
    cut = mm.incomplete_patches(rows)
    if cut:
        return j.stopped("incomplete_patches", [*refusals(facts, gate_name), *cut])
    shared = mvv.principles().decode("utf-8")
    materials = {
        "NDF の共通原則": shared,
        "プロジェクト MVV": project,
        f"Pull Request #{number} の本文": facts.body if facts.body is not None else "",
        "変えたファイルと差分": mm.patch_material(rows),
    }
    asked = {"gate": gate_name, "question": mm.SYSTEM, "material": mm.prompt(mm.redlines(shared, project), materials)}
    # 手順 (7): 機械の検査 2〜6 が外れても判定は打つ（省かない理由として why に載る）
    try:
        row, unrecorded = mvv.mvv_validate_sprint(root, asked, judge_with(ask), reuse=held is not None, rows=held, broken=broken)
    except GateError as exc:
        return j.stopped("llm_no_answer", ["MVV 判定を打てない: " + j.text("llm_no_answer", exc), *refusals(facts, gate_name)])
    why = [] if unrecorded is None else ["判定を記録（mvv-validation.jsonl）に書けない: " + j.text("records_unwritable", unrecorded)]
    verdict = _verdict(row)
    # 手順 (8): 進め方と区分は省くかだけを決める。記録に行の無い判定では省かない
    ok, reasons = may_skip(facts, verdict, gate_name)
    skip = ok and unrecorded is None
    why += reasons
    if inputs.get("pace") not in MVV_PACES:
        skip, why = False, [f"進め方 {inputs.get('pace')} は承認ゲートを MVV 判定で省かない", *why]
    if gate_name not in MVV_SKIP_GATES:
        skip, why = False, [*why, RELEASE_KEPT]
    return _recorded(hub, number, mm.gate_outcome(gate_name, verdict, skip, why, j.pull.head_sha, pr=number), j.where)


def post_note(hub: GitHub, number: int, text: str) -> publish.Published:
    """判定の記録を PR のコメントへ書く（`publish.Publisher` を通す）。"""
    return publish.Publisher(hub).comment(number, text)


def _recorded(hub: GitHub, number: int, outcome: dict[str, Any], where: Mapping[str, Any]) -> dict[str, Any]:
    """判定の結果（省く・省かないの両方）を PR へ載せ、人が後から確かめられるようにする。

    書けなければ判定を保ったまま省かない側へ倒し、`noted` を偽にする（原因は `failure_text` の定型文と識別子だけ）。
    """
    try:
        made = post_note(hub, number, note(outcome["gate"], outcome["verdict"], outcome["why"], outcome["holds"]))
    except GitHubError as exc:
        why = [*outcome["why"], "判定の記録を PR に書けない: " + failure_text("note_unwritten", exc, where)]
        return {**outcome, "skip": False, "why": why, "noted": False}
    return {**outcome, "noted": True, "note_url": made.url}


def has_gate(mode: Any, gate_name: str) -> bool:
    """モード `mode` の定義が承認ゲート `gate_name` のステップを持つか（定義の無いモードは持たない）。"""
    if mode not in definition.modes():
        return False
    return any(st.kind == "gate" and st.gate == gate_name for st in definition.load_definition(str(mode)).steps())


def gate(
    root: Path,
    gate_name: str,
    inputs: Mapping[str, Any],
    hub: Callable[[Path], GitHub],
    ask: Ask = llm.ask,
    warnings: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """承認ゲートの直前の MVV 判定を打ち、出力 `mvv_gate` の値を返す（`mm.gate_outcome`）。進め方によらず判定を打つ（#3959）。

    判定のステップはモードを問わず流れ（工程の出力 `mvv_gate` をどのモードでもそろえる）、承認ゲートを持たないモードでは判定しない。
    PR を読む前の失敗だけをここで受け、判定を持たない結果にする（PR を読んだ後の失敗は `_judged` が受ける）。
    MVV 判定の記録に読めない行があれば `warnings` へ足す。
    """
    if not has_gate(inputs.get("mode"), gate_name):
        return mm.stop_outcome(gate_name, "no_gate", [f"モード {inputs.get('mode')} は承認ゲート {gate_name} を持たない"])
    try:
        return _judged(root, gate_name, inputs, hub, ask, [] if warnings is None else warnings)
    except (GateError, GitError, GitHubError, identity.IdentityError, OSError) as exc:
        why = ["MVV 判定を打てない: " + failure_text("no_pr", exc, {"root": root, "gate": gate_name})]
        return mm.stop_outcome(gate_name, "no_pr", why)
