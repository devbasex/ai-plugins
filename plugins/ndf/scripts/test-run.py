#!/usr/bin/env python3
"""テストの実行の入口（#1334 F2・F4・F5）。supervise の run のステップが呼ぶ。

    python3 test-run.py scope --paths <パス>... [--changed <パス>...] [--only-kind test|lint] [--root DIR] [--template CMD] [--test-kind test|lint]
    python3 test-run.py whole [--paths <パス>...] [--pr N] [--base <ブランチ>] [--only-kind lint] [--root DIR] [--template CMD] [--test-kind test|lint]
    （どちらも `--limit <秒>` で上限を渡せる。省くと所要の記録と直近の全体テストの実測から決める）

宣言（`.ndf/project.json` の `test`。無ければ `.ndf/supervise.json` の `test.command` を 1 つの suite として読む）から
戦略を解き（`lib/test_strategy.resolve`）、範囲テストは `test_strategy.scope_runs` で組んでシェルで走らせる（テストの suite には
`--paths`、静的解析の suite には変更したファイル = `--changed`、無ければ起点との merge-base からの差分と追跡していない新しいファイル）。
全体テストは、テストの種別は戦略が手元なら suite の `command` を、CI に任せる戦略なら Pull Request のチェックを待って JUnit を読み、
静的解析は戦略に関わらず手元で走らせる。落ちたテストは `lib/test_triage.classify` で フレーキー・既存失敗・変更起因 に、
落ちた静的解析は `lib/test_triage.lint_verdict` で分ける。`--template` は引数の雛形で、宣言より先に効き、種別は `--test-kind`。
`whole --paths` は静的解析の雛形の全体テストの `{paths}` に入れる範囲である。
`scope --only-kind` は渡した種別の suite だけを打ち（当てる範囲は同じ）、打つものが無ければ通る（0）。
`whole --only-kind lint` は静的解析の全体テストだけを手元で走らせ、テストの種別は走らせず CI も待たない（CI テストの宣言のある
実装のプランが、`scope_command` の無い静的解析を打つ。#3056）。打つものが無ければ通る（0）。
手元の全体検証が上限の中で終わったら（通った・落ちたを問わない）、その秒を全体テストの実測として `lib/whole_runs.py` へ残す。
`whole` は差分が文書のファイルだけ（`lib/diff_kinds.py`。#2925）ならテストの種別を走らせず、テストが読む文書があれば対応するテストだけを
範囲テストの雛形で手元で走らせる（静的解析は今と同じ。読めなければ全体。分類は `items[].diff_kinds`）。

終了コード: 0 = 通った（フレーキーと既存失敗だけのときも 0。`items` に分類を持つ）/ 1 = 変更起因の失敗がある /
2 = 判断できない（起動の失敗・CI が上限までに終わらない・`gh` が使えない・宣言の不足・コンテナで走る suite が worktree を
見ていない。理由と待った秒を `summary` に出す）。起動の失敗は `items[].launch_failed` にコマンド・終了コード・理由・ログを持つ。
コンテナで走る suite は手元で走らせる前にテスト環境を用意し（`container_reach.env_for`。時間の上限の外）、起こそうとした実行は
`items[].testenv` に環境名・結果・秒を持つ。全体テストの落ちたコマンドは `items[].failed_commands` にコマンド・終了コード・ログ・
ログの末尾を持つ。
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
import deps  # noqa: E402

deps.require("schema", "pathmatch")  # 全体テストの実測（whole_runs → estimate_records）が pydantic を、差分の分類が pathspec を使う
import container_reach  # noqa: E402
import diff_kinds  # noqa: E402
import repo as repo_lib  # noqa: E402
import secret_redact  # noqa: E402
import step_result  # noqa: E402
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402
import whole_runs  # noqa: E402
from step_result import result  # noqa: E402

TOOL = "test-run"
LOG_DIR = pathlib.Path(".ndf") / "tmp" / "test-run"
LIMIT_KEY = {"scope": "test_timeout", "whole": "whole_timeout"}  # `--limit` が置き換える上限
LOG_TAIL_BYTES = 2000  # 結果へ写す、落ちたコマンドのログの末尾の量。ログは流し直しで上書きされる


def _emit_step(obj: dict, code: int | None = None):
    """結果を出す。テスト環境を起こそうとした実行は、どの止まり方でも `testenv` の行を載せる。"""
    events = container_reach.wake_events()
    if events:
        obj["items"].append({"testenv": events})
    step_result.emit(obj, code)


def _supervise_decl(root: pathlib.Path) -> dict:
    try:
        data = json.loads((root / ".ndf" / "supervise.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve(root: pathlib.Path, a) -> tuple[ts.Strategy, dict, list[str]]:
    decl, note = ts.decl_of(root, _supervise_decl(root))
    notes = [note] if note else []
    scope_paths = list(getattr(a, "paths", None) or []) if a.cmd == "whole" else None
    strategy = ts.resolve(decl, baseline_test=a.template, template_kind=a.test_kind, scope_paths=scope_paths)
    notes.extend(strategy.notes)
    limits = ts.unbudgeted_limits(strategy, decl, root)
    # プランのステップが決めた上限（`--limit`）は、ステップの `timeout` と同じ値から作ったものなので、それを使う（#1631）
    given = getattr(a, "limit", None)
    if given is not None:
        limits[LIMIT_KEY[a.cmd]] = int(given)
    return strategy, limits, notes


def _record_whole(root: pathlib.Path, strategy: ts.Strategy, started: float) -> None:
    """手元の全体検証が上限の中で終わった秒を、全体テストの実測として残す（#1631。書けなくても結果は変えない）。"""
    whole_runs.record_whole(root, ts.whole_key(strategy), time.monotonic() - started, "test-run")


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
    if strategy.name == ts.ROUND_ONLY and ci_xmls is None:
        return {"fallback_reason": "round-only は JUnit を読まない"}
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
    _emit_step(result(TOOL, "stopped", f"起動の失敗: {command}（{outcome.reason}）", [item], {"strategy": strategy.name}), code=2)
    return 2


def _emit_stopped(strategy: ts.Strategy, stopped: tuple, label: str, limit) -> int:
    """`_run_each` が止めた理由（起動の失敗か打ち切り）を結果に出して終了コードを返す。"""
    command, outcome, log = stopped
    if outcome.launch_failed:
        return _emit_launch(strategy, command, outcome, log)
    _emit_step(result(TOOL, "stopped", f"{label}が {limit} 秒で終わらなかった", [], {}))
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


def _log_tail(log: pathlib.Path) -> str:
    """ログの末尾。読み始めが行の途中なら、その行の残りを捨てる。先頭の切れた行は伏せ字の規則に当たらず、秘密の値が残るため。"""
    with open(log, "rb") as f:
        start = max(0, log.stat().st_size - LOG_TAIL_BYTES)
        f.seek(max(0, start - 1))  # 1 バイト前から読み、読み始めが行の先頭なら捨てる行を空にする
        data = f.read()
    return (data.partition(b"\n")[2] if start else data).decode("utf-8", "replace")


def _failed_commands(root: pathlib.Path, done: list) -> list[dict]:
    """落ちたコマンドの結果の行。ログの末尾は秘密を伏せて写す。"""
    out = []
    for command, outcome, log in done:
        if outcome.status == ts.PASSED:
            continue
        try:
            tail = _log_tail(log)
        except OSError:
            tail = ""
        out.append({"command": command, "code": outcome.code, "log": str(log.relative_to(root)), "tail": secret_redact.redact_output(tail)})
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
    if lint_caused:
        # 変更起因と確定した静的解析の失敗は、テスト側の見分け（判断不能の 2 を含む）より先に効く。
        status, code = "stopped", 1
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
    failed_commands: list[dict] | None = None
    extra: tuple = ()


def _emit_outcome(ctx: OutcomeContext) -> int:
    items = _outcome_items(ctx.strategy, ctx.limits, ctx.notes, ctx.triage, ctx.lint) + list(ctx.extra)
    if ctx.failed_commands:
        items.append({"failed_commands": ctx.failed_commands})
    status, code, suffix = _decide_status(ctx.passed, ctx.triage, ctx.lint)
    lint_caused = [v for v in ctx.lint or [] if v.get("verdict") == "caused"]
    _emit_step(result(TOOL, status, ctx.detail + suffix, items, {"caused": len((ctx.triage or {}).get("caused") or []) + len(lint_caused)}))
    return code


def _split_scope_failures(runs: list, failed: list[str]) -> tuple[list[dict], bool]:
    """落ちたコマンドを種別で分ける。戻りは（落ちた静的解析の判定の並び, テストの種別が落ちたか）。"""
    kinds = {r.command: r.kind for r in runs}
    lint_failed = [
        {"suite": r.suite, "verdict": "caused", "reason": "変更したファイルで落ちた", "command": r.command}
        for r in runs
        if r.kind == ts.LINT and r.command in failed
    ]
    return lint_failed, any(kinds.get(c) == ts.TEST for c in failed)


def cmd_scope(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a)
    only = getattr(a, "only_kind", None)
    lint_changed = strategy.name != ts.ROUND_ONLY and strategy.scoped_suites(ts.LINT) and only != ts.TEST
    runs, nothing_to_run = ts.select_scope_runs(strategy, list(a.paths), _changed(root, a) if lint_changed else [], only)
    if nothing_to_run:
        _emit_step(result(TOOL, "ok", f"範囲に当てる {only or ts.LINT} の suite が無い（戦略 {strategy.name}）", [], {"caused": 0}))
        return 0
    if not runs:
        _emit_step(result(TOOL, "stopped", "範囲テストの雛形（scope_command）を持つ suite が無いか、範囲に入るファイルが無い", [], {}))
        return 2
    container_reach.env_for(root)  # 用意の秒はテストの上限へ数えない
    test_triage.clear_junit(str(root), strategy)
    started = time.monotonic()
    done, stopped = _run_each(root, [r.command for r in runs], float(limits["test_timeout"]), "scope")
    if stopped is not None:
        return _emit_stopped(strategy, stopped, "範囲テスト", limits["test_timeout"])
    failed = [c for c, o, _ in done if o.status != ts.PASSED]
    seconds = round(time.monotonic() - started, 1)
    detail = f"範囲テスト {len(runs)} 本（{seconds} 秒 / 戦略 {strategy.name}）"
    if not failed:
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=True, detail=detail))
    lint_failed, test_failed = _split_scope_failures(runs, failed)
    if not test_failed:
        return _emit_outcome(OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", lint=lint_failed))
    triage = _classify(root, strategy, limits, a.base)
    return _emit_outcome(
        OutcomeContext(strategy, limits, notes, passed=False, detail=detail + " が落ちた", triage=triage, lint=lint_failed or None)
    )


# CI の待ちの照会だけが作る結論。チェックが未完了で PR が衝突している（#3076）
CONFLICTING = "conflicting"


class _Resp:
    def __init__(self, body):
        self.body = body


def _wait_ci(root: pathlib.Path, ctx: OutcomeContext, base: str | None, pr: int | None = None) -> int:
    """Pull Request のチェックを上限まで待ち、落ちたら CI の JUnit で見分ける（F4）。

    `pr` があれば、結論が `pending` の照会ごとに衝突の状態を 1 回読み、`CONFLICTING` なら待ちを打ち切る（#3076）。
    """
    import gh_checks
    import gh_pr_info

    strategy, limits = ctx.strategy, ctx.limits

    owner_repo = repo_lib.owner_repo(root)
    sha = _git_out(root, "rev-parse", "HEAD") or ""
    checks = list((strategy.ci or {}).get("checks") or [])
    if not owner_repo or not sha:
        _emit_step(result(TOOL, "stopped", "origin のリポジトリか HEAD が分からず、CI を読めない", [], {}))
        return 2
    if not checks:
        _emit_step(result(TOOL, "stopped", "待つチェックの名前が無い（test.ci.check か ci.required_checks）", [], {}))
        return 2

    last: dict = {}

    def fetch():
        last["runs"] = gh_checks.fetch_check_runs(owner_repo, sha, rest_get=lambda p: _Resp(test_triage.gh_json(p)), empty_ok=True)
        # 照会の失敗は `None`、チェックの未登録は `pending`（`gh_checks.checks_outcome`）
        outcome = gh_checks.checks_outcome(last["runs"], checks)
        # 衝突した PR では pull_request のワークフローが起動せず、待っても結論が出ない（#3076）
        if outcome == "pending" and pr is not None and gh_pr_info.fetch_mergeable(owner_repo, pr) == gh_pr_info.CONFLICTING:
            return CONFLICTING
        return outcome

    outcome, waited, attempts = test_triage.wait_check(fetch, float(limits["ci_wait_timeout"]))
    detail = (
        f"CI のチェック {', '.join(checks)}（{sha[:7]} / 待ち {waited:.0f} 秒・照会 {attempts} 回 / 上限 {limits['ci_wait_timeout']} 秒）"
    )
    if outcome == CONFLICTING:
        summary = f"PR #{pr} はベースブランチと衝突していて CI が起動しない（衝突の状態 CONFLICTING）。{detail}"
        nxt = f"python3 plugins/ndf/scripts/merged-steps.py base-sync --pr {pr}"
        _emit_step(result(TOOL, "stopped", summary, [{"waited_seconds": waited}], {}, next=nxt), step_result.EXIT_UNREADABLE)
        return step_result.EXIT_UNREADABLE
    if outcome is None:
        _emit_step(result(TOOL, "stopped", f"{detail} の結論を得られなかった（上限か照会の失敗）", [{"waited_seconds": waited}], {}))
        return 2
    if outcome == "success":
        return _emit_outcome(ctx._replace(passed=not ctx.lint, detail=detail))
    artifacts = (strategy.ci or {}).get("junit_artifacts")
    xmls = test_triage.ci_junit_xmls(owner_repo, sha, checks, artifacts, fetch_runs=lambda: last.get("runs"))
    triage = _classify(root, strategy, limits, base, ci_xmls=xmls)
    return _emit_outcome(ctx._replace(passed=False, detail=detail + f" の結論は {outcome}", triage=triage))


def _run_lint_whole(root: pathlib.Path, strategy: ts.Strategy, limits: dict, a, started: float) -> tuple[list[dict] | None, int | None]:
    """静的解析の全体テストを手元で走らせる（戦略に関わらず。#1483 決定 7）。上限は `started` から共有する。

    戻りは（落ちた suite の判定の並び。走らせるものが無ければ `None`, 止めたときの終了コード）。
    """
    commands = strategy.whole_commands(ts.LINT)
    if not commands:
        return None, None
    done, stopped = _run_each(root, commands, float(limits["whole_timeout"]), "whole-lint", started)
    if stopped is not None:
        return None, _emit_stopped(strategy, stopped, "静的解析の全体テスト", limits["whole_timeout"])
    failed = [c for c, o, _ in done if o.status != ts.PASSED]
    return _lint_verdicts(root, strategy, limits, a, failed), None


def _run_tests(root: pathlib.Path, a, ctx: OutcomeContext, commands: list[str], label: str, started: float | None) -> int:
    """テストの種別のコマンドを走らせて結果を出す。`started` を渡すと静的解析と上限を共有し、所要を全体テストの実測に残す。"""
    test_triage.clear_junit(str(root), ctx.strategy)
    t0 = time.monotonic()
    done, stopped = _run_each(root, commands, float(ctx.limits["whole_timeout"]), "whole" if started else "whole-docs", started)
    if stopped is not None:
        return _emit_stopped(ctx.strategy, stopped, label, ctx.limits["whole_timeout"])
    if started is not None:
        _record_whole(root, ctx.strategy, started)
    failed = _failed_commands(root, done)
    tail = f"（{round(time.monotonic() - t0, 1)} 秒 / 戦略 {ctx.strategy.name}）"
    if not failed:
        return _emit_outcome(ctx._replace(passed=not ctx.lint, detail=f"{label} {len(commands)} 本{tail}"))
    triage = _classify(root, ctx.strategy, ctx.limits, a.base)
    detail = f"{label} {len(commands)} 本のうち {len(failed)} 本が落ちた{tail}"
    return _emit_outcome(ctx._replace(passed=False, detail=detail, triage=triage, failed_commands=failed))


def _docs_tests(root: pathlib.Path, a, ctx: OutcomeContext, kinds: diff_kinds.DiffKinds) -> int | None:
    """文書だけの差分の全体テスト。範囲の雛形を持つ suite が無ければ `None`（呼び手が今と同じ全体テストを走らせる）。"""
    if not kinds.tests:
        detail = f"全体テストを飛ばした（差分が文書のファイルだけ: {len(kinds.docs)} ファイル）"
        return _emit_outcome(ctx._replace(passed=not ctx.lint, detail=detail))
    if unscoped := ts.unscoped_owners(ctx.strategy, list(kinds.tests)):  # 雛形を持つ別の suite へ渡さない
        ctx.notes.append(f"テストを受け持つ suite（{', '.join(unscoped)}）が範囲テストの雛形を持たず、全体テストを走らせる")
        return None
    runs = [r.command for r in ts.scope_runs(ctx.strategy, list(kinds.tests), []) if r.kind == ts.TEST]
    if not runs:
        ctx.notes.append("テストが読む文書を変えたが、範囲テストの雛形（scope_command）を持つ suite が無く、全体テストを走らせる")
        return None
    return _run_tests(root, a, ctx, runs, f"文書を読むテスト（全体テストの代わり。{len(kinds.tests)} ファイル）", None)


def cmd_whole(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a)
    # 起点との merge-base から HEAD までの差分（消したファイルを含む）。取れなければ全部をコードとして扱う
    kinds = diff_kinds.classify_root(root, diff_kinds.git_changed(root, f"{sha}..HEAD") if (sha := _base_sha(root, a.base)) else None)
    if strategy.whole_commands(ts.LINT) or (not strategy.whole_on_ci and strategy.whole_commands(ts.TEST)):
        container_reach.env_for(root)  # 用意の秒は全体テストの上限と実測へ数えない
    # 手元の全体検証（静的解析とテスト）は 1 つの whole_timeout に収める
    budget_started = time.monotonic()
    lint, code = _run_lint_whole(root, strategy, limits, a, budget_started)
    if code is not None:
        return code
    extra = ({"diff_kinds": json.loads(kinds.as_json())},)
    ctx = OutcomeContext(strategy, limits, notes, passed=not lint, detail="静的解析の全体テスト", lint=lint, extra=extra)
    if getattr(a, "only_kind", None) == ts.LINT:
        if lint is None:
            _emit_step(result(TOOL, "ok", f"全体で打つ lint の suite が無い（戦略 {strategy.name}）", [], {"caused": 0}))
            return 0
        return _emit_outcome(ctx)
    if kinds.docs_only() and (code := _docs_tests(root, a, ctx, kinds)) is not None:
        return code
    if strategy.whole_on_ci:
        return _wait_ci(root, ctx, a.base, getattr(a, "pr", None))
    commands = strategy.whole_commands(ts.TEST)
    if commands:
        return _run_tests(root, a, ctx, commands, "全体テスト", budget_started)
    if lint is not None:
        _record_whole(root, strategy, budget_started)
        return _emit_outcome(ctx)
    _emit_step(result(TOOL, "stopped", "全体テストのコマンド（suites[].command）が無い", [{"note": n} for n in notes], {}))
    return 2


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
    scope.add_argument(
        "--only-kind",
        choices=ts.KINDS,
        default=None,
        help="渡した種別（test / lint）の suite だけを打つ。範囲に当てるものが無ければ 0（範囲の静的解析を先に当てるプラン。#1651）",
    )
    whole = sub.add_parser("whole", help="全体テスト（手元か、CI に任せる戦略なら Pull Request のチェックを待つ）")
    whole.add_argument("--pr", type=int, default=None)
    whole.add_argument("--paths", nargs="+", default=None, help="静的解析の雛形の全体テストの {paths} に入れる範囲")
    whole.add_argument(
        "--only-kind", choices=[ts.LINT], default=None, help="静的解析の全体テストだけを手元で走らせる（CI を待たない。#3056）"
    )
    for p in (scope, whole):
        p.add_argument("--root", default=".")
        p.add_argument("--base", default=None, help="着手前の HEAD とみなす起点のブランチ（既定は .ndf/worktree.json の base_branch）")
        p.add_argument("--template", default=None, help="範囲テストの雛形（{paths} を含むコマンド。宣言より先に効く）")
        p.add_argument("--test-kind", choices=ts.KINDS, default=ts.TEST, help="--template の種別（test = テスト / lint = 静的解析）")
        p.add_argument(
            "--limit",
            type=int,
            default=None,
            help="この実行の上限の秒（scope は test_timeout、whole は whole_timeout を置き換える。既定は記録と直近の実測から決める）",
        )
    a = ap.parse_args(argv)
    try:
        return cmd_scope(a) if a.cmd == "scope" else cmd_whole(a)
    except ts.StrategyError as e:
        _emit_step(result(TOOL, "stopped", str(e), [], {}))
        return 2
    except container_reach.Unreachable as e:
        # 走らせるとメインディレクトリのコードの結果になる。通ったとも落ちたともせず、判断できないで返す（#1337）
        _emit_step(result(TOOL, "stopped", f"コンテナで走る suite へ worktree が届かない: {e.reason}", [], {}), code=2)


if __name__ == "__main__":
    sys.exit(main())
