#!/usr/bin/env python3
"""Check repository-relative Markdown links.

External URLs, mailto links, and absolute filesystem paths are ignored. This
keeps the check stable in CI while still catching broken links between
repository documents.

Heading references (`#name` and `other.md#name`) are matched against the ATX
headings of the target document, named by GitHub's rule (#445). Documents
outside the scanned set are not read for headings.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_HTML_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"']", re.IGNORECASE)
TITLE_RE = re.compile(r"\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))\s*$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
# GitHub の見出しアンカー規則: 記号は空白・ハイフン・アンダースコアだけ残す。
SLUG_KEEP_PUNCTUATION = " -_"
# 同規則: Unicode 一般カテゴリの先頭文字が L(字母)・M(結合文字)・N(数字) の文字を残す。
SLUG_KEEP_CATEGORIES = "LMN"
DEFAULT_SCAN_TARGETS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "KIRO.md",
    "docs",
    "plugins",
)


def iter_markdown_files(root: Path) -> list[Path]:
    roots = [root / target for target in DEFAULT_SCAN_TARGETS]
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
    return TITLE_RE.sub("", target)


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


def resolve_document(source: Path, path_part: str) -> Path:
    """The document a link's path part names; an empty part names the source."""
    if not path_part:
        return source.resolve()
    return (source.parent / unquote(path_part)).resolve()


def target_path(source: Path, raw_target: str) -> Path | None:
    target = strip_title(raw_target)
    if should_skip(target):
        return None
    path_part = target.split("#", 1)[0]
    if not path_part:
        return None
    return resolve_document(source, path_part)


def visible_lines(path: Path) -> list[str]:
    """Lines outside code fences and block quotes."""
    lines: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return lines


def link_targets(text: str) -> list[str]:
    targets = [m.group(1) for m in LINK_RE.finditer(text)]
    targets.extend(m.group(1) for m in INLINE_HTML_RE.finditer(text))
    return targets


def _is_kept_char(ch: str) -> bool:
    return ch in SLUG_KEEP_PUNCTUATION or unicodedata.category(ch)[0] in SLUG_KEEP_CATEGORIES


def slugify(text: str) -> str:
    """GitHub's heading anchor: keep letters, marks, digits, space, '-', '_'."""
    kept = "".join(
        ch for ch in text.strip().lower()
        if _is_kept_char(ch)
    )
    return kept.replace(" ", "-")


def heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    for line in visible_lines(path):
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = slugify(match.group(1))
        anchor = base
        while anchor in anchors:
            occurrences[base] = occurrences.get(base, 0) + 1
            anchor = f"{base}-{occurrences[base]}"
        anchors.add(anchor)
    return anchors


def anchor_refs(text: str) -> list[tuple[str, str, str]]:
    """(path part, fragment, raw target) for every link carrying a fragment."""
    refs: list[tuple[str, str, str]] = []
    for raw in link_targets(text):
        target = strip_title(raw)
        path_part, sep, fragment = target.partition("#")
        if not sep or not fragment:
            continue
        if path_part and should_skip(target):
            continue
        refs.append((path_part, fragment, raw))
    return refs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    failures: list[str] = []

    markdown_files = iter_markdown_files(root)
    scanned = {md.resolve() for md in markdown_files}
    anchors_by_path: dict[Path, set[str]] = {}

    def anchors_of(path: Path) -> set[str]:
        if path not in anchors_by_path:
            anchors_by_path[path] = heading_anchors(path)
        return anchors_by_path[path]

    def report(md: Path, reason: str, raw: str) -> str:
        return f"{md.relative_to(root)}: {reason}: {raw}"

    for md in markdown_files:
        text = "\n".join(visible_lines(md))
        for raw in link_targets(text):
            resolved = target_path(md, raw)
            if resolved is None:
                continue
            try:
                resolved.relative_to(root)
            except ValueError:
                failures.append(report(md, "link escapes repository", raw))
                continue
            if not resolved.exists():
                failures.append(report(md, "missing link target", raw))

        for path_part, fragment, raw in anchor_refs(text):
            document = resolve_document(md, path_part)
            if document not in scanned:
                continue
            if unquote(fragment).lower() not in anchors_of(document):
                failures.append(report(md, "missing heading anchor", raw))

    if failures:
        print("Markdown link check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Markdown local links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
