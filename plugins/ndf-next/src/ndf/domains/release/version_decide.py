"""版の計算（純粋な関数）。

版の形は Semantic Versioning 2.0.0（`semver` で読む）で、開発版は基底の版に接尾辞 `-dev.<n>` を付けた形である。

| チャネル | 次の版 |
| --- | --- |
| 本番（`production`） | 今の版の基底を、上げる幅（`major`・`minor`・`patch`）で上げた版。今の版が開発版なら、その基底をそのまま出す |
| 開発版（`dev`） | 本番の次の版に `-dev.<n>`。今の版が同じ基底の開発版なら `n` を 1 つ進め、ほかは 1 から始める |

リリースのブランチの名前・タグの名前・CHANGELOG の見出しもここで版から作る（作り方を 1 か所に置く）。
"""

from __future__ import annotations

from typing import NamedTuple

import semver

# 上げる幅（`semver.Version.next_version` の part の名前の先頭 3 つ）
LEVELS = semver.Version.NAMES[:3]
# チャネル
PRODUCTION, DEV = "production", "dev"
CHANNELS = (PRODUCTION, DEV)
# 開発版の接尾辞の名前（`-dev.<n>`）
DEV_LABEL = "dev"
# リリースのブランチの接頭辞（`release/v<版>`）
BRANCH_PREFIX = "release/v"
# タグの名前の区切り（`<プラグイン名>--v<版>`。今のタグの形と同じ）
TAG_SEPARATOR = "--v"


class VersionError(ValueError):
    """版として読めない・上げる幅やチャネルが知らない値。"""


class Planned(NamedTuple):
    """1 つのプラグインの版上げの計画。"""

    plugin: str
    current: str
    next: str


def parse(text: str) -> semver.Version:
    """版を読む。読めなければ `VersionError`。"""
    try:
        return semver.Version.parse(text.strip())
    except (ValueError, TypeError) as exc:
        raise VersionError(f"版として読めない: {text!r}（{exc}）") from None


def base_of(text: str) -> str:
    """接尾辞を除いた基底の版（`1.2.3-dev.4` → `1.2.3`）。"""
    v = parse(text)
    return str(semver.Version(v.major, v.minor, v.patch))


def is_dev(text: str) -> bool:
    """接尾辞のある版（開発版）か。"""
    return parse(text).prerelease is not None


def _dev_number(v: semver.Version) -> int:
    """開発版の番号（`dev.<n>` の n）。開発版でなければ 0。"""
    label, _, number = (v.prerelease if v.prerelease is not None else "").partition(".")
    return int(number) if label == DEV_LABEL and number.isdigit() else 0


def production_next(current: str, level: str) -> str:
    """本番の次の版。今の版が開発版なら、その基底（開発版が先に上げた幅を使う）。"""
    if level not in LEVELS:
        raise VersionError(f"上げる幅は {', '.join(LEVELS)} のどれか（渡された値: {level!r}）")
    v = parse(current)
    if v.prerelease is not None:
        return base_of(current)
    return str(v.next_version(level))


def dev_next(current: str, level: str) -> str:
    """開発版の次の版。今の版が開発版なら基底を保って番号を 1 つ進め、本番の版なら上げた基底の `dev.1`。"""
    v = parse(current)
    if v.prerelease is not None:
        return f"{base_of(current)}-{DEV_LABEL}.{_dev_number(v) + 1}"
    return f"{production_next(current, level)}-{DEV_LABEL}.1"


def next_version(current: str, level: str, channel: str) -> str:
    """チャネルに合わせた次の版。"""
    if channel == PRODUCTION:
        return production_next(current, level)
    if channel == DEV:
        return dev_next(current, level)
    raise VersionError(f"チャネルは {', '.join(CHANNELS)} のどれか（渡された値: {channel!r}）")


def branch_of(version: str) -> str:
    """リリースのブランチの名前。"""
    return f"{BRANCH_PREFIX}{parse(version)}"


def version_of_branch(branch: str) -> str:
    """リリースのブランチの名前から版を読む。リリースのブランチでなければ `VersionError`。"""
    if not branch.startswith(BRANCH_PREFIX):
        raise VersionError(f"リリースのブランチ（{BRANCH_PREFIX}<版>）でない: {branch!r}")
    return str(parse(branch[len(BRANCH_PREFIX) :]))


def tag_of(plugin: str, version: str) -> str:
    """基底の版のタグの名前。"""
    return f"{plugin}{TAG_SEPARATOR}{base_of(version)}"


def changelog_heading(plugin: str, version: str, date: str) -> str:
    """CHANGELOG の版の見出し（`## [<プラグイン名> <版>] - <日付>`）。"""
    return f"{changelog_key(plugin, version)} - {date}"


def changelog_key(plugin: str, version: str) -> str:
    """CHANGELOG の版の見出しの、日付を除いた先頭（節を探す鍵）。"""
    return f"## [{plugin} {base_of(version)}]"
