#!/usr/bin/env python3
"""doc-lint.py: 追加した行だけに、文書の書き方のチェックを掛ける（#870 B2）。

    python3 doc-lint.py [--base <ref>] [--root DIR] [--exclude PREFIX...] [--all-lines]

起点（--base と HEAD の分岐点。--base が無ければ origin/<.ndf/worktree.json の base_branch>）から追加した Markdown の行に、markdown-writing のセルフチェックの
「検討痕跡・変更履歴」の語と、課題番号の由来・以前との比較の語を掛ける。コードブロックの中は見ない。
結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ヒット無し / 1 = ヒットあり / 2 = 読めない。
`items[]` は 1 ヒット 1 件（`name` は `パス:行`、`rule` は当たった規則、`text` は行）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md", "mdtable", "textparse", "pathmatch")  # glossary.py の分を含めて 1 回で入れる（決定 23）
import md  # noqa: E402
from step_result import EXIT_UNREADABLE, StepError, emit, main_with, result  # noqa: E402
import proc  # noqa: E402
import repo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossary  # noqa: E402  追加した行の取り方を共有する

TOOL = "doc-lint"

# 規則名 → 正規表現。markdown-writing の「書き終えたらセルフチェック」の語に、課題番号の由来と
# 以前との比較の語を足したもの
RULES = {
    "history": re.compile(r"案 ?[A-Z]\b|Option ?[A-Z]\b|パターン[0-9]|今回|壁打ち|以前は|当初は|に変更|指摘を受け|"
                          r"レビュー対応|誤りのため"),
    "issue-origin": re.compile(r"#\d+\s*(?:で|から|により|によって|以来|の(?:指示|指摘|差し戻し|追記|追加|変更|対応|"
                               r"見直し|導入|修正))|（#\d+(?:\s*の[^）]{1,12})?）"),
    "comparison": re.compile(r"従来|以前|かつて|もともと|元々|旧来|過去に|によらず|ではな[いく]|に関わらず|にかかわらず"),
}
DEFAULT_EXCLUDE = ("CHANGELOG.md", "issues/", ".worktrees/", "docs/presentations/")


def lint_files(root: Path, files: dict[str, set[int]], all_lines: bool) -> tuple[list[dict], int]:
    items, total = [], 0
    for rel, nums in sorted(files.items()):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        skip = {i for i, inside in enumerate(md.fenced_lines(text), 1) if inside}
        targets = range(1, len(lines) + 1) if all_lines else sorted(nums)
        for n in targets:
            if n in skip or n > len(lines):
                continue
            total += 1
            text = lines[n - 1]
            for rule, rx in RULES.items():
                m = rx.search(text)
                if m:
                    items.append({"kind": "line", "name": f"{rel}:{n}", "result": "hit", "rule": rule,
                                  "match": m.group(0), "text": text.strip()[:200]})
    return items, total


def generated_documents(root: Path) -> tuple[str, ...]:
    """.ndf/glossary.json の document（render の生成物）。直すなら正本を直すため、--exclude によらず見ない。

    git diff のパスと突き合わせるため、`./` や区切りの揺れを正規化して返す。読めない設定は無いものとして扱う。
    """
    f = root / ".ndf" / "glossary.json"
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("document") if f.is_file() else None
    except (OSError, ValueError, AttributeError):
        return ()
    if not isinstance(v, str) or not v:
        return ()
    p = Path(v.replace("\\", "/")).as_posix()
    return (p,) if p not in (".", "") else ()


def cmd_lint(a):
    root = proc.git_root(a.root)
    base = a.base or repo.declared_base(root, remote=True)
    if not base:
        raise StepError("起点が分からない（--base か .ndf/worktree.json の base_branch）", EXIT_UNREADABLE)
    p = proc.git(root, "merge-base", base, "HEAD", check=False)
    if p.returncode != 0:
        raise StepError(f"起点 {base} を解決できない: {p.stderr.strip()[:200]}", EXIT_UNREADABLE)
    start = p.stdout.strip()
    files = glossary.added_lines(root, start, ("*.md", "**/*.md"))
    excl = tuple(a.exclude) if a.exclude is not None else DEFAULT_EXCLUDE
    generated = set(generated_documents(root))
    files = {k: v for k, v in files.items()
             if k not in generated and not any(k.startswith(e) or k == e.rstrip("/") for e in excl)}
    items, total = lint_files(root, files, a.all_lines)
    metrics = {"base": base, "files": len(files), "lines": total, "hits": len(items),
               "rules": {r: sum(1 for i in items if i["rule"] == r) for r in RULES}}
    if items:
        summary = f"追加した {total} 行のうち {len(items)} 行に書き方のチェックの語がある（{len(files)} ファイル）"
        emit(result(TOOL, "stopped", summary, items, metrics,
                    next="ヒットした行を、今の決まりだけを書く形に直す（経緯・比較・課題番号の由来を外す）"))
    emit(result(TOOL, "ok", f"追加した {total} 行に書き方のチェックの語は無い（{len(files)} ファイル）", items, metrics))


def build_parser():
    ap = argparse.ArgumentParser(prog="doc-lint.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    ap.add_argument("--base", help="起点の ref（既定 origin/<.ndf/worktree.json の base_branch>）")
    ap.add_argument("--exclude", nargs="*", help=f"見ないパスの接頭辞（既定 {' '.join(DEFAULT_EXCLUDE)}）")
    ap.add_argument("--all-lines", action="store_true", help="変わったファイルの全行を見る")
    ap.set_defaults(func=cmd_lint)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
