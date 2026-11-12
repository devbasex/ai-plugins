"""選んで集める取り込み方（`branch_flow.promotion: selected`。#3121）のリリース（`release-steps.py` の release-branch と
release から分けた）。

- リリースブランチを本番チャネルから組み、選んだリリースの単位（スプリントブランチ）だけを `--no-ff` でマージする（I5・E10）
- 本番チャネルへ直接 Pull Request を出し、先頭が承認したコミットに版上げだけを積んだものであるときだけマージする（決定 14）
- 本番チャネルへ入れた後に、リリースブランチをベースブランチへ戻すマージを開発版と同じ `-sync` のブランチで入れる（E13・決定 17）

どの取り込み方の行がこの経路を通るかは、呼び手が行の属性（`Promotion.selects_units`）で決める。出す（emit）のは呼び手である。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import approved_commit as ac
import branch_flow
from release_lib import approval, names
from release_lib.approval import HeadMoved
from step_result import EXIT_GATE, EXIT_UNREADABLE, StepError, emit, git


def build_branch(root, branch: str, production: str, units: list[str]) -> dict:
    """origin の `branch` を、本番チャネル `production` と選んだ単位 `units`（ブランチ名）のマージだけで組み、push する。

    origin に `branch` が無ければ本番チャネルの先頭から切る。有れば（組み直し）その先頭から、動いた本番チャネルと単位を
    マージで足す（強制 push をしない。決定 6）。組むのは一時の作業ツリーで、呼び手の作業ツリーに触れない。
    衝突すれば `branch_flow.ReleaseBuildError`（止めて人へ戻す。E10）。結果の欄（先頭と、組んだ時点の各先頭）を返す。"""
    if not units:
        raise branch_flow.ReleaseBuildError("選んだリリースの単位が無い（--units）")
    git(root, "fetch", "-q", "origin")
    have = git(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False).returncode == 0
    start = f"origin/{branch}" if have else f"origin/{production}"
    refs = [*([f"origin/{production}"] if have else []), *(f"origin/{u}" for u in units)]
    row = next(r for r in branch_flow.PROMOTIONS.values() if r.selects_units)
    tmp = Path(tempfile.mkdtemp(prefix="ndf-release-branch-"))
    wt = tmp / "wt"
    git(root, "worktree", "add", "-q", "--detach", str(wt), start)
    try:
        head = row.build_release_branch(wt, branch, start, refs)
        git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    finally:
        git(root, "worktree", "remove", str(wt), check=False)
        if not any(tmp.iterdir()):
            tmp.rmdir()
    heads = {u: git(root, "rev-parse", f"origin/{u}").stdout.strip() for u in units}
    return {
        "branch": branch,
        "head": head,
        "rebuilt": have,
        "production": git(root, "rev-parse", f"origin/{production}").stdout.strip(),
        "units": heads,
    }


def head_ref(root, branch: str | None) -> str:
    """`changed-plugins` が差分の終点にする ref。`branch`（承認に出すリリースブランチ）があれば origin から取り直したその先頭、
    無ければ HEAD。選んで集める取り込み方では、作業ツリーの HEAD に選ばなかったスプリントが入りうる。"""
    if not branch:
        return "HEAD"
    git(root, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return f"origin/{branch}"


def allowed_after(root, approved: str, branch: str, n: int) -> ac.AllowedPR:
    """承認したコミットの後に積んでよいコミット: origin の `branch` の first-parent のうちマージでないもの（版上げ・変更履歴・
    説明文・消費の記録）。承認の後にマージで足した単位のコミットはここに入らず、承認の外として止まる（決定 14）。"""
    git(root, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    tip = git(root, "rev-parse", f"origin/{branch}").stdout.strip()
    listed = git(root, "rev-list", "--first-parent", "--no-merges", f"{approved}..{tip}", check=False)
    return ac.AllowedPR(n, tip, listed.stdout.split() if listed.returncode == 0 else [])


def release_selected(root, a, plugin: str, plugins: list[str], branch: str, base: str, prod: str, approved: str, ops) -> tuple:
    """`branch`（リリースブランチ）→ 本番チャネルの Pull Request を使い回すか作り、承認と比べた先端だけをマージし、戻すマージを
    入れる。`ops` は `release-steps.py`（find_pr・create_pr・wait_and_merge・merge_commit_of・_merged_sync_pr・_merge_sync_pr と版の再試行の回数）。
    (本番の PR, マージコミット, items, metrics) を返す。承認の外の変更は承認ゲート（10）で止め、組み直しへ戻す。"""
    if a.channel != "prod":
        raise StepError("選んで集める取り込み方の release は --channel prod だけ（開発版はベースブランチで出す）", EXIT_UNREADABLE)
    tag = names.release_tag(plugin, a.version)
    metrics = {"channel": a.channel, "version": a.version, "release_pr": None, "sync_pr": None, "main_pr": None, "tag": None}
    metrics.update({"merge_commit": None, "plugins": ",".join(plugins), "promotion": a.promotion, "approved_sha": approved})
    items: list[dict] = []
    git(root, "fetch", "-q", "origin", "--tags")
    done = ops.find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":
        main_pr = done["number"]  # 再開: 本番チャネルへのマージは済んでいる（取り消せない）
        git(root, "fetch", "-q", "origin")
        merge = ops.merge_commit_of(root, main_pr) or git(root, "rev-parse", f"origin/{prod}").stdout.strip()
        if git(root, "merge-base", "--is-ancestor", approved, merge, check=False).returncode != 0:
            raise StepError(f"マージ済みの本番の PR #{main_pr} のコミット {merge[:8]} が承認したコミット {approved[:8]} を含まない")
        items.append({"kind": "pr", "name": f"#{main_pr}", "result": "already_merged", "base": prod})
    else:
        if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0:
            raise StepError(f"タグ {tag} は既にある")
        title = f"Release: {plugin} v{a.version} を {prod} へ"
        body = (
            f"{plugin} v{a.version} を {prod} へ出す（選んだスプリントブランチを {prod} から組んだリリースブランチ）。\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        main_pr = done["number"] if done else ops.create_pr(root, prod, branch, title, body)
        merge, verdict = _merge_approved(root, a, plugin, branch, main_pr, approved, items, metrics, ops)
        metrics.update({"compared_head": verdict.tip, "tolerated": len(verdict.tolerated)})
        items += approval.tolerated_items(verdict)
        items.append({"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod})
        git(root, "fetch", "-q", "origin")
        merge = merge or git(root, "rev-parse", f"origin/{prod}").stdout.strip()
    metrics["sync_pr"], sync_merged = merge_back(root, a, plugin, branch, base, prod, main_pr, ops)
    items.append({"kind": "pr", "name": f"#{metrics['sync_pr']}", "result": sync_merged, "base": base, "head": f"{branch}-sync"})
    return main_pr, merge, items, metrics


def _merge_approved(root, a, plugin, branch, main_pr, approved, items, metrics, ops):
    """承認したコミットと比べ（`allowed_after`）、比べた先端だけを本番チャネルへ入れる。先端が動けば比べ直す。(マージ, 判定)。"""
    by = approval.approved_by(root, a)
    for left in range(ops.HEAD_MOVED_RETRIES, -1, -1):
        verdict = approval.check_approved(root, approved, branch, allowed_after(root, approved, branch, main_pr))
        if not verdict.ok:
            emit(approval.gate(root, a, plugin, branch, verdict, items, metrics), EXIT_GATE)
        try:
            return ops.wait_and_merge(root, main_pr, expect=verdict.tip, ci_wait=a.ci_wait, gate_approved=by), verdict
        except HeadMoved:
            if not left:
                emit(approval.gate(root, a, plugin, branch, verdict, items, metrics, moved=True), EXIT_GATE)
    raise StepError("本番の PR をマージできない")  # 届かない（最後の回は gate で出る）


def merge_back(root, a, plugin, branch, base, prod, main_pr, ops) -> tuple[int, str]:
    """本番チャネルへ入れたリリースブランチを、`<リリースブランチ>-sync` でベースブランチへ戻す（E13・決定 17）。版数の衝突は本番の版を
    取る。前の回が戻し済みなら作り直さない。(PR 番号, 結果) を返す。"""
    if found := ops._merged_sync_pr(root, branch, base):
        return found, "already_merged"
    lead = f"{plugin} v{a.version} を {prod} へ出した（#{main_pr}）ため、リリースブランチを {base} へ戻す"
    return ops._merge_sync_pr(root, a, plugin, branch, base, main_pr, lead=lead)[0], "merged"
