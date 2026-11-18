"""リリース記録とリリース後テストの記録を組み、読む（純粋な関数。#3487）。

リリース記録は、本番ブランチへ入れたバージョンに含めた PR（承認資料と同じ「含む PR」の範囲）の各々へ書くコメントである。
リリース後テストの記録も同じ PR へ書き、課題を閉じる側は 1 つの PR から両方を読む。

| 記録 | マーカー | 本文の JSON |
| --- | --- | --- |
| リリース記録 | `RECORD_MARK` | `version`（v の前置きの無い版数）・`release`（リリースの PR の番号）・`included`（含めた PR の番号） |
| リリース後テストの記録 | `VERIFICATION_MARK` | スキーマ `release-verification` の形（`version`・`issues`） |

どちらも、マーカーの行・見出し・中身の分かる行・JSON のコードブロック（` ```json `）の順で書く。機械が読むのは
JSON のコードブロックだけで、見出しや本文の文から読まない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from ndf import identity
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR

_NS = identity.FIXED_WORDS["workflow_namespace"]
# リリース記録のマーカーと、リリース後テストの記録のマーカー
RECORD_MARK = f"<!-- {_NS}:release-record -->"
VERIFICATION_MARK = f"<!-- {_NS}:release-verification -->"
# 組み立てがリリースのコミットへ書く、承認資料の「含む PR」の行（git のトレーラーの形。値は `#<番号>` の空白区切りか `none`）
INCLUDED_TRAILER = "Ndf-Included-Pulls"
_INCLUDED_LINE = re.compile(rf"^{INCLUDED_TRAILER}: (?P<value>.*)$", re.MULTILINE)
# 単位が 2 つ以上のとき、同じコミットへ単位ごとに書く行（`<単位> <バージョン> #<番号>…` か `<単位> <バージョン> none`）
UNIT_TRAILER = "Ndf-Unit-Pulls"
_UNIT_LINE = re.compile(rf"^{UNIT_TRAILER}: (?P<unit>\S+) (?P<version>\S+) (?P<value>none|#\d+(?: #\d+)*)$", re.MULTILINE)
# 本文の JSON のコードブロック（マーカーの後の最初のもの）
_JSON_BLOCK = re.compile(r"^```json\n(?P<body>.*?)\n```$", re.MULTILINE | re.DOTALL)


class UnreadableRecord(ValueError):
    """マーカーを持つコメントの JSON のコードブロックが JSON として読めない（書き手が本文を手で壊した）。"""


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
        f"{RECORD_MARK}\n## リリース記録\n\n対象のバージョン: {record.version}\nリリースの PR: #{record.release}\n"
        f"含めた PR: {listed}\n\n{_block(record.as_json())}\n"
    )


def render_verification(payload: Mapping[str, Any]) -> str:
    """リリース後テストの記録のコメントの本文（`payload` はスキーマ `release-verification` で確かめたもの）。"""
    return f"{VERIFICATION_MARK}\n## リリース後テストの記録\n\n対象のバージョン: {payload['version']}\n\n{_block(payload)}\n"


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
    if not isinstance(version, str) or not version or not _is_int(release) or included is None:
        return None
    return Record(version, release, included)


def records(bodies: Iterable[str]) -> list[Record]:
    """本文の並び（古い順）から読めたリリース記録（古い順）。"""
    return [r for r in (parse_record(b) for b in bodies) if r is not None]


def has_record_of(found: Iterable[Record], version: str) -> bool:
    """リリース記録の並びに、バージョン `version` の記録があるか。"""
    return any(r.version == version for r in found)


def has_version(bodies: Iterable[str], version: str) -> bool:
    """本文の並びに、バージョン `version` のリリース記録があるか（打ち直しで同じバージョンの記録を重ねない）。"""
    return has_record_of(records(bodies), version)


def verifications(bodies: Iterable[str]) -> list[dict[str, Any]]:
    """本文の並び（古い順）から読めたリリース後テストの記録の JSON（古い順。形はスキーマで確かめない）。"""
    return [p for p in (payload_of(b, VERIFICATION_MARK) for b in bodies) if p is not None]


def _first_parent_messages(commits: Sequence[Mapping[str, Any]], head: str) -> list[str]:
    """PR のコミット（REST の `compare` の `commits` の形）を、HEAD `head` から first-parent で辿ったメッセージ（新しい順）。
    辿るのは PR のコミットの中だけで、PR の外（base から辿れるコミット）に出たら止まる。"""
    by_sha = {str(c["sha"]): c for c in commits}
    out: list[str] = []
    sha = head
    while sha in by_sha:
        commit = by_sha.pop(sha)
        out.append(str(commit["commit"]["message"]))
        parents = commit["parents"]
        sha = str(parents[0]["sha"]) if parents else ""
    return out


def first_parent_subjects(commits: Sequence[Mapping[str, Any]], head: str) -> list[str]:
    """PR のコミットを HEAD `head` から first-parent で辿った件名（新しい順）。範囲は承認資料の
    「含む PR」（`<前のリリース>..<ベースブランチ>` の first-parent）と同じになる。
    """
    return [m.partition("\n")[0] for m in _first_parent_messages(commits, head)]


def included_trailer(numbers: Iterable[int]) -> str:
    """リリースのコミットへ書く「含む PR」の行（承認資料に載せた PR の番号。小さい順）。"""
    got = sorted(set(numbers))
    return f"{INCLUDED_TRAILER}: {' '.join(f'#{n}' for n in got) if got else 'none'}"


def included_from_message(message: str) -> list[int] | None:
    """リリースのコミットのメッセージの「含む PR」の行の番号（小さい順）。行が無い・形が違えば None。"""
    m = _INCLUDED_LINE.search(message)
    if m is None:
        return None
    value = m.group("value").strip()
    if value == "none":
        return []
    words = value.split()
    if not words or not all(re.fullmatch(r"#\d+", w) for w in words):
        return None
    return sorted({int(w[1:]) for w in words})


def unit_trailer(unit: str, version: str, numbers: Iterable[int]) -> str:
    """単位 1 つの行（承認資料が確定した、その単位のバージョンとその単位の範囲の PR の番号。小さい順）。"""
    got = sorted(set(numbers))
    return f"{UNIT_TRAILER}: {unit} {version} {' '.join(f'#{n}' for n in got) if got else 'none'}"


def units_from_message(message: str) -> list[tuple[str, str, tuple[int, ...]]]:
    """リリースのコミットのメッセージの単位ごとの行の (単位, バージョン, 番号)。行が無ければ空。"""
    out = []
    for m in _UNIT_LINE.finditer(message):
        value = m.group("value")
        numbers = () if value == "none" else tuple(sorted({int(w[1:]) for w in value.split()}))
        out.append((m.group("unit"), m.group("version"), numbers))
    return out


def _approved_message(commits: Sequence[Mapping[str, Any]], head: str) -> str | None:
    """PR のコミットを HEAD `head` から first-parent で辿り、「含む PR」の行を持つ最も新しいコミット（組み立てのリリースの
    コミット）のメッセージ。組み立ての後に直しのコミットを積んでも読む。どのコミットも行を持たなければ None。"""
    return next((m for m in _first_parent_messages(commits, head) if included_from_message(m) is not None), None)


def approved_included(commits: Sequence[Mapping[str, Any]], head: str) -> list[int] | None:
    """組み立てのリリースのコミット（`_approved_message`）の「含む PR」の番号。行が無ければ None。"""
    message = _approved_message(commits, head)
    return included_from_message(message) if message is not None else None


def approved_units(commits: Sequence[Mapping[str, Any]], head: str) -> list[tuple[str, str, tuple[int, ...]]]:
    """組み立てのリリースのコミット（`_approved_message`）の単位ごとの行。無ければ空（単位が 1 つ・行の無いコミット）。"""
    message = _approved_message(commits, head)
    return units_from_message(message) if message is not None else []


def landed_trailers(message: str) -> tuple[int, str] | None:
    """`strict` の早送りで本番へ入れたコミットのメッセージの trailer が指す (PR の番号, 承認済みの head)。無ければ None。"""
    found = {}
    for line in message.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in (TRAILER_PR, TRAILER_HEAD):
            found[key] = value.strip()
    if TRAILER_PR not in found or TRAILER_HEAD not in found or not found[TRAILER_PR].isdigit():
        return None
    return int(found[TRAILER_PR]), found[TRAILER_HEAD]
