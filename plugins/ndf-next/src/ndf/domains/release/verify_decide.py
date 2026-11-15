"""検証用ブランチの組み立ての判定（純粋な関数。#3572 の決定 9）。

| 関数 | 決めること |
| --- | --- |
| `included` | 含める PR（下書きでなく、head が検証用ブランチでも版上げの PR でもなく、head が同じリポジトリのもの。番号の順） |
| `version_of` | 検証の版 `<main の版>-next.<組んだ時刻 UTC の YYYYMMDDHHMMSS>`（版が変わらない ref の移動は利用者のキャッシュに残るため） |
| `marketplace_of` | `marketplace.json` の名前を検証用マーケットプレイスの名前へ書き換えた中身（種類 `marketplace`） |
| `command_of` | 種類 `deploy` のコマンドの `{ref}`・`{head}` を置き換えた引数の並び（#3572 の決定 21） |
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

from ndf.domains.release import version_decide

_STAMP = "%Y%m%d%H%M%S"
# 種類 `deploy` のコマンドで置き換える語（検証用ブランチの名前と組んだ head）
PLACEHOLDERS = ("{ref}", "{head}")


class OpenPull(NamedTuple):
    """main 宛ての開いた PR（`head_repo` は head のリポジトリの `<所有者>/<名前>`）。"""

    number: int
    head_sha: str
    head_ref: str
    head_repo: str
    draft: bool


def included(pulls: Sequence[OpenPull], verify_ref: str, repo: str) -> list[OpenPull]:
    """検証用ブランチに含める PR を番号の順に返す（フォークの PR は含めない）。"""
    kept = [
        p
        for p in pulls
        if not p.draft and p.head_ref != verify_ref and not p.head_ref.startswith(version_decide.BRANCH_PREFIX) and p.head_repo == repo
    ]
    return sorted(kept, key=lambda p: p.number)


def version_of(main_version: str, at: datetime) -> str:
    """検証の版（`main_version` は接尾辞の無い版。版でなければ `VersionError`）。"""
    version_decide.parse(main_version)
    return f"{version_decide.base_of(main_version)}-next.{at.strftime(_STAMP)}"


def marketplace_of(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    """`marketplace.json` の中身の `name` だけを `name` にした写し（ほかの鍵は元のまま）。"""
    return {**raw, "name": name}


def command_of(command: Sequence[str], ref: str, head: str) -> list[str]:
    """種類 `deploy` のコマンドの各要素の `{ref}`・`{head}` だけを置き換える（要素の数と並びを変えない。ほかの `{...}` は残す）。"""
    values = dict(zip(PLACEHOLDERS, (ref, head)))
    out = []
    for part in command:
        for word, value in values.items():
            part = part.replace(word, value)
        out.append(part)
    return out
