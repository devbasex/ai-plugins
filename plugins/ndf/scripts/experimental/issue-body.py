#!/usr/bin/env python3
"""issue-body.py: 課題の本文を全文で書き直し、読み直して一致を確かめる（試行）。

    python3 issue-body.py set <番号> <ファイル> [--repo OWNER/REPO]

`gh issue view --json body -q .body` は最後に改行を 1 つ足すため、CR と末尾の空白・空行を除いて比べる。
一致しなければ食い違った最初の行を出して 1 で終わる。

書き込む前に、本文が `…` で指すリポジトリのパスのうち、手元にはあるが origin の起点のブランチ
（`.ndf/worktree.json` の `base_branch`、無ければ origin/HEAD）に無いものを探す。あれば GitHub から
読めない参照として、書かずに 1 で終わる（コミット前の `issues/` のファイルを指して済ませないため）。
どこにも無いパス（これから作るファイル）は見ない。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from step_result import emit, result  # noqa: E402

TOOL = "issue-body"


def norm(s: str) -> str:
    return s.replace("\r", "").rstrip()


PATH_RE = re.compile(r"`([^`\s<>*{}$:]+/[^`\s<>*{}$:]+)`")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def base_ref(root: Path) -> str | None:
    try:
        b = json.loads((root / ".ndf" / "worktree.json").read_text()).get("base_branch")
    except (OSError, ValueError):
        b = None
    if b:
        return f"origin/{b}"
    p = git(root, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if p.returncode == 0:
        return p.stdout.strip()
    return "origin/main" if git(root, "rev-parse", "-q", "--verify", "origin/main").returncode == 0 else None


def local_only_paths(text: str) -> list[str]:
    """手元にはあるが、origin の起点のブランチに無いパスを返す（リポジトリの外なら空）。"""
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return []
    root = Path(top.stdout.strip())
    ref = base_ref(root)
    if not ref:
        return []
    out = []
    for path in dict.fromkeys(m.group(1).rstrip("/") for m in PATH_RE.finditer(text)):
        if path.startswith((".", "~", "/")) or not (root / path).exists():
            continue
        if git(root, "cat-file", "-e", f"{ref}:{path}").returncode != 0:
            out.append(path)
    return out


def first_diff(a: str, b: str) -> dict:
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return {"line": i + 1, "github": x[:200], "file": y[:200]}
    n = min(len(la), len(lb))
    return {"line": n + 1, "github": (la[n] if n < len(la) else "")[:200],
            "file": (lb[n] if n < len(lb) else "")[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("number", type=int)
    s.add_argument("file")
    s.add_argument("--repo")
    a = ap.parse_args()
    repo = ["--repo", a.repo] if a.repo else []
    want = Path(a.file).read_text()
    missing = local_only_paths(want)
    if missing:
        emit(result(TOOL, "stopped", f"#{a.number} の本文が GitHub から読めないパスを指す: {'・'.join(missing)}",
                    [{"number": a.number, "result": "local_only", "path": m} for m in missing],
                    next="中身を本文へ入れるか、コミットして push してから打ち直す"))
    p = subprocess.run(["gh", "issue", "edit", str(a.number), "--body-file", a.file, *repo],
                       capture_output=True, text=True)
    if p.returncode != 0:
        emit(result(TOOL, "stopped", f"#{a.number} を書き直せない: {p.stderr.strip()[:300]}",
                    [{"number": a.number, "result": "edit_failed"}]))
    v = subprocess.run(["gh", "issue", "view", str(a.number), "--json", "body", "-q", ".body", *repo],
                       capture_output=True, text=True)
    if v.returncode != 0:
        emit(result(TOOL, "stopped", f"#{a.number} を読み直せない: {v.stderr.strip()[:300]}",
                    [{"number": a.number, "result": "view_failed"}]), 2)
    if norm(v.stdout) != norm(want):
        diff = first_diff(norm(v.stdout), norm(want))
        emit(result(TOOL, "stopped", f"#{a.number} の本文がファイルと食い違う（{diff['line']} 行目）",
                    [{"number": a.number, "result": "mismatch", **diff}]))
    emit(result(TOOL, "ok", f"#{a.number} の本文を書き直し、読み直して一致を確かめた",
                [{"number": a.number, "result": "matched", "lines": len(norm(want).splitlines())}]))


if __name__ == "__main__":
    sys.exit(main())
