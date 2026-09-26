"""シェルの字句・構文の解析の包み（#1142 の決定 19・20・種類 8）。tree-sitter-bash を呼ぶのはこのモジュールだけである。

hook の 1 本のエントリポイント（決定 20）が、書き込み先の推定・前景の sleep・プランを起こす副命令の判定に使う。
判定そのもの（どの語を書き込み先とみなすか）は呼び出し側が持ち、この包みは構文木を bash の読み方に揃えて渡す。

試行 T2（`experimental/hook-trial`・docs/ndf-experiments.md の台帳）で見つけた構文木の癖を、ここで直す。

| 癖 | 包みの扱い |
| --- | --- |
| 並び（`&&` / `||`）とパイプの末尾のリダイレクトが並び全体に付く | `split_redirects()` が本体とリダイレクトに分け、`reattach` で最後のコマンドへ付け直すかを返す |
| リダイレクトの被演算子の後ろの語が destination / argument として並ぶ | `command_words()` がコマンドの引数に戻す |
| `<>` が ERROR になる | `redirect_operator()` が演算子の字面をつないで読む |
| `time` がコマンド名になる | `command_name_index()` が `time`・`command`・`builtin` を飛ばす |
| ヒアドキュメントの本文の `$((…))` をコマンド置換と読み、バッククォートは読まない | `substitutions()` が算術展開を飛ばし、本文のバッククォートを読み直す |

読めない形（試行で食い違った 2 件）: `! case … esac` は否定した複合コマンドが ERROR になる（`has_parse_error()` が
真になるので、呼び出し側は判定を控える）。`cp a.txt $(basename b).txt` の展開を含む語は `$` を含んだまま返る
（書き込み先にしない）。

hook の経路で使うため、`deps.require()` は呼ばない（決定 20。hook は用意済みの環境の python から起動する）。
"""
from __future__ import annotations

import re
import shlex
from functools import lru_cache
from typing import Iterator, NamedTuple

import tree_sitter as ts
import tree_sitter_bash as tsb

Node = ts.Node
STATEMENT_LISTS = frozenset({"program", "compound_statement", "do_group", "else_clause"})
REATTACH_BODIES = frozenset({"list", "pipeline", "command", "redirected_statement", "negated_command"})
SUBSTITUTIONS = frozenset({"command_substitution", "process_substitution"})
COMMAND_PREFIXES = ("command", "builtin", "time")
_BACKTICK = re.compile(r"`([^`]*)`")
_TILDE = re.compile(r"~[^/\"'\\$]*(/|$)")


class Word(str):
    """引用を外した語。`tilde` は、引用の外の `~` で始まり bash がホームへ展開する形か。"""

    tilde = False


class Statement(NamedTuple):
    node: Node
    background: bool   # 直後が `&`


class Redirected(NamedTuple):
    body: Node | None
    redirects: list[Node]
    reattach: bool     # 本体が並び・パイプ・コマンドで、リダイレクトは最後のコマンドへ付け直す


@lru_cache(maxsize=1)
def _bash_parser() -> ts.Parser:
    return ts.Parser(ts.Language(tsb.language()))


def parse_bash(cmd: str) -> Node:
    """コマンドの文字列の構文木の根（`program`）。"""
    return _bash_parser().parse(cmd.encode("utf-8")).root_node


def has_parse_error(cmd: str) -> bool:
    """tree-sitter-bash が読めない箇所（ERROR・欠けた字句）を含むか。"""
    return parse_bash(cmd).has_error


def node_text(n: Node) -> str:
    return n.text.decode("utf-8", errors="replace") if n.text is not None else ""


def unquote(n: Node) -> Word:
    """語の引用を外した字面。展開を含む語は `$` を含んだまま返す。"""
    raw = node_text(n)
    try:
        parts = shlex.split(raw, posix=True)
        w = Word("".join(parts) if parts else "")
    except ValueError:
        w = Word(raw)
    w.tilde = bool(_TILDE.match(raw))
    return w


def field_of(parent: Node, child: Node) -> str | None:
    """`child` が `parent` の中で持つ欄の名前（`name` / `argument` / `redirect` / `destination` など）。"""
    try:
        return parent.field_name_for_child(parent.children.index(child))
    except ValueError:
        return None


def statement_list(n: Node) -> list[Statement]:
    """文の並び（`program` など）の子の文。コメントは除く。"""
    out = []
    for c in n.children:
        if c.is_named and c.type != "comment":
            nxt = c.next_sibling
            out.append(Statement(c, nxt is not None and nxt.type == "&"))
    return out


def split_redirects(n: Node) -> Redirected:
    """`redirected_statement` を本体とリダイレクトに分ける。"""
    body = n.child_by_field_name("body")
    rs = [c for c in n.children if field_of(n, c) == "redirect"]
    return Redirected(body, rs, body is not None and body.type in REATTACH_BODIES)


def redirect_operator(r: Node) -> str:
    """リダイレクトの演算子（`>`・`>>`・`&>`・`<>` など）。ERROR に割れた `<>` もつないで読む。"""
    return "".join(node_text(c) for c in r.children
                   if field_of(r, c) not in ("destination", "descriptor")).replace(" ", "")


def redirect_destination(r: Node) -> Node | None:
    """リダイレクトの被演算子（最初の destination）。"""
    return next((c for c in r.children if field_of(r, c) == "destination"), None)


def destination_after_newline(r: Node, dest: Node) -> bool:
    """演算子と被演算子の間に改行がある（bash では構文エラーで、ファイルを開かない）。"""
    return b"\n" in (r.text or b"")[:dest.start_byte - r.start_byte]


def heredoc_quoted(r: Node) -> bool:
    """ヒアドキュメントの区切りが引用されている（本文を展開しない）。"""
    start = next((c for c in r.children if c.type == "heredoc_start"), None)
    return start is not None and any(q in node_text(start) for q in "'\"\\")


def trailing_words(redirects: list[Node]) -> list[Node]:
    """リダイレクトの被演算子の後ろに並ぶ語。構文木は destination / argument とするが、bash ではコマンドの引数である。"""
    out: list[Node] = []
    for r in redirects:
        if r.type == "file_redirect":
            out += [c for c in r.children if field_of(r, c) == "destination"][1:]
        elif r.type in ("heredoc_redirect", "herestring_redirect"):
            out += [c for c in r.children if field_of(r, c) == "argument"]
            out += trailing_words([c for c in r.children if c.type == "file_redirect"])
    return out


def command_parts(n: Node, extra_redirects: list[Node] = ()) -> tuple[list[Node], list[Node], list[Node]]:
    """`command` の (語のノード, リダイレクト, 代入)。語はリダイレクトの後ろの語を戻して字面の順に並べる。

    `extra_redirects` は付け直すリダイレクト（`split_redirects` の `reattach`）で、コマンド自身のものの後ろに置く。
    """
    words, rs, assigns = [], [], []
    for c in n.children:
        f = field_of(n, c)
        if c.type in ("file_redirect", "heredoc_redirect", "herestring_redirect"):
            rs.append(c)
        elif c.type == "variable_assignment":
            assigns.append(c)
        elif f in ("name", "argument"):
            words.append(c)
    rs += list(extra_redirects)
    words += trailing_words(rs)
    words.sort(key=lambda c: c.start_byte)
    return words, rs, assigns


def command_words(n: Node, extra_redirects: list[Node] = ()) -> list[Word]:
    """`command` の語を引用を外して返す。"""
    return [unquote(c) for c in command_parts(n, extra_redirects)[0]]


def command_name_index(words: list[str]) -> int:
    """コマンドの名前の位置。前に付く `time`・`command`・`builtin`（とその `-p` / `--`）を飛ばす。"""
    i = 0
    while i < len(words) and words[i] in COMMAND_PREFIXES:
        i += 1
        while i < len(words) and words[i] in ("-p", "--"):
            i += 1
    return i


def substitutions(n: Node) -> Iterator[Node]:
    """語やヒアドキュメントの中のコマンド置換とプロセス置換を、部分シェルとして流す構文木で返す。

    ヒアドキュメントの本文の `$((…))` は算術展開なので返さない。本文のバッククォートは構文木に現れないため、
    中身を読み直した `program` を返す。
    """
    quoted = n.type == "heredoc_redirect" and heredoc_quoted(n)
    for c in n.children:
        if quoted and c.type == "heredoc_body":
            continue  # 区切りを引用したヒアドキュメントは本文を展開しない
        if c.type in SUBSTITUTIONS:
            if n.type == "heredoc_body" and node_text(c).startswith("$(("):
                continue
            yield c
        elif c.type == "heredoc_content" and n.type == "heredoc_body":
            for m in _BACKTICK.finditer(node_text(c)):
                yield parse_bash(m.group(1))
        elif c.type == "heredoc_body" and not c.child_count:
            for m in _BACKTICK.finditer(node_text(c)):
                yield parse_bash(m.group(1))
        elif c.child_count:
            yield from substitutions(c)
