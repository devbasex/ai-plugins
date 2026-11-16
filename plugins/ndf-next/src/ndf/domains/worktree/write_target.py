"""シェルのコマンドとパッチの本文から、書き込み先を推定する（worktree の guard の判断が使う。#1142 の決定 20）。

対象は 4 つの形に限る: 直接の書き換え（`sed -i`）・出力の付け替え（`>`・`>>`・`&>`・`&>>`・`>|`・`>&`・`<>`）・
標準入力からの書き出し（`tee`）・複製と移動（`cp`・`mv`）。構文木は tree-sitter-bash で作る。

**同じコマンドの中で先に実行される `cd` を反映する。** 起点（`base`）を渡すと出力は絶対パスになり、移動先を決められない
形（`cd` 単独・`cd -`・展開前の変数・条件つきの `cd`・ループや分岐の中の `cd`）の後は、相対パスの書き込み先を出さない。
起点を渡さない呼び方では字面のまま返す。展開前の変数を含む語は、どのパスを指すかを決められないので出さない。
構文を読み切れないコマンドは推定しない（空を返す）。hook への結線は hook の側が持つ。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from ndf.domains.hooks.context import _WORD_NODES
from ndf.domains.hooks.strict_shell import _REDIRECTIONS

# 書き込み先として扱わない語
NOT_TARGET = frozenset({"", ";", "&&", "/dev/null", "/dev/stdout", "/dev/stderr"})
# 続きを実行しない語（`cd x || exit` の右辺）
NONCONT = frozenset({"exit", "return", "break", "continue"})
# 書き込む向きのリダイレクト（`>` を含む演算子）
WRITE_OPS = frozenset(op for op in _REDIRECTIONS if ">" in op)
# 本体を順に読む文の並び
SEQUENCES = frozenset({"program", "compound_statement", "do_group", "else_clause", "elif_clause", "case_item"})
# 中で cd すると、その後の現在地を決められない構文
BRANCHES = frozenset({"if_statement", "while_statement", "for_statement", "c_style_for_statement", "case_statement"})
# 語の中で部分シェルとして読む置換
SUBSTITUTIONS = frozenset({"command_substitution", "process_substitution"})
# 引用を外して読む語
WORDS = _WORD_NODES
# 構文の種類 → 読み方（`_Scan` のメソッドの名前）。表に無い種類は中の置換だけを読む
DISPATCH = {
    **dict.fromkeys(SEQUENCES, "sequence"),
    **dict.fromkeys(BRANCHES, "branch"),
    "redirected_statement": "redirected",
    "command": "plain",
    "list": "andor",
    "pipeline": "pipeline",
    "subshell": "subshell",
    "negated_command": "negated",
    "function_definition": "function",
}
SED_INPLACE = re.compile(r"-[a-zA-Z]*i([a-zA-Z]*|\..*)")
PATCH_MARKS = ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: ")


@lru_cache(maxsize=1)
def _parser() -> Any:
    import tree_sitter
    import tree_sitter_bash

    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_bash.language()))


def normalize_path(path: str, cwd: str) -> str:
    """絶対パスへ直す。`.` と `..` を字面で畳み、実在する最も深い祖先だけを実体のパスへ直して残りを継ぎ足す。

    字面で畳むのは、存在しないパスで `..` が残ると前方一致の「配下か」の判定をすり抜けるため（`<対象>/a/../../外`）。
    """
    parts: list[str] = []
    for part in (path if path.startswith("/") else f"{cwd}/{path}").split("/"):
        if part == "..":
            parts = parts[:-1]
        elif part not in ("", "."):
            parts.append(part)
    for i in range(len(parts), 0, -1):
        head = "/" + "/".join(parts[:i])
        if os.path.isdir(head):
            return "/".join([os.path.realpath(head), *parts[i:]])
    return "/" + "/".join(parts)


def _text(node: Any) -> str:
    return node.text.decode("utf-8", "replace")


def _unquote(node: Any) -> str:
    """語の字面（引用を外す）。展開を含む語は `$` を残し、書き込み先から外せるようにする。"""
    text = _text(node)
    if node.type == "raw_string":
        return text[1:-1]
    if node.type == "string":
        inner = text[1:-1]
        return inner if not any(c.type in ("simple_expansion", "expansion", "command_substitution") for c in node.children) else "$" + inner
    if node.type == "concatenation":
        return "".join(_unquote(c) for c in node.children)
    if node.type in ("simple_expansion", "expansion", "command_substitution"):
        return "$" + text
    return text.replace("\\", "")


@dataclass
class _Place:
    """現在地。`cwd` が `None` なら相対パスの起点を決められない。`moving` は中で cd する関数の名前。"""

    base: Optional[str]
    cwd: Optional[str]
    cds: int = 0
    moving: set[str] = field(default_factory=set)

    def copy(self) -> "_Place":
        return _Place(self.base, self.cwd, self.cds, set(self.moving))


@dataclass(frozen=True)
class _AndOr:
    """`&&`・`||` の並びを読んだ結果。"""

    cd_count: int  # 数えた cd の数
    ends_with_cd: bool  # 最後が cd か
    crossed_or: bool  # `||` を跨いだか
    conditional_cd: bool  # 条件つきの cd があったか


class _Scan:
    """構文木を bash の実行の順に読み、書き込み先を `out` へ積む。"""

    def __init__(self) -> None:
        self.out: list[str] = []
        # 並び・パイプの末尾に付いたリダイレクト（構文木は並び全体に付ける）を、最後のコマンドへ付け直す
        self.pending: dict[int, list[Any]] = {}

    def take(self, n: Any) -> list[Any]:
        return self.pending.pop(n.id) if n.id in self.pending else []

    def emit(self, value: str, st: _Place) -> None:
        if value in NOT_TARGET or value.startswith(("&", "|")) or "$" in value:
            return
        if st.base is None:
            self.out.append(value)
            return
        if value == "~" or value.startswith("~/"):
            value = os.environ.get("HOME", "") + value[1:]
        elif value.startswith("~"):
            return
        if not value.startswith("/"):
            if st.cwd is None:
                return
            value = f"{st.cwd}/{value}"
        self.out.append(normalize_path(value, "/"))

    def node(self, n: Any, st: _Place) -> None:
        getattr(self, DISPATCH[n.type] if n.type in DISPATCH else "substitutions")(n, st)

    def plain(self, n: Any, st: _Place) -> None:
        self.command(n, st, [])

    def pipeline(self, n: Any, st: _Place) -> None:
        for c in n.named_children:
            self.node(c, st.copy())

    def subshell(self, n: Any, st: _Place) -> None:
        self.sequence(n, st.copy())

    def negated(self, n: Any, st: _Place) -> None:
        for c in n.named_children:
            self.node(c, st)

    def branch(self, n: Any, st: _Place) -> None:
        """分岐とループ。中で cd したら、その後の現在地を決められない。"""
        before = st.cds
        self.substitutions(n, st)
        for c in n.named_children:
            if c.type not in WORDS:
                self.node(c, st)
        if st.cds > before:
            st.cwd = None

    def sequence(self, n: Any, st: _Place) -> None:
        for c in n.named_children:
            if c.type == "comment" or c.type in WORDS:
                continue
            nxt = c.next_sibling
            self.node(c, st.copy() if nxt is not None and nxt.type == "&" else st)

    def function(self, n: Any, st: _Place) -> None:
        name, body = n.child_by_field_name("name"), n.child_by_field_name("body")
        inner = st.copy()
        if body is not None:
            self.node(body, inner)
        if inner.cds > st.cds and name is not None:
            st.moving.add(_text(name))

    def andor(self, n: Any, st: _Place) -> None:
        if self._andor(n, st).conditional_cd:
            # 条件つきの cd（`cmd && cd x`）の後は、cd が走ったかを字面から決められない
            st.cwd = None

    def _side(self, n: Any, st: _Place) -> int:
        before = st.cds
        self.node(n, st)
        return st.cds - before

    def _andor(self, n: Any, st: _Place) -> _AndOr:
        kids = [c for c in n.children if c.type != "comment"]
        left, op, right = kids[0], kids[1].type, kids[-1]
        entry = st.cwd
        if left.type == "list":
            first = self._andor(left, st)
        else:
            first = _AndOr(self._side(left, st), _is_cd(left), False, False)
        c1 = first.cd_count
        if op == "&&":
            c2 = self._side(right, st)
            return _AndOr(c1 + c2, _is_cd(right), first.crossed_or, first.conditional_cd or bool(c2 and not first.ends_with_cd))
        fail = st.copy()
        fail.cwd = entry if c1 == 1 and first.ends_with_cd else (None if c1 else fail.cwd)
        if not first.crossed_or and _noncontinuing(right):
            self.node(right, fail)  # `|| exit` を過ぎたなら左辺は成功している
            return _AndOr(0, False, False, False)
        st.cwd = fail.cwd
        c2 = self._side(right, st)
        if c1 + c2:
            st.cwd = None
        return _AndOr(c1 + c2, _is_cd(right), True, first.conditional_cd)

    def redirected(self, n: Any, st: _Place) -> None:
        body = n.child_by_field_name("body")
        redirects = [c for c in n.named_children if c != body] + self.take(n)
        if body is not None and body.type in ("list", "pipeline"):
            last = _last_statement(body)
            self.pending[last.id] = redirects
            self.node(body, st)
            return
        if body is not None and body.type == "command":
            self.command(body, st, redirects)
            return
        self.redirects(redirects, st)  # 複合コマンドのリダイレクトは入る前の位置で開く
        if body is not None:
            self.node(body, st)

    def redirects(self, nodes: list[Any], st: _Place) -> None:
        for r in nodes:
            self.substitutions(r, st)
            if r.type == "heredoc_redirect":
                self.redirects([c for c in r.named_children if c.type == "file_redirect"], st)
                continue
            value = _redirect_target(r) if r.type == "file_redirect" else None
            if value is not None:
                self.emit(value, st)

    def substitutions(self, n: Any, st: _Place) -> None:
        """語の中のコマンド置換とプロセス置換を、部分シェルとして読む。語そのものが置換（`echo $(…)`）でも読む。"""
        stack = [n]
        while stack:
            c = stack.pop()
            if c.type in SUBSTITUTIONS:
                self.sequence(c, st.copy())
            else:
                stack.extend(c.children)

    def command(self, n: Any, st: _Place, redirects: list[Any]) -> None:
        redirects = [*redirects, *self.take(n)]
        words: list[str] = []
        for c in n.named_children:
            self.substitutions(c, st)
            target = c.named_children[0] if c.type == "command_name" and c.named_children else c
            if target.type in WORDS:
                words.append(_unquote(target))
            elif c.type == "file_redirect":
                redirects = [*redirects, c]
        for value in command_targets(words):
            self.emit(value, st)
        self.redirects(redirects, st)  # リダイレクトは cd より前の位置で開く
        name = words[0] if words else ""
        if st.base is not None and name in st.moving:
            st.cwd = None
        if name == "cd" and st.base is not None:
            _apply_cd(st, words[1:])


def _redirect_target(r: Any) -> Optional[str]:
    """`file_redirect` 1 個が書き込む先の字面。書き込む向きでない・ファイル記述子の複製（`>&2`・`>&-`）なら `None`。"""
    op = next((c.type for c in r.children if not c.is_named), "")
    dest = r.child_by_field_name("destination")
    if dest is None or op not in WRITE_OPS:
        return None
    value = _unquote(dest)
    if op == ">&" and (value == "-" or value.isdigit()):
        return None
    return value


def _last_statement(n: Any) -> Any:
    """並び・パイプの最後の文。"""
    while n.type in ("list", "pipeline"):
        kids = [c for c in n.named_children if c.type != "comment"]
        if not kids:
            break
        n = kids[-1]
    return n


def _is_cd(n: Any) -> bool:
    body = n.child_by_field_name("body") if n.type == "redirected_statement" else n
    name = body.child_by_field_name("name") if body is not None and body.type == "command" else None
    return name is not None and _text(name) == "cd"


def _noncontinuing(n: Any) -> bool:
    """`exit`・`return`・`break`・`continue`（か、それで終わる `{ …; }`）か。"""
    if n.type == "redirected_statement" and n.child_by_field_name("body") is not None:
        n = n.child_by_field_name("body")
    if n.type == "compound_statement":
        return any(_noncontinuing(c) for c in n.named_children)
    name = n.child_by_field_name("name") if n.type == "command" else None
    return name is not None and _text(name) in NONCONT


def _apply_cd(st: _Place, args: list[str]) -> None:
    """`cd` の引数から現在地を進める。宛先を字面から決められなければ `cwd` を `None` にする。"""
    st.cds += 1
    dest = next((a for a in args if a == "--" or not a.startswith("-")), "")
    if dest == "--":
        rest = args[args.index("--") + 1 :]
        dest = rest[0] if rest else ""
    if not dest or "$" in dest or dest.startswith("~"):
        st.cwd = None
    elif dest.startswith("/"):
        st.cwd = normalize_path(dest, "/")
    elif st.cwd is not None:
        st.cwd = normalize_path(f"{st.cwd}/{dest}", "/")


def command_targets(words: list[str]) -> list[str]:
    """語の並びから `tee`・`sed -i`・`cp`・`mv` の書き込み先を、語の順に並べる（正規化の前の字面）。"""
    out: list[str] = []
    for k, w in enumerate(words):
        if w == "tee":
            out.extend(a for a in words[k + 1 :] if not a.startswith("-"))
        elif w == "sed":
            out.extend(sed_inplace_files(words[k + 1 :]))
        elif w in ("cp", "mv"):
            out.append(cp_mv_destination(words[k + 1 :]))
    return out


def _sed_kind(a: str) -> str:
    """sed の引数 1 つの種類: `inplace`・`script_next`（次の語が台本）・`script`・`flag`・`operand`。"""
    if a == "--in-place" or a.startswith("--in-place="):
        return "inplace"
    if a in ("-e", "-f", "--expression", "--file"):
        return "script_next"
    if a.startswith(("--expression=", "--file=", "-e", "-f")):
        return "script"
    if a.startswith("-"):
        return "inplace" if SED_INPLACE.fullmatch(a) else "flag"
    return "operand"


def sed_inplace_files(args: list[str]) -> list[str]:
    """`sed -i` が書き換えるファイル（台本の後の被演算子）。`-i` が無ければ空。"""
    inplace, script_seen, skip, files = False, False, False, []
    for a in args:
        kind = "skipped" if skip else _sed_kind(a)
        skip = kind == "script_next"
        inplace = inplace or kind == "inplace"
        if kind == "operand" and script_seen:
            files.append(a)
        script_seen = script_seen or kind in ("script_next", "script", "operand")
    return files if inplace else []


def _target_dir(a: str) -> Optional[str]:
    """`--target-directory=<d>`・`-t<d>` の `<d>`。当たらなければ `None`（`-t` 単独は空文字で、次の語が宛先）。"""
    if a.startswith("--target-directory="):
        return a.split("=", 1)[1]
    if a == "--target-directory":
        return ""
    return a[2:] if a.startswith("-t") else None


def cp_mv_destination(args: list[str]) -> str:
    """`-t`・`--target-directory` があればそれ、無ければ最後の被演算子。"""
    dest, tdir, take = "", "", False
    for a in args:
        given = None if take else _target_dir(a)
        if take:
            tdir, take = a, False
        elif given is not None:
            tdir, take = (given, False) if given else (tdir, True)
        elif not a.startswith("-"):
            dest = a
    return tdir if tdir else dest


def shell_targets(command: str, base: str = "") -> list[str]:
    """シェルのコマンドの書き込み先（実行の順。重複あり）。`base` を渡すと絶対パスで返す。読み切れなければ空。"""
    if not command:
        return []
    root = _parser().parse(command.encode("utf-8")).root_node
    if root.has_error:
        return []
    scan = _Scan()
    scan.node(root, _Place(base if base else None, base if base else None))
    return scan.out


def patch_targets(patch: str) -> list[str]:
    """`apply_patch` の本文（`*** Update File: <パス>` ほかの行）の書き込み先。"""
    out = []
    for line in patch.splitlines():
        mark = next((m for m in PATCH_MARKS if line.startswith(m)), None)
        target = line[len(mark) :].strip() if mark is not None else ""
        if target:
            out.append(target)
    return out
