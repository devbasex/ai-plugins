"""シェルのコマンドとパッチの本文から、書き込み先を推定する（worktree の guard が使う。#1142 の決定 20）。

対象は 4 つの形に限る: 直接の書き換え（`sed -i`）・出力の付け替え（`>` / `>>` / `&>` / `>|` / `>&` / `<>`）・標準入力からの
書き出し（`tee`）・複製と移動（`cp` / `mv`）。構文木は `lib/shparse.py`（tree-sitter-bash）が作る。

**同じコマンドの中で先に実行される `cd` を反映する。** 起点（`base`）を渡すと出力は絶対パスになり、移動先を決められない
形（`cd` 単独・`cd -`・展開前の変数・`||` の左辺のどこで失敗したのかを決められない形）の後は、相対パスの書き込み先を
出さない。起点を渡さない呼び方では字面のまま返す。展開前の変数を含む語は、どのパスを指すかを決められないので出さない。
"""
from __future__ import annotations

import os
import re

import shparse as sp

NOT_TARGET = frozenset({"", ";", "&&", "/dev/null", "/dev/stdout", "/dev/stderr"})
NONCONT = frozenset({"exit", "return", "break", "continue"})
WRITE_OPS = frozenset({">", ">>", "&>", "&>>", ">|", ">&", "<>"})
LOOPS = ("if_statement", "while_statement", "for_statement", "c_style_for_statement", "case_statement")
STATEMENTS = frozenset({"command", "list", "pipeline", "redirected_statement", "compound_statement",
                        "subshell", "negated_command"})
SED_INPLACE = re.compile(r"-[a-zA-Z]*i([a-zA-Z]*|\..*)")
PATCH_MARKS = ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: ")


def normalize_path(path: str, cwd: str) -> str:
    """絶対パスへ直す。`.` と `..` を字面で畳み、実在する最も深い祖先だけを実体のパスへ直して残りを継ぎ足す。

    字面で畳むのは、存在しないパスで `..` が残ると前方一致の「配下か」の判定をすり抜けるためである
    （`<対象>/a/../../外` が `<対象>/` で始まって見える）。"""
    if not path.startswith("/"):
        path = f"{cwd}/{path}"
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    d, suffix = "/" + "/".join(parts), ""
    while d and d != "/":
        if os.path.isdir(d):
            r = os.path.realpath(d)
            return f"{r}/{suffix}" if suffix else r
        suffix = f"{os.path.basename(d)}/{suffix}" if suffix else os.path.basename(d)
        d = os.path.dirname(d)
    return "/" + "/".join(parts)


class _Place:
    """現在地の追跡。`cwd` が None なら、相対パスの起点を決められない。"""

    def __init__(self, base: str | None) -> None:
        self.base, self.cwd, self.cds, self.moving = base, base, 0, set()

    def copy(self) -> "_Place":
        s = _Place(self.base)
        s.cwd, s.cds, s.moving = self.cwd, self.cds, set(self.moving)
        return s


class _Scan:
    """構文木を bash の実行の順に読み、書き込み先を `out` へ積む。"""

    def __init__(self, base: str | None) -> None:
        self.out: list[str] = []
        self.root = _Place(base or None)

    def emit(self, v: str, st: _Place) -> None:
        if v in NOT_TARGET or v.startswith(("&", "|")) or "$" in v:
            return
        if st.base is None:
            self.out.append(v)
            return
        if getattr(v, "tilde", False):  # 引用した `~` は字面のまま（bash も展開しない）
            if v == "~" or v.startswith("~/"):
                v = os.environ.get("HOME", "") + v[1:]
            else:
                return
        if not v.startswith("/"):
            if st.cwd is None:
                return
            v = st.cwd + "/" + v
        self.out.append(normalize_path(v, "/"))

    # 戻り値は (この並びで数えた cd の数, 最後が cd か, `||` を跨いだか)
    def seq(self, n: sp.Node, st: _Place) -> tuple[int, bool, bool]:
        info = (0, False, False)
        for c, bg in sp.statement_list(n):
            if c.type in ("{", "}"):
                continue
            if bg:
                self.stmt(c, st.copy())
                info = (info[0], False, False)
            else:
                info = self.stmt(c, st)
        return info

    def stmt(self, n: sp.Node, st: _Place, redirs=()) -> tuple[int, bool, bool]:
        t = n.type
        if t == "redirected_statement":
            red = sp.split_redirects(n)
            if red.body is None:
                self.redirects(red.redirects, st)
                return (0, False, False)
            if red.reattach:  # 並び・パイプの末尾のリダイレクトは最後のコマンドへ付け直す
                return self.stmt(red.body, st, list(redirs) + red.redirects)
            self.redirects(list(redirs) + red.redirects, st)  # 複合コマンドのリダイレクトは入る前の位置で開く
            return self.stmt(red.body, st)
        if t == "command":
            return self.command(n, st, redirs)
        if t == "list":
            return self.andor(n, st, redirs)
        if t == "pipeline":
            segs = [c for c in n.children if c.is_named]
            for i, c in enumerate(segs):
                self.stmt(c, st.copy(), redirs if i == len(segs) - 1 else ())
            return (0, False, False)
        if t == "subshell":
            self.redirects(redirs, st)
            self.seq(n, st.copy())
            return (0, False, False)
        if t == "negated_command":
            inner = [c for c in n.children if c.is_named]
            return self.stmt(inner[0], st, redirs) if inner else (0, False, False)
        if t in sp.STATEMENT_LISTS:
            self.redirects(redirs, st)
            return (self.seq(n, st)[0], False, False)
        if t in LOOPS:
            self.redirects(redirs, st)
            return self.block(n, st)
        if t == "function_definition":
            name, body = n.child_by_field_name("name"), n.child_by_field_name("body")
            inner = st.copy()
            before = inner.cds
            if body is not None:
                self.stmt(body, inner)
            if inner.cds > before and name is not None:
                st.moving.add(sp.node_text(name))
            return (0, False, False)
        self.substs(n, st)  # 代入・宣言・テストなど: 中の置換だけを見る
        self.redirects(redirs, st)
        return (0, False, False)

    def block(self, n: sp.Node, st: _Place) -> tuple[int, bool, bool]:
        entry_cds, t = st.cds, n.type
        if t == "if_statement":
            for c in n.children:
                if c.type in ("elif_clause", "else_clause"):
                    if st.cds > entry_cds:
                        st.cwd = None
                    for g, bg in sp.statement_list(c):
                        self.stmt(g, st.copy() if bg else st)
                elif c.is_named and c.type != "comment":
                    nxt = c.next_sibling
                    self.stmt(c, st.copy() if nxt is not None and nxt.type == "&" else st)
        elif t == "case_statement":
            entry, fell = st.cwd, False
            for c in n.children:
                if c.type != "case_item":
                    if c.is_named and sp.field_of(n, c) == "value":
                        self.substs(c, st)
                    continue
                if not fell:
                    st.cwd = entry
                for g, bg in sp.statement_list(c):
                    if sp.field_of(c, g) != "value":
                        self.stmt(g, st.copy() if bg else st)
                fell = any(k.type in (";&", ";;&") for k in c.children)
        else:
            for c in n.children:
                if not c.is_named or c.type == "comment":
                    continue
                if c.type == "do_group":
                    self.seq(c, st)
                elif sp.field_of(n, c) in ("value", "initializer", "condition", "update") and c.type not in STATEMENTS:
                    self.substs(c, st)
                else:
                    self.stmt(c, st)
        if st.cds > entry_cds:
            st.cwd = None
        return (st.cds - entry_cds, False, False)

    def andor(self, n: sp.Node, st: _Place, redirs) -> tuple[int, bool, bool]:
        c, last, or1, cond = self._andor(n, st, redirs)
        if cond:  # `cmd && cd x` の後は、cd が走ったかを字面から決められない
            st.cwd = None
        return (c, last, or1)

    def _andor(self, n: sp.Node, st: _Place, redirs):
        kids = [c for c in n.children if c.type != "comment"]
        left, op, right = kids[0], kids[1].type, kids[2]
        entry = st.cwd
        if left.type == "list":
            c1, last1, or1, cond1 = self._andor(left, st, ())
        else:
            (c1, last1, or1), cond1 = self.stmt(left, st), False
        if op == "&&":
            c2, last2, _ = self.stmt(right, st, redirs)
            return (c1 + c2, last2, or1, cond1 or bool(c2 and not last1))
        fail = st.copy()
        if c1 == 1 and last1:
            fail.cwd = entry
        elif c1:
            fail.cwd = None
        if not or1 and _noncontinuing(right):  # `|| exit` を過ぎたなら左辺は成功している
            self.stmt(right, fail, redirs)
            return (0, False, False, False)
        st.cwd = fail.cwd
        c2, last2, _ = self.stmt(right, st, redirs)
        if c1 + c2:
            st.cwd = None
        return (c1 + c2, last2, True, cond1)

    def redirects(self, rs, st: _Place) -> None:
        for r in rs:
            if r.type == "heredoc_redirect":
                self.redirects([c for c in r.children if c.type == "file_redirect"], st)
                for s in sp.heredoc_substitutions(r):
                    self.seq(s, st.copy())
                continue
            if r.type != "file_redirect":
                self.substs(r, st)
                continue
            dest = sp.redirect_destination(r)
            if dest is not None:
                self.substs(dest, st)
            if dest is None or sp.redirect_operator(r) not in WRITE_OPS:
                continue
            if sp.destination_after_newline(r, dest):  # `>` の直後の改行は bash の構文エラー（ファイルは開かない）
                continue
            v = sp.unquote(dest)
            if sp.redirect_operator(r) == ">&" and (v == "-" or v.isdigit()):
                continue
            self.emit(v, st)

    def substs(self, n: sp.Node, st: _Place) -> None:
        """語の中のコマンド置換とプロセス置換を、部分シェルとして流す。"""
        for s in sp.substitutions(n):
            self.seq(s, st.copy())

    def command(self, n: sp.Node, st: _Place, redirs) -> tuple[int, bool, bool]:
        nodes, rs, assigns = sp.command_parts(n, redirs)
        for a in assigns:
            self.substs(a, st)
        words = []
        for c in nodes:
            self.substs(c, st)
            words.append(sp.unquote(c))
        i = sp.command_name_index(words)
        name = words[i] if i < len(words) else ""
        self.redirects(rs, st)  # リダイレクトは命令より先に（cd の前の位置で）開く
        for k, w in enumerate(words):
            if w == "tee":
                for a in words[k + 1:]:
                    if not a.startswith("-"):
                        self.emit(a, st)
            elif w == "sed":
                for f in _sed_inplace_files(words[k + 1:]):
                    self.emit(f, st)
            elif w in ("cp", "mv"):
                self.emit(_cp_mv_destination(words[k + 1:]), st)
        if st.base is not None and name in st.moving:
            st.cwd = None
        if name != "cd" or st.base is None:
            return (0, False, False)
        st.cds += 1
        dest = _cd_destination(words[i + 1:])
        if dest == "" or "$" in dest or dest.startswith("~"):
            st.cwd = None
        elif dest.startswith("/"):
            st.cwd = normalize_path(dest, "/")
        elif st.cwd is not None:
            st.cwd = normalize_path(st.cwd + "/" + dest, "/")
        return (1, True, False)


def _noncontinuing(n: sp.Node) -> bool:
    """`exit`・`return`・`break`・`continue`（か、それで終わる `{ …; }`）か。"""
    while n.type == "redirected_statement" and n.child_by_field_name("body") is not None:
        n = n.child_by_field_name("body")
    if n.type == "command":
        name = n.child_by_field_name("name")
        return name is not None and sp.unquote(name) in NONCONT
    if n.type == "compound_statement":
        for c, bg in sp.statement_list(n):
            if bg:
                continue
            while c.type == "list":
                c = c.children[0]
            while c.type == "redirected_statement" and c.child_by_field_name("body") is not None:
                c = c.child_by_field_name("body")
            if c.type == "command":
                name = c.child_by_field_name("name")
                if name is not None and sp.unquote(name) in NONCONT:
                    return True
    return False


def _cd_destination(args: list[str]) -> str:
    dest, eoo = "", False
    for a in args:
        if a == "--" and not eoo:
            eoo = True
        elif a == "-" or (a.startswith("-") and not eoo):
            continue
        else:
            dest = dest or a
    return dest


def _sed_inplace_files(args: list[str]) -> list[str]:
    inplace, seen, skip, files = False, False, False, []
    for a in args:
        if skip:
            skip = False
        elif a == "--in-place" or a.startswith("--in-place="):
            inplace = True
        elif a in ("-e", "-f", "--expression", "--file"):
            seen, skip = True, True
        elif a.startswith(("--expression=", "--file=", "-e", "-f")):
            seen = True
        elif a == "--":
            pass
        elif a.startswith("-"):
            inplace = inplace or bool(SED_INPLACE.fullmatch(a))
        elif not seen:
            seen = True
        else:
            files.append(a)
    return files if inplace else []


def _cp_mv_destination(args: list[str]) -> str:
    dest, tdir, take = "", "", False
    for a in args:
        if take:
            tdir, take = a, False
        elif a in ("-t", "--target-directory"):
            take = True
        elif a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif a.startswith("-t"):
            tdir = a[2:]
        elif not a.startswith("-"):
            dest = a
    return tdir or dest


def shell_targets(cmd: str, base: str = "") -> list[str]:
    """シェルのコマンドの書き込み先（出た順・重複あり）。`base` を渡すと絶対パスで返す。"""
    if not cmd:
        return []
    scan = _Scan(base)
    scan.seq(sp.parse_bash(cmd), scan.root)
    return scan.out


def patch_targets(patch: str) -> list[str]:
    """`apply_patch` の本文（`*** Update File: <パス>` ほかの行）の書き込み先。"""
    out = []
    for line in (patch or "").splitlines():
        for mark in PATCH_MARKS:
            if line.startswith(mark):
                target = line[len(mark):].strip()
                if target:
                    out.append(target)
                break
    return out
