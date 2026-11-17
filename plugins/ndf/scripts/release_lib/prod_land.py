"""prod_land.py: 本番の取り込み（#3703）。本番ブランチへ入れるマージの 1 か所。

承認した head を、merge queue の状態・検査・PR の今の head を確かめてから、ブランチ戦略の宣言のマージの主体と方式
（`release_lib/land.py` の `land_approved`）で本番ブランチへ入れる。呼び手は `release-steps.py` の `_merge_prod_pr`・
`release_lib/trunk_release.py`・`release_lib/reviewed.py` の `merge_on_pr_approval`・`release_lib/strategy_route.py` の
`_merge_approved` の 4 か所で、ベースブランチへ入れるマージ（開発版の PR・`-sync` の PR）はここを通らない。

- 入れた（`merged`）: マージコミットを返す（早送りで閉じた PR は取り込みの記録のコミット。読めなければ None）
- merge queue へ入れた・人のマージを待つ（`queued`・`human`）: `LandPending` を投げる。呼び手は公開を欠けとして 6 で返す
- 承認した head と今の head が違う: `HeadMoved` を投げる
- 止まった: `StepError`（検査の中身の失敗・閉じた PR・`land_approved` の stopped は 1、上限までの待ちは 75）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import gh_call
import gh_graphql
import gh_parts
import limits
import waits
from release_lib import approval_trail, land as land_lib, land_checks, reviewed, strategy_route
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
    # メインディレクトリの宣言を読む（`land_cli` と同じ。作業ツリーの宣言は PR の変更で差し替えられる）
    return strategy_route.strategy_or_stop(root)


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
    return _recorded_commit(port, strategy.section["branches"]["production"], pr, head)


def _recorded_commit(port, prod: str, pr: int, head: str) -> str | None:
    rec = port.landed_record(prod, pr, None)
    if rec and rec.get("head") == str(head).lower() and rec.get("tree_ok") and rec.get("first_parent_is_tip"):
        return rec.get("commit")
    return None


def pr_no_checks_reason(root, pr: int) -> str | None:
    """PR に検査が 1 件も無いときに待つ理由（`land_checks.no_checks_reason`）。None なら PR の head で動く CI が無く、待たずに進める。
    PR を読めなければその理由を返す（CI が無いとはみなさない）。"""
    port = land_lib.GhPort(root, _repo(root), _strategy(root))
    try:
        pull = port.pull(pr)
    except land_lib.LandError as e:
        return str(e)
    return land_checks.no_checks_reason(port, pull["head"], pull)


def _merge_commit(root, pr: int) -> str | None:
    d = gh_json(root, ["pr", "view", str(pr), "--json", "mergeCommit"], "gh pr view") or {}
    return (d.get("mergeCommit") or {}).get("oid") or None


@dataclass(frozen=True)
class LandingIO:
    """本番の取り込み（`land`）が使う外部の操作（宣言・リポジトリ・merge queue・検査の待ち・GitHub の Port・git）。"""

    strategy: Callable  # (root) -> ブランチ戦略の宣言（メインディレクトリ）
    repo: Callable  # (root) -> owner/repo
    queue_state: Callable  # (root, pr) -> merged・queued・open・closed
    wait_queued: Callable  # (root, pr, timeout) -> 待った後の状態
    port: Callable  # (root, repo, strategy) -> GitHub の Port（`land_lib.GhPort`）
    merge_commit: Callable  # (root, pr) -> マージコミット（読めなければ None）
    fetch: Callable  # (root) -> None。origin を取り直す
    wait_ci: Callable  # (root, pr, timeout) -> None。承認の証跡の外の検査を待つ
    wait_trail: Callable  # (root, repo, prod, head) -> None。承認の証跡の検査を待つ


def _wait_checks_but_trail(root, pr: int, timeout: float) -> None:
    ops = SimpleNamespace(pr_check_buckets=pr_check_buckets, no_checks_reason=pr_no_checks_reason)
    reviewed.wait_ci_except(root, pr, (approval_trail.STATUS_CONTEXT,), timeout, ops)


def default_io() -> LandingIO:
    """呼ぶ時点のこのモジュールの実装で組んだ `LandingIO`（`land` が `io` を省いたときに使う）。"""
    return LandingIO(
        strategy=_strategy,
        repo=_repo,
        queue_state=queue_state,
        wait_queued=wait_queued,
        port=land_lib.GhPort,
        merge_commit=_merge_commit,
        fetch=lambda root: git(root, "fetch", "-q", "origin", check=False),
        wait_ci=_wait_checks_but_trail,
        wait_trail=reviewed.wait_trail_status,
    )


def land(root, a, pr: int, head: str, trail_url: str = "", *, io: LandingIO | None = None, admin_ok: bool = True) -> str | None:
    """承認した head `head` を、宣言の主体と方式で本番ブランチ宛ての PR `pr` から入れる（判定は詳細設計の「`prod_land.land` の判定」）。
    `admin_ok` が偽なら宣言の迂回を使わない（リリースの場合が迂回の場合でない。`land_approved` の `allow_admin`）。
    戻ったときは PR が本番ブランチへ入っている。入っていなければ例外で抜ける。外部の操作は `io`（省けば `default_io()`）越しに呼ぶ。"""
    io = io or default_io()
    root = Path(root)
    state = io.queue_state(root, pr)
    if state == "queued":
        state = io.wait_queued(root, pr, float(a.ci_wait))
        if state == "queued":
            raise StepError(f"PR #{pr} が merge queue に残ったまま上限 {float(a.ci_wait):g} 秒までにマージされない", EXIT_INFRA_WAIT)
    if state == "merged":
        # 照会の直前か待つ間に merge queue か人がマージした。入った head が承認した head と違えば公開へ進ませない（I2）
        _require_merged_head(io, root, pr, head)
        return io.merge_commit(root, pr)
    if state == "closed":
        raise StepError(f"PR #{pr} はマージされずに閉じている。開き直して承認を取り直してから打ち直す")
    strategy = io.strategy(root)
    repo = io.repo(root)
    prod = strategy.section["branches"]["production"]
    io.wait_ci(root, pr, float(a.ci_wait))
    io.wait_trail(root, repo, prod, head)
    port = io.port(root, repo, strategy)
    try:
        pull = port.pull(pr)
        if str(pull.get("head") or "").lower() != str(head).lower():
            raise HeadMoved(f"PR #{pr} の今の head {str(pull.get('head'))[:12]} が承認した {head[:12]} と違う")
        got = land_lib.land_approved(
            port, strategy, pr, land_lib.Approval(str(head).lower(), None, trail_url), pull=pull, allow_admin=admin_ok
        )
    except (land_lib.LandError, approval_trail.TrailError) as e:
        raise StepError(str(e)) from e
    if got["result"] == "merged":
        io.fetch(root)
        # ruleset の無い strict の早送りは PR を閉じるだけで mergeCommit が無い。取り込みのコミットを記録から引き、本番ブランチの
        # 先端（取り込みの後に入った承認の外の変更を含みうる）へタグを打たせない
        return io.merge_commit(root, pr) or _recorded_commit(port, prod, pr, head)
    if got["result"] in PENDING_REASONS:
        raise LandPending(got["result"], got.get("reason") or PENDING_REASONS[got["result"]], pr)
    raise StepError(f"PR #{pr} を本番ブランチ {prod} へ入れられない: {got.get('reason') or got['result']}")


def _require_merged_head(io: LandingIO, root, pr: int, head: str) -> None:
    """マージ済みの PR `pr` の head が承認した `head` と同じことを確かめる。違えば（承認の後に積んだコミットごと人がマージした）、
    読めなければ StepError（1）。マージ済みのため入れ直せず、承認ゲートでなく止まりで返す。"""
    try:
        merged = str(io.port(root, io.repo(root), io.strategy(root)).pull(pr).get("head") or "").lower()
    except land_lib.LandError as e:
        raise StepError(str(e)) from e
    if merged != str(head).lower():
        raise StepError(
            f"本番ブランチへ入った PR #{pr} の head {merged[:12] or '（読めない）'} が承認した {str(head)[:12]} と違う。公開しない"
        )


def release_retry_command(a) -> str:
    """本番の release の打ち直しのコマンド（`a` は release の引数）。開始時の承認の渡し方を引き継ぐ: 承認資料があれば --approval、
    承認したコミットで始めたなら --approved-sha を添える（落とすと再開で承認待ち・引数不足で止まる。2 つは同時に渡せない）。"""
    cmd = f"release-steps.py release --version {a.version} --channel prod"
    if material := getattr(a, "approval", None):
        return f"{cmd} --approval {material}"
    if approved := getattr(a, "approved_sha", None):
        return f"{cmd} --approved-sha {approved}"
    return cmd


def pending_result(plugin: str, version: str, prod: str, tag: str, pending: LandPending, nxt: str, metrics: dict | None = None) -> dict:
    """公開の欠けの結果: `stopped`・`gaps` に公開の行・`next` に打ち直し（終了コード 6 で出す）。"""
    why = PENDING_REASONS.get(pending.result, pending.reason)
    item = {"kind": "pr", "name": f"#{pending.pr}", "result": pending.result, "base": prod}
    m = {**(metrics or {}), "channel": "prod", "version": version, "main_pr": pending.pr, "tag": None}
    summary = f"{plugin} v{version} の版上げの PR #{pending.pr} は {prod} へまだ入っていない（{why}）。タグと GitHub Release はまだ作らない"
    return result(TOOL, "stopped", summary, [item], m, None, nxt, gaps=[{"kind": "publish", "name": tag, "reason": why}])
