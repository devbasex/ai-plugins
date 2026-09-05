#!/usr/bin/env python3
"""説明文書と配布物の Markdown が、分割の基準（501 行以上）を超えていないかを見る（#354）。

`markdown-writing` のルール 9 が基準を定めるが、機械では見ていなかった。
`check-skill-frontmatter.py` は `SKILL.md` の行数だけを 500 行で見ており、`docs/` と
`references/` と `README.md` とリポジトリ直下の文書は対象に入っていない。

**走査の対象は git が追跡する `.md` である。** 追跡していないファイルを入れると、実行した
環境で結果が変わる。

**記録は走査から外す。** 記録は起きたことをそのまま残すもので、読みやすさのために分ける
対象ではない。分けると、当時の 1 件がどこまでだったかが読み取れなくなる。

**書式が 1 ファイルであることを前提にする文書も外す。** 外した理由は `EXEMPT` に書く。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 分割の基準。501 行以上が分割の対象になるため、500 行までを通す。
LIMIT = 500

# 記録の置き場所。起きたことをそのまま残すもので、分割の対象ではない。
RECORD_PREFIXES = (
    "issues/",
    "docs/development-history/",
    "docs/superpowers/",
    "docs/presentations/",
    "docs/external-reviews/",
)

# 書式が 1 ファイルであることを前提にする文書。理由を値に書く。
EXEMPT: dict[str, str] = {
    "CHANGELOG.md": (
        "Keep a Changelog は最新の版が先頭にあり、版の見出しを上から辿れることを前提にする。"
        "分割すると『最新の版で何が変わったか』を探す入口が増える（#399）"
    ),
}


def tracked_markdown(root: Path) -> list[str]:
    """git が追跡する `.md` の相対パス。"""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "*.md"],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_record(path: str) -> bool:
    return path.startswith(RECORD_PREFIXES)


def line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="走査するリポジトリの根")
    parser.add_argument("--report", action="store_true", help="上位 10 本の行数を出す")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        paths = tracked_markdown(root)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[check-doc-line-limit] git ls-files を実行できない: {exc}", file=sys.stderr)
        return 2

    if not paths:
        print("[check-doc-line-limit] 走査対象が 1 件も無い", file=sys.stderr)
        return 2

    counts: list[tuple[int, str]] = []
    over: list[tuple[int, str]] = []
    for rel in paths:
        if is_record(rel):
            continue
        target = root / rel
        if not target.is_file():
            continue
        count = line_count(target)
        counts.append((count, rel))
        if count > LIMIT and rel not in EXEMPT:
            over.append((count, rel))

    # 除外したのに基準を下回っている文書は、除外そのものが要らなくなっている。
    stale = sorted(
        rel for rel in EXEMPT
        if (root / rel).is_file() and line_count(root / rel) <= LIMIT
    )

    if args.report:
        print(f"走査: {len(counts)} 本 / 上限 {LIMIT} 行")
        for count, rel in sorted(counts, reverse=True)[:10]:
            mark = "  (除外)" if rel in EXEMPT else ""
            print(f"{count:5d}  {rel}{mark}")

    for count, rel in sorted(over, reverse=True):
        print(
            f"ERROR: {rel} が分割の基準を超えている（{count} 行 / 上限 {LIMIT} 行）。"
            "セクションごとに分割するか、書式が 1 ファイルを前提にするなら EXEMPT へ"
            "理由とともに足す",
            file=sys.stderr,
        )
    for rel in stale:
        print(
            f"ERROR: {rel} は基準を下回っているのに EXEMPT に残っている。除外を外す",
            file=sys.stderr,
        )

    if over or stale:
        return 1
    print(f"Markdown line limits are satisfied ({len(counts)} files, limit {LIMIT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
