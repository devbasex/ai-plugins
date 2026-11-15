"""merged-steps.py の promote が本番の PR のマージの前後で照らす承認の証跡と、管理者の迂回の記録（#2773 の決定 14・#3506）。

マージの本体（`merge_when_green`）は `merged_lib/merge.py` が持ち、ここはその前に置く証跡・照らすレビューと、
マージの後に書く迂回の記録だけを持つ。
"""

from __future__ import annotations

import gh_parts
import repo as repo_lib
from merged_lib.checks import TOOL
from release_lib import approval_trail, reviewed
from step_result import EXIT_GATE, EXIT_PRECONDITION, StepError, emit, result


def admin_after(root, repo, case, n, sha, trail_url):
    """promote が merge_when_green へ渡す、迂回の記録を書く処理（#3506 の I7）。マージした行があれば記録を書き、マージ済みを
    見たときは再開として利用者の証跡と先端を照らしてから書く。書けなければ報告の文を返す（決定 7）。"""

    def run(items: list[dict]) -> str | None:
        if any(i.get("kind") == "pr" and i.get("result") == "merged" for i in items):
            row = reviewed.admin_record_item(repo, n, sha, trail_url)
        elif any(i.get("kind") == "pr" and i.get("result") == "already_merged" for i in items):
            row = reviewed.admin_record_on_resume(root, repo, case, n)
        else:
            row = None
        if row:
            items.append(row)
        return "迂回の記録を書けなかった（本番は出ている）" if row and row.get("result") == "unwritten" else None

    return run


def trail_before_merge(root, a, n, case) -> dict | None:
    """promote のマージの前（#2773 の決定 14）: 場合 1 は照合した先端の証跡を置き、場合 2 は有効な人の承認のレビューのコミットと
    先端を照らす。照合する先端は a.expect_head（承認資料の承認したコミット）。場合 2 で一致しなければ承認ゲート（10）で止める。
    場合 1 で先端が既に動いていれば証跡を書かずに None を返し、merge-when-green の先端の照合（head_moved で 1）に止めさせる。
    照らした証跡かレビューの結果の項目を返す。"""
    sha, repo = getattr(a, "expect_head", None), promote_repo(root)
    if not sha or not repo:
        raise StepError(
            "CD のある場合の promote には照合する承認したコミット（--approved-from か --expect-head-from）とリポジトリが要る",
            EXIT_PRECONDITION,
        )
    redo = "リリース承認ゲートからやり直す: プランなら run <プラン> --from promote（fast / auto は --from verify）"
    if case.user_trail_only and a.gate_approved != "user":
        what = f"リリースの場合 {case.case} では、MVV 判定で本番チャネルの承認レビューを代えない（利用者の承認が要る）"
        emit(result(TOOL, "gate", f"#{n} をマージしない: {what}", [], {"approved_sha": sha, "case": case.case}, None, redo), EXIT_GATE)
    try:
        if case.trail_approval:
            written, url = approval_trail.write_trail(repo, n, sha, a.gate_approved)
            # PR を開いた時点の failure（証跡が無い）を workflow が付け直すまで待つ。待たないと merge-when-green が落ちたチェックとして止まる
            reviewed.wait_trail_status(root, repo, a.base, sha)
            return {"kind": "comment", "name": url or f"#{n}", "result": "written" if written else "kept", "approved_sha": sha}
        got = approval_trail.read_review_commit(repo, n)
    except approval_trail.TrailError as e:
        if e.kind != "head_moved":
            raise StepError(str(e)) from e
        return None  # 証跡を書かない。マージは --expect-head の照合が止める
    if got != sha:
        what = "有効な人の承認のレビューが無い" if got is None else f"承認のレビュー {got[:8]} が承認したコミット {sha[:8]} と違う"
        emit(result(TOOL, "gate", f"#{n} をマージしない: {what}", [], {"approved_sha": sha, "review_commit": got}, None, redo), EXIT_GATE)
    return {"kind": "review", "name": f"#{n}", "result": "approved", "approved_sha": got}


def promote_repo(root) -> str | None:
    """`owner/repo`。origin の URL から読み、読めなければ gh に聞く。"""
    if got := repo_lib.owner_repo(root):
        return got
    p = gh_parts.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=root)
    return p.stdout.strip() or None if p.returncode == 0 else None
