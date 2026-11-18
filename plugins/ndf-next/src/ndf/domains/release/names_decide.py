"""タグ・リリースブランチ・CHANGELOG の見出しの名前を、設定の書式から作り、名前から読み戻す（純粋な関数。#3615 の決定 4）。

書式は設定 `.ndf/project.json` の `branch_strategy.release` の 3 つのキーで受ける。置き換えの語は `{plugin}`（バージョンの単位の名前）・
`{version}`（バージョン）・`{date}`（`YYYY-MM-DD`。見出しだけ）である。

| キー | 既定 | 例 |
| --- | --- | --- |
| `tag` | 無し（必須のキー。`null` はタグを作らない） | `v{version}`・`{plugin}@{version}` |
| `branch` | `DEFAULT_BRANCH` | `rel-{version}` |
| `changelog_heading` | `DEFAULT_HEADING` | `## [{version}] - {date}`（Keep a Changelog） |

名前を読み戻すときは、置き換えの語の外を正規表現の文字としてエスケープし、全体一致で読む。書式に合わない名前は読まない（I3）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

import semver

from ndf.domains.release import version_decide

# リリースブランチの名前の既定（設定のスキーマの `branch` の `default` と同じ値。読むのはこの定数）
DEFAULT_BRANCH = "release/v{version}"
# CHANGELOG の見出しの既定
DEFAULT_HEADING = "## [{plugin} {version}] - {date}"
# 置き換えの語
VERSION, PLUGIN, DATE = "{version}", "{plugin}", "{date}"
# 置き換えの語 → 読み戻す形（`{plugin}` は単位の名前をエスケープして入れるのでここに持たない）
_VERSION_FORM = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
_DATE_FORM = r"\d{4}-\d{2}-\d{2}"
_PLACEHOLDER = re.compile(r"\{(?:version|plugin|date)\}")


class FormatMismatch(version_decide.VersionError):
    """名前が書式に合わない（リリースブランチでない、など）。"""


def _fill(fmt: str, unit: str, version: str, date: str = "") -> str:
    return fmt.replace(PLUGIN, unit).replace(VERSION, version).replace(DATE, date)


def _pattern(fmt: str, unit: str, version: Optional[str] = None) -> re.Pattern[str]:
    """書式の置き換えの語の外をエスケープし、語を形に置き換えた全体一致の式（`version` を省けば名前付きの組 `version` を持ち、
    渡せばそのバージョンだけに当たる）。"""
    parts: list[str] = []
    named = False
    at = 0
    for m in _PLACEHOLDER.finditer(fmt):
        parts.append(re.escape(fmt[at : m.start()]))
        word = m.group(0)
        if word == VERSION and version is not None:
            parts.append(re.escape(version))
        elif word == VERSION:
            parts.append("(?P=version)" if named else f"(?P<version>{_VERSION_FORM})")
            named = True
        elif word == PLUGIN:
            parts.append(re.escape(unit))
        else:
            parts.append(_DATE_FORM)
        at = m.end()
    parts.append(re.escape(fmt[at:]))
    return re.compile("".join(parts))


def _read(fmt: str, unit: str, name: str) -> Optional[str]:
    m = _pattern(fmt, unit).fullmatch(name)
    if m is None or "version" not in m.groupdict() or not semver.Version.is_valid(m.group("version")):
        return None
    return m.group("version")


@dataclass(frozen=True)
class NameFormats:
    """名前の書式。`tag` が None ならタグを作らない。"""

    tag: Optional[str]
    branch: str = DEFAULT_BRANCH
    changelog_heading: str = DEFAULT_HEADING

    def tag_of(self, unit: str, version: str) -> Optional[str]:
        """単位と基底のバージョンのタグの名前（タグを作らない書式なら None）。"""
        if self.tag is None:
            return None
        return _fill(self.tag, unit, version_decide.base_of(version))

    def require_unit_tags(self, units: Iterable[str]) -> None:
        """バージョンの単位が 2 つ以上なら、タグの書式が単位を見分けられる（`{plugin}` を含む）ことを求める。
        見分けられない書式では別の単位のタグを自分の前のリリースとして読むので `FormatMismatch`。"""
        names = sorted(set(units))
        if self.tag is not None and len(names) > 1 and PLUGIN not in self.tag:
            raise FormatMismatch(
                f"バージョンの単位が {len(names)} 個（{', '.join(names)}）あるのに、タグの書式 {self.tag} が {PLUGIN} を含まない"
            )

    def version_of_tag(self, unit: str, name: str) -> Optional[str]:
        """タグの名前がその単位の書式に全体一致すればバージョン。合わなければ None。"""
        return None if self.tag is None else _read(self.tag, unit, name)

    def branch_of(self, version: str) -> str:
        """リリースブランチの名前。"""
        return _fill(self.branch, "", str(version_decide.parse(version)))

    def branch_version(self, name: str) -> Optional[str]:
        """名前がリリースブランチの書式に合えばそのバージョン。合わなければ None。"""
        return _read(self.branch, "", name)

    def version_of_branch(self, name: str) -> str:
        """リリースブランチの名前のバージョン。書式に合わなければ `FormatMismatch`。"""
        got = self.branch_version(name)
        if got is None:
            raise FormatMismatch(f"リリースブランチ（{self.branch}）でない: {name!r}")
        return got

    def is_release_branch(self, name: str) -> bool:
        """名前がリリースブランチの書式に合うか。"""
        return self.branch_version(name) is not None

    def heading_of(self, unit: str, version: str, date: str) -> str:
        """CHANGELOG のバージョンの見出し。"""
        return _fill(self.changelog_heading, unit, version_decide.base_of(version), date)

    def heading_pattern(self, unit: str, version: str) -> re.Pattern[str]:
        """CHANGELOG のそのバージョンの見出しに全体一致する式（`{date}` は日付の形。セクションを探すのに使う）。"""
        return _pattern(self.changelog_heading, unit, version_decide.base_of(version))


def formats_of(release: Mapping[str, Any]) -> NameFormats:
    """設定の `release` のセクションの書式（`branch`・`changelog_heading` が無ければ既定）。"""
    named = {key: release[key] for key in ("branch", "changelog_heading") if key in release}
    return NameFormats(release["tag"], **named)


def highest_tag(formats: NameFormats, unit: str, tags: Iterable[str]) -> Optional[tuple[str, str]]:
    """その単位の書式に合い、接尾辞の無い最も大きいバージョンの (タグ, バージョン)。無ければ None。"""
    found = []
    for name in tags:
        got = formats.version_of_tag(unit, name)
        if got is not None and not version_decide.is_dev(got):
            found.append((version_decide.parse(got), name))
    if not found:
        return None
    top, name = max(found)
    return name, str(top)
