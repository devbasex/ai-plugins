"""リリース記録とリリース後テストの記録を組み、読む（純粋な関数。#3487）。

リリース記録は、本番ブランチへ入れた版に含めた PR（承認資料と同じ「含む PR」の範囲）の各々へ書くコメントである。
リリース後テストの記録も同じ PR へ書き、課題を閉じる側は 1 つの PR から両方を読む。

| 記録 | 印 | 本文の JSON |
| --- | --- | --- |
| リリース記録 | `RECORD_MARK` | `version`（v の前置きの無い版数）・`release`（リリースの PR の番号）・`included`（含めた PR の番号） |
| リリース後テストの記録 | `VERIFICATION_MARK` | スキーマ `release-verification` の形（`version`・`issues`） |

どちらも、印の行・見出し・中身の分かる行・JSON のコードブロック（` ```json `）の順で書く。機械が読むのは
JSON のコードブロックだけで、見出しや本文の文から読まない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from ndf import identity

_NS = identity.FIXED_WORDS["workflow_namespace"]
# リリース記録の印と、リリース後テストの記録の印
RECORD_MARK = f"<!-- {_NS}:release-record -->"
VERIFICATION_MARK = f"<!-- {_NS}:release-verification -->"
# 本文の JSON のコードブロック（印の後の最初のもの）
_JSON_BLOCK = re.compile(r"^```json\n(?P<body>.*?)\n```$", re.MULTILINE | re.DOTALL)


class Record(NamedTuple):
    """リリース記録 1 件。`included` は含めた PR の番号（小さい順）。"""

    version: str
    release: int
    included: tuple[int, ...]

    def as_json(self) -> dict[str, Any]:
        return {"version": self.version, "release": self.release, "included": list(self.included)}


def _block(payload: Mapping[str, Any]) -> str:
    return f"```json\n{json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)}\n```"


def render_record(record: Record) -> str:
    """リリース記録のコメントの本文。"""
    listed = ", ".join(f"#{n}" for n in record.included)
    return (
        f"{RECORD_MARK}\n## リリース記録\n\n対象の版: {record.version}\nリリースの PR: #{record.release}\n"
        f"含めた PR: {listed}\n\n{_block(record.as_json())}\n"
    )


def render_verification(payload: Mapping[str, Any]) -> str:
    """リリース後テストの記録のコメントの本文（`payload` はスキーマ `release-verification` で確かめたもの）。"""
    return f"{VERIFICATION_MARK}\n## リリース後テストの記録\n\n対象の版: {payload['version']}\n\n{_block(payload)}\n"


def payload_of(body: str, mark: str) -> dict[str, Any] | None:
    """印 `mark` を持つコメントの JSON のコードブロック。印が無い・JSON が読めない・オブジェクトでなければ None。"""
    if mark not in body:
        return None
    m = _JSON_BLOCK.search(body, body.index(mark))
    if m is None:
        return None
    try:
        got = json.loads(m.group("body"))
    except json.JSONDecodeError:
        return None
    return got if isinstance(got, dict) else None


def _numbers(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, list) or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in value):
        return None
    return tuple(value)


def parse_record(body: str) -> Record | None:
    """コメント 1 件をリリース記録として読む。リリース記録でない・形が違えば None。"""
    got = payload_of(body, RECORD_MARK)
    if got is None:
        return None
    version, release, included = got.get("version"), got.get("release"), _numbers(got.get("included"))
    if not isinstance(version, str) or not version or not isinstance(release, int) or isinstance(release, bool) or included is None:
        return None
    return Record(version, release, included)


def records(bodies: Iterable[str]) -> list[Record]:
    """本文の並び（古い順）から読めたリリース記録（古い順）。"""
    return [r for r in (parse_record(b) for b in bodies) if r is not None]


def has_version(bodies: Iterable[str], version: str) -> bool:
    """本文の並びに、版 `version` のリリース記録があるか（打ち直しで同じ版の記録を重ねない）。"""
    return any(r.version == version for r in records(bodies))


def verifications(bodies: Iterable[str]) -> list[dict[str, Any]]:
    """本文の並び（古い順）から読めたリリース後テストの記録の JSON（古い順。形はスキーマで確かめない）。"""
    return [p for p in (payload_of(b, VERIFICATION_MARK) for b in bodies) if p is not None]


def first_parent_subjects(commits: Sequence[Mapping[str, Any]], head: str) -> list[str]:
    """PR のコミット（REST の `pulls/<番号>/commits` の形）を、先端 `head` から first-parent で辿った件名（新しい順）。

    辿るのは PR のコミットの中だけで、PR の外（base から辿れるコミット）に出たら止まる。範囲は承認資料の
    「含む PR」（`<前のリリース>..<ベースブランチ>` の first-parent）と同じになる。
    """
    by_sha = {str(c["sha"]): c for c in commits}
    out: list[str] = []
    sha = head
    while sha in by_sha:
        commit = by_sha.pop(sha)
        out.append(str(commit["commit"]["message"]).partition("\n")[0])
        parents = commit["parents"]
        sha = str(parents[0]["sha"]) if parents else ""
    return out
