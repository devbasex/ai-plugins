#!/usr/bin/env python3
"""mode-materials.py: 課題の本文と設計文書から、モードの判定の材料を集める（#2923）。

    python3 mode-materials.py --issue <番号>... [--root <リポジトリ>] [--out <判定の根拠のファイル>]

課題ごとに、種類・公開インタフェースの変更の有無（本文の「影響」の表）・触るファイルの候補・候補ごとのテストの
ファイルを出す。判定表の行は選ばない（行を選ぶのは conductor）。候補は code span とリンクの宛先からだけ取り、
取れなければ候補 0 件と出す（推して埋めない）。`--out` を渡すと、判定の根拠のファイルの下書き
（row・mode・condition が空）を書く。conductor が埋めて `sprint-state.py loop sprint --judgments` に渡す。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "mode-materials"`）。終了コードは
0 = 集めた / 1 = 本文を読めない課題がある（`gaps` に課題と理由）/ 2 = 引数の誤り。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md", "durable", "waits")
import jsonio  # noqa: E402
import md  # noqa: E402
import run_core  # noqa: E402
from step_result import EXIT_VIOLATION, StepError, emit, main_with, result  # noqa: E402

TOOL = "mode-materials"
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
TEST_FORMS = ("test_{}.", "{}_test.", "{}.test.", "{}.spec.")


def _section_of(heads: list, line: int) -> str:
    return next((h.title for h in reversed(heads) if h.line <= line), "")


def raw_candidates(body: str) -> list[tuple[str, str]]:
    """本文の code span の中身とリンクの宛先を、規則 2・3 で整えて (パス, 節の見出し) で返す（出てくる順）。
    コードの囲みの中は読まない（CommonMark の code span でないため）。"""
    heads = md.headings(body)
    found = [(c.line, c.content) for c in md.code_spans(body)] + [(x.line, x.href.split("#", 1)[0] or x.href) for x in md.links(body)]
    out = []
    for line, text in sorted(found, key=lambda t: t[0]):
        s = LINE_SUFFIX.sub("", text.strip().removeprefix("./"))
        if not s or any(ch.isspace() for ch in s) or s[0] in "-$#" or "://" in s:
            continue
        out.append((s, _section_of(heads, line)))
    return out


def exists_in(path: str, files: list[str], fileset: set[str]) -> bool:
    """`git ls-files` に同じパスがあるか、そのパスで始まるディレクトリに当たるか。"""
    base = path.rstrip("/")
    return base in fileset or any(f.startswith(base + "/") for f in files)


def looks_new(path: str) -> bool:
    """`/` を含み、拡張子を持つか `/` で終わる（新しく作るファイル・ディレクトリ）。識別子や語は当たらない。"""
    return "/" in path and (path.endswith("/") or PurePosixPath(path).suffix != "")


def is_test(path: str) -> bool:
    """`tests/` の下か、`test_<名>.*`・`<名>_test.*`・`<名>.test.*`・`<名>.spec.*` の名前のファイル。"""
    p = PurePosixPath(path)
    first = p.name.split(".")[0]
    return "tests" in p.parts[:-1] or p.name.startswith("test_") or first.endswith("_test") or ".test." in p.name or ".spec." in p.name


def tests_of(path: str, files: list[str], fileset: set[str]) -> list[str]:
    """存在するファイルのテストのファイル。テストのファイル自身なら自分だけ。ディレクトリ・無いファイルは空。"""
    if path not in fileset:
        return []
    if is_test(path):
        return [path]
    stem = PurePosixPath(path).stem
    names = tuple(f.format(stem) for f in TEST_FORMS)
    return [f for f in files if PurePosixPath(f).name.startswith(names) and f != path]


def kind_of(body: str) -> str | None:
    sec = md.section_named(body, "種類")
    if sec is None:
        return None
    lines = body.splitlines()[sec.start : sec.end]
    return next((x.strip() for x in lines if x.strip()), None)


def public_interface(body: str) -> dict:
    """本文の「影響」の表の `公開インタフェース` の行の 2 列目。無ければ `記載なし`。"""
    secs = [s for s in md.md_sections(body) if s.heading.title == "影響"]
    for t in md.tables(body):
        if not any(s.start <= t.start < s.end for s in secs):
            continue
        for row in t.rows:
            if len(row) >= 2 and row[0].strip() == "公開インタフェース":
                cell = row[1].strip()
                value = "変わらない" if cell.startswith("変わらない") else "変わる" if cell.startswith("変わる") else "記載なし"
                return {"value": value, "cell": cell}
    return {"value": "記載なし", "cell": ""}


def collect(number: int, body: str, files: list[str], designs: list[tuple[str, list[str]]]) -> dict:
    """1 つの課題の材料。`designs` は (設計文書のパス, その課題の「触るファイル」) の並び。"""
    fileset = set(files)
    seen: dict[str, dict] = {}
    for path, section in raw_candidates(body):
        if path in seen:
            continue
        if exists_in(path, files, fileset):
            seen[path] = {"path": path, "exists": True, "source": "本文", "section": section}
        elif looks_new(path):
            seen[path] = {"path": path, "exists": False, "source": "本文", "section": section}
    for doc, paths in designs:
        for path in paths:
            if path not in seen:
                seen[path] = {"path": path, "exists": exists_in(path, files, fileset), "source": "設計", "section": doc}
    out = [{**f, "tests": tests_of(f["path"], files, fileset) if f["exists"] else []} for f in seen.values()]
    return {"number": number, "kind": kind_of(body), "public_interface": public_interface(body), "files": out, "files_found": len(out)}


def draft(items: list[dict]) -> dict:
    """判定の根拠のファイルの下書き。`files` は存在する候補だけ。row・mode・condition は conductor が埋める。"""
    return {
        "issues": [
            {
                "number": it["number"],
                "row": None,
                "mode": None,
                "condition": "",
                "files": [f["path"] for f in it["files"] if f["exists"]],
                "materials": it,
            }
            for it in items
        ]
    }


# ---------------------------------------------------------------- 外の世界


def ls_files(root: str) -> list[str]:
    p = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"git ls-files が失敗した: {p.stderr.strip()[:200]}", 2)
    return p.stdout.splitlines()


def issue_body(number: int, root: str) -> str:
    import gh_rest

    r = gh_rest.view_json("issue", number, "body", cwd=root)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout).strip()[:300] or f"gh が {r.returncode} を返した")
    body = json.loads(r.stdout).get("body")
    if not isinstance(body, str):
        raise OSError("本文が無い")
    return body


def design_files(root: str, number: int) -> list[tuple[str, list[str]]]:
    """設計文書の「設計の結果」の表から、その課題の行の触るファイルを読む。読めない文書は飛ばす。"""
    import design_docs
    import design_results

    out = []
    try:
        docs = design_docs.find_designs(root, [str(number)])
    except StepError:
        return []
    for rel, text in docs:
        try:
            rows = design_results.parse_results(text, rel) or []
        except design_results.DesignResultsError:
            continue
        files = [f for r in rows if r.issue == number for f in r.files]
        if files:
            out.append((rel, files))
    return out


def write_draft(path: str, items: list[dict]) -> None:
    """判定の根拠のファイルの下書きを、呼ぶ側が渡したパスへ書く（契約 files の judgments-json）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(path, draft(items))


def run(a) -> None:
    root = str(Path(a.root).resolve())
    files = ls_files(root)
    items, gaps = [], []
    for n in a.issue:
        try:
            body = issue_body(n, root)
        except (OSError, ValueError) as e:
            gaps.append({"kind": "材料", "name": f"#{n}", "reason": f"課題の本文を読めない: {e}"})
            continue
        items.append(collect(n, body, files, design_files(root, n)))
    metrics = {"issues": len(items), "files_found": {str(i["number"]): i["files_found"] for i in items}}
    if gaps:
        emit(result(TOOL, "stopped", f"{len(gaps)} 件の課題の本文を読めない", items, metrics, gaps=gaps), EXIT_VIOLATION)
    if a.out:
        write_draft(a.out, items)
        metrics["out"] = a.out
    empty = [i["number"] for i in items if not i["files_found"]]
    note = f"。触るファイルの候補が 0 件: {' '.join(f'#{n}' for n in empty)}" if empty else ""
    emit(result(TOOL, "ok", f"{len(items)} 件の課題の判定の材料を集めた{note}", items, metrics))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="課題の本文と設計文書から、モードの判定の材料を集める")
    ap.add_argument("--issue", type=int, nargs="+", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", help="判定の根拠のファイルの下書きを書く先")
    ap.set_defaults(func=run)
    return ap


def main(argv: list[str] | None = None):
    return main_with(parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    run_core.main("scripts/mode-materials.py", main)
