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

# シェルの構文木で、ループとして数える節
_LOOP_NODES = frozenset({"while_statement", "until_statement", "for_statement", "c_style_for_statement"})
# 語として読む節（引用を外して読む）
_WORD_NODES = frozenset({"word", "string", "raw_string", "number", "concatenation", "simple_expansion", "expansion", "ansi_c_string"})

# 本文で人の答えを待つ言い回し（AskUserQuestion を使わずに待った応答）
_WAIT_RE = re.compile(
    r"(よろしいですか|いかがですか|どうしますか|どちらにしますか|選んでください|お知らせください|教えてください|"
    r"承認をお願いします|ご確認ください|ご判断ください|指示をください|お待ちしています|返答を待ちます|答えを待ちます)"
    r"|[?？]\s*$"
)
# 別の場所（ほかのセッション・レーン・計画）の待ちを報告する言い回し（#2324）
_ELSEWHERE_RE = re.compile(r"(別の|ほかの|他の)?(セッション|レーン|計画|オーガナイザー)[^\n]{0,40}(承認|答え|判断)[^\n]{0,10}待")
# 本文の待ちを見る末尾の行数
_TAIL_LINES = 4


class ShellCommand(NamedTuple):
    """シェルの単純なコマンド 1 つ。`argv` は引用を外した語、`background` は `&` で背景へ回したか。"""

    argv: tuple[str, ...]
    background: bool


class ShellScript(NamedTuple):
    """シェルの構文木から読んだもの。`loops` はループの節を含むか。"""

    commands: tuple[ShellCommand, ...]
    loops: bool


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
    tree = _parser().parse(text.encode("utf-8"))
    commands: list[ShellCommand] = []
    loops = False
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        loops = loops or node.type in _LOOP_NODES
        if node.type == "command":
            commands.append(ShellCommand(_argv(node), _in_background(node)))
        stack.extend(reversed(node.children))
    return ShellScript(tuple(commands), loops)


def _argv(node: Any) -> tuple[str, ...]:
    words = []
    for child in node.children:
        target = child.children[0] if child.type == "command_name" and child.children else child
        if target.type in _WORD_NODES:
            words.append(_unquote(target.text.decode("utf-8", "replace"), target.type))
    return tuple(words)


def _unquote(text: str, kind: str) -> str:
    if kind == "string" and len(text) >= 2:
        return text[1:-1]
    if kind == "raw_string" and len(text) >= 2:
        return text[1:-1]
    return text


def _in_background(node: Any) -> bool:
    current = node
    while current is not None and current.type not in ("program", "compound_statement", "subshell"):
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
    """最後の応答の種類: `none`（空）・`elsewhere_wait`（別の場所の待ちの報告）・`prose_wait`（本文で人の答えを待つ）・`report`。

    `code_lines` は行ごとのコードブロックの印（`ndf.adapters.markdown.code_lines`）。コードの中の行は見ない。
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
        """入力に無い鍵。"""
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
