#!/usr/bin/env python3
"""plan-to-spec-steps.py: 確定仕様化の決まった手順（#872。試作は #827 の phase-steps.py）。

    python3 plan-to-spec-steps.py spec-finalize --spec <確定仕様> --design <設計>... [--title <説明>] [--root <dir>]

設計のファイルを消し、確定仕様を docs/specifications/README.md の索引へ載せてコミットする。
用語集の設定（.ndf/glossary.json）があれば、消した設計を確定前の出所（pending_source）に持つ語の
正本（source）を確定仕様へ移し、文書を作り直して同じコミットに含める。
確定仕様の本文は呼ぶ前に LLM が書いておく。結果は lib/step_result.py の形の 1 行の JSON。
終了コードは 0 = ok / 1 = コミットする変更が無い・git が失敗 /
3 = 確定仕様か設計のファイルが無い、または用語集の設定か正本が読めない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (EXIT_PRECONDITION, StepError, commit, common_parser, emit, git,  # noqa: E402
                         git_root, main_with, result)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossary  # noqa: E402

TOOL = "plan-to-spec"


def update_index(index, text, name, link, title):
    """索引の表（| [..](..) | .. |）か一覧（- [..](..)）の最後の行の後へ 1 行を足す。"""
    lines = text.split("\n")
    desc = title or name
    last, kind = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and re.search(r"\]\([^)]+\.md\)", s):
            last, kind = i, "table"
        elif re.match(r"^[-*] \[[^\]]+\]\([^)]+\.md\)", s):
            last, kind = i, "list"
    if last is None:
        body = text.rstrip("\n") + "\n\n" + f"- [{name}]({link}) — {desc}" + "\n"
    else:
        if kind == "table":
            new = f"| [{name}]({link}) | {desc} |"
        else:
            sep = " — " if " — " in lines[last] else (": " if ": " in lines[last] else " — ")
            bullet = lines[last].lstrip()[0]
            indent = lines[last][: len(lines[last]) - len(lines[last].lstrip())]
            new = f"{indent}{bullet} [{name}]({link}){sep}{desc}"
        lines.insert(last + 1, new)
        body = "\n".join(lines)
    index.write_text(body, encoding="utf-8")


def load_checked_glossary(root: Path):
    """用語集の設定と正本。宣言が無ければ None。宣言・正本が読めないか語の形が崩れていれば設計を消す前に止める。"""
    try:
        decl = glossary.load_declaration(root)
        if decl is None:
            return None
        g = glossary.load_glossary(decl)
    except StepError as e:
        raise StepError(f"用語集の設定（.ndf/glossary.json）か正本が読めない: {e}", EXIT_PRECONDITION)
    bad = [f["detail"] for f in glossary.structure_findings(g, decl) if f["rule"] == "schema"]
    if bad:
        raise StepError(f"用語集の正本の形が崩れている（glossary.py check --rules structure で直す）: {bad[0]}",
                        EXIT_PRECONDITION)
    return decl, g


def plan_glossary(loaded, removed: set, spec_rel: str):
    """消す設計を pending_source に持つ語の source を確定仕様へ移し、書く正本と文書を作る。何も書かない。"""
    if loaded is None:
        return None
    decl, g = loaded
    moved = [t for t in glossary.terms_of(g) if t.get("pending_source") in removed]
    if not moved:
        return None
    for t in moved:
        t["source"] = spec_rel
        del t["pending_source"]
    items = [{"kind": "glossary", "name": t["term"], "result": "promoted", "source": spec_rel} for t in moved]
    return decl, json.dumps(g, ensure_ascii=False, indent=2) + "\n", glossary.render_text(g, decl.source), items


def write_glossary(plan) -> list:
    """plan_glossary の正本と文書を書いて git add する。"""
    if plan is None:
        return []
    decl, source_text, document_text, items = plan
    try:
        decl.source_path.write_text(source_text, encoding="utf-8")
        decl.document_path.parent.mkdir(parents=True, exist_ok=True)
        decl.document_path.write_text(document_text, encoding="utf-8")
    except OSError as e:
        raise StepError(f"用語集を書けない: {e}")
    git(decl.root, "add", "-A", "--", decl.source, decl.document)
    return items


def cmd_spec_finalize(a):
    root = git_root(a.root)
    spec = Path(a.spec) if Path(a.spec).is_absolute() else (root / a.spec).resolve()
    if not spec.is_file():
        raise StepError(f"確定仕様のファイルが無い: {a.spec}", EXIT_PRECONDITION)
    spec_rel = spec.relative_to(root).as_posix()
    rels = []
    for d in a.design:
        dp = Path(d)
        rel = dp.resolve().relative_to(root).as_posix() if dp.is_absolute() else dp.as_posix()
        if not (root / rel).exists():
            raise StepError(f"設計のファイルが無い: {d}", EXIT_PRECONDITION)
        rels.append(rel)
    plan = plan_glossary(load_checked_glossary(root), set(rels), spec_rel)
    # 用語集を書けなければ設計を消さずに止めるため、書き込みを git rm より前に置く
    promoted = write_glossary(plan)

    for rel in rels:
        git(root, "rm", "-q", "--", rel)
    items = [{"kind": "design", "name": rel, "result": "removed"} for rel in rels]
    items += promoted

    index = root / "docs" / "specifications" / "README.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        link = os.path.relpath(spec, index.parent).replace(os.sep, "/")
        if f"]({link})" not in text and f"]({spec_rel})" not in text and f"](./{link})" not in text:
            update_index(index, text, spec.name, link, a.title)
            git(root, "add", "--", index.relative_to(root).as_posix())
            items.append({"kind": "index", "name": index.relative_to(root).as_posix(), "result": "added",
                          "link": link})

    git(root, "add", "--", spec_rel)
    if git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise StepError("コミットする変更が無い")
    sha = commit(root, f"Docs: {spec.name} を確定仕様にする")
    items.append({"kind": "commit", "name": sha, "result": "committed"})
    emit(result(TOOL, "ok", f"{spec_rel} を確定仕様にした（設計 {len(a.design)} 件を削除、{sha[:8]}）",
                items, {"removed_designs": len(a.design), "commit": sha}))


def build_parser():
    ap = argparse.ArgumentParser(prog="plan-to-spec-steps.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("spec-finalize", parents=[common_parser()], help="設計を消し、確定仕様を索引へ載せてコミットする")
    p.add_argument("--spec", required=True)
    p.add_argument("--design", nargs="+", action="extend", required=True)
    p.add_argument("--title")
    p.set_defaults(func=cmd_spec_finalize)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
