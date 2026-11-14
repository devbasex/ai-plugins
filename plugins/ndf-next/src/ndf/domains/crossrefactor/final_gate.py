"""殻: 最終ゲート（PR の head へ送り、CI の全体テストを待ち、落ちたら対象範囲の中だけで直す）。

1. 作業ディレクトリを origin の先頭へ合わせて送る（`worktree.sync_worktree`。衝突したら `base_conflict` で止める）
2. CI を待つ前に PR がベースブランチと衝突していないかを見る。衝突していれば CI は始まらないので待たずに
   `base_conflict` で止める（#2680。`final_gate_decide.before_ci_wait`）。GitHub がまだ計算中なら間を置いて読み直す
3. 必須のチェックを待つ。テストが落ちたら最後の修正の担当を起動し、直しのコミットがリファクタリング計画の対象範囲の
   外を変えていれば取り込まずに戻し、落ちたテストと範囲外のファイルを記録して `refused` で止める（#3038）
4. 検査（テスト以外）の落ち・待ちの上限は、そのまま止まった理由にする
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ndf.adapters import ci, git, github
from ndf.contracts import limits
from ndf.core.converge import worktree
from ndf.domains.crossrefactor import final_gate_decide

CI_WAIT_POLICY = "test-run.ci-wait"
CHECK_WAIT_POLICY = "test-triage.check-wait"
# GitHub が衝突をまだ計算しているときに読み直す回数
RECHECK_READS = 3


class GateStopped(Exception):
    """最終ゲートが止まった。`kind` は止まった理由の種類、`record` は最終ゲートの記録。"""

    def __init__(self, kind: str, detail: str, record: Optional[dict[str, Any]] = None) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.record = record if record is not None else {}


@dataclass(frozen=True)
class GateConfig:
    pr: int
    branch: str
    required: tuple[str, ...]
    tests: tuple[str, ...]
    target_scope: tuple[str, ...]
    max_fix: int


# 最後の修正の担当を起動する（落ちたテストを渡し、結果を出したかを返す）
FixLauncher = Callable[[Sequence[str], int], bool]


def send(g: git.Git, branch: str) -> str:
    """origin の先頭へ合わせて送り、送った head を返す。"""
    synced = worktree.sync_worktree(g, branch)
    if synced.conflicts:
        raise GateStopped("base_conflict", worktree.conflict_detail(synced))
    done = g.attempt("push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    if done.returncode != 0:
        raise GateStopped("push_rejected", f"PR のブランチ {branch} へ送れない: {done.stderr.strip()}")
    return g.rev_parse("HEAD")


def _mergeable(pull: github.Pull) -> str:
    """REST の `mergeable_state`（小文字）から `before_ci_wait` へ渡す `mergeable` を求める。"""
    return "UNKNOWN" if pull.merge_state in ("unknown", "") else "MERGEABLE"


def ensure_mergeable(hub: github.GitHub, pr: int, sleep: Callable[[float], None]) -> None:
    """PR がベースブランチと衝突していれば `base_conflict` で止める（#2680）。"""
    interval = float(limits.policy(CHECK_WAIT_POLICY).interval)
    for _ in range(RECHECK_READS):
        pull = hub.pull(pr)
        verdict = final_gate_decide.before_ci_wait(_mergeable(pull), pull.merge_state)
        if verdict.kind == "conflict":
            raise GateStopped("base_conflict", verdict.detail)
        if verdict.kind == "wait":
            return
        sleep(interval)


def wait_ci(hub: github.GitHub, sha: str, cfg: GateConfig) -> ci.WatchResult:
    watch = ci.Watch(ci.watch_head(hub, sha, list(cfg.required), list(cfg.tests)), limits.seconds(CI_WAIT_POLICY))
    return watch.run()


def changed_by_commit(g: git.Git, before: str) -> dict[str, list[str]]:
    """`before` から HEAD までのコミット（古い順）→ 変えたファイル。"""
    out: dict[str, list[str]] = {}
    for sha in g.run("rev-list", "--reverse", f"{before}..HEAD").split():  # noqa: NDF201 上限: 最後の修正 1 回のコミットの数
        out[sha] = [p for p in g.run("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha).split("\0") if p]
    return out


def _check_fix(g: git.Git, before: str, failed: Sequence[str], cfg: GateConfig) -> None:
    """最後の修正の直しを確かめる。範囲外を変えていれば `before` へ戻して `refused` で止める（#3038）。"""
    verdict = final_gate_decide.verify_final_fix(changed_by_commit(g, before), cfg.target_scope)
    if verdict.ok:
        return
    g.run("reset", "-q", "--hard", before)
    record = {"status": "refused", "outside_failures": verdict.record(failed)}
    raise GateStopped("refused", str(verdict.cause()["detail"]), record)


def _failed_tests(hub: github.GitHub, watched: ci.WatchResult) -> Optional[list[str]]:
    """落ちたテストの node id。ログを読めなければ None（落ちたチェックの名前で直しを頼む）。"""
    try:
        return ci.failed_test_nodes_of(hub, watched.reading.failed_tests)
    except github.GitHubError:  # noqa: NDF401 ネットワーク: ログを読めなくても落ちたチェックの名前で直しを頼む
        return None


def _names(checks: Sequence[ci.Check]) -> list[str]:
    return [c.name for c in checks]


def run_gate(g: git.Git, hub: github.GitHub, cfg: GateConfig, fix: FixLauncher, sleep: Callable[[float], None]) -> dict[str, Any]:
    """最終ゲートを回し、通れば記録（`status` が `passed`・`rounds`）を返す。止まれば `GateStopped`。"""
    for round_no in range(cfg.max_fix + 1):
        sha = send(g, cfg.branch)
        ensure_mergeable(hub, cfg.pr, sleep)
        watched = wait_ci(hub, sha, cfg)
        kind = watched.cause(cfg.tests)
        if kind is None:
            return {"status": "passed", "rounds": round_no, "head": sha}
        if kind != "test_failed" or round_no == cfg.max_fix:
            raise GateStopped(kind, f"最終ゲートの CI が通らない（{watched.reading.verdict}）", {"status": kind, "rounds": round_no, "head": sha})
        nodes = _failed_tests(hub, watched)
        failed = nodes if nodes else _names(watched.reading.failed_tests)
        before = g.rev_parse("HEAD")
        if not fix(failed, round_no + 1):
            raise GateStopped("work_stopped", "最後の修正の担当が結果を出さずに終わった", {"status": "work_stopped", "rounds": round_no, "head": sha})
        _check_fix(g, before, failed, cfg)
    raise AssertionError("最終ゲートの回数の上限の後に戻った")
