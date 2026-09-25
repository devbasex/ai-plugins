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
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, main_with, result  # noqa: E402

TOOL = "glossary"
DECLARATION = ".ndf/glossary.json"
DEFAULT_SOURCE = "docs/glossary/glossary.json"
DEFAULT_DOCUMENT = "docs/glossary.md"
DEFAULT_PATHS = ["issues/*-requirements.md", "issues/*-design.md", "issues/*-design-decisions.md"]
DEFAULT_TERM_SECTIONS = ["用語"]
DESIGN_MODES = ("standard", "legacy-refactor")
FORMATS = ("json",)
STRUCTURE_RULES = ("schema", "duplicate", "stale_document")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
BOLD = re.compile(r"\*\*([^*\n]{1,12}?)\*\*")
TYPE_NAME = re.compile(r"\b(?:class|interface|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)|\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
ASCII_WORD = re.compile(r"^[A-Za-z0-9_\-]+$")
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


def read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise unreadable(f"{label} を読めない: {path}: {e}")


def load_declaration(root: Path) -> Declaration | None:
    p = root / DECLARATION
    if not p.exists():
        return None
    return Declaration(root, read_json(p, DECLARATION))


def parse_glossary(raw, label: str) -> dict:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise unreadable(f"{label} は version 1 のオブジェクトで書く")
    if not isinstance(raw.get("contexts", []), list) or not isinstance(raw.get("terms", []), list):
        raise unreadable(f"{label} の contexts / terms は配列で書く")
    return raw


def load_glossary(decl: Declaration) -> dict:
    return parse_glossary(read_json(decl.source_path, "用語集"), "用語集")


def contexts_of(g: dict) -> list[dict]:
    return [c for c in g.get("contexts", []) if isinstance(c, dict)]


def terms_of(g: dict) -> list[dict]:
    return [t for t in g.get("terms", []) if isinstance(t, dict)]


def deprecated_of(t: dict) -> list[str]:
    d = t.get("deprecated") or []
    return [w for w in d if isinstance(w, str)] if isinstance(d, list) else []


def live_words(g: dict) -> set[str]:
    return {t["term"] for t in terms_of(g) if isinstance(t.get("term"), str) and t["term"]}


# --- 文書の生成 -------------------------------------------------------------------

def cell(value) -> str:
    s = str(value or "").replace("\r\n", "\n").replace("\n", " ").replace("|", "\\|").strip()
    return s or "—"


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
        out += ["| 語 | 意味 | 廃止した語 | 正本 |", "| --- | --- | --- | --- |"]
        for t in rows:
            dep = "、".join(deprecated_of(t))
            src = f"`{t['source']}`" if t.get("source") else ""
            out.append(f"| {cell(t.get('term'))} | {cell(t.get('meaning'))} | {cell(dep)} | {cell(src)} |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --- 用語集の形 --------------------------------------------------------------------

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
    live_by_ctx: dict[str, set[str]] = {}
    for t in terms_of(g):
        if isinstance(t.get("term"), str) and isinstance(t.get("context"), str):
            live_by_ctx.setdefault(t["context"], set()).add(t["term"])
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
                not matches(t["source"], decl.source_paths):
            hit("unconfirmed_source", t["term"],
                f"terms[{i}] の source が確定仕様を指さない: {t['source']}（check.source_paths に当たるパスへ移す。"
                "確定前は source を空にし、plan-to-spec が確定仕様を書いたときに入れる）")
        if isinstance(t.get("pending_source"), str) and not (decl.root / t["pending_source"]).is_file():
            hit("schema", t["term"], f"terms[{i}] の pending_source が指す設計文書が無い: {t['pending_source']}")
        if t["context"] not in ids:
            hit("schema", t["term"], f"terms[{i}] の context が宣言されていない: {t['context']}")
        for w in deprecated_of(t):
            if len(w) < 2:
                hit("schema", w, f"terms[{i}] の廃止した語が 1 文字で照合できない")
            elif w in live_by_ctx.get(t["context"], set()):
                hit("schema", w, f"terms[{i}] の廃止した語が同じコンテキストの生きた語と重なる")
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


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def clean_term(cell_text: str) -> str:
    s = cell_text.strip()
    for mark in ("**", "__", "`"):
        if s.startswith(mark) and s.endswith(mark) and len(s) > 2 * len(mark):
            s = s[len(mark):-len(mark)].strip()
    return s


def scan(lines: list[str], term_sections: list[str]):
    """行ごとに (行番号, 照合する本文, 用語の表の 1 列目の語か None) を返す。コードブロックは飛ばす。"""
    in_fence = False
    section_level, in_terms = 0, False
    table_row = -1
    for n, line in enumerate(lines, 1):
        if FENCE.match(line):
            in_fence = not in_fence
            table_row = -1
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if in_terms and level <= section_level:
                in_terms = False
            if title in term_sections:
                in_terms, section_level = True, level
            table_row = -1
            yield n, mask_inline_code(line), None
            continue
        term = None
        if line.lstrip().startswith("|"):
            table_row += 1
            if in_terms and table_row >= 2 and not SEPARATOR.match(line):
                first = split_row(line)[0] if split_row(line) else ""
                term = clean_term(first) or None
        else:
            table_row = -1
        yield n, mask_inline_code(line), term


def text_findings(rel: str, text: str, wanted: set[int] | None, g: dict, decl: Declaration,
                  dep_re, live_re) -> list[dict]:
    live = live_words(g)
    items = []
    for n, body, term in scan(text.splitlines(), decl.term_sections):
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


def replacement(g: dict, word: str) -> str:
    names = [t["term"] for t in terms_of(g) if word in deprecated_of(t) and isinstance(t.get("term"), str)]
    return " / ".join(names) or "用語集の語"


# --- git ------------------------------------------------------------------------------

def git(root: Path, *args, check=True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise unreadable(f"git {' '.join(args)} が失敗: {p.stderr.strip()[:300]}")
    return p


def added_lines(root: Path, base: str) -> dict[str, set[int]]:
    """base と HEAD の分岐点から追加した行の番号を、ファイルごとに返す（作業ツリーの未コミット分も含む）。

    差分の取り方は doc-lint.py の added_lines と同じ `git diff --unified=0` で、起点だけを
    merge-base で解く。追跡していないファイルは全行とする。
    """
    mb = git(root, "merge-base", base, "HEAD").stdout.strip()
    diff = git(root, "diff", "--unified=0", "--no-color", "--diff-filter=AM", mb, "--").stdout
    out: dict[str, set[int]] = {}
    cur, ln = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            cur = None if path == "/dev/null" else path[2:] if path.startswith("b/") else path
            continue
        m = HUNK.match(line)
        if m:
            ln = int(m.group(1))
            continue
        if cur is None or line.startswith("---") or line.startswith("diff "):
            continue
        if line.startswith("+"):
            out.setdefault(cur, set()).add(ln)
            ln += 1
        elif not line.startswith("-") and not line.startswith("\\"):
            ln += 1
    for rel in git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
        rel = rel.strip()
        p = root / rel
        if rel and p.is_file():
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            out[rel] = set(range(1, n + 1))
    return out


def matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(rel, p.replace("**/", "")) for p in patterns)


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

    files = git(root, "ls-files").stdout.splitlines()
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
            for n, body, term in scan(lines, sections):
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
        emit(result(TOOL, "ok", f"宣言が無い（{DECLARATION}）。語のチェックをしない"))
    g = load_glossary(decl)
    items = structure_findings(g, decl) + stale_findings(g, decl)
    if a.rules == "all" and (a.diff or a.file):
        dep_words = {w for t in terms_of(g) for w in deprecated_of(t) if len(w) >= 2} - live_words(g)
        dep_re, live_re = word_pattern(dep_words), word_pattern(live_words(g))
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
                if rel == decl.document or not matches(rel, decl.paths):
                    continue
                p = root / rel
                if p.is_file():
                    targets.append((rel, p.read_text(encoding="utf-8", errors="replace"), lines))
        for rel, text, wanted in targets:
            if a.file and (root / decl.document).resolve() == Path(rel).resolve():
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
    p = git(root, "show", f"{ref}:{decl.source}", check=False)
    if p.returncode != 0:
        git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")  # ref そのものが無ければ 2
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
