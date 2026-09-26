"""リリース済みの版の段落の判定（`instructions-check.py` から分けた。#1142 の C7）。

リリース済みの版は `CHANGELOG` の見出しと git のタグから読む。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from instructions_lib.model import BULLET_RE, CheckError, Criteria, FENCE_RE, Finding, HEADING_RE, Target
from instructions_lib.declaration import Declaration
from instructions_lib.collect import _inside_root, display_path


# 版数の形（semver）。数字 3 つと、任意の接尾辞（`.` で割った各要素が空でなく、英数字と
# `-` だけで、数として読める要素に先頭の 0 が無い）。
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
# 行の書き出しに現れる版数。`1.2.3.4` のような別の形へは当たらない。
VERSION_AT_START = re.compile(
    r"^v?(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)(?![0-9A-Za-z.-])")
# 段落の書き出しから読み飛ばす目印（箇条書きの記号と強調）。
LEAD_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?[*_~]*")


# --- 判定: 出た版の段落 ------------------------------------------------------

def semver_key(version: str) -> tuple:
    """semver の順序。**数として読める要素は読めない要素より前**に来る。"""
    base, _, suffix = version.partition("-")
    numbers = tuple(int(part) for part in base.split("."))
    if not suffix:
        # 接尾辞の無い版は、同じ基底の接尾辞付きより後である。
        return (numbers, 1, ())
    parts = []
    for element in suffix.split("."):
        if element.isdigit():
            parts.append((0, int(element), ""))
        else:
            parts.append((1, 0, element))
    return (numbers, 0, tuple(parts))


def base_triple(version: str) -> tuple[int, int, int]:
    base = version.partition("-")[0]
    major, minor, patch = base.split(".")
    return (int(major), int(minor), int(patch))


def _read_changelog_lines(root: Path, raw: str) -> list[str]:
    candidate = os.path.normpath(str(root / raw))
    if not _inside_root(candidate, root):
        raise CheckError(f"released.path が根の外を指す: {raw}")
    try:
        return Path(candidate).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CheckError(f"released.path を読めない: {raw}（{exc}）") from exc


def _read_tag_lines(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "tag"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git tag を読めない: {exc}") from exc
    return result.stdout.splitlines()


def released_versions(root: Path, released: dict) -> list[str]:
    pattern = re.compile(released["pattern"])
    if released["source"] == "changelog":
        lines = _read_changelog_lines(root, released["path"])
    else:
        lines = _read_tag_lines(root)

    versions: list[str] = []
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        value = match.group("version")
        if not VERSION_RE.match(value) or not _valid_suffix(value):
            # その行を読み飛ばさずに止める。判定できないことを通過にしない。
            raise CheckError(f"捕捉した値が版数の形ではない: {value!r}")
        versions.append(value)
    return versions


def _valid_suffix(version: str) -> bool:
    suffix = version.partition("-")[2]
    if not suffix:
        return True
    for element in suffix.split("."):
        if not element or not re.fullmatch(r"[0-9A-Za-z-]+", element):
            return False
        if element.isdigit() and len(element) > 1 and element.startswith("0"):
            return False
    return True


def paragraph_starts(text: str) -> list[tuple[int, str, bool]]:
    """（行番号, 本文, 見出しか）。**段落の先頭行と見出しだけ**を返す。"""
    starts: list[tuple[int, str, bool]] = []
    previous = "blank"
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            previous = "blank" if not in_fence else "fence"
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            previous = "blank"
            continue
        heading = HEADING_RE.match(line)
        if heading:
            starts.append((number, heading.group(2), True))
            previous = "blank"
            continue
        if stripped.startswith("|") or stripped.startswith(">"):
            # 表の行・引用の行は段落の先頭として扱わない。
            previous = "other"
            continue
        if BULLET_RE.match(line) or previous == "blank":
            starts.append((number, LEAD_RE.sub("", line, count=1), False))
        previous = "text"
    return starts


def version_findings(target: Target, text: str, latest: str,
                     decl: Declaration, criteria: Criteria) -> list[Finding]:
    findings: list[Finding] = []
    if not criteria.enabled("released-version-paragraph"):
        return findings
    latest_base = base_triple(latest)
    where = decl.decisions or "退避先の文書"
    reported: set[int] = set()
    for number, body, _is_heading in paragraph_starts(text):
        match = VERSION_AT_START.match(body.strip())
        if not match:
            continue
        version = match.group("version")
        rest = body.strip()[match.end():].lstrip()
        pending = bool(decl.pending_marker) and rest.startswith(decl.pending_marker)
        if pending:
            hit = base_triple(version) < latest_base
            reason = (f"{version} は版が決まる前の段落で、基底が最新（{latest}）未満である")
        else:
            hit = base_triple(version) <= latest_base
            reason = f"{version} の段落が残っている（最新は {latest}）"
        if not hit:
            continue
        findings.append(Finding(
            "released-version-paragraph",
            f"{reason}。次の見出しまでを {where} へ移す",
            target.scope, display_path(target), number, target.source))
        reported.add(number)
    if decl.pending_marker:
        findings.extend(_inline_pending_findings(target, text, latest, decl.pending_marker,
                                                 reported))
    return findings


def _inline_pending_findings(target: Target, text: str, latest: str, marker: str,
                             reported: set[int]) -> list[Finding]:
    """段落の途中の「<版> の次の版で」。その版の次の版が既に出ていれば拾う（#945）。"""
    pattern = re.compile(
        r"(?<![0-9A-Za-z.-])v?(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)"
        r"(?![0-9A-Za-z.-])\s*" + re.escape(marker))
    latest_base = base_triple(latest)
    findings: list[Finding] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or number in reported:
            continue
        for match in pattern.finditer(line):
            version = match.group("version")
            if base_triple(version) >= latest_base:
                continue
            findings.append(Finding(
                "released-version-paragraph",
                f"段落の途中の「{version} {marker}」が指す版は既に出ている（最新は {latest}）。"
                "変更が入った版へ書き換えるか、目印を最新の版へ進める",
                target.scope, display_path(target), number, target.source))
            break
    return findings
