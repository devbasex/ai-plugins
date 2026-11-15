"""サブコマンド `ndf ci` のエントリポイント。PR の CI の結果を読み（`watch`）、PR をマージし（`merge`）、停止理由の種類で結果 JSON を返す。

読み取り・分類・マージの前段の確かめは外部との接続の層（`ndf.adapters.ci`・`ndf.adapters.github`）が持ち、
このエントリポイントは引数と出力だけを持つ。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters import github as github_mod
from ndf.adapters.ci import (
    Classifier,
    FailedTestSource,
    Reading,
    Watch,
    WatchResult,
    failed_tests_of,
    files_of,
    is_test_check,
    watch_head,
)
from ndf.adapters.github import GitHub
from ndf.contracts import result

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ndf ci", description="CI の結果を読み、停止理由の種類で返す")
    sub = p.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="PR の HEAD の必須のチェックの結論が出るまで待つ")
    w.add_argument("--pr", type=int, required=True)
    w.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    w.add_argument("--required", action="append", required=True, help="待つ必須のチェック（PR の HEAD の設定から渡す）")
    w.add_argument("--test", action="append", default=[], help="テストのチェックの名前（分割ジョブも数える）")
    w.add_argument("--budget", type=float, required=True, help="待ちの合計の秒（ランナーの割り当ての待ちは数えない）")
    w.add_argument("--after-merge", action="store_true", help="マージの段階が落ちた後の判定")
    w.add_argument("--merge-failed", action="append", default=[], help="マージの段階が落ちたと読んだチェックの名前")
    w.set_defaults(run=_watch)
    m = sub.add_parser(
        "merge",
        help="ブランチの開いた PR を、作業場所の HEAD のまま、前段の確かめ（決めていない残リスク・HEAD の食い違い）を通してマージする",
    )
    m.add_argument("--branch", required=True, help="PR の head のブランチ")
    m.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    m.add_argument("--method", help="マージの方式（squash・merge・rebase）。省けば基底のブランチで許された方式の先頭")
    m.set_defaults(run=_merge)
    return p


def _progress(reading: Reading, elapsed: float) -> None:
    """待つ間の 1 行（何を何秒待っているか。#2317）。"""
    waiting = reading.running + reading.queued + (reading.missing if not reading.settled else [])
    if waiting:
        print(f"CI を待っている（{int(elapsed)} 秒）: {', '.join(waiting)}", file=sys.stderr)


def _item(name: str, outcome: str, test: bool, job: Optional[int] = None) -> dict:
    """結果の項目 1 件（`outcome` はキー `result` の値）。`job` が無ければキーを置かない。"""
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


def _failed_metrics(hub: GitHub, reading: Reading, cwd: Path) -> dict:
    """落ちたテストの node id・ファイルと、チェックごとの読み取りの記録（結果 JSON の `metrics` のキー）。

    作業場所の設定ファイルの `test.ci.junit_artifacts` と suite を読み、JUnit の成果物を先に、ログを後に読む。
    ジョブを特定できないチェックがあっても分類は保ち、理由を `log_error` に残す。
    """
    source = FailedTestSource.from_work(cwd)
    found = failed_tests_of(hub, reading.failed_tests, source)
    out: dict[str, Any] = {
        "failed_tests": found.nodes,
        "test_files": files_of(found.nodes),
        "failed_test_reads": [r.as_metric() for r in found.reads],
    }
    if source.junit_artifacts is not None:
        out["junit_artifacts"] = source.junit_artifacts
    if found.job_errors:
        out["log_error"] = "; ".join(found.job_errors)
    return out


def _unread() -> Reading:
    raise AssertionError("Watch の読み手が差し込まれていない")


def _watch(args: argparse.Namespace) -> dict:
    # 上限の解除が待ちの残りを越えるなら githubkit の中で眠らず、`infra_wait` と `retry_after` で返す。
    # 残りは Watch と同じ数え方（ランナーの割り当ての待ちを予算に数えない）で読む
    # 読み手は PR の HEAD が分かってから差し込む（PR を読む API の呼び出しも同じ予算に入れる）
    # マージの段階がテストのチェックの落ちを読んでいれば、読み直しが pending でも理由は `test_failed` に決まるので待たない
    classifier = Classifier(args.test, args.merge_failed if args.after_merge else None)
    watch = Watch(_unread, args.budget, on_round=_progress, decided=lambda r: classifier.cause(r) == "test_failed")
    hub = GitHub(args.repo if args.repo else git.Git().remote_repo(), github_mod.client(remaining=watch.remaining))
    pull = hub.pull(args.pr)
    watch.reader = watch_head(hub, pull.head_sha, args.required, args.test)
    watched = watch.run()
    return _report(args, pull, hub, watched, watched.cause_by(classifier))


def _report(args: argparse.Namespace, pull: github_mod.Pull, hub: GitHub, watched: WatchResult, kind: Optional[str]) -> dict:
    """待ちの結果と停止理由の種類（通っていれば None）から結果 JSON を組む。"""
    metrics: dict[str, Any] = {"rounds": watched.rounds, "elapsed": round(watched.elapsed, 1), "queued_for": round(watched.queued_for, 1)}
    if kind == "test_failed":
        metrics.update(_failed_metrics(hub, watched.reading, Path.cwd()))
    items = _items(watched.reading, args.test)
    if kind is None:
        return result.result(TOOL, result.OK, f"#{args.pr} の必須のチェックが通った", items=items, metrics=metrics)
    detail = f"#{args.pr}（{pull.head_sha[:12]}）の CI: {watched.reading.verdict}"
    return result.stopped(TOOL, kind, detail, instance=pull.head_sha, items=items, metrics=metrics)


def _merge(args: argparse.Namespace) -> dict:
    """`--branch` の開いた PR を、作業場所の HEAD（HEAD）を添えて `GitHub.merge` でマージする（#2602）。

    本文に決めていない「マージの前に決める」残リスクの行（`- [ ]`）が残れば、マージせずに `refused` で止まり、残った行を理由に出す。
    """
    repo = git.Git()
    head = repo.rev_parse("HEAD")
    hub = GitHub(args.repo if args.repo else repo.remote_repo(), github_mod.client())
    opened = [p for p in hub.pulls_by_head([args.branch])[args.branch] if p.state == "open"]
    if not opened:
        return result.stopped(TOOL, "precondition", f"{args.branch} の開いた PR が無い")
    number = opened[0].number
    outcome = hub.merge(number, expect_head=head, method=args.method)
    if outcome.merged:
        return result.result(TOOL, result.OK, outcome.detail, items=[{"kind": "pr", "number": number}])
    kind = outcome.kind if outcome.kind is not None else "unknown"
    return result.stopped(TOOL, kind, outcome.detail, instance=outcome.head_sha, items=[{"kind": "pr", "number": number}])


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        out = args.run(args)
    except github_mod.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        out = result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)
    except git.GitError as exc:
        out = result.stopped(TOOL, "precondition", str(exc))
    return result.emit(out)
