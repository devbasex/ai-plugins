#!/usr/bin/env python3
"""Check repository-relative Markdown links.

External URLs, mailto links, and absolute filesystem paths are ignored. This
keeps the check stable in CI while still catching broken links between
repository documents.

Links and headings are read by `plugins/ndf/src/ndf/adapters/markdown.py` (CommonMark),
so code spans, code fences, and block quotes hold no checked link. Heading
references (`#name` and `other.md#name`) are matched against the headings of
the target document, named by GitHub's rule (#445). Documents outside the
scanned set are not read for headings. Links in the Skills a plugin ships
(`plugins/*/manifests/skills.json`) must stay inside that plugin, since the
installed copy holds only the plugin directory (#3910).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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


def _git_markdown_files(root: Path) -> list[Path] | None:
    """Tracked and not-ignored new `.md` files when `root` is a git top level, else None."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return None
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *DEFAULT_SCAN_TARGETS],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return None
    paths = (root / rel for rel in listed.stdout.split("\0") if rel.endswith(".md"))
    return [path for path in paths if path.is_file()]


def iter_markdown_files(root: Path) -> list[Path]:
    """Markdown files under the scan targets; in a git repository, ignored files are left out."""
    tracked = _git_markdown_files(root)
    if tracked is not None:
        return sorted(set(tracked))
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
    return md.blockquote_lines(text)


def _hrefs_in(html: str) -> list[str]:
    """`<a href>` destinations in an HTML fragment."""
    return [m.group(1) for m in INLINE_HTML_RE.finditer(html)]


def _html_hrefs(text: str, quoted: set[int]) -> list[str]:
    """`<a href>` in HTML blocks and inline HTML (code spans and fences hold no HTML tokens)."""
    return [href for fragment in md.html_fragments(text) if fragment.line not in quoted for href in _hrefs_in(fragment.html)]


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
    return _anchor_refs_of(link_targets(text))


def _anchor_refs_of(targets: list[str]) -> list[tuple[str, str, str]]:
    """(path part, fragment, raw target) for the link destinations that carry a fragment."""
    refs: list[tuple[str, str, str]] = []
    for raw in targets:
        path_part, fragment, target = parse_target(raw)
        if not fragment:
            continue
        if path_part and should_skip(target):
            continue
        refs.append((path_part, fragment, raw))
    return refs


def _target_failures(doc: Path, targets: list[str], root: Path) -> list[tuple[str, str]]:
    """(reason, raw) for links whose target escapes `root` or does not exist."""
    failures: list[tuple[str, str]] = []
    for raw in targets:
        resolved = target_path(doc, raw)
        if resolved is None:
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            failures.append(("link escapes repository", raw))
            continue
        if not resolved.exists():
            failures.append(("missing link target", raw))
    return failures


def shipped_skill_dirs(root: Path) -> dict[Path, Path]:
    """Skill directory -> plugin root, for the Skills each `plugins/*/manifests/skills.json` ships (#3910)."""
    dirs: dict[Path, Path] = {}
    for manifest in sorted(root.glob("plugins/*/manifests/skills.json")):
        plugin = manifest.parent.parent.resolve()
        for skill in json.loads(manifest.read_text(encoding="utf-8")).get("skills", []):
            dirs[(plugin / "skills" / skill["name"]).resolve()] = plugin
    return dirs


def _plugin_of(doc: Path, skill_dirs: dict[Path, Path]) -> Path | None:
    """The plugin root when `doc` belongs to a shipped Skill, else None."""
    resolved = doc.resolve()
    for skill_dir, plugin in skill_dirs.items():
        if resolved.is_relative_to(skill_dir):
            return plugin
    return None


def _plugin_failures(doc: Path, targets: list[str], skill_dirs: dict[Path, Path]) -> list[tuple[str, str]]:
    """(reason, raw) for links of a shipped Skill that leave its plugin; the installed copy holds only the plugin."""
    plugin = _plugin_of(doc, skill_dirs)
    if plugin is None:
        return []
    failures: list[tuple[str, str]] = []
    for raw in targets:
        resolved = target_path(doc, raw)
        if resolved is not None and not resolved.is_relative_to(plugin):
            failures.append(("link escapes plugin", raw))
    return failures


def _anchor_failures(doc: Path, targets: list[str], scanned: set[Path], anchors_by_path: dict[Path, set[str]]) -> list[tuple[str, str]]:
    """(reason, raw) for heading references to scanned documents that lack the heading. `anchors_by_path` caches headings."""
    failures: list[tuple[str, str]] = []
    for path_part, fragment, raw in _anchor_refs_of(targets):
        document = resolve_document(doc, path_part)
        if document not in scanned:
            continue
        if document not in anchors_by_path:
            anchors_by_path[document] = heading_anchors(document)
        if unquote(fragment).lower() not in anchors_by_path[document]:
            failures.append(("missing heading anchor", raw))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    failures: list[str] = []

    markdown_files = iter_markdown_files(root)
    scanned = {doc.resolve() for doc in markdown_files}
    anchors_by_path: dict[Path, set[str]] = {}
    skill_dirs = shipped_skill_dirs(root)

    for doc in markdown_files:
        targets = link_targets(doc.read_text(encoding="utf-8"))
        found = (
            _target_failures(doc, targets, root)
            + _plugin_failures(doc, targets, skill_dirs)
            + _anchor_failures(doc, targets, scanned, anchors_by_path)
        )
        failures.extend(f"{doc.relative_to(root)}: {reason}: {raw}" for reason, raw in found)

    if failures:
        print("Markdown link check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Markdown local links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
