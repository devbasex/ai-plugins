"""版数の比較の包み（#1142 の決定 19・種類 9）。semver を呼ぶのはこのモジュールだけである。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の検査（`check-doc-staleness.py`）と
`token-usage.py` が使う比較（`version_order`）だけを持つ。版の書き換えはプラグインの側に残る。

規則は SemVer 2.0 である（packaging の PEP 440 は規則が合わない）。このリポジトリの版の形は `X.Y.Z`・
`X.Y.Z-dev.N`・`X.Y.Z-rc.N` で、同じ `X.Y.Z` では `-dev.N` < `-rc.N` < 接尾辞なし、`dev.10` は `dev.9` より後である
（接尾辞の数の要素は数として比べる）。

使う側は `ndf_wrappers.require("versions")` を先に呼ぶ。
"""

from __future__ import annotations


import semver

PRERELEASES = ("dev", "rc")


def parse_version(v: str | None) -> semver.Version | None:
    """`X.Y.Z[-dev.N|-rc.N]`（先頭の `v` は許す）を読む。形が違えば None。"""
    s = (v or "").strip()
    if s.startswith("v"):
        s = s[1:]
    try:
        ver = semver.Version.parse(s)
    except (ValueError, TypeError):
        return None
    if ver.build:
        return None
    if ver.prerelease:
        label, _, num = ver.prerelease.partition(".")
        if label not in PRERELEASES or not num.isdigit():
            return None
    return ver


def version_order(v: str | None) -> semver.Version | None:
    """並べ替えの鍵（`sorted(vs, key=...)` は None を含まない列に使う）。読めなければ None。"""
    return parse_version(v)
