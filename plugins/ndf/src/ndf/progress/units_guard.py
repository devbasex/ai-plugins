"""実装のプランを通すかを決める（#3702 の決定 12。issues/issue-3702-design-detail.md の「実装のプランの確かめ」）。

テンプレート `impl` のステップ `ndf progress units guard`（マージ済みの確かめの次で、実装の前）が打つ。分け方の承認が要る
スプリントでは、単位を流す処理が今の承認の記録で起動した単位だけを通すので、どの経路で組んだプランも承認していない分け方では流れない。
起動の記録は単位を流す処理が起動のたびに書く（`record_launch`。呼び手は `run.sprint_plan` が `SprintPlan.launched` に束ねる。
単位を流す処理がこのモジュールを import すると、承認の記録を読む層との間に import の輪ができる）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.contracts import internal
from ndf.core.plan import files as plan_files
from ndf.progress import command, design_record, model, run_view, stages, store, units_invoke
from ndf.progress.issue_values import UNITS_DIR

# 起動の記録の保存先（ステートマシンの作業の保存先の下）と、ファイル名に使うブランチの sha256 の先頭の字数
LAUNCH_DIR = f"{UNITS_DIR}/guard"
BRANCH_HASH_CHARS = 16


class Unreadable(Exception):
    """単位の指示のファイルを読めず、通すかを判定できない。"""


def _guard_path(work_root: Path, branch: str) -> Path:
    digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:BRANCH_HASH_CHARS]
    return work_root / LAUNCH_DIR / f"{digest}.json"


def _instruction_hash(unit_file: Optional[Path]) -> str:
    """単位の指示のファイルの中身の sha256。指示の無い単位は空文字。読めなければ `Unreadable`。"""
    if unit_file is None:
        return ""
    try:
        return hashlib.sha256(unit_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise Unreadable(f"単位の指示のファイル {unit_file} を読めない: {exc.strerror}") from None


class Approval(NamedTuple):
    """今の承認。`seq` は承認の回答の事象の番号（`design_record.approval_seq_of`）で、同じ HEAD の承認し直しを見分ける。"""

    record: Mapping[str, Any]
    seq: Optional[int]


class Instructions(NamedTuple):
    """単位の指示のファイル。`plan` は worker が読む `--unit-file`、`review` はレビューが読む環境変数 `NDF_UNIT_FILE` の値。"""

    plan: Optional[Path]
    review: Optional[Path]


def _guard_record(branch: str, base: str, approval: Approval, issues: Collection[int], digest: str) -> dict[str, Any]:
    """起動の記録の中身。"""
    return {
        "branch": branch,
        "base": base,
        "record_head": approval.record["review"]["head"],
        "approval_seq": approval.seq,
        "issues": sorted(set(issues)),
        "unit_sha256": digest,
    }


def record_launch(work_root: Path, branch: str, base: str, approval: Approval, issues: Collection[int], unit_file: Optional[Path]) -> None:
    """単位のブランチ `branch` の実装のプランを、承認 `approval` の下で起動することを残す。起動のたびに書き直す。"""
    content = _guard_record(branch, base, approval, issues, _instruction_hash(unit_file))
    plan_files.write_json(_guard_path(work_root, branch), content)


def _covers(context: Mapping[str, Any], issues: Collection[int], base: str) -> bool:
    """スプリントがプランの課題のどれかを含むか、スプリントブランチがプランのマージ先か。"""
    held = context["issues"] if "issues" in context else []
    return bool(set(issues) & {int(n) for n in held}) or ("branch" in context and context["branch"] == base)


def _sprints(st: store.Store, repo: str, issues: Collection[int], base: str) -> list[run_view.View]:
    """リポジトリの、終わっていないスプリントのうちプランに当たるもの。

    全事象は候補のスプリントの分だけ読む（過去のスプリントが増えても、無関係なプランの読み込みを増やさない）。候補は
    定義の文脈で絞り、後から書かれるスプリントブランチは、マージ先がスプリントブランチのときだけ出力の事象を読んで足す。
    当たりは全事象を読んだ今の文脈で決め直す（承認で外した課題は定義の文脈に残る）。
    """
    with store.transaction(st) as conn:
        definitions = store.read_sprint_definitions(conn, repo)
        outputs: dict[str, list[model.Event]] = {}
        if base.startswith(model.branch_prefix("sprint")):
            outputs = store.read_events_of_kind(conn, definitions.keys(), stages.OUTPUTS_EVENT)
        views = []
        for mid, (rev, saved) in definitions.items():
            initial = internal.read_record("stage-snapshot", saved)["context"]
            written = stages.recorded_outputs(outputs[mid] if mid in outputs else [], rev)
            if not _covers(stages.compose_context(initial, written), issues, base):
                continue
            c = command.read_current(conn, mid)
            views.append(run_view.View(mid, c.rev, c.snapshot, c.raw, c.events))
    return [v for v in views if not model.is_terminal(v.whole) and _covers(v.context(), issues, base)]


def _matches_launch(st: store.Store, view: run_view.View, branch: str, base: str, issues: Collection[int], digest: str) -> bool:
    """今の承認の記録で起動した単位の記録と、プランのブランチ・マージ先・課題・指示が一致するか。"""
    record = design_record.approved_of(view)
    if record is None:
        return False
    held = plan_files.read_json(_guard_path(run_view.work_root(st.root, view.machine_id), branch))
    approval = Approval(record, design_record.approval_seq_of(view))
    return held == _guard_record(branch, base, approval, issues, digest)


def guard(st: store.Store, repo: str, issues: Collection[int], base: str, branch: str, instructions: Instructions) -> list[str]:
    """プランを通さない理由。空なら通す。指示のファイルを読めなければ `Unreadable`。

    `issues` は実装の対象の全課題。承認が要るスプリントの起動の記録と一致し、レビューが読む指示も同じ中身なら通す。
    通せず、当たるスプリントのどれかが承認を要るなら止める。当たるスプリントが無く、マージ先がスプリントブランチなら、
    承認が要るかを判定できないので止める。
    """
    # 当たるスプリントが無くても、渡された指示を読めないプランは通さない
    digest = _instruction_hash(instructions.plan)
    found = _sprints(st, repo, issues, base)
    if not found:
        if not base.startswith(model.branch_prefix("sprint")):
            return []
        return [f"マージ先 {base} のスプリントが状態の保存先に無く、分け方の承認が要るかを判定できない"]
    gated = [v for v in found if design_record.required(v.snap)]
    if not gated:
        return []
    if _instruction_hash(instructions.review) != digest:
        return [
            f"分け方の承認が要るスプリントで、レビューが読む指示（環境変数 {units_invoke.UNIT_FILE_ENV}）が --unit-file と違う。"
            f"ndf progress units run {v.machine_id} を打つ"
            for v in gated
        ]
    if any(_matches_launch(st, v, branch, base, issues, digest) for v in gated):
        return []
    return [
        f"分け方の承認が要るスプリントで、単位を流す処理が起動したプラン（ブランチ・マージ先・課題・指示）と違う。"
        f"ndf progress units run {v.machine_id} を打つ"
        for v in gated
    ]
