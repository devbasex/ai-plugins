#!/usr/bin/env python3
"""Check repository-relative Markdown links.

External URLs, mailto links, pure anchors, and absolute filesystem paths are
ignored. This keeps the check stable in CI while still catching broken links
between repository documents.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_HTML_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"']", re.IGNORECASE)


def iter_markdown_files(root: Path) -> list[Path]:
    roots = [root / "README.md", root / "AGENTS.md", root / "CLAUDE.md", root / "KIRO.md", root / "docs", root / "plugins"]
    files: list[Path] = []
    for item in roots:
        if item.is_file():
            files.append(item)
        elif item.is_dir():
            files.extend(item.rglob("*.md"))
    return sorted(set(files))


def strip_title(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if " " in target and not target.startswith(("./", "../", "/")):
        return target.split()[0]
    return target


def should_skip(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    parsed = urlparse(target)
    if parsed.scheme or target.startswith("//"):
        return True
    if target.startswith("/"):
        return True
    if "{" in target or "}" in target:
        return True
    return False


def target_path(root: Path, source: Path, raw_target: str) -> Path | None:
    target = strip_title(raw_target)
    if should_skip(target):
        return None
    path_part = target.split("#", 1)[0]
    if not path_part:
        return None
    path_part = unquote(path_part)
    return (source.parent / path_part).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    failures: list[str] = []

    for md in iter_markdown_files(root):
        raw_lines = md.read_text(encoding="utf-8").splitlines()
        filtered: list[str] = []
        in_fence = False
        for line in raw_lines:
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or line.lstrip().startswith(">"):
                continue
            filtered.append(line)
        text = "\n".join(filtered)
        targets = [m.group(1) for m in LINK_RE.finditer(text)]
        targets.extend(m.group(1) for m in INLINE_HTML_RE.finditer(text))
        for raw in targets:
            resolved = target_path(root, md, raw)
            if resolved is None:
                continue
            try:
                resolved.relative_to(root)
            except ValueError:
                failures.append(f"{md.relative_to(root)}: link escapes repository: {raw}")
                continue
            if not resolved.exists():
                failures.append(f"{md.relative_to(root)}: missing link target: {raw}")

    if failures:
        print("Markdown link check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Markdown local links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
