"""副命令 `ndf ci` の入口。CI の結果を読み、止まった理由の種類で結果 JSON を返す。

読み取りと分類は外部との接続の層（`ndf.adapters.ci`）が持ち、この入口は引数と出力だけを持つ。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ndf.adapters import git
from ndf.adapters import github as github_mod
from ndf.adapters.ci import Reading, Watch, failed_tests_of, files_of, is_test_check, watch_head
from ndf.adapters.github import GitHub
from ndf.contracts import result

# 副命令 `ndf ci` の道具の名前（結果 JSON の tool）
TOOL = "ci"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ndf ci", description="CI の結果を読み、止まった理由の種類で返す")
    sub = p.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="PR の先端の必須のチェックの結論が出るまで待つ")
    w.add_argument("--pr", type=int, required=True)
    w.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    w.add_argument("--required", action="append", required=True, help="待つ必須のチェック（PR の先端の宣言から渡す）")
    w.add_argument("--test", action="append", default=[], help="テストのチェックの名前（分割ジョブも数える）")
    w.add_argument("--budget", type=float, required=True, help="待ちの合計の秒（ランナーの割り当ての待ちは数えない）")
    w.add_argument("--after-merge", action="store_true", help="マージの段階が落ちた後の判定")
    w.add_argument("--merge-failed", action="append", default=[], help="マージの段階が落ちたと読んだチェックの名前")
    return p


def _progress(reading: Reading, elapsed: float) -> None:
    """待つ間の 1 行（何を何秒待っているか。#2317）。"""
    waiting = reading.running + reading.queued + (reading.missing if not reading.settled else [])
    if waiting:
        print(f"CI を待っている（{int(elapsed)} 秒）: {', '.join(waiting)}", file=sys.stderr)


def _items(reading: Reading, tests: list[str]) -> list[dict]:
    rows = [{"kind": "check", "name": c.name, "result": c.conclusion, "job": c.job, "test": True} for c in reading.failed_tests]
    rows += [{"kind": "check", "name": c.name, "result": c.conclusion, "job": c.job, "test": False} for c in reading.failed_other]
    rows += [{"kind": "check", "name": n, "result": "missing", "test": is_test_check(n, tests)} for n in reading.missing]
    rows += [{"kind": "check", "name": n, "result": "stale", "test": is_test_check(n, tests)} for n in reading.stale]
    return rows


def _watch(a: argparse.Namespace) -> dict:
    hub = GitHub(a.repo if a.repo else git.Git().remote_repo(), github_mod.client())
    pull = hub.pull(a.pr)
    done = Watch(watch_head(hub, pull.head_sha, a.required, a.test), a.budget, on_round=_progress).run()
    merge_failed = a.merge_failed if a.after_merge else None
    kind = done.cause(a.test, merge_failed)
    metrics: dict[str, Any] = {"rounds": done.rounds, "elapsed": round(done.elapsed, 1), "queued_for": round(done.queued_for, 1)}
    if kind == "test_failed":
        nodes = failed_tests_of(hub, done.reading.failed_tests)
        metrics.update({"failed_tests": nodes, "test_files": files_of(nodes)})
    items = _items(done.reading, a.test)
    if kind is None:
        return result.result(TOOL, result.OK, f"#{a.pr} の必須のチェックが通った", items=items, metrics=metrics)
    detail = f"#{a.pr}（{pull.head_sha[:12]}）の CI: {done.reading.verdict}"
    return result.stopped(TOOL, kind, detail, instance=pull.head_sha, items=items, metrics=metrics)


def main(argv: list[str]) -> int:
    a = _parser().parse_args(argv)
    try:
        out = _watch(a)
    except github_mod.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        out = result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)
    except git.GitError as exc:
        out = result.stopped(TOOL, "precondition", str(exc))
    return result.emit(out)
