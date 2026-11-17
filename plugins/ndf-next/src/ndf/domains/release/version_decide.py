"""バージョンの計算（純粋な関数）。

バージョンの形は Semantic Versioning 2.0.0（`semver` で読む）で、開発版は基底のバージョンに接尾辞 `-dev.<n>` を付けた形である。

| チャネル | 次のバージョン |
| --- | --- |
| 本番（`production`） | 今のバージョンの基底を、上げる幅（`major`・`minor`・`patch`）で上げたバージョン。今のバージョンが開発版なら、その基底をそのまま出す |
| 開発版（`dev`） | 本番の次のバージョンに `-dev.<n>`。今のバージョンが同じ基底の開発版なら `n` を 1 つ進め、ほかは 1 から始める |

出し済みのバージョン（本番ブランチのバージョンとタグのバージョンのうち大きい方）より大きくない次のバージョンは、出し済みのバージョンを同じ幅で上げたバージョンへ
繰り上げる（`clear_of`。既にある版数で組まない。#2554）。

リリースのブランチの名前・タグの名前もここでバージョンから作る（作り方を 1 か所に置く）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple, Optional

import semver

# 上げる幅（`semver.Version.next_version` の part の名前の先頭 3 つ）
LEVELS = semver.Version.NAMES[:3]
# チャネル
PRODUCTION, DEV = "production", "dev"
CHANNELS = (PRODUCTION, DEV)
# 開発版の接尾辞の名前（`-dev.<n>`）
DEV_LABEL = "dev"
# リリースのブランチの接頭辞（`release/v<バージョン>`）
BRANCH_PREFIX = "release/v"
# タグの名前の区切り（`<プラグイン名>--v<バージョン>`。今のタグの形と同じ）
TAG_SEPARATOR = "--v"


class VersionError(ValueError):
    """バージョンとして読めない・上げる幅やチャネルが知らない値。"""


class Planned(NamedTuple):
    """1 つのプラグインの版上げの計画。"""

    plugin: str
    current: str
    next: str


def parse(text: str) -> semver.Version:
    """バージョンを読む。読めなければ `VersionError`。"""
    try:
        return semver.Version.parse(text.strip())
    except (ValueError, TypeError) as exc:
        raise VersionError(f"バージョンとして読めない: {text!r}（{exc}）") from None


def base_of(text: str) -> str:
    """接尾辞を除いた基底のバージョン（`1.2.3-dev.4` → `1.2.3`）。"""
    v = parse(text)
    return str(semver.Version(v.major, v.minor, v.patch))


def is_dev(text: str) -> bool:
    """接尾辞のあるバージョン（開発版）か。"""
    return parse(text).prerelease is not None


def _dev_number(v: semver.Version) -> int:
    """開発版の番号（`dev.<n>` の n）。開発版でなければ 0。"""
    label, _, number = (v.prerelease if v.prerelease is not None else "").partition(".")
    return int(number) if label == DEV_LABEL and number.isdigit() else 0


def production_next(current: str, level: str) -> str:
    """本番の次のバージョン。今のバージョンが開発版なら、その基底（開発版が先に上げた幅を使う）。"""
    if level not in LEVELS:
        raise VersionError(f"上げる幅は {', '.join(LEVELS)} のどれか（渡された値: {level!r}）")
    v = parse(current)
    if v.prerelease is not None:
        return base_of(current)
    return str(v.next_version(level))


def dev_next(current: str, level: str) -> str:
    """開発版の次のバージョン。今のバージョンが開発版なら基底を保って番号を 1 つ進め、本番のバージョンなら上げた基底の `dev.1`。"""
    v = parse(current)
    if v.prerelease is not None:
        return f"{base_of(current)}-{DEV_LABEL}.{_dev_number(v) + 1}"
    return f"{production_next(current, level)}-{DEV_LABEL}.1"


def next_version(current: str, level: str, channel: str) -> str:
    """チャネルに合わせた次のバージョン。"""
    if channel == PRODUCTION:
        return production_next(current, level)
    if channel == DEV:
        return dev_next(current, level)
    raise VersionError(f"チャネルは {', '.join(CHANNELS)} のどれか（渡された値: {channel!r}）")


def branch_of(version: str) -> str:
    """リリースのブランチの名前。"""
    return f"{BRANCH_PREFIX}{parse(version)}"


def version_of_branch(branch: str) -> str:
    """リリースのブランチの名前からバージョンを読む。リリースのブランチでなければ `VersionError`。"""
    if not branch.startswith(BRANCH_PREFIX):
        raise VersionError(f"リリースのブランチ（{BRANCH_PREFIX}<バージョン>）でない: {branch!r}")
    return str(parse(branch[len(BRANCH_PREFIX) :]))


def tag_of(plugin: str, version: str) -> str:
    """基底のバージョンのタグの名前。"""
    return f"{plugin}{TAG_SEPARATOR}{base_of(version)}"


def version_of_tag(plugin: str, tag: str) -> Optional[str]:
    """そのプラグインのタグ（`<プラグイン名>--v<バージョン>`）のバージョン。そのプラグインのタグでない・バージョンとして読めなければ None。"""
    prefix = f"{plugin}{TAG_SEPARATOR}"
    text = tag.removeprefix(prefix)
    return text if text != tag and semver.Version.is_valid(text) else None


def highest(versions: Iterable[str]) -> Optional[str]:
    """バージョンのうち、基底が最も大きいものの基底（無ければ None）。"""
    bases = [parse(base_of(v)) for v in versions]
    return str(max(bases)) if bases else None


def clear_of(planned: str, shipped: Optional[str], level: str, channel: str) -> str:
    """次のバージョン `planned` を、出し済みのバージョン `shipped` より大きくする（#2554）。

    `planned` の基底が `shipped` より大きければそのまま。大きくなければ、`shipped` を上げる幅で上げたバージョン（開発版の
    チャネルなら、それに `-dev.1`）へ繰り上げる。`shipped` が None（まだ出していない）ならそのまま。
    """
    if shipped is None or parse(base_of(planned)) > parse(base_of(shipped)):
        return planned
    raised = production_next(base_of(shipped), level)
    return raised if channel == PRODUCTION else f"{raised}-{DEV_LABEL}.1"
