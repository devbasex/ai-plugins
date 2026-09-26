"""版数の比較と一括の書き換えの包み（#1142 の決定 19・種類 9）。semver と bump-my-version を呼ぶのはこのモジュールだけである。

版を付ける対象はプラグインのマニフェストで、規則は SemVer 2.0 である（packaging の PEP 440 は規則が合わない）。
このリポジトリの版の形は `X.Y.Z`・`X.Y.Z-dev.N`・`X.Y.Z-rc.N` で、同じ `X.Y.Z` では `-dev.N` < `-rc.N` <
接尾辞なし、`dev.10` は `dev.9` より後である（接尾辞の数の要素は数として比べる）。

- 比較: `version_order(v)`（並べ替えの鍵。読めなければ None）・`compare_versions(a, b)`
- 形: `release_base(v)`（`X.Y.Z`）・`dev_number(v)`・`next_dev(v)`・`next_patch(v)`
- 書き換え: `bump_replace(config, current, new, cwd)` が bump-my-version の `replace` を 1 回流す
  （版数を持つ箇所の表は `config` の TOML が持つ。手で直す箇所の報告と check-doc-staleness.py は呼び出し側に残る）

比較だけを使う側は `deps.require("versions")`、書き換えも使う側は加えて `deps.require("bump")` を先に呼ぶ。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import semver

PRERELEASES = ("dev", "rc")


class BumpResult(NamedTuple):
    ok: bool
    returncode: int
    output: str      # 標準出力と標準エラーの末尾（失敗の理由を読むため）


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


def compare_versions(a: str, b: str) -> int:
    """a < b なら負、同じなら 0、a > b なら正。読めない版は `ValueError`。"""
    va, vb = parse_version(a), parse_version(b)
    if va is None or vb is None:
        raise ValueError(f"版の形が違う: {a if va is None else b}（X.Y.Z / X.Y.Z-dev.N / X.Y.Z-rc.N）")
    return va.compare(vb)


def release_base(v: str) -> str:
    """接尾辞を外した `X.Y.Z`。"""
    ver = parse_version(v)
    if ver is None:
        raise ValueError(f"版の形が違う: {v}")
    return str(ver.finalize_version())


def dev_number(v: str) -> int | None:
    """`-dev.N` の N。開発版でなければ None。"""
    ver = parse_version(v)
    if ver is None or not ver.prerelease or not ver.prerelease.startswith("dev."):
        return None
    return int(ver.prerelease.split(".", 1)[1])


def next_dev(v: str) -> str:
    """次の開発版。`-dev.N` は `-dev.N+1`、正式版 `X.Y.Z` は `X.Y.(Z+1)-dev.1`。"""
    n = dev_number(v)
    if n is not None:
        return f"{release_base(v)}-dev.{n + 1}"
    ver = parse_version(v)
    if ver is None:
        raise ValueError(f"版の形が違う: {v}")
    if ver.prerelease:
        raise ValueError(f"-rc の版からは開発版を作らない: {v}")
    return f"{ver.bump_patch()}-dev.1"


def next_patch(v: str) -> str:
    """次の正式版。接尾辞付きはその基底（`10.17.31-dev.2` → `10.17.31`）、正式版は PATCH を上げる。"""
    ver = parse_version(v)
    if ver is None:
        raise ValueError(f"版の形が違う: {v}")
    return str(ver.finalize_version()) if ver.prerelease else str(ver.bump_patch())


def _bump_cli() -> list[str]:
    beside = Path(sys.executable).parent / "bump-my-version"
    if beside.is_file() and os.access(beside, os.X_OK):
        return [str(beside)]
    found = shutil.which("bump-my-version")
    return [found] if found else [sys.executable, "-m", "bumpversion"]


def bump_replace(config: os.PathLike[str] | str, current: str, new: str, cwd: os.PathLike[str] | str) -> BumpResult:
    """bump-my-version の `replace` で `config` の箇所を `current` から `new` へ書き換える（コミットもタグもしない）。"""
    for v in (current, new):
        if parse_version(v) is None:
            raise ValueError(f"版の形が違う: {v}")
    cmd = [*_bump_cli(), "replace", "--config-file", os.fspath(config), "--current-version", current,
           "--new-version", new, "--allow-dirty"]
    try:
        p = subprocess.run(cmd, cwd=os.fspath(cwd), capture_output=True, text=True)
    except OSError as exc:
        return BumpResult(False, 127, f"bump-my-version を起動できない: {exc}")
    return BumpResult(p.returncode == 0, p.returncode, (p.stdout + p.stderr)[-2000:])
