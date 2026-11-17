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
# 見出しのキーの末尾から落とす区切り（`## [x 1.2.3] - {date}` の ` - `）
_KEY_TAIL = " -–—"


class FormatMismatch(version_decide.VersionError):
    """名前が書式に合わない（リリースブランチでない、など）。"""


def _fill(fmt: str, unit: str, version: str, date: str = "") -> str:
    return fmt.replace(PLUGIN, unit).replace(VERSION, version).replace(DATE, date)


def _pattern(fmt: str, unit: str) -> re.Pattern[str]:
    """書式の置き換えの語の外をエスケープし、語を形に置き換えた全体一致の式（名前付きの組 `version` を持つ）。"""
    parts: list[str] = []
    named = False
    at = 0
    for m in _PLACEHOLDER.finditer(fmt):
        parts.append(re.escape(fmt[at : m.start()]))
        word = m.group(0)
        if word == VERSION:
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

    def version_of_tag(self, unit: str, name: str) -> Optional[str]:
        """タグの名前がその単位の書式に全体一致すればバージョン。合わなければ None。"""
        return None if self.tag is None else _read(self.tag, unit, name)

    def branch_of(self, version: str) -> str:
        """リリースブランチの名前。"""
        return _fill(self.branch, "", str(version_decide.parse(version)))

    def version_of_branch(self, name: str) -> str:
        """リリースブランチの名前のバージョン。書式に合わなければ `FormatMismatch`。"""
        got = _read(self.branch, "", name)
        if got is None:
            raise FormatMismatch(f"リリースブランチ（{self.branch}）でない: {name!r}")
        return got

    def is_release_branch(self, name: str) -> bool:
        """名前がリリースブランチの書式に合うか。"""
        return _read(self.branch, "", name) is not None

    def heading_of(self, unit: str, version: str, date: str) -> str:
        """CHANGELOG のバージョンの見出し。"""
        return _fill(self.changelog_heading, unit, version_decide.base_of(version), date)

    def heading_key(self, unit: str, version: str) -> str:
        """見出しの、日付から後ろを除いた先頭（セクションを探すキー）。書式に `{date}` が無ければ見出しの全体。"""
        head = self.changelog_heading.split(DATE, 1)[0]
        key = _fill(head, unit, version_decide.base_of(version))
        return key.rstrip(_KEY_TAIL) if DATE in self.changelog_heading else key


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
