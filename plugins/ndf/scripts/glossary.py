#!/usr/bin/env python3
"""glossary.py: プロジェクトの用語集を扱う 6 つの副命令。

    python3 glossary.py gate --mode M [--root DIR]
    python3 glossary.py init [--source P] [--document P] [--root DIR]
    python3 glossary.py candidates [--limit N] [--root DIR]
    python3 glossary.py render [--check] [--root DIR]
    python3 glossary.py check [--diff BASE | --file P...] [--rules all|structure] [--root DIR]
    python3 glossary.py diff --base REF [--head REF] [--root DIR]

宣言は `<root>/.ndf/glossary.json`。宣言と用語集の形は
`skills/requirements-design/references/glossary-format.md` にある。語はすべて用語集から読み、
このスクリプトは特定のプロジェクトの語を持たない。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "glossary"`）。終了コードは副命令ごとに
0 = 通す・当たりなし / 1 = 止める・当たりあり / 2 = 読めない・パスが境界を越える。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md", "mdtable", "textparse", "pathmatch")
import md  # noqa: E402
import mdtable  # noqa: E402
import pathmatch  # noqa: E402
import textparse  # noqa: E402
from step_result import EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, main_with, result  # noqa: E402
import jsonio  # noqa: E402
import proc  # noqa: E402

TOOL = "glossary"
DECLARATION = ".ndf/glossary.json"
DEFAULT_SOURCE = "docs/glossary/glossary.json"
DEFAULT_DOCUMENT = "docs/glossary.md"
DEFAULT_PATHS = ["issues/*-requirements.md", "issues/*-design.md", "issues/*-design-decisions.md"]
DEFAULT_TERM_SECTIONS = ["用語"]
DESIGN_MODES = ("standard", "legacy-refactor")
FORMATS = ("json",)
STRUCTURE_RULES = ("schema", "duplicate", "stale_document")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
BOLD = re.compile(r"\*\*([^*\n]{1,12}?)\*\*")
TYPE_NAME = re.compile(r"\b(?:class|interface|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)|\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
ASCII_WORD = re.compile(r"^[A-Za-z0-9_\-]+$")
CODE_SHAPE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
CODE_SUFFIXES = (".py", ".sh", ".js", ".ts")
MAX_CODE_BYTES = 1_000_000


def unreadable(msg):
    return StepError(msg, EXIT_UNREADABLE)


# --- パスの境界 -----------------------------------------------------------------

def inside(root: Path, rel: str, label: str) -> Path:
    """リポジトリの根の内側のパスだけを受ける。違反は何も書かずに 2 で止める。"""
    if not isinstance(rel, str) or not rel:
        raise unreadable(f"{label} が空")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise unreadable(f"{label} は根からの相対パスで書く（絶対パス・.. は受けない）: {rel}")
    full = (root / p).resolve()
    base = root.resolve()
    if full != base and base not in full.parents:
        raise unreadable(f"{label} がリポジトリの外を指す: {rel}")
    return root / p


# --- 宣言と用語集の読み込み ------------------------------------------------------

class Declaration:
    def __init__(self, root: Path, raw: dict):
        if not isinstance(raw, dict):
            raise unreadable(f"{DECLARATION} はオブジェクトで書く")
        if raw.get("version") != 1:
            raise unreadable(f"{DECLARATION} の version は 1: {raw.get('version')!r}")
        if raw.get("format") not in FORMATS:
            raise unreadable(f"{DECLARATION} の format は {' / '.join(FORMATS)}: {raw.get('format')!r}")
        self.root = root
        self.source = raw.get("source")
        self.document = raw.get("document")
        self.source_path = inside(root, self.source, "source")
        self.document_path = inside(root, self.document, "document")
        check = raw.get("check") or {}
        if not isinstance(check, dict):
            raise unreadable(f"{DECLARATION} の check はオブジェクトで書く")
        paths = check.get("paths", [])
        sections = check.get("term_sections", DEFAULT_TERM_SECTIONS)
        source_paths = check.get("source_paths", [])
        for name, value in (("check.paths", paths), ("check.term_sections", sections),
                            ("check.source_paths", source_paths)):
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise unreadable(f"{DECLARATION} の {name} は文字列の配列で書く")
        for pat in paths:
            inside(root, pat, "check.paths")
        self.paths = paths
        self.term_sections = sections
        self.source_paths = source_paths  # 語の正本（terms[].source）に認めるパス。空なら見ない


def _read_declared(path: Path, label: str):
    try:
        return jsonio.read(path)
    except jsonio.JsonReadError as e:
        raise unreadable(f"{label} を読めない: {e}")


def load_declaration(root: Path) -> Declaration | None:
    p = root / DECLARATION
    if not p.exists():
        return None
    return Declaration(root, _read_declared(p, DECLARATION))


def parse_glossary(raw, label: str) -> dict:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise unreadable(f"{label} は version 1 のオブジェクトで書く")
    if not isinstance(raw.get("contexts", []), list) or not isinstance(raw.get("terms", []), list):
        raise unreadable(f"{label} の contexts / terms は配列で書く")
    return raw


def load_glossary(decl: Declaration) -> dict:
    return parse_glossary(_read_declared(decl.source_path, "用語集"), "用語集")


def contexts_of(g: dict) -> list[dict]:
    return [c for c in g.get("contexts", []) if isinstance(c, dict)]


def terms_of(g: dict) -> list[dict]:
    return [t for t in g.get("terms", []) if isinstance(t, dict)]


def deprecated_of(t: dict) -> list[str]:
    d = t.get("deprecated") or []
    return [w for w in d if isinstance(w, str)] if isinstance(d, list) else []


def code_of(t: dict) -> str | None:
    c = t.get("code")
    return c if isinstance(c, str) and CODE_SHAPE.fullmatch(c) else None


def deprecated_code_of(t: dict) -> list[str]:
    d = t.get("deprecated_code") or []
    return [w for w in d if isinstance(w, str) and CODE_SHAPE.fullmatch(w)] if isinstance(d, list) else []


def spellings(code: str) -> tuple[str, str, str, str]:
    """識別子の基本形（snake_case）から 4 つの書き方を導く。基本形（JSON のキー・変数・関数）・PascalCase（クラス）・
    大文字（定数）・kebab-case（CLI の引数・ファイル名）の順。書き方の変換はここだけで行う。"""
    parts = code.split("_")
    return code, "".join(w.capitalize() for w in parts), code.upper(), "-".join(parts)


def code_forms(code: str) -> list[str]:
    """spellings の重なる形を 1 つにした一覧（`plan` なら plan / Plan / PLAN）。"""
    return list(dict.fromkeys(spellings(code)))


def live_words(g: dict) -> set[str]:
    return {t["term"] for t in terms_of(g) if isinstance(t.get("term"), str) and t["term"]}


# --- 文書の生成 -------------------------------------------------------------------

def _plain(value) -> str:
    """セルと地の文に書く字面（改行は空白、空は「—」）。`|` のエスケープは呼ぶ側（mdtable）が持つ。"""
    return str(value or "").replace("\r\n", "\n").replace("\n", " ").strip() or "—"


def cell(value) -> str:
    return mdtable.cell_text(_plain(value))


def render_text(g: dict, source: str) -> str:
    out = ["# 用語集", "", f"この文書は `{source}` から `glossary.py render` で作る。手で直さない。", ""]
    terms = terms_of(g)
    for c in contexts_of(g):
        out += [f"## {cell(c.get('name'))}（`{c.get('id')}`）", ""]
        if c.get("meaning"):
            out += [cell(c["meaning"]), ""]
        rows = [t for t in terms if t.get("context") == c.get("id")]
        if not rows:
            out += ["語はまだ無い。", ""]
            continue
        body = []
        for t in rows:
            dep = "、".join(deprecated_of(t))
            code = f"`{code_of(t)}`" if code_of(t) else ""
            dep_code = "、".join(f"`{w}`" for w in deprecated_code_of(t))
            src = f"`{t['source']}`" if t.get("source") else ""
            body.append([_plain(v) for v in (t.get("term"), code, t.get("meaning"), dep, dep_code, src)])
        out += [mdtable.table_markdown(["語", "識別子", "意味", "廃止した語", "廃止した識別子", "正本"], body,
                                       align=["left"] * 6), ""]
    return "\n".join(out).rstrip("\n") + "\n"


# --- 用語集の形 --------------------------------------------------------------------

def pending_problem(root: Path, rel: str) -> str | None:
    """pending_source は根の内側の正規の相対パスで設計文書を指す。spec-finalize はこの形でだけ照合する。"""
    try:
        inside(root, rel, "pending_source")
    except StepError:
        return "は根の内側の相対パスで書く（絶対パス・.. は受けない）"
    if Path(rel).as_posix() != rel:
        return f"は正規の形で書く（{Path(rel).as_posix()}）"
    return None if (root / rel).is_file() else "が指す設計文書が無い"


def structure_findings(g: dict, decl: Declaration) -> list[dict]:
    items = []

    def hit(rule, term, detail):
        items.append({"rule": rule, "path": decl.source, "line": 0, "term": term, "detail": detail})

    ids = []
    for i, c in enumerate(g.get("contexts", [])):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or \
                not isinstance(c.get("name"), str) or not c["name"]:
            hit("schema", "", f"contexts[{i}] に id と name が要る")
            continue
        if c["id"] in ids:
            hit("schema", c["id"], f"contexts[{i}] の id が重なる")
        ids.append(c["id"])
    seen: dict[tuple[str, str], int] = {}
    code_seen: dict[tuple[str, str], int] = {}
    live_by_ctx: dict[str, set[str]] = {}
    codes_by_ctx: dict[str, set[str]] = {}
    for t in terms_of(g):
        if isinstance(t.get("term"), str) and isinstance(t.get("context"), str):
            live_by_ctx.setdefault(t["context"], set()).add(t["term"])
        if isinstance(t.get("context"), str) and code_of(t):
            codes_by_ctx.setdefault(t["context"], set()).add(code_of(t))
    for i, t in enumerate(g.get("terms", [])):
        if not isinstance(t, dict):
            hit("schema", "", f"terms[{i}] はオブジェクトで書く")
            continue
        missing = [k for k in ("term", "context", "meaning") if not isinstance(t.get(k), str) or not t[k]]
        if missing:
            hit("schema", str(t.get("term") or ""), f"terms[{i}] に {' / '.join(missing)} が無い")
            continue
        if "deprecated" in t and not isinstance(t["deprecated"], list):
            hit("schema", t["term"], f"terms[{i}] の deprecated は文字列の配列で書く")
        if "source" in t and not isinstance(t["source"], str):
            hit("schema", t["term"], f"terms[{i}] の source は文字列で書く")
        elif "pending_source" in t and not isinstance(t["pending_source"], str):
            hit("schema", t["term"], f"terms[{i}] の pending_source は文字列で書く")
        elif decl.source_paths and t.get("source") and "://" not in t["source"] and \
                not declared_path_matches(t["source"], decl.source_paths):
            hit("unconfirmed_source", t["term"],
                f"terms[{i}] の source が確定仕様を指さない: {t['source']}（check.source_paths に当たるパスへ移す。"
                "確定前は source を空にし、plan-to-spec が確定仕様を書いたときに入れる）")
        if isinstance(t.get("pending_source"), str) and (problem := pending_problem(decl.root, t["pending_source"])):
            hit("schema", t["term"], f"terms[{i}] の pending_source {problem}: {t['pending_source']}")
        if t["context"] not in ids:
            hit("schema", t["term"], f"terms[{i}] の context が宣言されていない: {t['context']}")
        for w in deprecated_of(t):
            if len(w) < 2:
                hit("schema", w, f"terms[{i}] の廃止した語が 1 文字で照合できない")
            elif w in live_by_ctx.get(t["context"], set()):
                hit("schema", w, f"terms[{i}] の廃止した語が同じコンテキストの生きた語と重なる")
        if "code" in t and code_of(t) is None:
            hit("schema", str(t["code"]), f"terms[{i}] の code は英小文字の snake_case で書く（例: approval_gate）")
        if "deprecated_code" in t and (not isinstance(t["deprecated_code"], list)
                                       or len(deprecated_code_of(t)) != len(t["deprecated_code"])):
            hit("schema", t["term"], f"terms[{i}] の deprecated_code は英小文字の snake_case の文字列の配列で書く")
        for w in deprecated_code_of(t):
            if w in codes_by_ctx.get(t["context"], set()):
                hit("schema", w, f"terms[{i}] の廃止した識別子が同じコンテキストの生きた識別子と重なる")
        if code_of(t):
            ckey = (t["context"], code_of(t))
            if ckey in code_seen:
                hit("schema", code_of(t),
                    f"コンテキスト {t['context']} で terms[{code_seen[ckey]}] と terms[{i}] が同じ識別子")
            else:
                code_seen[ckey] = i
        key = (t["context"], t["term"])
        if key in seen:
            hit("duplicate", t["term"], f"コンテキスト {t['context']} で terms[{seen[key]}] と terms[{i}] が同じ語")
        else:
            seen[key] = i
    return items


def stale_findings(g: dict, decl: Declaration) -> list[dict]:
    want = render_text(g, decl.source)
    try:
        have = decl.document_path.read_text(encoding="utf-8")
    except OSError:
        have = None
    if have == want:
        return []
    return [{"rule": "stale_document", "path": decl.document, "line": 0, "term": "",
             "detail": f"用語集から作り直していない。`glossary.py render` を打つ（{decl.source}）"}]


# --- 文書の中の語 --------------------------------------------------------------------

def word_pattern(words) -> re.Pattern | None:
    words = sorted({w for w in words if w}, key=len, reverse=True)
    if not words:
        return None
    parts = [rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])" if ASCII_WORD.match(w) else re.escape(w)
             for w in words]
    return re.compile("|".join(parts))


def mask_inline_code(line: str) -> str:
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


def clean_term(cell_text: str) -> str:
    s = cell_text.strip()
    for mark in ("**", "__", "`"):
        if s.startswith(mark) and s.endswith(mark) and len(s) > 2 * len(mark):
            s = s[len(mark):-len(mark)].strip()
    return s


def term_rows(text: str, term_sections: list[str]) -> dict[int, str]:
    """用語の節（`term_sections` の見出しの下。同じか浅い見出しまで）の表の本体の行の 1 列目の語を、行番号（1 始まり）で返す。"""
    spans = [(s.start, s.end) for s in md.md_sections(text) if s.heading.title in term_sections]
    out: dict[int, str] = {}
    for tb in md.tables(text):
        if not any(a <= tb.start < b for a, b in spans):
            continue
        for k, row in enumerate(tb.rows):
            term = clean_term(row[0]) if row else ""
            if term:
                out[tb.start + 3 + k] = term
    return out


def scan(text: str, term_sections: list[str]):
    """行ごとに (行番号, 照合する本文, 用語の表の 1 列目の語か None) を返す。コードブロックは飛ばす。"""
    lines = text.splitlines()
    fenced = md.fenced_lines(text)
    terms = term_rows(text, term_sections)
    for n, line in enumerate(lines, 1):
        if fenced[n - 1]:
            continue
        yield n, mask_inline_code(line), terms.get(n)


def text_findings(rel: str, text: str, wanted: set[int] | None, g: dict, decl: Declaration,
                  dep_re, live_re) -> list[dict]:
    live = live_words(g)
    items = []
    for n, body, term in scan(text, decl.term_sections):
        if wanted is not None and n not in wanted:
            continue
        if term and term not in live:
            items.append({"rule": "unregistered", "path": rel, "line": n, "term": term,
                          "detail": "用語集に無い語。同じ変更で用語集へ足して render する"})
        if dep_re is None:
            continue
        spans = [m.span() for m in live_re.finditer(body)] if live_re else []
        for m in dep_re.finditer(body):
            s, e = m.span()
            if any(a <= s and e <= b for a, b in spans):
                continue
            items.append({"rule": "deprecated", "path": rel, "line": n, "term": m.group(0),
                          "detail": f"廃止した語。{replacement(g, m.group(0))} と書く"})
    return items


def mask_comments(lines: list[str], suffix: str) -> list[str]:
    """コードの行のコメントを空白に置き換える。`#` は .py / .sh（.sh は語の頭だけ）、`//` と `/* */` は .js / .ts。
    文字列の内側の記号はコメントとみなさない（`"--cart"` の識別子は残す）。行をまたぐ文字列（.py の三連引用符、
    .js / .ts のバッククォート、.sh の引用）は次の行へ持ち越す。.sh は引用の外の `\\` で次の 1 文字を飛ばし、
    `'` の内側の `\\` はエスケープとみなさない。.py / .js / .ts の通常の引用も、行末の `\\` で改行を
    エスケープしたときは次の行へ持ち越す。"""
    hash_style, sh = suffix in (".py", ".sh"), suffix == ".sh"
    opens = {".py": ('"""', "'''", '"', "'"), ".sh": ('"', "'")}.get(suffix, ("`", '"', "'"))
    multiline = {'"', "'"} if sh else {'"""', "'''", "`"}
    out, block, quote = [], False, None
    for line in lines:
        chars, i, escaped_eol = list(line), 0, False
        while i < len(line):
            if block:
                end = line.find("*/", i)
                stop = len(line) if end < 0 else end + 2
                chars[i:stop] = " " * (stop - i)
                block, i = end < 0, stop
                continue
            c = line[i]
            if quote:
                if c == "\\" and not (sh and quote == "'"):
                    escaped_eol, i = i + 1 == len(line), i + 2
                elif line.startswith(quote, i):
                    i, quote = i + len(quote), None
                else:
                    i += 1
                continue
            if sh and c == "\\":
                i += 2
                continue
            opened = next((q for q in opens if line.startswith(q, i)), None)
            if opened:
                i, quote = i + len(opened), opened
                continue
            if (c == "#" and hash_style and (suffix == ".py" or i == 0 or line[i - 1] in " \t;|&(")) \
                    or (not hash_style and line.startswith("//", i)):
                chars[i:] = " " * (len(line) - i)
                break
            if not hash_style and line.startswith("/*", i):
                chars[i:i + 2], block, i = "  ", True, i + 2
                continue
            i += 1
        if quote not in multiline and not escaped_eol:
            quote = None
        out.append("".join(chars))
    return out


def code_findings(rel: str, text: str, wanted: set[int] | None, g: dict, dep_re, live_re) -> list[dict]:
    """コードの行から、廃止した識別子とその書き方を変えた形を拾う。生きた識別子の内側とコメントの中の出現は当てない。"""
    items = []
    for n, line in enumerate(mask_comments(text.splitlines(), Path(rel).suffix), 1):
        if wanted is not None and n not in wanted:
            continue
        spans = [m.span() for m in live_re.finditer(line)] if live_re else []
        for m in dep_re.finditer(line):
            s, e = m.span()
            if any(a <= s and e <= b for a, b in spans):
                continue
            items.append({"rule": "deprecated_code", "path": rel, "line": n, "term": m.group(0),
                          "detail": f"廃止した識別子。{code_replacement(g, m.group(0))} と書く"})
    return items


def code_replacement(g: dict, form: str) -> str:
    """廃止した識別子の出た書き方に合わせて、生きた識別子を同じ書き方で返す（PascalCase で出たら PascalCase）。"""
    names = []
    for t in terms_of(g):
        if not code_of(t) or not isinstance(t.get("term"), str):
            continue
        for w in deprecated_code_of(t):
            hits = [live for dep, live in zip(spellings(w), spellings(code_of(t))) if dep == form]
            if hits:
                names.append(f"{hits[0]}（{t['term']}）")
                break
    return " / ".join(dict.fromkeys(names)) or "用語集の識別子"


def replacement(g: dict, word: str) -> str:
    names = [t["term"] for t in terms_of(g) if word in deprecated_of(t) and isinstance(t.get("term"), str)]
    return " / ".join(names) or "用語集の語"


# --- git ------------------------------------------------------------------------------

def git_checked(root: Path, *args, check=True) -> subprocess.CompletedProcess:
    p = proc.git(root, *args, check=False)
    if check and p.returncode != 0:
        raise unreadable(f"git {' '.join(args)} が失敗: {p.stderr.strip()[:300]}")
    return p


def added_lines(root: Path, base: str, pathspecs: tuple[str, ...] = ()) -> dict[str, set[int]]:
    """base と HEAD の分岐点（merge-base）から追加した行の番号を、ファイルごとに返す（未コミット分も含む）。
    追跡していないファイルは全行とする。`pathspecs` を渡すとそのパスに絞る（doc-lint.py は `*.md`）。"""
    mb = git_checked(root, "merge-base", base, "HEAD").stdout.strip()
    diff = git_checked(root, "diff", "--unified=0", "--no-color", "--diff-filter=AM", mb, "--", *pathspecs).stdout
    try:
        out = textparse.diff_added_lines(diff)
    except textparse.DiffParseError as e:
        raise unreadable(f"git diff の出力を読めない: {e}")
    for rel in git_checked(root, "ls-files", "--others", "--exclude-standard", "--", *pathspecs).stdout.splitlines():
        rel = rel.strip()
        p = root / rel
        if rel and p.is_file():
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            out[rel] = set(range(1, n + 1))
    return out


def declared_path_matches(rel: str, patterns: list[str]) -> bool:
    """宣言のパスのパターン（git の wildmatch。`*` は `/` をまたがない）に当たるか。"""
    return pathmatch.path_matches(rel, patterns)


# --- 副命令 ----------------------------------------------------------------------------

def script_cmd(root: Path, sub: str) -> str:
    return f"python3 {Path(__file__).resolve()} {sub} --root {root}"


def cmd_gate(a):
    root = Path(a.root)
    decl = load_declaration(root)
    if a.mode not in DESIGN_MODES:
        emit(result(TOOL, "ok", f"{a.mode} は設計の工程の入口で用語集を見ない"))
    missing = []
    if decl is None:
        missing.append(DECLARATION)
    else:
        if decl.source_path.exists():
            load_glossary(decl)
        else:
            missing.append(decl.source)
        if not decl.document_path.exists():
            missing.append(decl.document)
    if not missing:
        emit(result(TOOL, "ok", "宣言と用語集が揃っている"))
    steps = [
        {"text": f"用語集を起こす: {script_cmd(root, 'init')}"},
        {"text": f"語の候補を集める: {script_cmd(root, 'candidates')}（0 件なら要求から語を起こす）"},
        {"text": "手順: requirements-design の手順 0（用語集を用意する）"},
    ]
    emit(result(TOOL, "stopped", f"{' / '.join(missing)} が無い。設計の工程へ入る前に用語集を作る。"
                                 + " → ".join(s["text"] for s in steps), steps), EXIT_VIOLATION)


def cmd_init(a):
    root = Path(a.root)
    decl_path = root / DECLARATION
    if decl_path.exists():
        decl = load_declaration(root)
        source, document = decl.source, decl.document
        raw_decl = None
    else:
        source, document = a.source or DEFAULT_SOURCE, a.document or DEFAULT_DOCUMENT
        raw_decl = {"version": 1, "format": "json", "source": source, "document": document,
                    "check": {"paths": list(DEFAULT_PATHS), "term_sections": list(DEFAULT_TERM_SECTIONS)}}
        decl = Declaration(root, raw_decl)
    glossary = load_glossary(decl) if decl.source_path.exists() else {"version": 1, "contexts": [], "terms": []}
    writes = []
    if raw_decl is not None:
        writes.append((decl_path, json.dumps(raw_decl, ensure_ascii=False, indent=2) + "\n", DECLARATION))
    if not decl.source_path.exists():
        writes.append((decl.source_path, json.dumps(glossary, ensure_ascii=False, indent=2) + "\n", source))
    if not decl.document_path.exists():
        writes.append((decl.document_path, render_text(glossary, source), document))
    items = []
    for path, text, rel in writes:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            raise unreadable(f"{rel} を書けない: {e}")
        items.append({"name": rel, "result": "created"})
    emit(result(TOOL, "ok", f"{len(items)} 個のファイルを作った" if items else "宣言・用語集・文書は揃っている",
                items, {"created": len(items)}))


def cmd_candidates(a):
    root = Path(a.root)
    decl = load_declaration(root)
    sections = decl.term_sections if decl else DEFAULT_TERM_SECTIONS
    known = live_words(load_glossary(decl)) if decl and decl.source_path.exists() else set()
    skip = {decl.document, decl.source} if decl else set()
    found: dict[tuple[str, str], dict] = {}

    def add(term, kind, where):
        if not term or term in known:
            return
        it = found.setdefault((term, kind), {"term": term, "count": 0, "kind": kind, "first": where})
        it["count"] += 1

    files = git_checked(root, "ls-files").stdout.splitlines()
    for rel in files:
        p = root / rel
        if rel in skip or not p.is_file() or p.is_symlink():
            continue
        try:
            if p.stat().st_size > MAX_CODE_BYTES:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        if rel.endswith(".md"):
            for n, body, term in scan(text, sections):
                if term:
                    add(term, "table", f"{rel}:{n}")
                for m in BOLD.finditer(body):
                    word = m.group(1).strip()
                    if not word.endswith((":", "：")):  # 「対象:」のような見出しの札は語ではない
                        add(word, "bold", f"{rel}:{n}")
        else:
            for n, line in enumerate(lines, 1):
                for m in TYPE_NAME.finditer(line):
                    add(m.group(1) or m.group(2), "type", f"{rel}:{n}")
    items = [it for it in found.values() if it["kind"] != "bold" or it["count"] >= 3]
    items.sort(key=lambda it: (-it["count"], it["kind"], it["term"]))
    items = items[:a.limit]
    summary = f"語の候補 {len(items)} 件" if items else "語の候補は 0 件（スクラッチ）。要求から語を起こす"
    emit(result(TOOL, "ok", summary, items, {"candidates": len(items)}))


def require_declaration(root: Path) -> Declaration:
    decl = load_declaration(root)
    if decl is None:
        raise unreadable(f"{DECLARATION} が無い。先に init を打つ")
    return decl


def cmd_render(a):
    decl = require_declaration(Path(a.root))
    g = load_glossary(decl)
    text = render_text(g, decl.source)
    if a.check:
        found = stale_findings(g, decl)
        if found:
            emit(result(TOOL, "stopped", found[0]["detail"], found), EXIT_VIOLATION)
        emit(result(TOOL, "ok", f"{decl.document} は用語集と一致する"))
    try:
        decl.document_path.parent.mkdir(parents=True, exist_ok=True)
        decl.document_path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise unreadable(f"{decl.document} を書けない: {e}")
    emit(result(TOOL, "ok", f"{decl.document} を書いた", [{"name": decl.document, "result": "written"}]))


def cmd_check(a):
    root = Path(a.root)
    decl = load_declaration(root)
    if decl is None:
        emit(result(TOOL, "ok", f"宣言が無い（{DECLARATION}）。用語チェックをしない"))
    g = load_glossary(decl)
    items = structure_findings(g, decl) + stale_findings(g, decl)
    if a.rules == "all" and (a.diff or a.file):
        dep_words = {w for t in terms_of(g) for w in deprecated_of(t) if len(w) >= 2} - live_words(g)
        dep_re, live_re = word_pattern(dep_words), word_pattern(live_words(g))
        live_forms = {f for t in terms_of(g) if code_of(t) for f in code_forms(code_of(t))}
        dep_forms = {f for t in terms_of(g) for w in deprecated_code_of(t) for f in code_forms(w)} - live_forms
        dep_code_re, live_code_re = word_pattern(dep_forms), word_pattern(live_forms)
        targets: list[tuple[str, str, set[int] | None]] = []
        if a.file:
            for f in a.file:
                p = Path(f)
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError as e:
                    raise unreadable(f"{f} を読めない: {e}")
                targets.append((f, text, None))
        else:
            for rel, lines in sorted(added_lines(root, a.diff).items()):
                if rel == decl.document or not (rel.endswith(CODE_SUFFIXES) or declared_path_matches(rel, decl.paths)):
                    continue
                p = root / rel
                if p.is_file():
                    targets.append((rel, p.read_text(encoding="utf-8", errors="replace"), lines))
        for rel, text, wanted in targets:
            if a.file and (root / decl.document).resolve() == Path(rel).resolve():
                continue
            if rel.endswith(CODE_SUFFIXES):
                if dep_code_re is not None:
                    items += code_findings(rel, text, wanted, g, dep_code_re, live_code_re)
                continue
            items += text_findings(rel, text, wanted, g, decl, dep_re, live_re)
    for it in items:
        print(f"ERROR: {it['path']}:{it['line']}: {it['rule']}: {it['term']}", file=sys.stderr)
    metrics = {"hits": len(items)}
    if items:
        rules = sorted({it["rule"] for it in items})
        emit(result(TOOL, "stopped", f"{len(items)} 件の当たり（{', '.join(rules)}）", items, metrics),
             EXIT_VIOLATION)
    emit(result(TOOL, "ok", "当たりなし", [], metrics))


def glossary_at(root: Path, decl: Declaration, ref: str | None) -> dict:
    empty = {"version": 1, "contexts": [], "terms": []}
    if ref is None:
        return load_glossary(decl) if decl.source_path.exists() else empty
    p = git_checked(root, "show", f"{ref}:{decl.source}", check=False)
    if p.returncode != 0:
        git_checked(root, "rev-parse", "--verify", f"{ref}^{{commit}}")  # ref そのものが無ければ 2
        return empty
    try:
        return parse_glossary(json.loads(p.stdout), f"{ref} の用語集")
    except ValueError as e:
        raise unreadable(f"{ref} の用語集を読めない: {e}")


def cmd_diff(a):
    root = Path(a.root)
    decl = load_declaration(root)
    if decl is None:
        emit(result(TOOL, "ok", f"宣言が無い（{DECLARATION}）。用語集の変化は無い"))
    before, after = glossary_at(root, decl, a.base), glossary_at(root, decl, a.head)

    def index(g):
        return {(t["context"], t["term"]): t for t in terms_of(g)
                if isinstance(t.get("context"), str) and isinstance(t.get("term"), str)}

    b, h = index(before), index(after)
    items = []
    for key, t in h.items():
        ctx, term = key
        if key not in b:
            items.append({"change": "added", "context": ctx, "term": term, "before": None, "after": t.get("meaning")})
            continue
        old = b[key]
        if old.get("meaning") != t.get("meaning"):
            items.append({"change": "meaning_changed", "context": ctx, "term": term,
                          "before": old.get("meaning"), "after": t.get("meaning")})
        for w in deprecated_of(t):
            if w not in deprecated_of(old):
                items.append({"change": "deprecated", "context": ctx, "term": w, "before": None, "after": term})
    for key, t in b.items():
        if key not in h:
            items.append({"change": "removed", "context": key[0], "term": key[1], "before": t.get("meaning"),
                          "after": None})
    counts = {c: sum(1 for it in items if it["change"] == c) for c in ("added", "meaning_changed", "deprecated", "removed")}
    summary = "用語集の変化は無い" if not items else \
        f"足した {counts['added']}・意味を変えた {counts['meaning_changed']}・廃止した {counts['deprecated']}・消した {counts['removed']}"
    emit(result(TOOL, "ok", summary, items, counts))


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func):
        p = sub.add_parser(name)
        p.add_argument("--root", default=".")
        p.set_defaults(func=func)
        return p

    add("gate", cmd_gate).add_argument("--mode", required=True)
    p = add("init", cmd_init)
    p.add_argument("--source")
    p.add_argument("--document")
    add("candidates", cmd_candidates).add_argument("--limit", type=int, default=100)
    add("render", cmd_render).add_argument("--check", action="store_true")
    p = add("check", cmd_check)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--diff", metavar="BASE")
    g.add_argument("--file", nargs="+")
    p.add_argument("--rules", choices=("all", "structure"), default="all")
    p = add("diff", cmd_diff)
    p.add_argument("--base", required=True)
    p.add_argument("--head")
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
