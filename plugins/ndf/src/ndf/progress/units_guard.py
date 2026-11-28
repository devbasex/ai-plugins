"""実装のプランを通すかを決める（#3702 の決定 12。issues/issue-3702-design-detail.md の「実装のプランの確かめ」）。

テンプレート `impl` の最初のステップ `ndf progress units guard` が打つ。分け方の承認が要るスプリントでは、単位を流す処理が
今の承認の記録で起動した単位だけを通すので、どの経路で組んだプランも承認していない分け方では流れない。
起動の記録は単位を流す処理が起動のたびに書く（`record_launch`。呼び手は `run.sprint_plan` が `SprintPlan.launched` に束ねる。
単位を流す処理がこのモジュールを import すると、承認の記録を読む層との間に import の輪ができる）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf.core.plan import files as plan_files
from ndf.progress import command, design_record, model, run_view, store
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


def _guard_record(branch: str, base: str, record: Mapping[str, Any], issue: int, digest: str) -> dict[str, Any]:
    """起動の記録の中身。"""
    return {"branch": branch, "base": base, "record_head": record["review"]["head"], "issue": issue, "unit_sha256": digest}


def record_launch(work_root: Path, branch: str, base: str, record: Mapping[str, Any], issue: int, unit_file: Optional[Path]) -> None:
    """単位のブランチ `branch` の実装のプランを、承認の記録 `record` の下で起動することを残す。起動のたびに書き直す。"""
    content = _guard_record(branch, base, record, issue, _instruction_hash(unit_file))
    plan_files.write_json(_guard_path(work_root, branch), content)


def _sprints(st: store.Store, repo: str) -> list[run_view.View]:
    """リポジトリの、終わっていないスプリント。"""
    with store.transaction(st) as conn:
        read = [(m["id"], command.read_current(conn, m["id"])) for m in store.read_machines(conn, repo)]
    views = [run_view.View(mid, c.rev, c.snapshot, c.raw, c.events) for mid, c in read]
    return [v for v in views if not model.is_terminal(v.whole)]


def _covers(context: Mapping[str, Any], issue: int, base: str) -> bool:
    """スプリントがプランの課題を含むか、スプリントブランチがプランのマージ先か。"""
    issues = context["issues"] if "issues" in context else []
    return issue in [int(n) for n in issues] or ("branch" in context and context["branch"] == base)


def _matches_launch(st: store.Store, view: run_view.View, branch: str, base: str, issue: int, digest: str) -> bool:
    """今の承認の記録で起動した単位の記録と、プランのブランチ・マージ先・課題・指示が一致するか。"""
    record = design_record.approved_of(view)
    if record is None:
        return False
    held = plan_files.read_json(_guard_path(run_view.work_root(st.root, view.machine_id), branch))
    return held == _guard_record(branch, base, record, issue, digest)


def guard(st: store.Store, repo: str, issue: int, base: str, branch: str, unit_file: Optional[Path]) -> list[str]:
    """プランを通さない理由。空なら通す。指示のファイルを読めなければ `Unreadable`。

    承認が要るスプリントの起動の記録と一致すれば通す。一致せず、当たるスプリントのどれかが承認を要るなら止める。
    当たるスプリントが無く、マージ先がスプリントブランチなら、承認が要るかを判定できないので止める。
    """
    # 当たるスプリントが無くても、渡された指示を読めないプランは通さない
    digest = _instruction_hash(unit_file)
    found = [v for v in _sprints(st, repo) if _covers(v.context(), issue, base)]
    if not found:
        if not base.startswith(model.branch_prefix("sprint")):
            return []
        return [f"マージ先 {base} のスプリントが状態の保存先に無く、分け方の承認が要るかを判定できない"]
    gated = [v for v in found if design_record.required(v.snap)]
    if not gated or any(_matches_launch(st, v, branch, base, issue, digest) for v in gated):
        return []
    return [
        f"分け方の承認が要るスプリントで、単位を流す処理が起動したプラン（ブランチ・マージ先・課題・指示）と違う。"
        f"ndf progress units run {v.machine_id} を打つ"
        for v in gated
    ]
