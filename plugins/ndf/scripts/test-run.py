#!/usr/bin/env python3
"""テストの実行の入口（#1334 F2・F4・F5）。supervise の run のステップが呼ぶ。

    python3 test-run.py scope --paths <パス>... [--changed <パス>...] [--root DIR] [--template CMD] [--test-kind test|lint]
    python3 test-run.py whole [--paths <パス>...] [--pr N] [--base <ブランチ>] [--root DIR] [--template CMD] [--test-kind test|lint]

宣言（`.ndf/project.json` の `test`。無ければ `.ndf/supervise.json` の `test.command` を 1 つの suite として読む）から
戦略を解き（`lib/test_strategy.resolve`）、範囲テストは `test_strategy.scope_runs` で組んでシェルで走らせる（テストの suite には
`--paths`、静的解析の suite には変更したファイル = `--changed`、無ければ起点との merge-base からの差分と追跡していない新しいファイル）。
全体テストは、テストの種別は戦略が手元なら suite の `command` を、CI に任せる戦略なら Pull Request のチェックを待って JUnit を読み、
静的解析は戦略に関わらず手元で走らせる。落ちたテストは `lib/test_triage.classify` で フレーキー・既存失敗・変更起因 に、
落ちた静的解析は `lib/test_triage.lint_verdict` で分ける。`--template` は引数の雛形で、宣言より先に効き、種別は `--test-kind`。
`whole --paths` は静的解析の雛形の全体テストの `{paths}` に入れる範囲である。

終了コード: 0 = 通った（フレーキーと既存失敗だけのときも 0。`items` に分類を持つ）/ 1 = 変更起因の失敗がある /
2 = 判断できない（起動の失敗・CI が上限までに終わらない・`gh` が使えない・宣言の不足・コンテナで走る suite が worktree を
見ていない。理由と待った秒を `summary` に出す）。起動の失敗は `items[].launch_failed` にコマンド・終了コード・理由・ログを持つ。
出力は `lib/step_result.py` の形の 1 行の JSON。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import NamedTuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

import container_reach  # noqa: E402
import repo as repo_lib  # noqa: E402
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "test-run"
LOG_DIR = pathlib.Path(".ndf") / "tmp" / "test-run"


def _supervise_decl(root: pathlib.Path) -> dict:
    f = root / ".ndf" / "supervise.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve(root: pathlib.Path, a) -> tuple[ts.Strategy, dict, list[str]]:
    decl, note = ts.decl_of(root, _supervise_decl(root))
    notes = [note] if note else []
    scope_paths = list(getattr(a, "paths", None) or []) if a.cmd == "whole" else None
    strategy = ts.resolve(decl, baseline_test=a.template, template_kind=a.test_kind, scope_paths=scope_paths)
    notes.extend(strategy.notes)
    w, w_source = ts.whole_seconds(decl)
    c = ts.ci_wall_seconds(decl, (strategy.ci or {}).get("check") if strategy.ci else None)
    limits = ts.limits(strategy, None, whole_seconds_value=w, whole_source=w_source, ci_seconds=c)
    return strategy, limits, notes


def _git_out(root: pathlib.Path, *args: str) -> str | None:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def _base_sha(root: pathlib.Path, base: str | None) -> str | None:
    """着手前の HEAD とみなす SHA = `--base`（無ければ worktree.json の base_branch）と HEAD の merge-base。"""
    branch = base or repo_lib.declared_base(root) or repo_lib.base_branch(root)
    if not branch:
        return None
    for ref in (f"origin/{branch}", branch):
        sha = _git_out(root, "merge-base", ref, "HEAD")
        if sha:
            return sha
    return None


def _classify(root: pathlib.Path, strategy: ts.Strategy, limits: dict, base: str | None, ci_xmls: list[bytes] | None = None) -> dict:
    work = str(root)
    if ci_xmls is not None:
        failed, reason = test_triage.merged_failed_ids(ci_xmls, test_triage.tracked_files(work))
    else:
        failed, reason = test_triage.read_junit(work, strategy)
    if failed is not None and not failed:
        failed, reason = None, "JUnit に落ちたテストが無い"
    return test_triage.classify(
        work=work,
        strategy=strategy,
        failed=failed,
        fallback_reason=reason,
        base_sha=_base_sha(root, base),
        timeout=int(limits["test_timeout"]),
        log_dir=root / LOG_DIR,
        existing_failures=[],
    )


def _emit_launch(strategy: ts.Strategy, command: str, outcome: ts.Outcome, log: pathlib.Path) -> int:
    """起動の失敗を「落ちた」と別の状態で返す（#1483 AC12）。終了コードは 2。"""
    item = {"launch_failed": {"command": command, "code": outcome.code, "reason": outcome.reason, "log": str(log)}}
    emit(result(TOOL, "stopped", f"起動の失敗: {command}（{outcome.reason}）", [item], {"strategy": strategy.name}), code=2)
    return 2


def _run_each(
    root: pathlib.Path, commands: list[str], limit: float, prefix: str, started: float | None = None
) -> tuple[list, tuple | None]:
    """コマンドをシェルで 1 本ずつ走らせる。上限は全体で 1 つ。

    `started`（`time.monotonic()`）を渡すと、その時刻からの残りで走らせる（種別をまたいで上限を共有する）。
    戻りは（`(コマンド, Outcome, ログ)` の並び, 止めた理由）。止めた理由は起動の失敗か打ち切りの `(コマンド, Outcome, ログ)`。
    """
    (root / LOG_DIR).mkdir(parents=True, exist_ok=True)
    started = time.monotonic() if started is None else started
    done = []
    for i, command in enumerate(commands):
        log = root / LOG_DIR / f"{prefix}-{i}.log"
        code, timed_out = test_triage.run_within(
            limit, started, lambda left, command=command, log=log: test_triage.run_command(command, str(root), left, log)
        )
        outcome = ts.outcome(code, timed_out)
        if outcome.status in (ts.LAUNCH_FAILED, ts.TIMED_OUT):
            return done, (command, outcome, log)
        done.append((command, outcome, log))
    return done, None


def _changed(root: pathlib.Path, a) -> list[str]:
    """静的解析の範囲 = `--changed`、無ければ起点との merge-base からの差分と追跡していない新しいファイル。"""
    if getattr(a, "changed", None):
        return [str(p) for p in a.changed]
    sha = _base_sha(root, a.base)
    return test_triage.changed_since(str(root), sha) if sha else []


def _lint_verdicts(root: pathlib.Path, strategy: ts.Strategy, limits: dict, a, failed: list[str]) -> list[dict]:
    """全体で落ちた静的解析の suite を分ける。着手前の記録を持たないため、絞れなければ変更起因（#1483 決定 9）。"""
    by_command = {s.command: s for s in strategy.suites if s.kind == ts.LINT and s.command}
    changed = _changed(root, a) if failed else []
    out = []
    for i, command in enumerate(failed):
        verdict = test_triage.lint_verdict(
            work=str(root),
            suite=by_command[command],
            baseline=None,
            changed=changed,
            timeout=int(limits["test_timeout"]),
            log=root / LOG_DIR / f"lint-scope-{i}.log",
        )
        verdict["outcome"] = verdict["outcome"].status if verdict["outcome"] is not None else None
        out.append(verdict)
    return out


def _outcome_items(strategy: ts.Strategy, limits: dict, notes: list[str], triage: dict | None, lint: list[dict] | None) -> list[dict]:
    items = [{"strategy": strategy.name, "source": strategy.source, "limits": {k: v for k, v in limits.items() if k != "basis"}}]
    for note in notes:
        items.append({"note": note})
    if triage is not None:
        items.append({k: triage.get(k) for k in ("failed_tests", "flaky", "preexisting", "caused", "fallback_reason")})
    if lint:
        items.append({"lint": lint})
    return items


def _decide_status(passed: bool, triage: dict | None, lint: list[dict] | None) -> tuple[str, int, str]:
    """（status, 終了コード, detail へ足す文）を決める。"""
    lint_caused = [v for v in lint or [] if v.get("verdict") == "caused"]
    caused = bool(triage and triage.get("caused"))
    suffix = ""
    if passed:
        status, code = "ok", 0
    elif triage is None and lint is not None:
        status, code = "ok", 0
    elif triage is not None and triage.get("fallback_reason") is None and not caused:
        status, code = "ok", 0
        suffix += "（落ちたテストはフレーキーと既存失敗だけ）"
    elif triage is not None and triage.get("fallback_reason"):
        status, code = "stopped", 2
        suffix += f"。見分けを全体の走らせ直しに落とした: {triage['fallback_reason']}"
    else:
        status, code = "stopped", 1
    if lint_caused and code == 0:
        status, code = "stopped", 1
    if lint_caused:
        suffix += "。静的解析が落ちた: " + "・".join(f"{v['suite']}（{v['reason']}）" for v in lint_caused)
    elif lint:
        suffix += "。落ちた静的解析は既存失敗: " + "・".join(f"{v['suite']}（{v['reason']}）" for v in lint)
    return status, code, suffix


class OutcomeContext(NamedTuple):
    """結果の 1 行を組む材料。戦略・上限・注記は解決した戦略から、残りは走らせた結果から渡す。"""

    strategy: ts.Strategy
    limits: dict
    notes: list[str]
    passed: bool
    detail: str
    triage: dict | None = None
    lint: list[dict] | None = None


def _emit_outcome(ctx: OutcomeContext) -> int:
    items = _outcome_items(ctx.strategy, ctx.limits, ctx.notes, ctx.triage, ctx.lint)
    status, code, suffix = _decide_status(ctx.passed, ctx.triage, ctx.lint)
    lint_caused = [v for v in ctx.lint or [] if v.get("verdict") == "caused"]
    emit(result(TOOL, status, ctx.detail + suffix, items, {"caused": len((ctx.triage or {}).get("caused") or []) + len(lint_caused)}))
    return code


def cmd_scope(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a)
    if strategy.name == ts.ROUND_ONLY:
        runs = [ts.ScopeRun("round", ts.TEST, c) for c in strategy.round_commands()]
    else:
        changed = _changed(root, a) if strategy.scoped_suites(ts.LINT) else []
        runs = ts.scope_runs(strategy, list(a.paths), changed)
    if not runs:
        emit(result(TOOL, "stopped", "範囲テストの雛形（scope_command）を持つ suite が無いか、範囲に入るファイルが無い", [], {}))
        return 2
    test_triage.clear_junit(str(root), strategy)
    started = time.monotonic()
    done, stopped = _run_each(root, [r.command for r in runs], float(limits["test_timeout"]), "scope")
    if stopped is not None:
        command, outcome, log = stopped
        if outcome.launch_failed:
            return _emit_launch(strategy, command, outcome, log)
        emit(result(TOOL, "stopped", f"範囲テストが {limits['test_timeout']} 秒で終わらなかった", [], {}))
        return 2
    kinds = {r.command: r.kind for r in runs}
    failed = [c for c, o, _ in done if o.status != ts.PASSED]
    seconds = round(time.monotonic() - started, 1)
    detail = f"範囲テスト {len(runs)} 本（{seconds} 秒 / 戦略 {strategy.name}）"
    if not failed:
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=True, detail=detail))
    lint_failed = [
        {"suite": r.suite, "verdict": "caused", "reason": "変更したファイルで落ちた", "command": r.command}
        for r in runs
        if r.kind == ts.LINT and r.command in failed
    ]
    if not any(kinds.get(c) == ts.TEST for c in failed):
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", lint=lint_failed))
    triage = (
        _classify(root, strategy, limits, a.base)
        if strategy.name != ts.ROUND_ONLY
        else {"fallback_reason": "round-only は JUnit を読まない"}
    )
    return _emit_outcome(
        OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", triage=triage, lint=lint_failed or None)
    )


class _Resp:
    def __init__(self, body):
        self.body = body


def _wait_ci(
    root: pathlib.Path, strategy: ts.Strategy, limits: dict, notes: list[str], base: str | None, lint: list[dict] | None = None
) -> int:
    """Pull Request のチェックを上限まで待ち、落ちたら CI の JUnit で見分ける（F4）。"""
    import gh_checks

    owner_repo = repo_lib.owner_repo(root)
    sha = _git_out(root, "rev-parse", "HEAD") or ""
    checks = list((strategy.ci or {}).get("checks") or [])
    if not owner_repo or not sha:
        emit(result(TOOL, "stopped", "origin のリポジトリか HEAD が分からず、CI を読めない", [], {}))
        return 2
    if not checks:
        emit(result(TOOL, "stopped", "待つチェックの名前が無い（test.ci.check か ci.required_checks）", [], {}))
        return 2

    def runs_now():
        return gh_checks.fetch_check_runs(owner_repo, sha, rest_get=lambda p: _Resp(test_triage.gh_json(p)), empty_ok=True)

    last: dict = {}

    def fetch():
        runs = runs_now()
        last["runs"] = runs
        # 照会の失敗は `None`、チェックの未登録は `pending`（`gh_checks.checks_outcome`）
        return gh_checks.checks_outcome(runs, checks)

    outcome, waited, attempts = test_triage.wait_check(fetch, float(limits["ci_wait_timeout"]))
    detail = (
        f"CI のチェック {', '.join(checks)}（{sha[:7]} / 待ち {waited:.0f} 秒・照会 {attempts} 回 / 上限 {limits['ci_wait_timeout']} 秒）"
    )
    if outcome is None:
        emit(result(TOOL, "stopped", f"{detail} の結論を得られなかった（上限か照会の失敗）", [{"waited_seconds": waited}], {}))
        return 2
    if outcome == "success":
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=not lint, detail=detail, lint=lint))
    xmls = test_triage.ci_junit_xmls(
        owner_repo, sha, checks, (strategy.ci or {}).get("junit_artifacts"), fetch_runs=lambda: last.get("runs")
    )
    triage = _classify(root, strategy, limits, base, ci_xmls=xmls)
    return _emit_outcome(
        OutcomeContext(strategy, limits, notes, passed=False, detail=detail + f" の結論は {outcome}", triage=triage, lint=lint)
    )


def _run_lint_whole(root: pathlib.Path, strategy: ts.Strategy, limits: dict, a, started: float) -> tuple[list[dict] | None, int | None]:
    """静的解析の全体テストを手元で走らせる（戦略に関わらず。#1483 決定 7）。上限は `started` から共有する。

    戻りは（落ちた suite の判定の並び。走らせるものが無ければ `None`, 止めたときの終了コード）。
    """
    commands = strategy.whole_commands(ts.LINT)
    if not commands:
        return None, None
    done, stopped = _run_each(root, commands, float(limits["whole_timeout"]), "whole-lint", started)
    if stopped is not None:
        command, outcome, log = stopped
        if outcome.launch_failed:
            return None, _emit_launch(strategy, command, outcome, log)
        emit(result(TOOL, "stopped", f"静的解析の全体テストが {limits['whole_timeout']} 秒で終わらなかった", [], {}))
        return None, 2
    failed = [c for c, o, _ in done if o.status != ts.PASSED]
    return _lint_verdicts(root, strategy, limits, a, failed), None


def cmd_whole(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a)
    # 手元の全体検証（静的解析とテスト）は 1 つの whole_timeout に収める
    budget_started = time.monotonic()
    lint, code = _run_lint_whole(root, strategy, limits, a, budget_started)
    if code is not None:
        return code
    if strategy.whole_on_ci:
        return _wait_ci(root, strategy, limits, notes, a.base, lint)
    commands = strategy.whole_commands(ts.TEST)
    if not commands:
        if lint is not None:
            return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=not lint, detail="静的解析の全体テスト", lint=lint))
        emit(result(TOOL, "stopped", "全体テストのコマンド（suites[].command）が無い", [{"note": n} for n in notes], {}))
        return 2
    test_triage.clear_junit(str(root), strategy)
    started = time.monotonic()
    done, stopped = _run_each(root, commands, float(limits["whole_timeout"]), "whole", budget_started)
    if stopped is not None:
        command, outcome, log = stopped
        if outcome.launch_failed:
            return _emit_launch(strategy, command, outcome, log)
        emit(result(TOOL, "stopped", f"全体テストが {limits['whole_timeout']} 秒で終わらなかった", [], {}))
        return 2
    failed_any = any(o.status != ts.PASSED for _, o, _ in done)
    seconds = round(time.monotonic() - started, 1)
    detail = f"全体テスト {len(commands)} 本（{seconds} 秒 / 戦略 {strategy.name}）"
    if not failed_any:
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=not lint, detail=detail, lint=lint))
    if strategy.name == ts.ROUND_ONLY:
        triage = {"fallback_reason": "round-only は JUnit を読まない"}
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", triage=triage, lint=lint))
    triage = _classify(root, strategy, limits, a.base)
    return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", triage=triage, lint=lint))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    scope = sub.add_parser("scope", help="範囲テスト（雛形の {paths} を置き換えて走らせる）")
    scope.add_argument("--paths", nargs="+", required=True, help="テストの suite の対象")
    scope.add_argument(
        "--changed",
        nargs="+",
        default=None,
        help="静的解析の suite の範囲（既定は起点との merge-base からの差分と追跡していない新しいファイル）",
    )
    whole = sub.add_parser("whole", help="全体テスト（手元か、CI に任せる戦略なら Pull Request のチェックを待つ）")
    whole.add_argument("--pr", type=int, default=None)
    whole.add_argument("--paths", nargs="+", default=None, help="静的解析の雛形の全体テストの {paths} に入れる範囲")
    for p in (scope, whole):
        p.add_argument("--root", default=".")
        p.add_argument("--base", default=None, help="着手前の HEAD とみなす起点のブランチ（既定は .ndf/worktree.json の base_branch）")
        p.add_argument("--template", default=None, help="範囲テストの雛形（{paths} を含むコマンド。宣言より先に効く）")
        p.add_argument("--test-kind", choices=ts.KINDS, default=ts.TEST, help="--template の種別（test = テスト / lint = 静的解析）")
    a = ap.parse_args(argv)
    try:
        return cmd_scope(a) if a.cmd == "scope" else cmd_whole(a)
    except ts.StrategyError as e:
        emit(result(TOOL, "stopped", str(e), [], {}))
        return 2
    except container_reach.Unreachable as e:
        # 走らせるとメインディレクトリのコードの結果になる。通ったとも落ちたともせず、判断できないで返す（#1337）
        emit(result(TOOL, "stopped", f"コンテナで走る suite へ worktree が届かない: {e.reason}", [], {}), code=2)


if __name__ == "__main__":
    sys.exit(main())
