"""リリース記録のコメントの本文を読む（純粋な関数。#3487）。

記録を組むのは `ndf.domains.release.record_decide` で、読むのはリリースのドメインと、手で終えるデプロイの工程の
確かめ（`ndf.progress`。#4520）の両方なので、読む側はここに置く。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, NamedTuple

from ndf import identity

# リリース記録のマーカー
RECORD_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:release-record -->"
# 本文の JSON のコードブロック（マーカーの後の最初のもの）
_JSON_BLOCK = re.compile(r"^```json\n(?P<body>.*?)\n```$", re.MULTILINE | re.DOTALL)


class UnreadableRecord(ValueError):
    """マーカーを持つコメントの JSON のコードブロックが JSON として読めない（書き手が本文を手で壊した）。"""


class Record(NamedTuple):
    """リリース記録 1 件。`included` は含めた PR の番号（小さい順）。`unit` は単位が 2 つ以上のリリースの単位の名前（1 つなら None）。"""

    version: str
    release: int
    included: tuple[int, ...]
    unit: str | None = None

    def as_json(self) -> dict[str, Any]:
        got: dict[str, Any] = {"version": self.version, "release": self.release, "included": list(self.included)}
        if self.unit is not None:
            got["unit"] = self.unit
        return got


def payload_of(body: str, mark: str) -> dict[str, Any] | None:
    """マーカー `mark` を持つコメントの JSON のコードブロック。マーカーかコードブロックが無い・オブジェクトでなければ None。

    マーカーとコードブロックを持つのに JSON として読めなければ `UnreadableRecord`（記録を壊れたまま読み飛ばさない）。
    """
    if mark not in body:
        return None
    m = _JSON_BLOCK.search(body, body.index(mark))
    if m is None:
        return None
    try:
        got = json.loads(m.group("body"))
    except json.JSONDecodeError as exc:
        raise UnreadableRecord(f"{mark} の記録の JSON が読めない: {exc}") from exc
    return got if isinstance(got, dict) else None


def _is_int(value: Any) -> bool:
    """bool でない int か。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _numbers(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, list) or not all(_is_int(n) and n > 0 for n in value):
        return None
    return tuple(value)


def parse_record(body: str) -> Record | None:
    """コメント 1 件をリリース記録として読む。リリース記録でない・形が違えば None。"""
    got = payload_of(body, RECORD_MARK)
    if got is None:
        return None
    version, release, included = got.get("version"), got.get("release"), _numbers(got.get("included"))
    unit = got["unit"] if "unit" in got else None
    if not isinstance(version, str) or not version or not _is_int(release) or included is None:
        return None
    if unit is not None and (not isinstance(unit, str) or not unit):
        return None
    return Record(version, release, included, unit)


def records(bodies: Iterable[str]) -> list[Record]:
    """本文の並び（古い順）から読めたリリース記録（古い順）。"""
    return [r for r in (parse_record(b) for b in bodies) if r is not None]
