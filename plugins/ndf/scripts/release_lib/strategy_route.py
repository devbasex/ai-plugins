"""strategy_route.py: 取り込み方の値から、版上げの PR の起点・宛先・同期の PR の要否を決める（#3672 の D17）。

取り込み方の名前で振る舞いを分ける分岐は `route_of` の 1 か所に置く（本体は依存の無い `release_lib/route.py`。計画の組み立てが
GitHub の照会の部品を読まずに使えるよう分けた。#3703）。

| 取り込み方 | 承認の対象 | 版上げの PR | 起点 → 宛先 | 同期の PR |
| --- | --- | --- | --- | --- |
| `merge_base_branch` | `release_pr` | 有り（版数の置き場が `none` なら base → production の PR そのもの。`bump` が偽） | base → production | 有り |
| `release_branch` | `release_pr` | 有り（選んだスプリントを集めたリリースブランチ） | production → production | 有り |
| `trunk` | `release_pr` | 有り（版数と CHANGELOG だけ） | production → production | 無し |
| `trunk` | `production_pr` | 無し（スプリントの PR が本番の PR を兼ねる） | — | 無し |

選んで集める取り込み方（`release_branch`。行の属性 `selects_units`）のリリース（`release-steps.py` の release-branch と
release）もここに置く（D17）。

- リリースブランチを本番ブランチから組み、選んだリリースの単位（スプリントブランチ）だけを `--no-ff` でマージする
- 本番ブランチへ直接 Pull Request を出し、先頭が承認したコミットに版上げだけを積んだものであるときだけマージする
- 本番ブランチへ入れてタグと GitHub Release を作った後に、リリースブランチをベースブランチへ戻すマージを `-sync` の
  ブランチで入れる。戻すマージの失敗は本番の完了と分けて報告する

どの取り込み方の行がこの経路を通るかは、呼び手が行の属性で決める。出す（emit）のは呼び手である。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import NamedTuple

import approved_commit as ac
import repo as repo_lib
import strategy_rows
from release_lib import approval, names, reviewed
from release_lib.approval import HeadMoved
from release_lib.context import ReleaseCtx
from release_lib.land_read import merged_or_tip
from release_lib.route import Route, route_of  # noqa: F401  取り込み方の分岐の正本（呼び手はここから読む）
from step_result import EXIT_GATE, EXIT_UNREADABLE, StepError, emit, git


def admin_bypass(root) -> bool:
    """管理者の迂回でマージするか。宣言（メインディレクトリ）の `merge` が `method: direct` で `admin: true` のときだけ（#3672 の I6）。
    宣言を読めなければ StepError で止める（迂回の有無を既定へ倒さない）。"""
    import branch_strategy

    try:
        merge = repo_lib.branch_strategy_of(root).section["merge"]
    except branch_strategy.StrategyError as e:
        raise StepError(e.reason, e.exit_code) from e
    return merge.get("admin") is True and merge.get("method") == "direct"


class ReleaseBranches(NamedTuple):
    """選んで集める取り込み方のリリースが使う 3 つのブランチの役割。"""

    release: str  # リリースブランチ
    base: str  # 戻すマージの宛先のベースブランチ（開発版のチャネル）
    production: str  # 本番チャネル


def build_branch(root, branch: str, production: str, units: list[str]) -> dict:
    """origin の `branch` を、本番チャネル `production` と選んだ単位 `units`（ブランチ名）のマージだけで組み、push する。

    origin に `branch` が無ければ本番チャネルの先頭から切る。有れば（組み直し）その先頭から、動いた本番チャネルと単位を
    マージで足す（強制 push をしない。決定 6）。組むのは一時の作業ツリーで、呼び手の作業ツリーに触れない。
    衝突すれば `strategy_rows.ReleaseBuildError`（止めて人へ戻す。E10）。結果の欄（先頭と、組んだ時点の各先頭）を返す。"""
    if not units:
        raise strategy_rows.ReleaseBuildError("選んだリリースの単位が無い（--units）")
    git(root, "fetch", "-q", "origin")
    have = git(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False).returncode == 0
    start = f"origin/{branch}" if have else f"origin/{production}"
    refs = [*([f"origin/{production}"] if have else []), *(f"origin/{u}" for u in units)]
    row = next(r for r in strategy_rows.ROWS.values() if r.selects_units)
    tmp = Path(tempfile.mkdtemp(prefix="ndf-release-branch-"))
    wt = tmp / "wt"
    git(root, "worktree", "add", "-q", "--detach", str(wt), start)
    try:
        head = _build_release_branch(row, wt, branch, start, refs)
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


def _build_release_branch(row: strategy_rows.Row, root, branch: str, production_ref: str, units: list[str]) -> str:
    """`production_ref` から `branch` を切り、`units` を `--no-ff` で順にマージして先頭の SHA を返す。

    単位を選ばない行（`row.selects_units` が偽）では使わない（ValueError）。衝突すれば取り消して ReleaseBuildError（E10）。
    同じ衝突の解き方は rerere で再利用し、rerere が衝突をすべて解いたマージはそのままコミットして進む。組むのは detached HEAD で、
    ローカルの `branch` を作らず動かさない。送るのは呼び手が HEAD を送る。"""
    if not row.selects_units:
        raise ValueError(f"取り込み方 {row.name} はリリースの単位を選ばない")
    if not units:
        raise strategy_rows.ReleaseBuildError("選んだリリースの単位が無い")
    p = strategy_rows.git_run(root, "checkout", "-q", "--detach", production_ref)
    if p.returncode != 0:
        raise strategy_rows.ReleaseBuildError(f"{production_ref} から {branch} を切れない: {p.stderr.strip()}")
    for u in units:
        if strategy_rows.ancestor_of(root, u, "HEAD"):
            continue
        subject = strategy_rows.unit_merge_subject(u, branch)
        p = strategy_rows.git_run(
            root, "-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true", "merge", "--no-ff", "--no-edit", "-m", subject, u
        )
        if p.returncode != 0 and not strategy_rows.commit_resolved_merge(root, subject):
            strategy_rows.git_run(root, "merge", "--abort")
            raise strategy_rows.ReleaseBuildError(
                f"{u} を {branch} へマージできない（衝突は人が解く）: {(p.stdout + p.stderr).strip()[-400:]}"
            )
    return strategy_rows.git_run(root, "rev-parse", "HEAD").stdout.strip()


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


def release_selected(ctx: ReleaseCtx, approved, ops, case=None) -> tuple:
    """`branch`（リリースブランチ）→ 本番チャネルの Pull Request を使い回すか作り、承認と比べた先端だけをマージする。
    `ops` は `release-steps.py`（find_pr・create_pr・land（本番の取り込み）・merge_commit_of・_merged_sync_pr・_merge_sync_pr と版の再試行の
    回数）。(本番の PR, マージコミット, items, metrics, 戻すマージ) を返し、戻すマージ（`merge_back_after`）は呼び手が公開の後に打つ。
    承認の外の変更は承認ゲート（10）で止め、組み直しへ戻す。`case` が CD のある場合（#2773）なら、承認したコミットを引数で受けず、
    本番の PR の先端と PR の上の承認（証跡かレビュー）の完全な一致だけで通す（`_merge_exact`。許可リストの許容を使わない）。"""
    root, a, plugin, branch, base, prod = ctx.root, ctx.a, ctx.plugin, ctx.branch, ctx.base, ctx.prod
    branches = ReleaseBranches(release=branch, base=base, production=prod)
    if a.channel != "prod":
        raise StepError("選んで集める取り込み方の release は --channel prod だけ（開発版はベースブランチで出す）", EXIT_UNREADABLE)
    metrics = ctx.prod_metrics(release_strategy=getattr(a, "release_strategy", None), approved_sha=approved)
    items: list[dict] = []
    git(root, "fetch", "-q", "origin", "--tags")
    done = ops.find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":
        main_pr = done["number"]  # 再開: 本番チャネルへのマージは済んでいる（取り消せない）
        merge, rows = _resume_merged(ctx, main_pr, approved, case, ops)
        items += rows
    else:
        names.require_untagged(root, plugin, a.version, git=git)
        title = f"Release: {plugin} v{a.version} を {prod} へ"
        body = (
            f"{plugin} v{a.version} を {prod} へ出す（選んだスプリントブランチを {prod} から組んだリリースブランチ）。\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        main_pr = done["number"] if done else ops.create_pr(root, prod, branch, title, body)
        if case is not None:
            metrics["case"] = case.case
            merge = _merge_exact(ctx, main_pr, case, items, metrics, ops)
        else:
            merge, verdict = _merge_approved(ctx, main_pr, approved, items, metrics, ops)
            metrics.update({"compared_head": verdict.tip, "tolerated": len(verdict.tolerated)})
            items += approval.tolerated_items(verdict)
        admin = case is not None and case.admin_merge
        items.append({"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod, "admin": admin})
        git(root, "fetch", "-q", "origin")
        merge = merged_or_tip(root, merge, prod, git)
    return main_pr, merge, items, metrics, reviewed.admin_back(merge_back_after(root, a, plugin, branches, main_pr, ops))


def _resume_merged(ctx: ReleaseCtx, main_pr, approved, case, ops) -> tuple[str, list[dict]]:
    """再開: マージ済みの本番の PR のマージコミットが承認したコミットを含むかを確かめ、マージし直さない。
    (マージコミット, 足す行) を返す。迂回でマージしたのに記録が無ければ書く（#3506 の I7）。"""
    root, prod = ctx.root, ctx.prod
    git(root, "fetch", "-q", "origin")
    merge = merged_or_tip(root, ops.merge_commit_of(root, main_pr), prod, git)
    if approved and git(root, "merge-base", "--is-ancestor", approved, merge, check=False).returncode != 0:
        raise StepError(f"マージ済みの本番の PR #{main_pr} のコミット {merge[:8]} が承認したコミット {approved[:8]} を含まない")
    rows = [{"kind": "pr", "name": f"#{main_pr}", "result": "already_merged", "base": prod}]
    if case is not None and (row := reviewed.admin_record_on_resume(root, repo_lib.owner_repo(root), case, main_pr)):
        rows.append(row)
    return merge, rows


def _merge_approved(ctx: ReleaseCtx, main_pr, approved, items, metrics, ops):
    """承認したコミットと比べ（`allowed_after`）、比べた先端だけを本番の取り込み（`ops.land`）で本番チャネルへ入れる。先端が動けば比べ直す（I9）。
    (マージ, 判定)。merge queue へ入れた・人のマージを待つときは `prod_land.LandPending` で抜ける。"""
    root, a, plugin, branch = ctx.root, ctx.a, ctx.plugin, ctx.branch
    for left in range(ops.HEAD_MOVED_RETRIES, -1, -1):
        verdict = approval.check_approved(root, approved, branch, allowed_after(root, approved, branch, main_pr))
        if not verdict.ok:
            emit(approval.gate(root, a, plugin, branch, verdict, items, metrics), EXIT_GATE)
        try:
            return ops.land(root, a, main_pr, verdict.tip), verdict
        except HeadMoved:
            if not left:
                emit(approval.gate(root, a, plugin, branch, verdict, items, metrics, moved=True), EXIT_GATE)
    raise StepError("本番の PR をマージできない")  # 届かない（最後の回は gate で出る）


def _merge_exact(ctx: ReleaseCtx, main_pr, case, items, metrics, ops):
    """CD のある場合（#2773 の決定 7）: 本番の PR の先端が PR の上の承認と完全に一致するときだけ、1 回だけマージする（迂回は `cd-admin` だけ。#3506）。
    承認の後のコミットの追加・組み直しで先端が動けば承認ゲート（10）で止め、入れ直さない。リリースブランチは `ctx.branch`、本番チャネルは `ctx.prod`。"""
    root, a, plugin, branch, prod = ctx.root, ctx.a, ctx.plugin, ctx.branch, ctx.prod
    redo = f"release-steps.py release-branch で組み直したら承認資料を作り直し、本番の PR #{main_pr} の新しい先端で承認を取り直して打ち直す"

    def waiting(head, judged):
        return f"{plugin} v{a.version} のリリース承認を待つ（本番の PR #{main_pr}・{branch} の先端 {head[:8]}）: {judged.description}", redo

    def moved(head):
        return f"{plugin} v{a.version} のリリースを止めた: 本番の PR #{main_pr} の先端が承認した {head[:8]} から動いた", redo

    return reviewed.merge_on_pr_approval(
        root, repo_lib.owner_repo(root), a, case, main_pr, prod, items, metrics, ops, waiting=waiting, moved=moved
    )


def merge_back(root, a, plugin, branches: ReleaseBranches, main_pr, ops) -> tuple[int, str]:
    """本番チャネルへ入れたリリースブランチを、`<リリースブランチ>-sync` でベースブランチへ戻す（E13・決定 17）。版数の衝突は本番の版を、
    CHANGELOG.md の同じ版の節の衝突はリリースブランチの節を取る（開発版のデプロイがベースブランチへ同じ見出しの節を書くため）。
    前の回が戻し済みなら作り直さない。(PR 番号, 結果) を返す。"""
    branch, base, prod = branches.release, branches.base, branches.production
    if found := ops._merged_sync_pr(root, branch, base):
        return found, "already_merged"
    lead = f"{plugin} v{a.version} を {prod} へ出した（#{main_pr}）ため、リリースブランチを {base} へ戻す"
    return ops._merge_sync_pr(root, a, plugin, branch, base, main_pr, lead=lead, sections=True)[0], "merged"


def merge_back_after(root, a, plugin, branches: ReleaseBranches, main_pr, ops):
    """公開（タグ・GitHub Release）の後に打つ戻すマージ（`release-steps.py` の `_published` の `back`）。本番は出ているため、戻すマージで
    止まっても本番の完了と分けて報告する: 結果の項目を足し、(状態, 終了コード, 要約の後半) を返す。打ち直すと本番のマージと公開は
    済みとして飛ばし、戻すマージからやり直す。"""

    branch, base = branches.release, branches.base

    def back(items: list[dict], metrics: dict, done: str) -> tuple[str, int, str]:
        sync = {"kind": "pr", "base": base, "head": f"{branch}-sync"}
        try:
            metrics["sync_pr"], merged = merge_back(root, a, plugin, branches, main_pr, ops)
        except StepError as e:
            items.append({**sync, "name": f"{branch}-sync", "result": "failed", "reason": str(e)[:300]})
            return "stopped", e.code or 1, f"{done}。{base} へ戻すマージで止まった（本番は出ている）: {str(e)[:200]}"
        items.append({**sync, "name": f"#{metrics['sync_pr']}", "result": merged})
        return "ok", 0, done

    return back
