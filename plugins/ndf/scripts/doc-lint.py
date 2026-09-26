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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import EXIT_UNREADABLE, StepError, emit, main_with, result  # noqa: E402

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
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(root, *args):
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"git {' '.join(args)} が失敗: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    return p.stdout


def added_lines(root, base) -> dict[str, set[int]]:
    """起点から追加した行の番号を、ファイルごとに返す（作業ツリーの未コミット分も含む）。"""
    diff = git(root, "diff", "--unified=0", "--no-color", "--diff-filter=AM", base, "--", "*.md", "**/*.md")
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
    # まだ追跡していない .md は全行が追加した行
    for rel in git(root, "ls-files", "--others", "--exclude-standard", "--", "*.md", "**/*.md").splitlines():
        rel = rel.strip()
        if rel and (Path(root) / rel).is_file():
            n = len((Path(root) / rel).read_text(encoding="utf-8", errors="replace").splitlines())
            out[rel] = set(range(1, n + 1))
    return out


def fenced(lines: list[str]) -> set[int]:
    """コードブロックの中の行番号（1 始まり）。"""
    inside, mark, out = False, "", set()
    for i, line in enumerate(lines, 1):
        s = line.lstrip()
        if not inside and (s.startswith("```") or s.startswith("~~~")):
            inside, mark = True, s[:3]
            out.add(i)
        elif inside:
            out.add(i)
            if s.startswith(mark):
                inside = False
    return out


def scan(root: Path, files: dict[str, set[int]], all_lines: bool) -> tuple[list[dict], int]:
    items, total = [], 0
    for rel, nums in sorted(files.items()):
        path = root / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        skip = fenced(lines)
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


def declared_base(root: Path) -> str | None:
    """.ndf/worktree.json の base_branch を origin/<名前> で返す。無ければ None。"""
    f = root / ".ndf" / "worktree.json"
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("base_branch") if f.is_file() else None
    except (ValueError, AttributeError):
        return None
    return f"origin/{v}" if isinstance(v, str) and v else None


def generated_documents(root: Path) -> tuple[str, ...]:
    """.ndf/glossary.json の document（render の生成物）。直すなら正本を直すため、--exclude によらず見ない。"""
    f = root / ".ndf" / "glossary.json"
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("document") if f.is_file() else None
    except (ValueError, AttributeError):
        return ()
    return (v,) if isinstance(v, str) and v else ()


def cmd_lint(a):
    root = Path(a.root).resolve() if a.root else Path(git(".", "rev-parse", "--show-toplevel").strip())
    base = a.base or declared_base(root)
    if not base:
        raise StepError("起点が分からない（--base か .ndf/worktree.json の base_branch）", EXIT_UNREADABLE)
    p = subprocess.run(["git", "-C", str(root), "merge-base", base, "HEAD"], capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"起点 {base} を解決できない: {p.stderr.strip()[:200]}", EXIT_UNREADABLE)
    start = p.stdout.strip()
    files = added_lines(root, start)
    excl = (tuple(a.exclude) if a.exclude is not None else DEFAULT_EXCLUDE) + generated_documents(root)
    files = {k: v for k, v in files.items() if not any(k.startswith(e) or k == e.rstrip("/") for e in excl)}
    items, total = scan(root, files, a.all_lines)
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
