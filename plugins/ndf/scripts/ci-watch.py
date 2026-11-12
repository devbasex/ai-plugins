#!/usr/bin/env python3
"""ci-watch.py: CI の結果の監視。PR の CI のテストのチェックを待たずに 1 回だけ読む（#3056）。

    python3 ci-watch.py peek --pr <PR番号> --state <状態ディレクトリ> [--after-merge] [--root DIR]

CI テストの宣言（`.ndf/project.json` の `test.ci.only: true`）のあるリポジトリのプランが、`pr` の後・`doc-lint` の後・
マージが落ちた後に置く（`supervise_lib/verify_steps.ci_watch_steps`）。チェックの読み方は `merged-steps.py probe` と
同じ（`merged_lib/reading.read_checks`。SKIPPED は通過）で、テストのチェック（`test.ci.check` → `ci.required_checks`。
どちらも無ければ head のすべて）だけで結論を決める。落ちたチェックの名前・リンクと、失敗したジョブのログの末尾
（`gh run view --log-failed` の最後の 200 行。認証情報は伏せる）を人が読む行として出し、記録 `<状態>/work/ci-watch.jsonl` へ 1 行を足す。
GitHub の PR の先端（`headRefOid`）が作業ディレクトリの HEAD と食い違うとき（push の直後に先端の更新が遅れた）は、読んだ
チェックを前の先端のものとみなし、結論を未完了（pending）にする（落ちた行として記録しない）。

終了コード: 0 = 進めてよい（通った・未完了・チェックがまだ無い・取得できなかった）/ 1 = テストのチェックが落ちた（改修の回数は
上限の内）/ 2 = 落ちたが改修の回数が上限（`contracts/limits.json` の `supervise.ci-fix` の `attempts`）に当たった、または同じ head のまま前の改修が終わった /
3 = `--after-merge` で、落ちたテストのチェックが無い。結果は lib/step_result.py の形の 1 行の JSON。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
import deps  # noqa: E402

deps.require("waits", "durable", "locks", "schema")
import ci_watch  # noqa: E402
import gh_parts  # noqa: E402
import limits  # noqa: E402
import proc  # noqa: E402
import project_decl  # noqa: E402
import test_strategy as ts  # noqa: E402
import run_core  # noqa: E402
import secret_redact  # noqa: E402
from step_result import EXIT_OK, EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, gh_json, git_root, main_with, result  # noqa: E402

from merged_lib.reading import read_checks  # noqa: E402

TOOL = "ci-watch"
LOG_TAIL_LINES = 200


def _read_test_checks(root, pr: int, names: list[str]) -> tuple[str | None, dict, list[str], str | None]:
    """(head, 結論ごとのテストのチェックの CheckItem, 現れないテストのチェック, 取得できなかった理由)。
    Runner が付かなかった取り消し（基盤待ち）は落ちたものに数えず、未完了として扱う。"""
    try:
        info = gh_json(root, ["pr", "view", str(pr), "--json", "headRefOid,statusCheckRollup"], f"gh pr view {pr}")
        reading = read_checks(root, (info or {}).get("statusCheckRollup"))
    except (StepError, OSError, ValueError) as e:
        return None, {}, [], str(e)
    groups = {"failed": reading.failed, "pending": [*reading.pending, *reading.infra], "passed": reading.passed}
    picked = {k: [i for i in v if ci_watch.is_test_check(names, i.label, str(i.key[-1]))] for k, v in groups.items()}
    absent = ci_watch.absent_checks(names, ((i.label, str(i.key[-1])) for v in groups.values() for i in v))
    return (info or {}).get("headRefOid"), picked, absent, None


def _failed_log(root, run: str | None) -> str:
    """失敗したジョブのログの末尾（認証情報は `secret_redact` で伏せる）。読めなければ空。"""
    if not run:
        return ""
    p = gh_parts.gh(["run", "view", str(run), "--log-failed"], cwd=root)
    if p.returncode != 0:
        return ""
    return secret_redact.redact_text("\n".join(p.stdout.splitlines()[-LOG_TAIL_LINES:]))


def _print_failures(root, failed: list) -> None:
    """落ちたチェックの名前と、失敗したジョブのログの末尾を出す。`gh run view <run> --log-failed` は run の失敗した
    ジョブをすべて含むため、同じ run のチェック（matrix・同じワークフローの複数のジョブ）のログは 1 回だけ取って出す。"""
    by_run: dict = {}
    for item in failed:
        by_run.setdefault(item.run, []).append(item.label)
    for item in failed:
        print(f"落ちたチェック: {item.label}" + (f"（run {item.run}）" if item.run else ""))
    for run, labels in by_run.items():
        log = _failed_log(root, run)
        if log:
            print(f"--- {', '.join(labels)} の失敗したジョブのログの末尾 ---\n{log}\n---")


def _peek_summary(
    verdict: str, code: int, fixes: int, limit: int, failed: list, absent: list[str], why: str | None, stuck: bool = False
) -> str:
    if why is not None:
        return f"CI の結果を取得できない（記録して進める）: {why[:200]}"
    if code == EXIT_UNREADABLE and stuck:
        return f"CI のテストのチェックが落ちた（{', '.join(failed)}）。前の改修が head を変えずに終わったため、改修を起こし直さず止める"
    if code == EXIT_UNREADABLE:
        return f"CI のテストのチェックが落ちた（{', '.join(failed)}）。改修の回数が上限 {limit} に当たった（{fixes} 回目の失敗）"
    if code == EXIT_VIOLATION:
        return f"CI のテストのチェックが落ちた（{', '.join(failed)}）。直して push する（{fixes} / {limit} 回目）"
    if code == EXIT_PRECONDITION:
        return "マージが落ちたが、落ちた CI のテストのチェックが無い（CI 以外の理由）"
    if verdict == ci_watch.PASSED:
        return "CI のテストのチェックが通った"
    tail = f"（現れない: {', '.join(absent)}）" if absent else ""
    return f"CI のテストのチェックはまだ終わっていない{tail}。待たずに進める"


def cmd_peek(a) -> int:
    root = git_root(a.root)
    names = ts.ci_checks(project_decl.read_project_decl(root))
    limit = int(limits.value("supervise.ci-fix", "attempts"))
    head, picked, absent, why = _read_test_checks(root, a.pr, names)
    local = proc.git_out(root, "rev-parse", "HEAD")
    lagging = why is None and bool(head) and bool(local) and local != head
    failed = [] if lagging else picked.get("failed", [])
    if why is not None:
        verdict = ci_watch.UNKNOWN
    elif lagging:
        verdict = ci_watch.PENDING
    else:
        verdict = ci_watch.verdict(len(failed), len(picked["pending"]), len(picked["passed"]), len(absent))
    rows = ci_watch.read_watch_log(a.state)
    code, fixes = ci_watch.decide_exit(verdict, head, rows, limit, a.after_merge)
    ci_watch.append_log(a.state, head, verdict, [i.label for i in failed], a.after_merge)
    if code in (EXIT_VIOLATION, EXIT_UNREADABLE):
        _print_failures(root, failed)
    items = [i.item(kind) for kind, group in picked.items() for i in group] + [
        {"kind": "check", "name": n, "result": "absent"} for n in absent
    ]
    metrics = {
        "head": head,
        "local_head": local,
        "verdict": verdict,
        "fixes": fixes,
        "limit": limit,
        "log": str(ci_watch.watch_log_path(a.state)),
    }
    stuck = bool(head) and head in ci_watch.failed_heads(rows)
    summary = _peek_summary(verdict, code, fixes, limit, [i.label for i in failed], absent, why, stuck)
    if lagging:
        summary = f"PR の先端（{head[:12]}）が作業ディレクトリの HEAD（{local[:12]}）とまだ揃っていない。前の先端の結果とみなし、{summary}"
    nxt = f"gh pr checks {a.pr} で失敗を読む" if code in (EXIT_UNREADABLE, EXIT_PRECONDITION) else None
    emit(result(TOOL, "ok" if code == EXIT_OK else "stopped", summary, items, metrics, next=nxt), code)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("peek", help="PR の CI のテストのチェックを 1 回だけ読む")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--state", required=True, help="プランの状態ディレクトリ（記録 work/ci-watch.jsonl を置く）")
    p.add_argument("--after-merge", action="store_true", help="マージが落ちた後に読む")
    p.add_argument("--root", default=None)
    p.set_defaults(func=cmd_peek)
    return ap


def main(argv=None) -> int:
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    run_core.main("scripts/ci-watch.py", main)
