"""hook の 1 回の事象の文脈（#3345 の決定 88・IH1）。

payload・シェルの構文木・会話の記録の末尾・最後の応答の分類は、最初に要った guard のときに 1 度だけ作り、
ほかの guard は同じものを使う。解析はこのモジュールの関数（`parse_payload`・`parse_shell`・`read_tail`・
`classify_reply`）だけが行い、`HookContext` はその結果を持つ。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.domains.hooks import tables
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

# 本文で人の回答を待つ言い回し（AskUserQuestion を使わずに待った応答）
_WAIT_RE = re.compile(
    r"(よろしいですか|いかがですか|どうしますか|どちらにしますか|選んでください|お知らせください|教えてください|"
    r"承認をお願いします|ご確認ください|ご判断ください|指示をください|お待ちしています|返答を待ちます|回答を待ちます)"
    r"|[?？]\s*$"
)
# 別の場所（ほかのセッション・レーン・計画）の待ちを報告する言い回し（#2324）
_ELSEWHERE_RE = re.compile(r"(別の|ほかの|他の)?(セッション|レーン|計画|オーガナイザー)[^\n]{0,40}(承認|回答|判断)[^\n]{0,10}待")
# 本文の待ちを見る末尾の行数
_TAIL_LINES = 4


class ShellCommand(NamedTuple):
    """シェルの単純なコマンド 1 つ。

    `argv` は引用を外した語、`background` は `&` で背景へ回したか。`loop_body`・`loop_condition` は `while` / `until`
    の本体・条件の中か。`body_argv` は先頭のラッパーのコマンド（`timeout 590` など）とその引数を外した語で、
    読めないラッパーの引数か、コマンドを起動しない形（`command -v`）なら空。`source` は語ごとの引用とエスケープを残した字面
    （#3995。運用上限の guard が Bash と同じ値に読み直す）。
    """

    argv: tuple[str, ...]
    background: bool
    loop_body: bool = False
    loop_condition: bool = False
    body_argv: tuple[str, ...] = ()
    source: tuple[str, ...] = ()

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
            argv, joined, source = _words(node)
            commands.append(ShellCommand(argv, _in_background(node), body, condition, _strip_wrappers(joined), source))
        elif node.type == "file_redirect":
            redirects.append(_redirect(node))
        stack.extend(reversed(node.children))
    return ShellScript(tuple(commands), loops, (), tuple(redirects), heredocs, assignments)


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
    """`bash`・`sh`・`zsh`・`dash` の `-c` の引数と、`eval` の引数をつないだ文字列。"""
    body_argv, head = cmd.body_argv, cmd.body_name
    if head == "eval" and len(body_argv) > 1:
        return [" ".join(body_argv[1:])]
    if head in _SHELLS:
        for i, word in enumerate(body_argv[1:-1], start=1):
            if word.startswith("-") and not word.startswith("--") and "c" in word:
                return [body_argv[i + 1]]
    return []


class _Opts(NamedTuple):
    """ラッパーのコマンドのオプションの表の 1 行（`_WRAPPER_ARGS`）。

    `flags` は値を取らない・`values` は値を取る・`attached` は値を付けた 1 語（`--x=値`・`-x値`）だけで取る・`stops` は
    コマンドを起動しないオプション。`lead` はオプションの後の前置きの語の読み方（`duration` は 1 語・`env` は `NAME=値`）。
    `numeric` は `-` と数字だけの 1 語（`nice -10`）を値付きのオプションとして読み飛ばす。
    """

    flags: frozenset[str]
    values: frozenset[str]
    attached: frozenset[str] = frozenset()
    stops: frozenset[str] = frozenset()
    lead: str = ""
    numeric: bool = False


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
    ),
    nice=_Opts(frozenset(), _names("-n", "--adjustment"), numeric=True),
    ionice=_Opts(_names("-t", "--ignore"), _names("-c", "--class", "-n", "--classdata"), stops=_names("-p", "-P", "-u")),
    stdbuf=_Opts(frozenset(), _names("-i", "--input", "-o", "--output", "-e", "--error")),
    nohup=_Opts(frozenset(), frozenset()),
    time=_Opts(_names("-p", "-a", "--append", "-v", "--verbose", "-q", "--quiet"), _names("-f", "--format", "-o", "--output")),
    exec=_Opts(_names("-c", "-l"), _names("-a")),
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
    ),
)
# `env -S` の文字列の、引用符の外と `"` の中のエスケープ（`\_`・`\c` は別に読む）
_ENV_ESCAPES = {"\\": "\\", '"': '"', "'": "'", "$": "$", "#": "#", "t": "\t", "n": "\n", "r": "\r", "f": "\f", "v": "\v"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `env -S` の文字列を読む途中の状態（続きを読む・ここで打ち切る）
_SPLIT_OK = "ok"
_SPLIT_CUT = "cut"


def _strip_wrappers(argv: tuple[str, ...]) -> tuple[str, ...]:
    """先頭のラッパーのコマンドとその引数を外した語。読めない引数・コマンドを起動しない形なら空。"""
    words: Optional[list[str]] = list(argv)
    while words and os.path.basename(words[0]) in _WRAPPER_ARGS:
        words = _after_options(os.path.basename(words[0]), words[1:])
    return tuple(words) if words else ()


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

    def run(self) -> Optional[tuple[str, ...]]:
        state: Optional[str] = _SPLIT_OK
        while state == _SPLIT_OK and self.i < len(self.text):
            state = self._single() if self.quote == "'" else self._plain()
        if state is None or (state == _SPLIT_OK and self.quote):
            return None
        self._end()
        return tuple(self.words)

    def _add(self, text: str) -> None:
        if self.word is None:
            self.word = []
        self.word.append(text)

    def _end(self) -> None:
        if self.word is not None:
            self.words.append("".join(self.word))
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
        self._add(self.text[self.i : end + 1])
        self.i = end + 1
        return "ok"


def _words(node: Any) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """単純コマンドの語を子の 1 回の走査で読む: 引用を外した語・連結の語（`-S'a b'`）も部分ごとに引用を外した語（ラッパーの引数を読むため）・字面（引用とエスケープを残す）。

    `command_name` はその中の語の要素に置き換える。3 つの並びは同じ長さで、位置ごとに同じ語を指す。
    """
    argv: list[str] = []
    joined: list[str] = []
    source: list[str] = []
    for child in node.children:
        target = child.children[0] if child.type == "command_name" and child.children else child
        if target.type not in _WORD_NODES:
            continue
        text = target.text.decode("utf-8", "replace")
        word = _unquote(text, target.type)
        argv.append(word)
        source.append(text)
        if target.type == "concatenation":
            joined.append("".join(_unquote(part.text.decode("utf-8", "replace"), part.type) for part in target.children))
        else:
            joined.append(word)
    return tuple(argv), tuple(joined), tuple(source)


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


def classify_reply(text: str, code_lines: Optional[list[bool]] = None) -> str:
    """最後の応答の種類: `none`（空）・`elsewhere_wait`（別の場所の待ちの報告）・`prose_wait`（本文で人の回答を待つ）・`report`。

    `code_lines` は行ごとのコードブロックのマーカー（`ndf.adapters.markdown.code_lines`）。コードの中の行は見ない。
    """
    lines = text.splitlines()
    flags = code_lines if code_lines is not None and len(code_lines) == len(lines) else [False] * len(lines)
    prose = [line for line, code in zip(lines, flags) if not code and line.strip()]
    if not prose:
        return "none"
    tail = "\n".join(prose[-_TAIL_LINES:])
    if _ELSEWHERE_RE.search(tail):
        return "elsewhere_wait"
    if any(_WAIT_RE.search(line.strip()) for line in prose[-_TAIL_LINES:]):
        return "prose_wait"
    return "report"


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
    def reply_code_lines(self) -> list[bool]:
        from ndf.adapters import markdown

        return markdown.code_lines(self.last_reply) if self.last_reply else []

    @cached_property
    def last_reply_kind(self) -> str:
        return classify_reply(self.last_reply, self.reply_code_lines)

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


def _record_text(rec: dict[str, Any]) -> Optional[str]:
    """会話の記録の 1 レコードの本文。text の要素が無ければ None（文字列でない text だけなら空文字）。"""
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    texts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    if not texts:
        return None
    return "\n".join(t for t in texts if isinstance(t, str))
