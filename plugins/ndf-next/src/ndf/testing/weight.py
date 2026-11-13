"""重い処理の判定（#2711 の決定 4）。ファイルもプロセスも触らない純粋な関数だけを置く。

判定は宣言（`.ndf/project.json` の `test.suites`）の `runner` と `command` だけを照らし、実行器の一覧を持たない。
持つのは並列の指定の語の表と、コマンドの位置を探すときに読み飛ばすラッパーの表だけである。
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath

# 並列の指定の語（この表を持つのはこのモジュールだけ）
PARALLEL_FLAGS = ("-n", "--numprocesses", "-j", "--jobs", "--parallel", "--maxWorkers")
# 後ろの語へコマンドの位置を渡すラッパーと起動の語
WRAPPERS = frozenset({"env", "timeout", "nice", "nohup", "time", "exec", "command", "stdbuf", "ionice", "xargs"})
LAUNCHERS = frozenset({"uv", "uvx", "python", "python3", "npx", "pnpm", "yarn", "npm", "bunx", "poetry", "pipx"})
# シェルの区切り（単純コマンドの境）とリダイレクトを作る文字
_SEPARATOR_CHARS = frozenset(";&|()\n")
_REDIRECT_CHARS = frozenset("<>&|")

WHOLE = "whole"
SCOPE = "scope"


def split_commands(text: str) -> list[list[str]]:
    """シェルのコマンドの文字列を単純コマンドの語の並びへ分ける。読めない文字列は空の一覧を返す。"""
    lexer = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    commands: list[list[str]] = [[]]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_redirect(token):
            i += 2  # リダイレクトとその先の語を読み飛ばす
            continue
        if token.isdigit() and i + 1 < len(tokens) and _is_redirect(tokens[i + 1]):
            i += 1  # `2>&1` の記述子の番号
            continue
        if set(token) <= _SEPARATOR_CHARS:
            commands.append([])
        else:
            commands[-1].append(token)
        i += 1
    return [c for c in commands if c]


def _is_redirect(token: str) -> bool:
    return ("<" in token or ">" in token) and set(token) <= _REDIRECT_CHARS


def _basename(word: str) -> str:
    return PurePosixPath(word).name


def runner_position(words: Sequence[str], runner: str) -> int | None:
    """`runner` がコマンドの位置にある語の添字。無ければ None。

    コマンドの位置は、先頭の語（環境変数の代入を除く）か、先頭がラッパーか起動の語のときはその後ろの語のうち
    パスの最後の要素が `runner` と一致する最初の語である。
    """
    i = 0
    while i < len(words) and "=" in words[i] and not words[i].startswith("-"):
        i += 1
    if i >= len(words):
        return None
    if _basename(words[i]) == runner:
        return i
    if _basename(words[i]) not in WRAPPERS | LAUNCHERS:
        return None
    for j in range(i + 1, len(words)):
        if _basename(words[j]) == runner:
            return j
    return None


def _has_parallel(args: Sequence[str]) -> bool:
    for arg in args:
        if arg in PARALLEL_FLAGS or any(arg.startswith(flag + "=") for flag in PARALLEL_FLAGS if flag.startswith("--")):
            return True
        if arg.startswith("-n") and arg[2:].isdigit():
            return True
    return False


def _targets(args: Sequence[str]) -> list[str]:
    """対象の語（選択肢でも、選択肢の値でも、並列の指定の値でもない語）。"""
    out: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in PARALLEL_FLAGS:
            skip = True
            continue
        if arg.startswith("-") or "=" in arg:
            continue
        out.append(arg)
    return out


def is_test_suite(suite: Mapping[str, object]) -> bool:
    """宣言の suite がテストか（`kind` が `lint` でなければテスト。既定は `test`）。"""
    return suite.get("kind", "test") != "lint"


def classify(command: str | Sequence[str], suites: Iterable[Mapping[str, object]]) -> str | None:
    """重い処理なら種類（`whole` / `scope`）、重くなければ None を返す。

    `command` は文字列（シェルのコマンド）か語の並び（そのまま 1 つの単純コマンドとして読む）。
    `suites` は宣言のうちテストの suite（`kind` が `lint` でないもの）を渡す。
    """
    suite_list = [s for s in suites if is_test_suite(s)]
    if isinstance(command, str):
        text = command.strip()
        if any(text == str(s.get("command", "")).strip() for s in suite_list if s.get("command")):
            return WHOLE
        commands = split_commands(command)
    else:
        commands = [list(command)]
    for words in commands:
        kind = _classify_words(words, suite_list)
        if kind:
            return kind
    return None


def _classify_words(words: list[str], suites: list[Mapping[str, object]]) -> str | None:
    for suite in suites:
        declared = split_commands(str(suite.get("command") or ""))
        if declared and words == declared[-1]:
            return WHOLE
    for suite in suites:
        runner = suite.get("runner")
        if not isinstance(runner, str) or not runner:
            continue
        pos = runner_position(words, runner)
        if pos is None:
            continue
        args = words[pos + 1 :]
        if _has_parallel(args):
            return SCOPE
        if all(t == "." for t in _targets(args)):
            return WHOLE
    return None


def is_heavy(command: str | Sequence[str], suites: Iterable[Mapping[str, object]]) -> bool:
    return classify(command, suites) is not None
