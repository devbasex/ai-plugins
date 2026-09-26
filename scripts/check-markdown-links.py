#!/usr/bin/env python3
"""Check repository-relative Markdown links.

External URLs, mailto links, and absolute filesystem paths are ignored. This
keeps the check stable in CI while still catching broken links between
repository documents.

Links and headings are read by `plugins/ndf/scripts/lib/md.py` (CommonMark),
so code spans, code fences, and block quotes hold no checked link. Heading
references (`#name` and `other.md#name`) are matched against the headings of
the target document, named by GitHub's rule (#445). Documents outside the
scanned set are not read for headings.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("md")
import md  # noqa: E402  Markdown の構造は lib/md.py（markdown-it-py）で読む

INLINE_HTML_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"']", re.IGNORECASE)
DEFAULT_SCAN_TARGETS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "KIRO.md",
    "docs",
    "plugins",
    "issues",
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


def parse_target(raw: str) -> tuple[str, str, str]:
    """Split a link destination into (path part, fragment, stripped target)."""
    target = raw.strip()
    path_part, _, fragment = target.partition("#")
    return path_part, fragment, target


def _resolvable_path_part(raw: str) -> str | None:
    """The path part to resolve, or None when the link names no other document."""
    path_part, _fragment, target = parse_target(raw)
    if should_skip(target):
        return None
    if not path_part:
        return None
    return path_part


def target_path(source: Path, raw_target: str) -> Path | None:
    path_part = _resolvable_path_part(raw_target)
    if path_part is None:
        return None
    return resolve_document(source, path_part)


def quoted_lines(text: str) -> set[int]:
    """Line numbers (0-based) inside block quotes; links and headings there are not checked."""
    lines: set[int] = set()
    for token in md.md_tokens(text):
        if token.type == "blockquote_open" and token.map:
            lines.update(range(token.map[0], token.map[1]))
    return lines


def _html_hrefs(text: str, quoted: set[int]) -> list[str]:
    """`<a href>` in HTML blocks and inline HTML (code spans and fences hold no HTML tokens)."""
    hrefs: list[str] = []
    for token in md.md_tokens(text):
        if not token.map or token.map[0] in quoted:
            continue
        if token.type == "html_block":
            hrefs.extend(m.group(1) for m in INLINE_HTML_RE.finditer(token.content))
        elif token.type == "inline":
            for child in token.children or []:
                if child.type == "html_inline":
                    hrefs.extend(m.group(1) for m in INLINE_HTML_RE.finditer(child.content))
    return hrefs


def link_targets(text: str) -> list[str]:
    """Markdown link destinations first, then `<a href>`; images, code, and block quotes are left out."""
    quoted = quoted_lines(text)
    targets = [link.href for link in md.links(text) if link.line not in quoted]
    targets.extend(_html_hrefs(text, quoted))
    return targets


def heading_anchors(path: Path) -> set[str]:
    """GitHub anchors of the document's headings outside block quotes (`lib/md.py` numbers duplicates)."""
    text = path.read_text(encoding="utf-8")
    quoted = quoted_lines(text)
    return {h.anchor for h in md.headings(text) if h.line not in quoted}


def anchor_refs(text: str) -> list[tuple[str, str, str]]:
    """(path part, fragment, raw target) for every link carrying a fragment."""
    refs: list[tuple[str, str, str]] = []
    for raw in link_targets(text):
        path_part, fragment, target = parse_target(raw)
        if not fragment:
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
    scanned = {doc.resolve() for doc in markdown_files}
    anchors_by_path: dict[Path, set[str]] = {}

    def anchors_of(path: Path) -> set[str]:
        if path not in anchors_by_path:
            anchors_by_path[path] = heading_anchors(path)
        return anchors_by_path[path]

    def report(doc: Path, reason: str, raw: str) -> str:
        return f"{doc.relative_to(root)}: {reason}: {raw}"

    for doc in markdown_files:
        text = doc.read_text(encoding="utf-8")
        for raw in link_targets(text):
            resolved = target_path(doc, raw)
            if resolved is None:
                continue
            try:
                resolved.relative_to(root)
            except ValueError:
                failures.append(report(doc, "link escapes repository", raw))
                continue
            if not resolved.exists():
                failures.append(report(doc, "missing link target", raw))

        for path_part, fragment, raw in anchor_refs(text):
            document = resolve_document(doc, path_part)
            if document not in scanned:
                continue
            if unquote(fragment).lower() not in anchors_of(document):
                failures.append(report(doc, "missing heading anchor", raw))

    if failures:
        print("Markdown link check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Markdown local links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
