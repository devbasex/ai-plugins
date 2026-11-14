"""副命令 `ndf ci` のエントリポイント。CI の結果を読み、止まった理由の種類で結果 JSON を返す。

読み取りと分類は外部との接続の層（`ndf.adapters.ci`）が持ち、このエントリポイントは引数と出力だけを持つ。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters import github as github_mod
from ndf.adapters.ci import Reading, Watch, WatchResult, cause_of, failed_test_nodes_of, files_of, is_test_check, watch_head
from ndf.adapters.github import GitHub
from ndf.contracts import result

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)


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


def _item(name: str, outcome: str, test: bool, job: Optional[int] = None) -> dict:
    """結果の項目 1 件（`outcome` は鍵 `result` の値）。`job` が無ければ鍵を置かない。"""
    row: dict[str, Any] = {"kind": "check", "name": name, "result": outcome}
    if job is not None:
        row["job"] = job
    row["test"] = test
    return row


def _items(reading: Reading, tests: list[str]) -> list[dict]:
    rows = [_item(c.name, c.conclusion, True, c.job) for c in reading.failed_tests]
    rows += [_item(c.name, c.conclusion, False, c.job) for c in reading.failed_other]
    rows += [_item(n, "missing", is_test_check(n, tests)) for n in reading.missing]
    rows += [_item(n, "stale", is_test_check(n, tests)) for n in reading.stale]
    return rows


def _failed_nodes(hub: GitHub, reading: Reading) -> dict:
    """落ちたテストの node id とファイル。ログを読めなくても（権限・Actions 以外のチェック）分類は保ち、理由を `log_error` に残す。"""
    try:
        nodes = failed_test_nodes_of(hub, reading.failed_tests)
    except github_mod.GitHubError as exc:
        return {"failed_tests": [], "test_files": [], "log_error": str(exc)}
    return {"failed_tests": nodes, "test_files": files_of(nodes)}


def _unread() -> Reading:
    raise AssertionError("Watch の読み手が差し込まれていない")


def _watch(args: argparse.Namespace) -> dict:
    # 上限の解除が待ちの残りを越えるなら githubkit の中で眠らず、`infra_wait` と `retry_after` で返す。
    # 残りは Watch と同じ数え方（ランナーの割り当ての待ちを予算に数えない）で読む
    # 読み手は PR の先端が分かってから差し込む（PR を読む API の呼び出しも同じ予算に入れる）
    # マージの段階がテストのチェックの落ちを読んでいれば、読み直しが pending でも理由は `test_failed` に決まるので待たない
    merge_failed = args.merge_failed if args.after_merge else None
    watch = Watch(_unread, args.budget, on_round=_progress, decided=lambda r: cause_of(r, args.test, merge_failed) == "test_failed")
    hub = GitHub(args.repo if args.repo else git.Git().remote_repo(), github_mod.client(remaining=watch.remaining))
    pull = hub.pull(args.pr)
    watch.reader = watch_head(hub, pull.head_sha, args.required, args.test)
    watched = watch.run()
    return _report(args, pull, hub, watched, watched.cause(args.test, merge_failed))


def _report(args: argparse.Namespace, pull: github_mod.Pull, hub: GitHub, watched: WatchResult, kind: Optional[str]) -> dict:
    """待ちの結果と止まった理由の種類（通っていれば None）から結果 JSON を組む。"""
    metrics: dict[str, Any] = {"rounds": watched.rounds, "elapsed": round(watched.elapsed, 1), "queued_for": round(watched.queued_for, 1)}
    if kind == "test_failed":
        metrics.update(_failed_nodes(hub, watched.reading))
    items = _items(watched.reading, args.test)
    if kind is None:
        return result.result(TOOL, result.OK, f"#{args.pr} の必須のチェックが通った", items=items, metrics=metrics)
    detail = f"#{args.pr}（{pull.head_sha[:12]}）の CI: {watched.reading.verdict}"
    return result.stopped(TOOL, kind, detail, instance=pull.head_sha, items=items, metrics=metrics)


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        out = _watch(args)
    except github_mod.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        out = result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)
    except git.GitError as exc:
        out = result.stopped(TOOL, "precondition", str(exc))
    return result.emit(out)
