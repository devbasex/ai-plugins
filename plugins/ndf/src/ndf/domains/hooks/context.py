"""hook の 1 回の事象の文脈（docs/specifications/ndf-rebuild.md の決定 88・IH1）。

payload・シェルの構文木・会話の記録の末尾・最後の応答の分類は、最初に要った guard のときに 1 度だけ作り、
ほかの guard は同じものを使う。解析はこのモジュールの関数（`parse_payload`・`parse_shell`・`read_tail`）と、本文の
待ちのルール（`reply_wait` の `prose_lines`・`read_prose`。#4000 の決定 1）だけが行い、`HookContext` はその結果を持つ。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf.domains.hooks import reply_wait, tables
from ndf.domains.hooks.strict import _SHELLS

# 待ちのループ（本体と条件の位置を持たせる。`until` も `while_statement` として読まれる。`for` は数えない）
_WAIT_LOOPS = frozenset({"while_statement"})
# シェルの構文木で、ループとして数えるセクション（待ちのループと `for` のループ）
_LOOP_NODES = _WAIT_LOOPS | frozenset({"for_statement", "c_style_for_statement"})
# `bash -c` 系・`eval` の中身を読み直す深さの上限
_INNER_DEPTH = 5
# 語として読むセクション（引用を外して読む）
_WORD_NODES = frozenset({"word", "string", "raw_string", "number", "concatenation", "simple_expansion", "expansion", "ansi_c_string"})
# リダイレクトの先が実行時に決まるセクションの種類（変数・算術の展開、コマンド置換・プロセス置換）
_DYNAMIC_NODE = re.compile(r"(expansion|substitution)$")
# ヒアドキュメントとヒアストリングのセクション
_HEREDOC_NODES = frozenset({"heredoc_redirect", "herestring_redirect"})
# `only_commands` が許すセクション（単純なコマンドを `;`・`&&`・`||` でつないだ列と、付いたリダイレクト。#4049）
_PLAIN_NODES = _WORD_NODES - {"ansi_c_string"} | frozenset(
    "program list command command_name string_content variable_name redirected_statement file_redirect file_descriptor".split()
)
# 外側のシェルが展開しうる字（引用の外）
_EXPANDING = re.compile(r"[$`*?\[{~]")
# `only_commands` が中の文を読むシェル（`zsh` は `-c` でも起動ファイルを読む）と、起動ファイルを読まないオプション
_STEADY_SHELLS = _SHELLS - {"zsh"}
_STEADY_OPTION = re.compile(r"-[cefuvx]+|--norc|--noprofile")

# 人へ問う Tool の名前（会話の記録の `tool_use` の `name`）
_ASK_TOOL = "AskUserQuestion"


class ShellCommand(NamedTuple):
    """シェルの単純なコマンド 1 つ。

    `argv` は引用を外した語、`background` は `&` で背景へ回したか。`loop_body`・`loop_condition` は `while` / `until`
    の本体・条件の中か。`body_argv` は先頭のラッパーのコマンド（`timeout 590` など）とその引数を外した語で、
    読めないラッパーの引数か、コマンドを起動しない形（`command -v`）なら空。`source` は語ごとの引用とエスケープを残した字面
    （#3995。運用上限の guard が Bash と同じ値に読み直す）。`body_source` は `body_argv` と位置をそろえた字面（#3877。
    `rm` の guard が展開の効く位置を読む。ラッパーが無ければ `source` そのもの。`env -S` で分けた語は、env が文字にした部分を
    単引用で囲み、env が展開する `${NAME}` を字面のまま置く）。
    """

    argv: tuple[str, ...]
    background: bool
    loop_body: bool = False
    loop_condition: bool = False
    body_argv: tuple[str, ...] = ()
    source: tuple[str, ...] = ()
    body_source: tuple[str, ...] = ()

    @property
    def body_name(self) -> str:
        """ラッパーを外した後のコマンド名（パスを外した名前）。`body_argv` が空なら空。"""
        return os.path.basename(self.body_argv[0]) if self.body_argv else ""


class Redirect(NamedTuple):
    """`file_redirect` の 1 つ。`op` は演算子（`2>&-` は `>&` と先 `-`）、`target` は先の字面、`dynamic` は先が実行時に決まるか。"""

    op: str
    target: str
    dynamic: bool


class ShellScript(NamedTuple):
    """シェルの構文木から読んだもの。

    `commands` と `loops`（ループのセクションを含むか）は外側の単純コマンドだけを見る。`waits` は外側の単純コマンドに、
    `bash -c` 系・`eval` の中身の単純コマンド（外側の背景とループの位置を引き継ぐ）を足した列で、待ちの guard が見る。
    `redirects`・`heredocs`（ヒアドキュメントとヒアストリングの数）・`assignments`（変数の代入の数）は外側のスクリプトだけを見る
    （#3995。運用上限の guard が引継ぎのコマンドを照らす）。
    """

    commands: tuple[ShellCommand, ...]
    loops: bool
    waits: tuple[ShellCommand, ...] = ()
    redirects: tuple[Redirect, ...] = ()
    heredocs: int = 0
    assignments: int = 0


def parse_payload(text: str) -> Optional[dict[str, Any]]:
    """標準入力の JSON。オブジェクトでなければ None。"""
    return tables.json_object(text)


@lru_cache(maxsize=1)
def _parser() -> Any:
    import tree_sitter
    import tree_sitter_bash

    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_bash.language()))


def parse_shell(text: str) -> ShellScript:
    """シェルの文を構文木で読む（tree-sitter-bash）。"""
    script = _walk(text)
    waits = list(script.commands)
    for cmd in script.commands:
        waits += [c for inner in _inner_scripts(cmd) for c in _collect(inner, 1, cmd)]
    return script._replace(waits=tuple(waits))


def _walk(text: str) -> ShellScript:
    """1 つのスクリプトの単純コマンド・ループのセクションを含むか・リダイレクト・ヒアドキュメント・変数の代入（`waits` は空）。"""
    tree = _parser().parse(text.encode("utf-8"))
    commands: list[ShellCommand] = []
    redirects: list[Redirect] = []
    loops = False
    heredocs = assignments = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        loops = loops or node.type in _LOOP_NODES
        heredocs += node.type in _HEREDOC_NODES
        assignments += node.type == "variable_assignment"
        if node.type == "command":
            body, condition = _loop_position(node)
            commands.append(_command(node)._replace(background=_in_background(node), loop_body=body, loop_condition=condition))
        elif node.type == "file_redirect":
            redirects.append(_redirect(node))
        stack.extend(reversed(node.children))
    return ShellScript(tuple(commands), loops, (), tuple(redirects), heredocs, assignments)


def _command(node: Any) -> ShellCommand:
    """`command` のセクションの語（バックグラウンドとループの位置は読まない）。"""
    argv, joined, source = _words(node)
    stripped, stripped_source = _strip_wrappers(joined, source)
    return ShellCommand(argv, False, body_argv=stripped, source=source, body_source=stripped_source)


def only_commands(text: str, accept: Callable[[ShellCommand], bool]) -> bool:
    """文の全体が、`accept` を満たす単純なコマンドを `;`・`&&`・`||`・改行でつないだ列と、それに付いたリダイレクトだけか（#4049）。

    `bash -c` 系・`eval` は外側のコマンドを `accept` に渡さず、中の文に同じ確かめを当てる。確かめきれない文（コマンドが無い・
    構文の誤り・ほかの形・展開かロケールの翻訳（`$"…"`）で決まるコマンド名と中の文・動かすものを変えうるラッパー・起動ファイルを
    読みうるシェル・深さの上限）は偽。
    """
    return _only(text, accept, 0)


def _only(text: str, accept: Callable[[ShellCommand], bool], depth: int) -> bool:
    root = _parser().parse(text.encode("utf-8")).root_node
    if root.has_error:
        return False
    commands = []
    stack = [root]
    while stack:
        node = stack.pop()
        if not _is_plain(node):
            return False
        if node.type == "command":
            commands.append(node)
        stack.extend(node.children)
    return bool(commands) and all(_only_command(node, accept, depth) for node in commands)


def _is_plain(node: Any) -> bool:
    """`only_commands` が許すセクションか。

    バックグラウンドへ回す `&`・ロケールで翻訳される引用（`$"…"`）・コマンドの無いリダイレクト（`; > output`）は偽。
    """
    if not node.is_named:
        following = node.next_sibling
        return node.type != "&" and not (node.type == "$" and following is not None and following.type == "string")
    if node.type == "redirected_statement":
        body = node.child_by_field_name("body")
        return body is not None and body.type in ("command", "list")
    return node.type in _PLAIN_NODES


def _only_command(node: Any, accept: Callable[[ShellCommand], bool], depth: int) -> bool:
    cmd = _command(node)
    if not _is_steady(node, cmd):
        return False
    inner = _inner_scripts(cmd)
    if not inner:
        return accept(cmd)
    return depth < _INNER_DEPTH and _fixed_inner(node, cmd) and all(_only(script, accept, depth + 1) for script in inner)


def _is_steady(node: Any, cmd: ShellCommand) -> bool:
    """動かすコマンドが字面で決まるか（外したラッパーの語とコマンド名の値がすべて決まり、ラッパーが動かすものを変えない）。

    `env -S` で分けた語はセクションと対応しないので偽。
    """
    skipped = len(cmd.source) - len(cmd.body_source)
    if skipped < 0 or cmd.source[skipped:] != cmd.body_source:
        return False
    names = {os.path.basename(shell_value(text)) for text in cmd.source[:skipped]}
    altered = any(name in _WRAPPER_ARGS and _WRAPPER_ARGS[name].alters for name in names)
    return not altered and _all_fixed(_word_groups(node)[: skipped + 1])


def _fixed_inner(node: Any, cmd: ShellCommand) -> bool:
    """動くのが `_inner_scripts` の返す中の文だけで、外側のシェルの展開によらず決まるか（`_is_steady` が真のコマンドに使う）。

    シェルは、`-c` までの語がすべて起動ファイルを読まない 1 語のオプションで（`bash x.sh -c …` は `x.sh` を、`bash -lc …` は
    起動ファイルを動かす）、`-c` の語とその次の語の値が決まるときだけ真。`eval` は引数のすべての値が決まるときだけ真。
    """
    body = _word_groups(node)[len(cmd.source) - len(cmd.body_source) :]
    position = _c_option_position(cmd)
    if position is None:
        return _all_fixed(body[1:])
    steady = cmd.body_name in _STEADY_SHELLS and all(_STEADY_OPTION.fullmatch(word) for word in cmd.body_argv[1 : position + 1])
    return steady and _all_fixed(body[1 : position + 2])


def _all_fixed(groups: list[list[Any]]) -> bool:
    return all(len(group) == 1 and _is_fixed(group[0]) for group in groups)


def _is_fixed(node: Any) -> bool:
    """語の値が展開によらず決まるか（単引用・展開を含まない二重引用・展開しうる字を含まない語と数値）。"""
    if node.type == "string":
        return all(child.type == "string_content" for child in node.named_children)
    if node.type in ("word", "number"):
        return not _EXPANDING.search(node.text.decode("utf-8", "replace"))
    return node.type == "raw_string"


def _redirect(node: Any) -> Redirect:
    """`file_redirect` の演算子・先の字面・先が実行時に決まるか。`>&-`・`<&-` は演算子と先 `-` に分ける。"""
    target = node.child_by_field_name("destination")
    op = "".join(c.text.decode("utf-8", "replace") for c in node.children if c != target and c.type != "file_descriptor")
    text = target.text.decode("utf-8", "replace") if target is not None else ""
    if op in (">&-", "<&-") and not text:
        op, text = op[:-1], "-"
    return Redirect(op, text, target is not None and _has_dynamic(target))


def _has_dynamic(node: Any) -> bool:
    """`node` とその子孫に、値が実行時に決まる要素（展開・置換など）があるか。"""
    stack = [node]
    while stack:
        current = stack.pop()
        if _DYNAMIC_NODE.search(current.type):
            return True
        stack.extend(current.children)
    return False


def _collect(text: str, depth: int, outer: Optional[ShellCommand]) -> list[ShellCommand]:
    """`bash -c` 系・`eval` の中身の単純コマンド。`outer` の背景とループの位置を論理和で引き継ぎ、深さの上限まで読み直す。"""
    found: list[ShellCommand] = []
    for inner in _walk(text).commands:
        cmd = _inherit(inner, outer)
        found.append(cmd)
        if depth < _INNER_DEPTH:
            found += [c for script in _inner_scripts(cmd) for c in _collect(script, depth + 1, cmd)]
    return found


def _inherit(cmd: ShellCommand, outer: Optional[ShellCommand]) -> ShellCommand:
    """`outer` の背景とループの位置を `cmd` へ論理和で引き継ぐ。`outer` が None ならそのまま。"""
    if outer is None:
        return cmd
    return cmd._replace(
        background=cmd.background or outer.background,
        loop_body=cmd.loop_body or outer.loop_body,
        loop_condition=cmd.loop_condition or outer.loop_condition,
    )


def _loop_position(node: Any) -> tuple[bool, bool]:
    """祖先の `while` / `until` の（本体の中か, 条件の中か）。"""
    body = condition = False
    current = node
    while current.parent is not None:
        parent = current.parent
        if parent.type in _WAIT_LOOPS:
            body = body or current == parent.child_by_field_name("body")
            condition = condition or current in parent.children_by_field_name("condition")
        current = parent
    return body, condition


def _inner_scripts(cmd: ShellCommand) -> list[str]:
    """`bash`・`sh`・`zsh`・`dash` の `-c` の引数と、`eval` の引数をつないだ文字列。

    外側のシェルが引用とエスケープを解いた後の値を返す（`bash -c "rm \\"\\$W\\""` の中身は `rm "$W"`）。
    """
    if cmd.body_name == "eval" and len(cmd.body_argv) > 1:
        return [" ".join(shell_value(text) for text in cmd.body_source[1:])]
    position = _c_option_position(cmd)
    return [] if position is None else [shell_value(cmd.body_source[position + 1])]


def _c_option_position(cmd: ShellCommand) -> Optional[int]:
    """シェルの先頭の `-c` の語の `body_argv` での位置。シェルでないか、後ろに中の文が無ければ None。"""
    if cmd.body_name not in _SHELLS:
        return None
    return next((i for i, word in enumerate(cmd.body_argv[1:-1], start=1) if _is_c_option(word)), None)


def _is_c_option(word: str) -> bool:
    """シェルの `-c`（`-lc` など）か。"""
    return word.startswith("-") and not word.startswith("--") and "c" in word


# ANSI-C 引用（`$'…'`）の中のエスケープ
_ANSI_C_ESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
# 二重引用の中で `\\` が文字にする字（改行は行の継続として消える）。guards も同じ集まりを使う（`context_limit`）
DQUOTE_ESCAPABLE = frozenset('$`"\\\n')


# ANSI-C 引用の数値のエスケープ（字・桁の上限・基数）
_ANSI_C_NUMBERS = {"x": (2, 16), "u": (4, 16), "U": (8, 16)}
_OCTAL = "01234567"
_HEX = "0123456789abcdefABCDEF"


def ansi_c_escape(text: str, i: int) -> tuple[str, int]:
    """`text[i]` の `\\` から読む ANSI-C 引用のエスケープの値と、読んだ字数（Bash の `$'…'` の規則）。

    値が NUL（`\\0`・`\\x0` など）なら、Bash はそこで引用の値を打ち切る（呼び出し側が残りを捨てる）。Unicode の字にならない
    値（サロゲート・`0x10FFFF` を超える値）は、例外にせず置換文字（U+FFFD）にする。
    """
    nx = text[i + 1]
    if nx in _ANSI_C_NUMBERS:
        width, base = _ANSI_C_NUMBERS[nx]
        digits = _digits(text, i + 2, width, _HEX)
        return (_code_point(int(digits, base)), 2 + len(digits)) if digits else ("\\" + nx, 2)
    if nx in _OCTAL:
        digits = _digits(text, i + 1, 3, _OCTAL)
        return chr(int(digits, 8) & 0xFF), 1 + len(digits)
    if nx == "c" and i + 2 < len(text):
        return chr(ord(text[i + 2]) & 0x1F), 3
    if nx in _ANSI_C_ESCAPES:
        return _ANSI_C_ESCAPES[nx], 2
    return "\\" + nx, 2


def _code_point(value: int) -> str:
    if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        return "\ufffd"
    return chr(value)


def _digits(text: str, start: int, width: int, allowed: str) -> str:
    end = start
    while end < len(text) and end - start < width and text[end] in allowed:
        end += 1
    return text[start:end]


# ANSI-C 引用の中で NUL を読んだ後（閉じる `'` まで値に足さない）
_CUT = "$'\0"


def shell_value(text: str) -> str:
    """語の字面から、外側のシェルが引用とエスケープを解いた値。展開（`$NAME`・`$(…)`）は値が分からないので字面のまま残す。"""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        if quote == "'":
            piece, quote, step = _single_step(text[i], quote)
        elif quote in ("$'", _CUT):
            piece, quote, step = _ansi_c_step(text, i, quote)
        else:
            piece, quote, step = _plain_step(text, i, quote)
        out.append(piece)
        i += step
    return "".join(out)


def _single_step(ch: str, quote: str) -> tuple[str, str, int]:
    """単引用の中の 1 字の値と、次の引用の状態と読んだ字数（`'` で引用が終わり、ほかの字はそのまま値になる）。"""
    if ch == "'":
        return "", "", 1
    return ch, quote, 1


def _ansi_c_step(text: str, i: int, quote: str) -> tuple[str, str, int]:
    """ANSI-C 引用の中の 1 字の値と、次の引用の状態と読んだ字数（NUL の後は閉じる `'` まで値に足さない）。"""
    ch, nx = text[i], text[i + 1 : i + 2]
    if ch == "\\" and nx:
        char, step = ansi_c_escape(text, i)
        quote = _CUT if char == "\0" else quote
        return ("" if quote == _CUT else char), quote, step
    if ch == "'":
        return "", "", 1
    return ("" if quote == _CUT else ch), quote, 1


def _plain_step(text: str, i: int, quote: str) -> tuple[str, str, int]:
    """引用の外と二重引用の中の 1 字の値と、次の引用の状態と読んだ字数。"""
    ch, nx = text[i], text[i + 1 : i + 2]
    if ch == "\\" and nx and (quote == "" or nx in DQUOTE_ESCAPABLE):
        return ("" if nx == "\n" else nx), quote, 2
    if ch == "$" and nx == "'" and quote == "":
        return "", "$'", 2
    return _quote_step(ch, quote)


def _quote_step(ch: str, quote: str) -> tuple[str, str, int]:
    """引用を開け閉てする字（`"` と引用の外の `'`）なら値を残さず状態を変え、ほかの字はそのまま値にする。"""
    if ch == '"':
        return "", ("" if quote == '"' else '"'), 1
    if ch == "'" and quote == "":
        return "", "'", 1
    return ch, quote, 1


class _Opts(NamedTuple):
    """ラッパーのコマンドのオプションの表の 1 行（`_WRAPPER_ARGS`）。

    `flags` は値を取らない・`values` は値を取る・`attached` は値を付けた 1 語（`--x=値`・`-x値`）だけで取る・`stops` は
    コマンドを起動しないオプション。`lead` はオプションの後の前置きの語の読み方（`duration` は 1 語・`env` は `NAME=値`）。
    `numeric` は `-` と数字だけの 1 語（`nice -10`）を値付きのオプションとして読み飛ばす。
    `alters` は起動するコマンドの環境（`env`）・引数（`xargs`）・argv[0]（`exec`）を変える。
    """

    flags: frozenset[str]
    values: frozenset[str]
    attached: frozenset[str] = frozenset()
    stops: frozenset[str] = frozenset()
    lead: str = ""
    numeric: bool = False
    alters: bool = False


def _names(*names: str) -> frozenset[str]:
    return frozenset(names)


# ラッパーのコマンドごとのオプション（詳細設計の「ラッパーの引数の読み方」）。キーの集まりは weight.WRAPPERS と同じで、
# テストで縛る（集まりのオーナーは weight.WRAPPERS）
_WRAPPER_ARGS = dict(
    timeout=_Opts(
        _names("-f", "--foreground", "-p", "--preserve-status", "-v", "--verbose"),
        _names("-s", "--signal", "-k", "--kill-after"),
        lead="duration",
    ),
    env=_Opts(
        _names("-i", "--ignore-environment", "-0", "--null", "-v", "--debug", "--list-signal-handling"),
        _names("-u", "--unset", "-C", "--chdir", "-f", "--file", "-a", "--argv0", "-S", "--split-string"),
        attached=_names("--ignore-signal", "--default-signal", "--block-signal"),
        lead="env",
        alters=True,
    ),
    nice=_Opts(frozenset(), _names("-n", "--adjustment"), numeric=True),
    ionice=_Opts(_names("-t", "--ignore"), _names("-c", "--class", "-n", "--classdata"), stops=_names("-p", "-P", "-u")),
    stdbuf=_Opts(frozenset(), _names("-i", "--input", "-o", "--output", "-e", "--error")),
    nohup=_Opts(frozenset(), frozenset()),
    time=_Opts(_names("-p", "-a", "--append", "-v", "--verbose", "-q", "--quiet"), _names("-f", "--format", "-o", "--output")),
    exec=_Opts(_names("-c", "-l"), _names("-a"), alters=True),
    command=_Opts(_names("-p"), frozenset(), stops=_names("-v", "-V")),
    xargs=_Opts(
        _names(
            "-0",
            "--null",
            "-o",
            "--open-tty",
            "-p",
            "--interactive",
            "-r",
            "--no-run-if-empty",
            "-t",
            "--verbose",
            "-x",
            "--exit",
            "--show-limits",
        ),
        _names(
            "-a",
            "--arg-file",
            "-d",
            "--delimiter",
            "-E",
            "-I",
            "-L",
            "--max-lines",
            "-n",
            "--max-args",
            "-P",
            "--max-procs",
            "-s",
            "--max-chars",
            "--process-slot-var",
        ),
        attached=_names("-e", "--eof", "-i", "--replace", "-l"),
        alters=True,
    ),
)
# `env -S` の文字列の、引用符の外と `"` の中のエスケープ（`\_`・`\c` は別に読む）
_ENV_ESCAPES = {"\\": "\\", '"': '"', "'": "'", "$": "$", "#": "#", "t": "\t", "n": "\n", "r": "\r", "f": "\f", "v": "\v"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `env -S` の文字列を読む途中の状態（続きを読む・ここで打ち切る）
_SPLIT_OK = "ok"
_SPLIT_CUT = "cut"


class _Word(str):
    """ラッパーを外す途中の語。値（`str`）と、位置をそろえた字面（`source`）を持つ。

    `env -S` で分けた語の字面は、GNU env が文字にした部分を単引用で囲み、`${NAME}` を字面のまま残す。
    """

    source: str

    def __new__(cls, value: str, source: str) -> "_Word":
        word = super().__new__(cls, value)
        word.source = source
        return word


def _strip_wrappers(argv: tuple[str, ...], source: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """先頭のラッパーのコマンドとその引数を外した語と、それに位置をそろえた字面。読めない引数・コマンドを起動しない形なら空。

    先頭がラッパーでなければ、語と字面をそのまま返す。ラッパーの後ろの語は、外側のシェルが引用とエスケープを解いた値
    （`shell_value`）で読む（`env -S "rm \\${D}"` の env が受け取るのは `rm ${D}`）。
    """
    if not argv or os.path.basename(argv[0]) not in _WRAPPER_ARGS:
        return argv, source
    words: Optional[list[str]] = [_Word(shell_value(text), text) for text in source]
    while words and os.path.basename(words[0]) in _WRAPPER_ARGS:
        words = _after_options(os.path.basename(words[0]), words[1:])
    if not words:
        return (), ()
    return tuple(str(word) for word in words), tuple(getattr(word, "source", word) for word in words)


def _after_options(name: str, words: list[str]) -> Optional[list[str]]:
    """ラッパー `name` のオプションと前置きの語を外した残り。読めなければ None。"""
    opts = _WRAPPER_ARGS[name]
    rest: Optional[list[str]] = list(words)
    while rest and rest[0].startswith("-") and rest[0] != "-":
        word = rest.pop(0)
        if word == "--":
            break
        if opts.numeric and word[1:].isdigit():
            continue
        rest = _long_option(opts, word, rest) if word.startswith("--") else _short_options(opts, word, rest)
    return None if rest is None else _after_lead(opts.lead, rest)


def _long_option(opts: _Opts, word: str, rest: list[str]) -> Optional[list[str]]:
    name, eq, value = word.partition("=")
    if eq:
        return _option_value(name, value, rest) if name in opts.values | opts.attached else None
    if name in opts.flags | opts.attached:
        return rest
    if name in opts.values and rest:
        return _option_value(name, rest[0], rest[1:])
    return None


def _short_options(opts: _Opts, word: str, rest: list[str]) -> Optional[list[str]]:
    for j, ch in enumerate(word[1:], start=2):
        flag = "-" + ch
        if flag in opts.stops:
            return None
        if flag in opts.attached:
            return rest
        if flag in opts.values:
            tail = word[j:]
            if tail:
                return _option_value(flag, tail, rest)
            return _option_value(flag, rest[0], rest[1:]) if rest else None
        if flag not in opts.flags:
            return None
    return rest


def _option_value(flag: str, value: str, rest: list[str]) -> Optional[list[str]]:
    """値を取ったオプションの後の残り。`env -S` は分けた語を残りの前に置く（オプションの読み取りへ戻す）。"""
    if flag not in ("-S", "--split-string"):
        return rest
    words = _split_env_string(value)
    return None if words is None else [*words, *rest]


def _after_lead(kind: str, rest: list[str]) -> Optional[list[str]]:
    if kind == "duration":
        return rest[1:] if rest else None
    if kind == "env":
        i = 0
        while i < len(rest) and (rest[i] == "-" or _ASSIGNMENT.match(rest[i])):
            i += 1
        return rest[i:]
    return rest


def _split_env_string(text: str) -> Optional[tuple[str, ...]]:
    """`env -S` の文字列を GNU env の規則で語に分ける。読めない文字列（閉じない引用符・知らないエスケープ・`${` の無い `$`）は None。"""
    return _EnvSplit(text).run()


class _EnvSplit:
    """`env -S` の文字列を 1 文字ずつ読む。`${NAME}` は値が分からないため、空でない字面のまま残す。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.quote = ""
        self.words: list[str] = []
        self.word: Optional[list[str]] = None
        # 語の部分ごとの（値, 展開の字面）。文字のままの部分は展開の字面が None
        self.parts: list[tuple[str, Optional[str]]] = []

    def run(self) -> Optional[tuple[str, ...]]:
        state: Optional[str] = _SPLIT_OK
        while state == _SPLIT_OK and self.i < len(self.text):
            state = self._single() if self.quote == "'" else self._plain()
        if state is None or (state == _SPLIT_OK and self.quote):
            return None
        self._end()
        return tuple(self.words)

    def _add(self, text: str, expansion: Optional[str] = None) -> None:
        """語へ `text` を足す。`expansion` は GNU env が展開する部分（`${NAME}`）の字面で、文字のままの部分は None。"""
        if self.word is None:
            self.word, self.parts = [], []
        self.word.append(text)
        self.parts.append((text, expansion))

    def _end(self) -> None:
        if self.word is not None:
            self.words.append(_Word("".join(self.word), _quoted_source(self.parts)))
            self.word = None

    def _single(self) -> str:
        ch, nx = self.text[self.i], self.text[self.i + 1 : self.i + 2]
        if ch == "'":
            self.quote = ""
            self.i += 1
        elif ch == "\\" and nx in ("\\", "'"):
            self._add(nx)
            self.i += 2
        else:
            self._add(ch)
            self.i += 1
        return "ok"

    def _plain(self) -> Optional[str]:
        ch = self.text[self.i]
        if ch == "\\":
            return self._escape()
        if ch == "$":
            return self._variable()
        self.i += 1
        if self.quote == '"':
            if ch == '"':
                self.quote = ""
            else:
                self._add(ch)
        elif ch in " \t\n":
            self._end()
        elif ch == "#" and self.word is None:
            return "cut"
        elif ch in "'\"":
            self.quote = ch
            self._add("")
        else:
            self._add(ch)
        return "ok"

    def _escape(self) -> Optional[str]:
        nx = self.text[self.i + 1 : self.i + 2]
        self.i += 2
        if nx == "c":
            return "cut"
        if nx == "_" and self.quote:
            self._add(" ")
        elif nx == "_":
            self._end()
        elif nx and nx in _ENV_ESCAPES:
            self._add(_ENV_ESCAPES[nx])
        else:
            return None
        return "ok"

    def _variable(self) -> Optional[str]:
        end = self.text.find("}", self.i)
        if not self.text.startswith("${", self.i) or end < 0:
            return None
        expansion = self.text[self.i : end + 1]
        self._add(expansion, expansion)
        self.i = end + 1
        return "ok"


def _quoted_source(parts: list[tuple[str, Optional[str]]]) -> str:
    """`env -S` で分けた 1 語の字面。続いた文字のままの部分を 1 つの単引用で囲み、展開の部分を字面のまま置く。"""
    out: list[str] = []
    literal: list[str] = []
    for text, expansion in parts + [("", "")]:
        if expansion is None:
            literal.append(text)
            continue
        if literal:
            out.append("'" + "".join(literal).replace("'", "'\\''") + "'")
            literal = []
        out.append(expansion)
    return "".join(out)


def _words(node: Any) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """単純コマンドの語を読む: 引用を外した語・連結の語（`-S'a b'`）も部分ごとに引用を外した語（ラッパーの引数を読むため）・字面（引用とエスケープを残す）。

    3 つの並びは同じ長さで、位置ごとに同じ語を指す。
    """
    argv: list[str] = []
    joined: list[str] = []
    source: list[str] = []
    for group in _word_groups(node):
        texts = [target.text.decode("utf-8", "replace") for target in group]
        argv.append("".join(_unquote(text, target.type) for text, target in zip(texts, group)))
        joined.append("".join(_joined(text, target) for text, target in zip(texts, group)))
        source.append("".join(texts))
    return tuple(argv), tuple(joined), tuple(source)


def _word_groups(node: Any) -> list[list[Any]]:
    """単純コマンドの語ごとのセクション。`command_name` はその中の語の要素に置き換える。

    隙間の無い 2 つのセクションは 1 つの語（tree-sitter-bash は `$W/$n.md.done` を `$W/$` と `n.md.done` に割る）。
    """
    groups: list[list[Any]] = []
    previous_end: Optional[int] = None
    for child in node.children:
        target = child.children[0] if child.type == "command_name" and child.children else child
        if target.type not in _WORD_NODES:
            previous_end = None
            continue
        if previous_end == child.start_byte:
            groups[-1].append(target)
        else:
            groups.append([target])
        previous_end = child.end_byte
    return groups


def _joined(text: str, node: Any) -> str:
    """語の要素の引用を外した値。連結の語は部分ごとに外す。"""
    if node.type == "concatenation":
        return "".join(_unquote(p.text.decode("utf-8", "replace"), p.type) for p in node.children)
    return _unquote(text, node.type)


def _unquote(text: str, kind: str) -> str:
    if kind in ("string", "raw_string") and len(text) >= 2:
        return text[1:-1]
    return text


def _in_background(node: Any) -> bool:
    current = node
    while current is not None and current.type != "program":
        sibling = current.next_sibling
        if sibling is not None and sibling.type == "&":
            return True
        current = current.parent
    return False


def read_tail(path: str, size: int) -> str:
    """会話の記録の末尾 `size` バイト（行の途中から始まる先頭の 1 行は捨てる）。読めなければ空。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            raw = f.read()
    except OSError:  # noqa: NDF401 ファイル: 会話の記録が無い・読めないときは空として扱う
        return ""
    text = raw.decode("utf-8", "replace")
    return text.split("\n", 1)[1] if end > size and "\n" in text else text


def asked_since_prompt(tail: str) -> bool:
    """会話の記録の末尾を後ろから読み、利用者の入力に当たる前に `AskUserQuestion` の呼び出しがあれば真。

    Tool の結果だけを持つ `user` の行と、Claude Code が書くメタの行（`isMeta` が真）は利用者の入力に数えない
    （`ndf.domains.relay.context_records._user_text` と同じ条件）。末尾に利用者の入力が無ければ、読めた範囲で探す。
    """
    for line in reversed(tail.splitlines()):
        if '"user"' not in line and _ASK_TOOL not in line:
            continue
        rec = tables.json_object(line)
        if rec is None:
            continue
        content = _content(rec)
        if _is_user_input(rec, content):
            return False
        if _asks(content):
            return True
    return False


def context_tokens(tail: str) -> Optional[int]:
    """会話の記録の末尾から、最後の応答の文脈の量（入力の token と cache の token の和）。無ければ None。"""
    for line in reversed(tail.splitlines()):
        if '"usage"' not in line:
            continue
        rec = tables.json_object(line)
        usage = ((rec or {}).get("message") or {}).get("usage") if rec else None  # noqa: NDF501 ファイル: 応答でない行は usage を持たない
        if isinstance(usage, dict):
            keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            return sum(int(usage.get(k) or 0) for k in keys)  # noqa: NDF501 ファイル: 無い項目は 0 token
    return None


@dataclass
class HookContext:
    """1 回の事象の文脈。解析は遅延で 1 度だけ行う。"""

    runtime: str
    event: str
    payload: dict[str, Any]
    tail_bytes: int = 262144
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    # 同じ事象の guard どうしで分け合う判定（キーは判定した guard の名前。#3995 の `handoff_bash`）
    memo: dict[str, Any] = field(default_factory=dict, repr=False)

    def _str(self, key: str) -> str:
        value = self.payload.get(key)
        return value if isinstance(value, str) else ""

    @property
    def session_id(self) -> str:
        return self._str("session_id")

    @property
    def cwd(self) -> str:
        return self._str("cwd") or os.getcwd()

    @property
    def tool(self) -> str:
        return self._str("tool_name")

    @property
    def tool_input(self) -> dict[str, Any]:
        value = self.payload.get("tool_input")
        return value if isinstance(value, dict) else {}

    @property
    def command(self) -> str:
        value = self.tool_input.get("command")
        return value if isinstance(value, str) else ""

    def missing(self, keys: list[str]) -> list[str]:
        """入力に無いキー。"""
        return [k for k in keys if k not in self.payload]

    @cached_property
    def shell(self) -> ShellScript:
        return parse_shell(self.command)

    @cached_property
    def transcript_tail(self) -> str:
        path = self._str("transcript_path")
        return read_tail(path, self.tail_bytes) if path else ""

    @cached_property
    def last_reply(self) -> str:
        """最後の応答の本文。入力の `last_assistant_message` が無ければ会話の記録の末尾から読む。"""
        text = self.payload.get("last_assistant_message")
        return text if isinstance(text, str) else _last_assistant_text(self.transcript_tail)

    @cached_property
    def last_reply_id(self) -> Optional[str]:
        """会話の記録の最後の `assistant` の記録の `uuid`。中継の活動の記録（`activity.json` の `reply`）に書き、読む側
        （`observe.reply_id`。同じく最後の `assistant` の記録を選ぶ）が今の応答の記録かを突き合わせる。読めなければ None。"""
        return _last_assistant_uuid(self.transcript_tail)

    @cached_property
    def reply_code_lines(self) -> list[bool]:
        from ndf.adapters import markdown

        return markdown.code_lines(self.last_reply) if self.last_reply else []

    @cached_property
    def reply_prose_lines(self) -> list[reply_wait.ProseLine]:
        """最後の応答の本文の行（ルールの `prose_lines`）。`has_reply_prose` と `last_reply_kind` が同じ返りを読む。"""
        return reply_wait.prose_lines(self.last_reply)

    @cached_property
    def has_reply_prose(self) -> bool:
        """最後の応答に本文の行があるか。guard `stop-wait` の最初の確かめ。"""
        return bool(self.reply_prose_lines)

    @cached_property
    def reply_reading(self) -> reply_wait.ProseReading:
        """最後の応答の本文の判定（`reply_wait.read_prose`）。`last_reply_kind` と guard `stop-wait` が同じ返りを読む。"""
        return reply_wait.read_prose(self.reply_prose_lines) if self.has_reply_prose else reply_wait.ProseReading("none")

    @cached_property
    def last_reply_kind(self) -> str:
        """最後の応答の種類（`none`・`elsewhere_wait`・`prose_wait`・`heading_wait`・`closing_report`・`report`）。ルールの判定（#4000 の決定 1）。

        本文の行は `reply_prose_lines` を読み、抜き出し直さない（`reply_wait.reply_kind` を呼ばない）。
        """
        return self.reply_reading.kind

    @cached_property
    def asked_this_turn(self) -> bool:
        """今の利用者の入力の後に `AskUserQuestion` で人へ問うたか。"""
        return asked_since_prompt(self.transcript_tail)

    @property
    def stop_hook_active(self) -> bool:
        return self.payload.get("stop_hook_active") is True

    @property
    def background_tasks(self) -> list[Any]:
        value = self.payload.get("background_tasks")
        return value if isinstance(value, list) else []

    def env_path(self, name: str) -> Optional[Path]:
        value = self.env.get(name)
        return Path(value) if value else None


def _last_assistant_text(tail: str) -> str:
    for line in reversed(tail.splitlines()):
        rec = tables.json_object(line) if '"assistant"' in line else None
        text = _record_text(rec) if rec else None
        if text is not None:
            return text
    return ""


def _last_assistant_uuid(tail: str) -> Optional[str]:
    for line in reversed(tail.splitlines()):
        rec = tables.json_object(line) if '"assistant"' in line else None
        if rec and rec.get("type") == "assistant":
            uuid = rec.get("uuid")
            return uuid if isinstance(uuid, str) else None
    return None


def _content(rec: dict[str, Any]) -> Any:
    """会話の記録の 1 レコードの content。`message` が無い・形が違えば None。"""
    message = rec.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _items(content: Any, kind: str) -> list[dict[str, Any]]:
    """content の要素のうち type が `kind` の dict。content が list でなければ空。"""
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == kind]


def _is_prompt(content: Any) -> bool:
    """利用者の入力の content か（文字列か text の要素を持つ）。Tool の結果だけを持つ行は偽。"""
    if isinstance(content, str):
        return True
    return bool(_items(content, "text"))


def _is_user_input(rec: dict[str, Any], content: Any) -> bool:
    """利用者の入力のレコードか。`user` の行のうち、メタの行（`isMeta` が真）と Tool の結果だけを持つ行は偽。"""
    return rec.get("type") == "user" and not rec.get("isMeta") and _is_prompt(content)


def _asks(content: Any) -> bool:
    """`AskUserQuestion` の呼び出しを持つ content か。"""
    return any(c.get("name") == _ASK_TOOL for c in _items(content, "tool_use"))


def _record_text(rec: dict[str, Any]) -> Optional[str]:
    """会話の記録の 1 レコードの本文。text の要素が無ければ None（文字列でない text だけなら空文字）。"""
    texts = [c.get("text") for c in _items(_content(rec), "text")]
    if not texts:
        return None
    return "\n".join(t for t in texts if isinstance(t, str))
