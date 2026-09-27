"""項目ごとの範囲テストの語の並びを組み立てる（#933 I8・#1334 F2）。

**実装担当はコマンドを返さず、テストの対象（`test_targets`）だけを返す。** 担当が書いたコマンドをそのまま
走らせると、検証の中身を担当が決められてしまう。進行側が戦略の範囲テストの雛形（宣言の `scope_command` か
`{paths}` を含む引数）の `{paths}` を対象へ置き換えて組み立て、`shell=False` で走らせる。

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

from .process import run_with_timeout
from .scope import covered_by_roots, test_locations
from .timeline import strategy_of

# 対象の語に含まれてはならない文字。組み立てた語の並びは `shell=False` で走らせるが、
# 担当が書いた値を語として通す以上、シェルの構文に読める値は最初から受け取らない。
_SHELL_CHARS = frozenset(";&|$`<>()\n")


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
    for target in targets:
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
        # `covered_by_roots` は起点が空なら全てを入れるため、空の場合は上で弾いてある。
        if not covered_by_roots(normalized, locations):
            return False
    return True


def scope_words_for(strategy: ts.Strategy, paths: list[str]) -> Optional[list[list[str]]]:
    """対象のパスを受け持つ suite ごとに、その suite の雛形で組んだ語の並び（suite ごとに 1 つ）。

    分け方は失敗の走らせ直し（`test_triage.rerun_groups`）と同じ。`scope_command` を持つ suite が無ければ `None`。
    """
    if not paths:
        return None
    return test_triage.rerun_words(strategy, [str(p) for p in paths]) or None


def as_commands(command: Any) -> list[list[str]]:
    """項目の `command` をコマンドの並びにする。

    `command` は 1 つの語の並び（`list[str]`。suite が 1 つ）か、suite ごとの語の並びの並び
    （`list[list[str]]`。対象が複数の suite にまたがる）。どちらでもなければ空。
    """
    if not isinstance(command, list) or not command:
        return []
    if all(isinstance(c, list) for c in command):
        return [[str(w) for w in c] for c in command if c]
    return [[str(w) for w in command]]


def command_key(command: Any) -> tuple[tuple[str, ...], ...]:
    """同じ検証を 1 回だけ走らせるための鍵。"""
    return tuple(tuple(c) for c in as_commands(command))


def item_command(commands: list[list[str]]) -> Any:
    """項目の `command` に書く形。suite が 1 つなら語の並びのまま、複数なら suite ごとの並び。"""
    return list(commands[0]) if len(commands) == 1 else [list(c) for c in commands]


def command_text(command: Any) -> str:
    """表示用の 1 行（suite ごとの語の並びを ` && ` でつなぐ。走らせるときはつながない）。"""
    return " && ".join(" ".join(c) for c in as_commands(command))


def run_commands(command: Any, work: str, timeout: int, log: pathlib.Path) -> tuple[Optional[int], bool]:
    """項目の `command`（形は `as_commands`）をシェルを通さずに順に走らせる。戻りは（終了コード, 打ち切ったか）。

    上限 `timeout` は suite 群全体で 1 つ（`test_triage.run_within`）。落ちた・打ち切った時点で止める。
    suite が複数なら 2 本目からの出力は `log` の名前に番号を足したファイルへ書き、最後に `log` へ足す。
    """
    commands = as_commands(command)
    if not commands:
        return None, False
    started = time.monotonic()
    logs = [log if i == 0 else log.with_name(f"{log.stem}-{i + 1}{log.suffix}") for i in range(len(commands))]
    code: Optional[int] = 0
    timed_out = False
    ran = 0
    for words, out in zip(commands, logs):
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, words=words, out=out: run_with_timeout(words, work, left, output=out)
        )
        ran += 1
        if timed_out or code != 0:
            break
    if ran > 1:
        with open(log, "ab") as sink:
            for extra in logs[1:ran]:
                if extra.exists():
                    sink.write(extra.read_bytes())
    return code, timed_out


def round_words(strategy: Optional[ts.Strategy]) -> Optional[list[str]]:
    """`round-only` のラウンドテストの語の並び。ほかの戦略・無ければ `None`。"""
    if strategy is None or strategy.name != ts.ROUND_ONLY or not strategy.round_command:
        return None
    try:
        words = shlex.split(strategy.round_command)
    except ValueError:
        return None
    return words or None


def limited_command(
    state_like: dict[str, Any],
    test_targets: list[str],
    work: str,
    planned: Iterable[str] = (),
) -> tuple[Any, str]:
    """項目の検証に使う語の並び（形は `as_commands`）と、その由来（`targets` / `round_test` / `none`）。

    対象が複数の suite にまたがるときは、suite ごとにその雛形で組んだ語の並びを全て返す。

    `round-only` はラウンドテストをそのまま走らせる。ほかの戦略は `test_targets` を雛形の `{paths}` へ入れ、
    入れられなければ `none`（呼ぶ側が `no_target` で見送る）。**全体テストを項目の検証に使うことはない。**
    """
    strategy = strategy_of(state_like)
    words = round_words(strategy)
    if words is not None:
        return words, "round_test"
    scope = state_like.get("target_scope") or state_like.get("scope") or []
    targets = list(test_targets or [])
    if valid_targets(targets, work, scope, planned):
        built = scope_words_for(strategy, targets)
        if built:
            return item_command(built), "targets"
    return None, "none"
