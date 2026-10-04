"""項目ごとの範囲テストを組み立てて走らせる（#933 I8・#1334 F2・#1483）。

**実装担当はコマンドを返さず、テストの対象（`test_targets`）だけを返す。** 担当が書いたコマンドをそのまま
走らせると、検証の中身を担当が決められてしまう。進行側が戦略の範囲テストの雛形（宣言の `scope_command` か
`{paths}` を含む引数）の `{paths}` を、シェルの引用で守った対象の並びへ置き換えて組み立て（`test_strategy.scope_runs`）、
シェルで 1 本ずつ走らせる（#1483 I5）。テストの種別の範囲テストは計画の時点で組んで項目の `scope_commands` へ置き、
静的解析の範囲テストは検証のたびに項目のコミットが変えたファイルから組む（#1483 決定 8）。

**コマンドの語は `{paths}` の置き換えのほかに読まない**（#1334 I2）。実行器の名前・前置き・オプションの値を
見て差し替えの位置を決める処理は持たない。
"""

from __future__ import annotations

import os
import pathlib
import shlex
import time
from typing import Any, Iterable, Optional

import test_strategy as ts
import test_triage

from . import launch
from .gitfacts import changed_files
from .items import item_shas
from .paths import work_dir
from .process import run_with_timeout
from .scope import covered_by_roots, test_locations
from .timeline import state_test_timeout, state_whole_timeout, strategy_of

# 対象の語に含まれてはならない文字。組み立てるときにシェルの引用で守るが（`test_strategy.fill`）、
# 担当が書いた値を語として通す以上、シェルの構文に読める値は最初から受け取らない。
_SHELL_CHARS = frozenset(";&|$`<>()\n")


def _target_ok(target: object, work: str, locations: list[str], planned_paths: set[str]) -> bool:
    """`test_targets` の 1 つが組み立てに使えるか（条件は `valid_targets` の説明）。"""
    if not isinstance(target, str) or not target:
        return False
    if any(ch in _SHELL_CHARS or ch.isspace() for ch in target):
        return False
    path = target.split("::", 1)[0]
    if not path or os.path.isabs(path):
        return False
    normalized = os.path.normpath(path)
    if normalized == ".." or normalized.startswith("../"):
        return False
    if not (pathlib.Path(work) / normalized).exists() and normalized not in planned_paths:
        return False
    # `covered_by_roots` は起点が空なら全てを入れるため、空の場合は呼び出し元で弾いてある。
    return covered_by_roots(normalized, locations)


def valid_targets(
    targets: list[str],
    work: str,
    scope: list[str],
    planned: Iterable[str] = (),
) -> bool:
    """`test_targets` が組み立てに使えるか。1 つでも満たさなければ偽。

    - シェルの構文の文字と空白を含まない
    - `::` より前のパスが作業ディレクトリの中に実在する（絶対パスと `..` で外へ出るものは不可）。
      **ただし、その項目が足すテスト（`planned` = 改修計画の `tests`）は実在しなくてよい**
      （改修計画の時点ではまだ無い。足さなければ項目ごと `not_done` で見送られる。実装計画 I11）
    - `--scope` のテストの置き場所の中にある
    """
    planned_paths = {os.path.normpath(p) for p in planned if isinstance(p, str) and p}
    if not targets:
        return False
    locations = [os.path.normpath(loc) for loc in test_locations(list(scope), work)]
    if not locations:
        return False
    return all(_target_ok(target, work, locations, planned_paths) for target in targets)


def scope_runs_for(strategy: ts.Strategy, paths: list[str]) -> Optional[list[ts.ScopeRun]]:
    """対象のパスを受け持つテストの suite ごとに、その suite の雛形で組んだ範囲テスト（suite ごとに 1 つ）。

    分け方は失敗の走らせ直し（`test_triage.rerun_groups`）と同じ。テストの `scope_command` が無ければ `None`。
    """
    if not paths:
        return None
    return ts.scope_runs(strategy, [str(p) for p in paths], []) or None


def _legacy_commands(command: Any) -> list[str]:
    """旧形の項目の `command`（語の並びか、その並び）をシェルで走らせる文字列へ直す（`shlex.join`。語は変わらない）。"""
    if isinstance(command, str):
        return [command] if command else []
    if not isinstance(command, list) or not command:
        return []
    if all(isinstance(c, list) for c in command):
        return [shlex.join([str(w) for w in c]) for c in command if c]
    return [shlex.join([str(w) for w in command])]


def item_runs(item: dict[str, Any]) -> list[ts.ScopeRun]:
    """項目に置いた範囲テスト（`scope_commands`）。無ければ旧形の `command` をテストとして読む。"""
    runs = item.get("scope_commands")
    if isinstance(runs, list):
        return [ts.ScopeRun.from_state(r) for r in runs if isinstance(r, dict)]
    return [ts.ScopeRun("legacy", ts.TEST, c) for c in _legacy_commands(item.get("command"))]


def lints_scoped(state: dict[str, Any]) -> bool:
    """静的解析の範囲テストを組むか。宣言から導いた `round-only` はラウンドテストが静的解析の全体を兼ねるため組まない。"""
    strategy = strategy_of(state)
    return bool(strategy.scoped_suites(ts.LINT)) and not (strategy.name == ts.ROUND_ONLY and not strategy.round_command)


def lint_runs_for(state: dict[str, Any], files: list[str]) -> list[ts.ScopeRun]:
    """ファイルの並びから組む静的解析の範囲テスト。suite の `paths` に当たるものだけを入れる（I10。#1693 決定 7）。

    項目の検証と最終ゲート修正の公開前の静的解析が同じ組み方を使う。
    """
    if not lints_scoped(state):
        return []
    return [r for r in ts.scope_runs(strategy_of(state), [], list(files)) if r.kind == ts.LINT]


def lint_runs(state: dict[str, Any], item: dict[str, Any]) -> list[ts.ScopeRun]:
    """静的解析の範囲テスト。項目のコミットが変えたファイルから組む（`lint_runs_for`）。"""
    if not lints_scoped(state):
        return []
    return lint_runs_for(state, changed_files(work_dir(state), item_shas(item)))


def verify_runs(state: dict[str, Any], item: dict[str, Any]) -> list[ts.ScopeRun]:
    """項目の検証で走らせる範囲テスト（テストの種別と静的解析）。"""
    return item_runs(item) + lint_runs(state, item)


def run_key(runs: list[ts.ScopeRun]) -> tuple[str, ...]:
    """同じ検証を 1 回だけ走らせるための鍵（コマンドの並び）。"""
    return tuple(r.command for r in runs)


def command_key(item: dict[str, Any]) -> tuple[str, ...]:
    """項目のテストの種別の範囲テストの鍵（取り消しで共有した項目をまとめる）。"""
    return run_key(item_runs(item))


def runs_state(runs: list[ts.ScopeRun]) -> list[dict[str, str]]:
    return [r.as_state() for r in runs]


def command_text(runs: Any) -> str:
    """表示用の 1 行（コマンドを ` ; ` で並べる。走らせるときはつながない）。"""
    commands = [r.command if isinstance(r, ts.ScopeRun) else str(r) for r in runs or []]
    return " ; ".join(commands)


def run_commands(commands: list[str], work: str, timeout: int, log: pathlib.Path) -> tuple[ts.Outcome, Optional[str]]:
    """コマンドをシェルで 1 本ずつ順に走らせ、結果（`test_strategy.outcome`）と最後に走らせたコマンドを返す。

    上限 `timeout` は全体で 1 つ（`test_triage.run_within`）。落ちた・打ち切った・起動の失敗の時点で止める。
    2 本目からの出力は `log` の名前に番号を足したファイルへ書き、最後に `log` へ足す。
    """
    commands = [c for c in commands if c]
    if not commands:
        return ts.outcome(0, False), None
    started = time.monotonic()
    logs = [log if i == 0 else log.with_name(f"{log.stem}-{i + 1}{log.suffix}") for i in range(len(commands))]
    result = ts.outcome(0, False)
    last: Optional[str] = None
    ran = 0
    for command, out in zip(commands, logs):
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, command=command, out=out: run_with_timeout(command, work, left, output=out)
        )
        ran += 1
        last = command
        result = ts.outcome(code, timed_out)
        if result.status != ts.PASSED:
            break
    if ran > 1:
        with open(log, "ab") as sink:
            for extra in logs[1:ran]:
                if extra.exists():
                    sink.write(extra.read_bytes())
    return result, last


def log_text(log: pathlib.Path) -> str:
    """範囲テストのログの本文（落ちたファイルの手がかり）。読めなければ空。"""
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def round_runs(strategy: Optional[ts.Strategy]) -> Optional[list[ts.ScopeRun]]:
    """`round-only` のラウンドテスト（suite ごとに 1 本。I6）。ほかの戦略・無ければ `None`。"""
    if strategy is None or strategy.name != ts.ROUND_ONLY:
        return None
    if strategy.round_command:
        return [ts.ScopeRun("round", strategy.round_kind, strategy.round_command)]
    runs = [ts.ScopeRun(s.name, s.kind, s.command) for s in strategy.suites if s.command]
    return runs or None


def limited_runs(
    state_like: dict[str, Any],
    test_targets: list[str],
    work: str,
    planned: Iterable[str] = (),
) -> tuple[Optional[list[ts.ScopeRun]], str]:
    """項目の検証に使うテストの種別の範囲テストと、その由来（`targets` / `round_test` / `lint` / `none`）。

    対象が複数の suite にまたがるときは、suite ごとにその雛形で組んだ範囲テストを全て返す。

    `round-only` はラウンドテストをそのまま走らせる。テストの種別の suite が無い戦略は空の並び（`lint`。
    静的解析の範囲テストは検証のたびに組む）。ほかの戦略は `test_targets` を雛形の `{paths}` へ入れ、
    入れられなければ `none`（呼ぶ側が `no_target` で見送る）。**全体テストを項目の検証に使うことはない。**
    """
    strategy = strategy_of(state_like)
    runs = round_runs(strategy)
    if runs is not None:
        return runs, "round_test"
    if not strategy.has_kind(ts.TEST):
        return [], "lint"
    scope = state_like.get("target_scope") or state_like.get("scope") or []
    targets = list(test_targets or [])
    if valid_targets(targets, work, scope, planned):
        built = scope_runs_for(strategy, targets)
        if built:
            return built, "targets"
    return None, "none"


def run_or_stop(
    path: pathlib.Path, state: dict[str, Any], commands: Any, log: pathlib.Path, *, whole: bool = False, phase: str = "verify"
) -> bool:
    """コマンドをシェルで 1 本ずつ走らせる（#1483 I5）。通ったら真、落ちた・打ち切ったら偽。

    `commands` は文字列 1 つか文字列の並び（旧形の語の並びは `shlex.join` で読む）。上限は `whole` なら全体テストの、
    そうでなければテストの上限で、全体で 1 つ。起動の失敗なら `launch.stop` で止まる（I8）。
    """
    if isinstance(commands, str):
        commands = [commands]
    elif isinstance(commands, list) and commands and not all(isinstance(c, str) for c in commands):
        commands = [r.command for r in item_runs({"command": commands})]
    limit = state_whole_timeout(state) if whole else state_test_timeout(state)
    result, last = run_commands([str(c) for c in commands or []], work_dir(state), limit, log)
    if result.launch_failed:
        launch.stop(path, state, phase, last, result, log)
    return result.status == "passed"
