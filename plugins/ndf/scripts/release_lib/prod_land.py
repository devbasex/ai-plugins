"""prod_land.py: 本番の取り込み（#3703）。本番ブランチへ入れるマージの 1 か所。

承認した head を、merge queue の状態・検査・PR の今の head を確かめてから、ブランチ戦略の宣言のマージの主体と方式
（`release_lib/land.py` の `land_approved`）で本番ブランチへ入れる。呼び手は `release-steps.py` の `_merge_prod_pr`・
`release_lib/trunk_release.py`・`release_lib/reviewed.py` の `merge_on_pr_approval`・`release_lib/strategy_route.py` の
`_merge_approved` の 4 か所で、ベースブランチへ入れるマージ（開発版の PR・`-sync` の PR）はここを通らない。

- 入れた（`merged`）: マージコミットを返す（読めなければ None）
- merge queue へ入れた・人のマージを待つ（`queued`・`human`）: `LandPending` を投げる。呼び手は公開を欠けとして 6 で返す
- 承認した head と今の head が違う: `HeadMoved` を投げる
- 止まった: `StepError`（検査の中身の失敗・閉じた PR・`land_approved` の stopped は 1、上限までの待ちは 75）
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import branch_strategy
import gh_call
import gh_graphql
import gh_parts
import limits
import repo as repo_lib
import waits
from release_lib import approval_trail, land as land_lib, reviewed
from release_lib.approval import HeadMoved
from step_result import EXIT_INFRA_WAIT, StepError, gh_json, git, result

TOOL = "release"
PR_QUEUE_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {"
    " pullRequest(number: $number) { number merged state isInMergeQueue } } }"
)
PENDING_REASONS = {"queued": "merge queue のマージを待つ", "human": "人のマージを待つ"}


class LandPending(Exception):
    """本番ブランチへまだ入っていない（merge queue へ入れた・人のマージを待つ）。公開を欠けとして返す。"""

    def __init__(self, result: str, reason: str, pr: int):
        super().__init__(reason)
        self.result, self.reason, self.pr = result, reason, pr


def pr_check_buckets(root, n):
    """PR のチェックの並び（`gh pr checks --json name,bucket`）。読めなければ空。"""
    p = gh_parts.gh(["pr", "checks", str(n), "--json", "name,bucket"], cwd=root)
    try:
        return json.loads(p.stdout or "[]") or []
    except ValueError:
        return []


def _repo(root) -> str:
    repo = gh_call.resolve_repo(None, cwd=root)
    if not repo:
        raise StepError("リポジトリ（origin の owner/repo）を決められない", 2)
    return repo


def _strategy(root):
    try:
        # メインディレクトリの宣言を読む（`land_cli` と同じ。作業ツリーの宣言は PR の変更で差し替えられる）
        return repo_lib.branch_strategy_of(root)
    except branch_strategy.StrategyError as e:
        raise StepError(e.reason, e.exit_code) from e


def queue_state(root, pr: int) -> str:
    """PR の `merged`・`state`・`isInMergeQueue` を 1 回の GraphQL の照会で読み、`merged`・`queued`・`open`・`closed` に写す。
    読めなければ StepError（merge queue に無いとはみなさない。merge queue に残る PR を二重に入れないため）。"""
    owner, _, name = _repo(root).partition("/")
    got = gh_graphql.graphql(PR_QUEUE_QUERY, {"owner": owner, "name": name, "number": int(pr)})
    node = ((got.value or {}).get("repository") or {}).get("pullRequest") if got.ok and isinstance(got.value, dict) else None
    if not isinstance(node, dict):
        raise StepError(f"PR #{pr} の merge queue の状態を読めない: {str(got.error or '応答に PR が無い')[:300]}")
    if node.get("merged"):
        return "merged"
    if str(node.get("state") or "").upper() == "OPEN":
        return "queued" if node.get("isInMergeQueue") else "open"
    return "closed"


def wait_queued(root, pr: int, timeout: float) -> str:
    """merge queue にある PR が `queued` から外れるまで、上限 `timeout` 秒まで読み直す。最後に読んだ状態を返す。"""
    policy = limits.policy("release-steps.ci-wait")._replace(seconds=timeout)
    got = waits.wait_until(lambda: queue_state(root, pr), lambda s: s != "queued", policy=policy)
    return got.value or "queued"


def landed_commit(root, pr: int, head: str) -> str | None:
    """ruleset の無い `strict` の早送りが PR を閉じて入れた取り込みを、本番ブランチの取り込みの記録から引く。
    記録の head が `head` と同じで、tree が一致し、記録の先端が最初の親のときだけ取り込みのコミットを返す。それ以外は None。"""
    strategy = _strategy(root)
    port = land_lib.GhPort(root, _repo(root), strategy)
    rec = port.landed_record(strategy.section["branches"]["production"], pr, None)
    if rec and rec.get("head") == str(head).lower() and rec.get("tree_ok") and rec.get("first_parent_is_tip"):
        return rec.get("commit")
    return None


def _merge_commit(root, pr: int) -> str | None:
    d = gh_json(root, ["pr", "view", str(pr), "--json", "mergeCommit"], "gh pr view") or {}
    return (d.get("mergeCommit") or {}).get("oid") or None


def land(root, a, pr: int, head: str, trail_url: str = "") -> str | None:
    """承認した head `head` を、宣言の主体と方式で本番ブランチ宛ての PR `pr` から入れる（判定は詳細設計の「`prod_land.land` の判定」）。
    戻ったときは PR が本番ブランチへ入っている。入っていなければ例外で抜ける。"""
    root = Path(root)
    state = queue_state(root, pr)
    if state == "queued":
        state = wait_queued(root, pr, float(a.ci_wait))
        if state == "queued":
            raise StepError(f"PR #{pr} が merge queue に残ったまま上限 {float(a.ci_wait):g} 秒までにマージされない", EXIT_INFRA_WAIT)
    if state == "merged":
        return _merge_commit(root, pr)  # 照会の直前に merge queue か人がマージした
    if state == "closed":
        raise StepError(f"PR #{pr} はマージされずに閉じている。開き直して承認を取り直してから打ち直す")
    strategy = _strategy(root)
    repo = _repo(root)
    prod = strategy.section["branches"]["production"]
    reviewed.wait_ci_except(
        root, pr, (approval_trail.STATUS_CONTEXT,), float(a.ci_wait), SimpleNamespace(pr_check_buckets=pr_check_buckets)
    )
    reviewed.wait_trail_status(root, repo, prod, head)
    port = land_lib.GhPort(root, repo, strategy)
    try:
        pull = port.pull(pr)
        if str(pull.get("head") or "").lower() != str(head).lower():
            raise HeadMoved(f"PR #{pr} の今の head {str(pull.get('head'))[:12]} が承認した {head[:12]} と違う")
        got = land_lib.land_approved(port, strategy, pr, land_lib.Approval(str(head).lower(), None, trail_url), pull=pull)
    except (land_lib.LandError, approval_trail.TrailError) as e:
        raise StepError(str(e)) from e
    if got["result"] == "merged":
        git(root, "fetch", "-q", "origin", check=False)
        return _merge_commit(root, pr)
    if got["result"] in PENDING_REASONS:
        raise LandPending(got["result"], got.get("reason") or PENDING_REASONS[got["result"]], pr)
    raise StepError(f"PR #{pr} を本番ブランチ {prod} へ入れられない: {got.get('reason') or got['result']}")


def pending_result(plugin: str, version: str, prod: str, tag: str, pending: LandPending, nxt: str, metrics: dict | None = None) -> dict:
    """公開の欠けの結果: `stopped`・`gaps` に公開の行・`next` に打ち直し（終了コード 6 で出す）。"""
    why = PENDING_REASONS.get(pending.result, pending.reason)
    item = {"kind": "pr", "name": f"#{pending.pr}", "result": pending.result, "base": prod}
    m = {**(metrics or {}), "channel": "prod", "version": version, "main_pr": pending.pr, "tag": None}
    summary = f"{plugin} v{version} の版上げの PR #{pending.pr} は {prod} へまだ入っていない（{why}）。タグと GitHub Release はまだ作らない"
    return result(TOOL, "stopped", summary, [item], m, None, nxt, gaps=[{"kind": "publish", "name": tag, "reason": why}])
