"""hook の 3 つの判定を tree-sitter-bash の構文木で行う（T2 の試作。hook-trial.py と ht_hook.py が使う）。

- write_targets(cmd, base): 書き込み先（lib/worktree-write-target*.sh の wt_extract_write_target と同じ規則）
- sleep_deny / plan_command: 前景の sleep とプランを起こす副命令（ht_shsleep.py）

構文木の癖を 3 つ包みの中で直す: 並び（`&&` / `||`）とパイプの末尾のリダイレクトが並び全体に付く
（最後のコマンドへ付け直す）・`<>` が ERROR になる（演算子の字面をつないで読む）・`time` がコマンド名になる。
"""
from __future__ import annotations

import os
import re
import shlex

import tree_sitter as ts
import tree_sitter_bash as tsb

PARSER = ts.Parser(ts.Language(tsb.language()))
SEQ = {"program", "compound_statement", "do_group", "else_clause"}
NOT_TARGET = {"", ";", "&&", "/dev/null", "/dev/stdout", "/dev/stderr"}
NONCONT = {"exit", "return", "break", "continue"}
WRITE_OPS = {">", ">>", "&>", "&>>", ">|", ">&", "<>"}
SUBST = {"command_substitution", "process_substitution"}


def parse(cmd: str):
    return PARSER.parse(cmd.encode()).root_node


def text(n) -> str:
    return n.text.decode(errors="replace")


class Word(str):
    """引用を外した語。`tilde` は、引用の外の `~` で始まり bash がホームへ展開する形か。"""
    tilde = False


TILDE = re.compile(r"~[^/\"'\\$]*(/|$)")


def value(n) -> Word:
    """語の引用を外した字面。展開を含む語は `$` を含んだまま返す（書き込み先にしない）。"""
    t = text(n)
    try:
        parts = shlex.split(t, posix=True)
        w = Word("".join(parts) if parts else "")
    except ValueError:
        w = Word(t)
    w.tilde = bool(TILDE.match(t))
    return w


def field(parent, child) -> str | None:
    for i, c in enumerate(parent.children):
        if c.id == child.id:
            return parent.field_name_for_child(i)
    return None


def statements(n):
    """並びの子の文と、背景（直後が `&`）かを返す。"""
    kids = [c for c in n.children if c.is_named and c.type != "comment"]
    out = []
    for c in kids:
        nxt = c.next_sibling
        out.append((c, bool(nxt is not None and nxt.type == "&")))
    return out


# --- 書き込み先 --------------------------------------------------------------

class St:
    def __init__(self, base):
        self.base, self.cwd, self.cds, self.moving = base, base, 0, set()

    def copy(self):
        s = St(self.base)
        s.cwd, s.cds, s.moving = self.cwd, self.cds, set(self.moving)
        return s


def _abs(p: str) -> str:
    """wt_normalize_path と同じ: 字面で正規化し、実在する最も深い祖先だけを実パスへ直す。"""
    p = os.path.normpath(p)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    d, suffix = p, ""
    while d and d != "/":
        if os.path.isdir(d):
            r = os.path.realpath(d)
            return f"{r}/{suffix}" if suffix else r
        suffix = f"{os.path.basename(d)}/{suffix}" if suffix else os.path.basename(d)
        d = os.path.dirname(d)
    return p


class Targets:
    def __init__(self, base: str | None):
        self.out: list[str] = []
        self.root = St(base or None)

    def emit(self, v: str, st: St) -> None:
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
        self.out.append(_abs(v))

    # 文を流す。戻り値は (この並びで数えた cd の数, 最後が cd か, `||` を跨いだか)
    def seq(self, n, st: St, redirs=()):
        info = (0, False, False)
        for c, bg in statements(n):
            if c.type in ("{", "}"):
                continue
            if bg:
                self.stmt(c, st.copy())
                info = (info[0], False, False)
            else:
                info = self.stmt(c, st)
        return info

    def stmt(self, n, st: St, redirs=()):
        t = n.type
        if t == "redirected_statement":
            body = n.child_by_field_name("body")
            rs = [c for c in n.children if field(n, c) == "redirect"]
            if body is None:
                self.redirects(rs, st)
                return (0, False, False)
            if body.type in ("list", "pipeline", "command", "redirected_statement", "negated_command"):
                return self.stmt(body, st, list(redirs) + rs)  # 最後のコマンドへ付け直す
            self.redirects(list(redirs) + rs, st)  # 複合コマンドのリダイレクトは入る前の位置で開く
            return self.stmt(body, st)
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
        if t in SEQ:
            self.redirects(redirs, st)
            c = self.seq(n, st)
            return (c[0], False, False)
        if t in ("if_statement", "while_statement", "for_statement", "c_style_for_statement",
                 "case_statement"):
            self.redirects(redirs, st)
            return self.block(n, st)
        if t == "function_definition":
            name = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            inner = st.copy()
            before = inner.cds
            if body is not None:
                self.stmt(body, inner)
            if inner.cds > before and name is not None:
                st.moving.add(text(name))
            return (0, False, False)
        # 代入・宣言・テストなど: 中の置換だけを見る
        self.substs(n, st)
        self.redirects(redirs, st)
        return (0, False, False)

    def block(self, n, st: St):
        entry_cds = st.cds
        t = n.type
        if t == "if_statement":
            for c in n.children:
                if c.type in ("elif_clause", "else_clause"):
                    if st.cds > entry_cds:
                        st.cwd = None
                    for g, bg in statements(c):
                        self.stmt(g, st.copy() if bg else st)
                elif c.is_named and c.type != "comment":
                    nxt = c.next_sibling
                    self.stmt(c, st.copy() if nxt is not None and nxt.type == "&" else st)
        elif t == "case_statement":
            entry = st.cwd
            fell = False
            for c in n.children:
                if c.type != "case_item":
                    if c.is_named and field(n, c) == "value":
                        self.substs(c, st)
                    continue
                if not fell:
                    st.cwd = entry
                for g, bg in statements(c):
                    if field(c, g) == "value":
                        continue
                    self.stmt(g, st.copy() if bg else st)
                fell = any(k.type in (";&", ";;&") for k in c.children)
        else:
            for c in n.children:
                if c.is_named and c.type != "comment":
                    if c.type == "do_group":
                        self.seq(c, st)
                    elif field(n, c) in ("value", "initializer", "condition", "update") and c.type not in (
                            "command", "list", "pipeline", "redirected_statement", "compound_statement",
                            "subshell", "negated_command"):
                        self.substs(c, st)
                    else:
                        self.stmt(c, st)
        if st.cds > entry_cds:
            st.cwd = None
        return (st.cds - entry_cds, False, False)

    def andor(self, n, st: St, redirs):
        c, last, or1, cond = self._andor(n, st, redirs)
        if cond:  # `cmd && cd x` の後は、cd が走ったかを字面から決められない（並びの終わりで捨てる）
            st.cwd = None
        return (c, last, or1)

    def _andor(self, n, st: St, redirs):
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
        if not or1 and self.noncont(right):  # `|| exit` を過ぎたなら左辺は成功している
            self.stmt(right, fail, redirs)
            return (0, False, False, False)
        st.cwd = fail.cwd
        c2, last2, _ = self.stmt(right, st, redirs)
        if c1 + c2:
            st.cwd = None
        return (c1 + c2, last2, True, cond1)

    def noncont(self, n) -> bool:
        while n.type == "redirected_statement":
            n = n.child_by_field_name("body") or n
            if n.type == "redirected_statement":
                break
        if n.type == "command":
            name = n.child_by_field_name("name")
            return name is not None and value(name) in NONCONT
        if n.type == "compound_statement":
            for c, bg in statements(n):
                if bg:
                    continue
                while c.type == "list":
                    c = c.children[0]
                while c.type == "redirected_statement" and c.child_by_field_name("body") is not None:
                    c = c.child_by_field_name("body")
                if c.type == "command":
                    name = c.child_by_field_name("name")
                    if name is not None and value(name) in NONCONT:
                        return True
        return False

    def redirects(self, rs, st: St) -> None:
        for r in rs:
            if r.type == "heredoc_redirect":
                start = next((c for c in r.children if c.type == "heredoc_start"), None)
                quoted = start is not None and any(q in text(start) for q in "'\"\\")
                for c in r.children:
                    if c.type == "file_redirect":
                        self.redirects([c], st)
                    elif c.type == "heredoc_body" and not quoted:
                        if c.child_count:
                            self.substs(c, st)
                        else:
                            for m in BACKTICK.finditer(text(c)):
                                self.seq(parse(m.group(1)), st.copy())
                continue
            if r.type != "file_redirect":
                self.substs(r, st)
                continue
            dests = [c for c in r.children if field(r, c) == "destination"]
            dest = dests[0] if dests else None
            op = "".join(text(c) for c in r.children
                         if field(r, c) not in ("destination", "descriptor")).replace(" ", "")
            if dest is not None:
                self.substs(dest, st)
            if op not in WRITE_OPS or dest is None:
                continue
            gap = r.text[:dest.start_byte - r.start_byte]
            if b"\n" in gap:  # `>` の直後の改行は bash の構文エラー（ファイルは開かない）
                continue
            v = value(dest)
            if op == ">&" and (v == "-" or v.isdigit()):
                continue
            self.emit(v, st)

    def substs(self, n, st: St) -> None:
        """語の中のコマンド置換とプロセス置換を部分シェルとして流す。"""
        for c in n.children:
            if c.type in SUBST:
                if n.type == "heredoc_body" and text(c).startswith("$(("):
                    continue  # 本文の `$((...))` は算術展開（構文木はコマンド置換と部分シェルに読む）
                self.seq(c, st.copy())
            elif c.type == "heredoc_content" and n.type == "heredoc_body":
                for m in BACKTICK.finditer(text(c)):  # 本文のバッククォートは構文木に現れない
                    self.seq(parse(m.group(1)), st.copy())
            elif c.type == "heredoc_body" or c.child_count:
                self.substs(c, st)

    def command(self, n, st: St, redirs):
        nodes, rs = [], list(redirs)
        for c in n.children:
            f = field(n, c)
            if c.type in ("file_redirect", "heredoc_redirect", "herestring_redirect"):
                rs.insert(len(rs) - len(redirs), c)
            elif c.type == "variable_assignment":
                self.substs(c, st)
            elif f in ("name", "argument"):
                nodes.append(c)
        nodes += extra_args(rs)
        nodes.sort(key=lambda c: c.start_byte)
        words = []
        for c in nodes:
            self.substs(c, st)
            words.append(value(c))
        i = 0
        while i < len(words) and words[i] in ("command", "builtin", "time"):  # `time` はコマンド名として読まれる
            i += 1
            while i < len(words) and words[i] in ("-p", "--"):
                i += 1
        name = words[i] if i < len(words) else ""
        is_cd = name == "cd" and st.base is not None
        self.redirects(rs, st)  # リダイレクトは命令より先に（cd の前の位置で）開く
        for k, w in enumerate(words):
            if w == "tee":
                for a in words[k + 1:]:
                    if not a.startswith("-"):
                        self.emit(a, st)
            elif w == "sed":
                self.sed(words[k + 1:], st)
            elif w in ("cp", "mv"):
                self.cp_mv(words[k + 1:], st)
        if st.base is not None and name in st.moving:
            st.cwd = None
        if not is_cd:
            return (0, False, False)
        dest, eoo = "", False
        for a in words[i + 1:]:
            if a == "--" and not eoo:
                eoo = True
                continue
            if a == "-":
                continue
            if a.startswith("-") and not eoo:
                continue
            dest = dest or a
        st.cds += 1
        if dest == "" or "$" in dest or dest.startswith("~"):
            st.cwd = None
        elif dest.startswith("/"):
            st.cwd = _abs(dest)
        elif st.cwd is not None:
            st.cwd = _abs(st.cwd + "/" + dest)
        return (1, True, False)

    def sed(self, args, st: St) -> None:
        inplace, seen, skip, files = False, False, False, []
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("--in-place",) or a.startswith("--in-place="):
                inplace = True
            elif a in ("-e", "-f", "--expression", "--file"):
                seen, skip = True, True
            elif a.startswith(("--expression=", "--file=", "-e", "-f")):
                seen = True
            elif a == "--":
                pass
            elif a.startswith("-"):
                if re.fullmatch(r"-[a-zA-Z]*i([a-zA-Z]*|\..*)", a):
                    inplace = True
            elif not seen:
                seen = True
            else:
                files.append(a)
        if inplace:
            for f in files:
                self.emit(f, st)

    def cp_mv(self, args, st: St) -> None:
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
            elif a.startswith("-"):
                continue
            else:
                dest = a
        self.emit(tdir or dest, st)


BACKTICK = re.compile(r"`([^`]*)`")


def extra_args(rs):
    """リダイレクトの被演算子の後ろの語。構文木は destination / argument として並べるが、bash ではコマンドの引数である。"""
    out = []
    for r in rs:
        if r.type == "file_redirect":
            out += [c for c in r.children if field(r, c) == "destination"][1:]
        elif r.type in ("heredoc_redirect", "herestring_redirect"):
            out += [c for c in r.children if field(r, c) == "argument"]
            for c in r.children:
                if c.type == "file_redirect":
                    out += extra_args([c])
    return out


def write_targets(cmd: str, base: str = "") -> list[str]:
    if not cmd:
        return []
    t = Targets(base)
    t.seq(parse(cmd), t.root)
    return t.out


def has_error(cmd: str) -> bool:
    return parse(cmd).has_error
