"""検証用ブランチ の組み立ての判定（純粋な関数。#3572 の決定 9）。

| 関数 | 決めること |
| --- | --- |
| `included` | 含める PR（下書きでなく、head が検証用ブランチ でも版上げの PR でもなく、head が同じリポジトリのもの。番号の順） |
| `version_of` | 検証の版 `<main の版>-next.<組んだ時刻 UTC の YYYYMMDDHHMMSS>`（版が変わらない ref の移動は利用者のキャッシュに残るため） |
| `marketplace_of` | `marketplace.json` の名前を 検証用マーケットプレイスの名前へ書き換えた中身 |
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

from ndf.domains.release import version_decide

_STAMP = "%Y%m%d%H%M%S"


class OpenPull(NamedTuple):
    """main 宛ての開いた PR（`head_repo` は head のリポジトリの `<所有者>/<名前>`）。"""

    number: int
    head_sha: str
    head_ref: str
    head_repo: str
    draft: bool


def included(pulls: Sequence[OpenPull], verify_ref: str, repo: str) -> list[OpenPull]:
    """検証用ブランチ に含める PR を番号の順に返す（フォークの PR は含めない）。"""
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
