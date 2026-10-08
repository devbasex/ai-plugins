#!/usr/bin/env python3
"""design-match.py: 完了判定の設計との突き合わせのうち、機械で行う 2 つ（#1241）。LLM を呼ばず、読むだけ。

    python3 design-match.py tests (--issue N... | --design PATH...) [--root DIR]
    python3 design-match.py specs --base REF [--max-lines 10] [--root DIR]

- `tests`（確認 (a)）: 設計文書の `## 決定の記録`・`## テスト設計`・`## 構成要素` にバッククォートで書かれた
  テストファイルのパス・`<パス>::<名前>`・`## テスト設計` の `test` で始まる識別子が、HEAD にあるかを判定する。
  終了コードは 0 = すべてある（0 件を含む）/ 1 = 無いものがある / 2 = 設計文書か git が読めない、または
  無いものが無く確かめられなかったものがある / 3 = 設計文書が見つからない（該当なし）
- `specs`（確認 (b)）: `git merge-base <base> HEAD` から HEAD までに変えたファイルのパスと増減したシンボルの名前を
  含む既存の確定仕様（`docs/specifications/` の `.md`）と `CHANGELOG.md` を、文書と行で並べる。merge-base に
  無かった文書（同じ差分で足した文書）は並べない。終了コードは 0 = 列挙した（0 件を含む）/ 2 = 読めない

結果は lib/step_result.py の形の 1 行の JSON。手順は quality-gates の references/design-match.md にある。
"""

from __future__ import annotations

import argparse
import bisect
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md")
import md  # noqa: E402
import repo  # noqa: E402
from step_result import (  # noqa: E402
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_UNREADABLE,
    EXIT_VIOLATION,
    StepError,
    common_parser,
    emit,
    git,
    git_root,
    main_with,
    result,
)

TOOL = "design-match"
DESIGN_DIR = "issues"
DESIGN_NAME = re.compile(r"^issue-(?P<nums>\d+(?:-\d+)*)-design(?:-[^/]+)?\.md$")
# 名前を拾う節（決定 2）。bare の test 識別子は `## テスト設計` だけから拾う
NAME_SECTIONS = ("決定の記録", "テスト設計", "構成要素")
BARE_SECTION = "テスト設計"
CODE_SPAN = re.compile(r"`([^`\n]+)`")
SHAPE_CHARS = ("<", "{", "*", "…")
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
TEST_FILE = re.compile(r"^(?:test_.+|.+_test\.[^.]+|.+\.test\.[^.]+|.+\.spec\.[^.]+|.+Tests?\.[^.]+)$")
# `test_x`・`testX`・`TestX` の形（`tests`・`test_` のような置き場や接頭辞の字面は拾わない）
BARE_TEST = re.compile(r"^(?:test|Test|TEST)(?:_\w*[A-Za-z0-9]|[A-Z0-9]\w*)$")
PATH_LIKE = re.compile(r"^[\w./@+-]+$")
ARGS = re.compile(r"\[.*\]$")

# 確認 (b) のシンボル（決定 5）
DEF_NAME = re.compile(r"\b(?:def|class|function|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)")
CONST_NAME = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*_[A-Z0-9_]*)(?![A-Za-z0-9_])")
MIN_SYMBOL = 4
IDENT = "A-Za-z0-9_"


def _git_list(root, *args) -> list[str]:
    p = git(root, *args, check=False)
    if p.returncode != 0:
        raise StepError(f"git {' '.join(args[:3])} が読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    return [ln for ln in p.stdout.splitlines() if ln]


def _require_repo(root) -> None:
    try:
        _git_list(root, "rev-parse", "--verify", "HEAD")
    except OSError as e:
        raise StepError(f"git を起動できない: {e}", EXIT_UNREADABLE)


# --- 確認 (a): 設計に名前の出るテスト ------------------------------------------------


def find_designs(root, issues: list[str]) -> list[tuple[str, str]]:
    """HEAD の `issues/` から、番号の並びに `issues` のどれかを含む設計文書を `(パス, 中身)` で返す。"""
    want = {str(int(n)) for n in issues}
    out = []
    for rel in _git_list(root, "ls-tree", "-r", "--name-only", "HEAD", "--", f"{DESIGN_DIR}/"):
        m = DESIGN_NAME.match(PurePosixPath(rel).name)
        if not m or not want & {str(int(n)) for n in m.group("nums").split("-")}:
            continue
        p = git(root, "show", f"HEAD:{rel}", check=False)
        if p.returncode != 0:
            raise StepError(f"設計文書を HEAD から読めない: {rel}", EXIT_UNREADABLE)
        out.append((rel, p.stdout))
    return out


def read_designs(root, paths: list[str]) -> list[tuple[str, str]]:
    out = []
    for raw in paths:
        p = Path(raw) if Path(raw).is_absolute() else Path(root) / raw
        try:
            out.append((raw, p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            raise StepError(f"設計文書を読めない: {raw}（{e}）", EXIT_UNREADABLE)
    return out


def looks_like_test_file(s: str) -> bool:
    """パスの区切りのどれかがテストの置き場か、ファイル名がテストの命名のものか。"""
    if not PATH_LIKE.match(s) or ("/" not in s and "." not in s):
        return False
    parts = PurePosixPath(s.strip("/")).parts
    if not parts:
        return False
    return any(p in TEST_DIRS for p in parts) or bool(TEST_FILE.match(parts[-1]))


def classify_span(span: str, section: str) -> tuple[str, str | None, list[str]] | None:
    """字面 → `(種類, パス, 名前の並び)`。形の説明なら `("skipped", None, [])`、対象外なら None。"""
    s = span.strip()
    if any(c in s for c in SHAPE_CHARS):
        probe = re.sub(r"[<>{}*…]+", "x", s)  # 形の字を語に置き換えて、テストの名前の形かを見る
        return ("skipped", None, []) if ("::" in s or looks_like_test_file(probe) or BARE_TEST.match(probe)) else None
    if "::" in s:
        path, *names = s.split("::")
        names = [ARGS.sub("", n).strip() for n in names]
        if looks_like_test_file(path) and names and all(names):
            return ("test_function", path.removeprefix("./"), names)
        return None
    if looks_like_test_file(s):
        return ("test_file", s.removeprefix("./").rstrip("/"), [])
    if section == BARE_SECTION and BARE_TEST.match(s):
        return ("test_function", None, [s])
    return None


def _target_lines(text: str) -> list[tuple[str, int, str]]:
    """拾う節の、囲み・引用の外の行を `(節, 行番号 1 始まり, 行)` で返す。"""
    lines = text.splitlines()
    fenced = md.fenced_lines(text)
    out = []
    for sec in md.md_sections(text):
        if sec.heading.level != 2 or sec.heading.title not in NAME_SECTIONS:
            continue
        for i in range(sec.start, min(sec.end, len(lines))):
            if not fenced[i] and not lines[i].lstrip().startswith(">"):
                out.append((sec.heading.title, i + 1, lines[i]))
    return out


def extract_names(doc: str, text: str, seen: set[str]) -> tuple[list[dict], int]:
    """設計文書 1 つから名前を拾う。`seen` に入った字面は飛ばす（最初の 1 か所だけを出す）。"""
    items, skipped = [], 0
    for section, lineno, line in _target_lines(text):
        for span in CODE_SPAN.findall(line):
            if span in seen:
                continue
            c = classify_span(span, section)
            if c is None:
                continue
            seen.add(span)
            if c[0] == "skipped":
                skipped += 1
                continue
            items.append({"kind": c[0], "name": span, "result": None, "doc": doc, "line": lineno, "section": section,
                          "_path": c[1], "_names": c[2]})  # fmt: skip
    return items, skipped


def _grep_words(root, names: list[str], pathspec: list[str]) -> set[str] | None:
    """HEAD の `pathspec` に語として現れる `names` の集合。git grep が失敗すれば None。"""
    if not names:
        return set()
    args = ["grep", "-w", "-F", "-o", "-h", "-I"]
    for n in names:
        args += ["-e", n]
    p = git(root, *args, "HEAD", "--", *pathspec, check=False)
    if p.returncode not in (0, 1):
        return None
    return {ln.strip() for ln in p.stdout.splitlines() if ln.strip()}


def _path_present(path: str, files: set[str], dirs: set[str]) -> bool:
    """根からのパスならそのファイルかディレクトリが、ファイル名だけならその名前のファイルがあるか。"""
    if path in files or path in dirs:
        return True
    return "/" not in path and any(f.rsplit("/", 1)[-1] == path for f in files)


def judge_names(root, items: list[dict]) -> None:
    """各名前の `result` を present / missing / unverified に決める。"""
    files = set(_git_list(root, "ls-tree", "-r", "--name-only", "HEAD"))
    dirs = {str(p) for f in files for p in PurePosixPath(f).parents if str(p) != "."}
    by_path: dict[str, list[dict]] = {}
    bare: list[dict] = []
    for it in items:
        if it["kind"] == "test_file":
            it["result"] = "present" if _path_present(it["_path"], files, dirs) else "missing"
        elif it["_path"] is None:
            bare.append(it)
        elif it["_path"] not in files:
            it["result"] = "missing"
        else:
            by_path.setdefault(it["_path"], []).append(it)
    for path, group in by_path.items():
        found = _grep_words(root, sorted({n for it in group for n in it["_names"]}), [path])
        for it in group:
            it["result"] = "unverified" if found is None else ("present" if set(it["_names"]) <= found else "missing")
    found = _grep_words(root, sorted({it["_names"][0] for it in bare}), [".", ":(exclude)*.md"])
    for it in bare:
        it["result"] = "unverified" if found is None else ("present" if it["_names"][0] in found else "missing")


def tests_outcome(items: list[dict]) -> tuple[str, int, str]:
    missing = [it for it in items if it["result"] == "missing"]
    unverified = [it for it in items if it["result"] == "unverified"]
    if missing:
        return "stopped", EXIT_VIOLATION, f"設計に名前の出るテストのうち {len(missing)} 件が HEAD に無い"
    if unverified:
        return "stopped", EXIT_UNREADABLE, f"設計に名前の出るテストのうち {len(unverified)} 件を確かめられなかった"
    return "ok", EXIT_OK, f"設計に名前の出るテスト {len(items)} 件がすべて HEAD にある"


def cmd_tests(a):
    root = git_root(a.root)
    _require_repo(root)
    designs = find_designs(root, a.issue) if a.issue else read_designs(root, a.design)
    if not designs:
        nums = ", ".join(f"#{n}" for n in a.issue or [])
        emit(result(TOOL, "stopped", f"設計文書が無い（{nums}）。3 つの確認は該当なし（設計文書が無い）",
                    metrics={"designs": 0, "names": 0, "missing": 0, "unverified": 0, "skipped": 0}),
             EXIT_PRECONDITION)  # fmt: skip
    seen: set[str] = set()
    items: list[dict] = []
    skipped = 0
    for doc, text in designs:
        got, sk = extract_names(doc, text, seen)
        items += got
        skipped += sk
    judge_names(root, items)
    for it in items:
        it.pop("_path")
        it.pop("_names")
    status, code, summary = tests_outcome(items)
    summary = f"設計文書 {len(designs)} 件（{', '.join(doc for doc, _ in designs)}）。{summary}"
    metrics = {
        "designs": len(designs),
        "names": len(items),
        "missing": sum(it["result"] == "missing" for it in items),
        "unverified": sum(it["result"] == "unverified" for it in items),
        "skipped": skipped,
    }
    emit(result(TOOL, status, summary, items, metrics), code)


# --- 確認 (b): 既存の確定仕様と変更履歴 ----------------------------------------------


def diff_paths(root, mb: str) -> list[str]:
    out: list[str] = []
    for ln in _git_list(root, "diff", "--name-status", "-M", mb, "HEAD"):
        out += ln.split("\t")[1:]
    return list(dict.fromkeys(out))


def diff_symbols(root, mb: str) -> list[str]:
    """`.md` 以外の差分の hunk の見出しと増減の行から、定義の名前と `_` を含む大文字の識別子を抜く。"""
    p = git(root, "diff", "-U0", "--no-color", mb, "HEAD", "--", ".", ":(exclude)*.md", check=False)
    if p.returncode != 0:
        raise StepError(f"git diff が読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    syms: dict[str, None] = {}
    for ln in p.stdout.splitlines():
        if ln.startswith(("+++", "---")):
            continue
        if ln.startswith("@@"):
            ln = ln.split("@@", 2)[-1]
        elif not ln.startswith(("+", "-")):
            continue
        for name in DEF_NAME.findall(ln) + CONST_NAME.findall(ln):
            if len(name) >= MIN_SYMBOL:
                syms[name] = None
    return list(syms)


def target_docs(root, mb: str) -> list[str]:
    """HEAD の確定仕様の置き場の `.md` と `CHANGELOG.md` のうち、merge-base にもあったもの。"""
    old = set(_git_list(root, "ls-tree", "-r", "--name-only", mb))
    out = []
    for rel in _git_list(root, "ls-tree", "-r", "--name-only", "HEAD"):
        is_spec = rel.startswith(repo.SPEC_DIR + "/") and rel.endswith(".md")
        if (is_spec or PurePosixPath(rel).name == "CHANGELOG.md") and rel in old:
            out.append(rel)
    return out


def read_blobs(root, rels: list[str]) -> dict[str, str]:
    """`git cat-file --batch` 1 回で HEAD の文書を読む。"""
    if not rels:
        return {}
    data = "".join(f"HEAD:{r}\n" for r in rels).encode()
    try:
        p = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input=data, capture_output=True)
    except OSError as e:
        raise StepError(f"git を起動できない: {e}", EXIT_UNREADABLE)
    if p.returncode != 0:
        raise StepError("git cat-file が読めない", EXIT_UNREADABLE)
    out, buf, pos = {}, p.stdout, 0
    for rel in rels:
        nl = buf.index(b"\n", pos)
        head = buf[pos:nl].split()
        pos = nl + 1
        if len(head) < 3 or head[1] != b"blob":
            continue
        size = int(head[2])
        out[rel] = buf[pos : pos + size].decode("utf-8", errors="replace")
        pos += size + 1
    return out


def term_pattern(paths: list[str], symbols: list[str]) -> re.Pattern | None:
    """語を 1 つの正規表現にまとめる（決定 6）。パスは字面、シンボルは前後が識別子の文字でない一致。"""
    alts = []
    if symbols:
        syms = "|".join(re.escape(s) for s in sorted(set(symbols), key=lambda s: (-len(s), s)))
        alts.append(f"(?<![{IDENT}])(?:{syms})(?![{IDENT}])")
    if paths:
        alts.append("|".join(re.escape(s) for s in sorted(set(paths), key=lambda s: (-len(s), s))))
    return re.compile("|".join(alts)) if alts else None


def scan_doc(text: str, pat: re.Pattern, max_lines: int) -> tuple[list[str], list[int], int]:
    """文書を 1 回走査し、一致した語・行（上限まで）・行の数を返す。"""
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    terms: dict[str, None] = {}
    lines: dict[int, None] = {}
    for m in pat.finditer(text):
        terms[m.group(0)] = None
        lines[bisect.bisect_right(starts, m.start())] = None
    nums = list(lines)
    return sorted(terms), nums[:max_lines], len(nums)


def cmd_specs(a):
    root = git_root(a.root)
    _require_repo(root)
    mb_lines = _git_list(root, "merge-base", a.base, "HEAD")
    mb = mb_lines[0]
    paths = diff_paths(root, mb)
    symbols = diff_symbols(root, mb)
    pat = term_pattern(paths, symbols)
    docs = target_docs(root, mb)
    items = []
    if pat is not None:
        changed = set(paths)
        for rel, text in read_blobs(root, docs).items():
            terms, lines, count = scan_doc(text, pat, a.max_lines)
            if terms:
                kind = "changelog" if PurePosixPath(rel).name == "CHANGELOG.md" else "spec"
                items.append({"kind": kind, "name": rel, "result": "listed", "terms": terms, "lines": lines,
                              "line_count": count, "changed_in_diff": rel in changed})  # fmt: skip
    items.sort(key=lambda it: (-len(it["terms"]), it["name"]))
    metrics = {"changed_paths": len(paths), "symbols": len(symbols), "terms": len(set(paths) | set(symbols)), "listed": len(items)}
    summary = f"変えたパス {len(paths)}・シンボル {len(symbols)} を含む既存の確定仕様と変更履歴 {len(items)} 件"
    emit(result(TOOL, "ok", summary, items, metrics), EXIT_OK)


def build_parser():
    common = common_parser()
    ap = argparse.ArgumentParser(prog="design-match.py", description=__doc__.splitlines()[0], parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tests", parents=[common], help="確認 (a): 設計に名前の出るテストが HEAD にあるか")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", nargs="+", type=int, help="課題の番号（issues/ の設計文書を HEAD から読む）")
    g.add_argument("--design", nargs="+", help="設計文書のパス")
    p.set_defaults(func=cmd_tests)
    p = sub.add_parser("specs", parents=[common], help="確認 (b): 変えたものを参照する既存の確定仕様と変更履歴")
    p.add_argument("--base", required=True, help="比べる起点（例: origin/<起点のブランチ>）")
    p.add_argument("--max-lines", type=int, default=10, help="1 文書に出す行の上限")
    p.set_defaults(func=cmd_specs)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
