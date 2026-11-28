"""リリース記録とリリース後テストの記録を組み、読む（純粋な関数。#3487）。

リリース記録は、本番ブランチへ入れたバージョンに含めた PR（承認資料と同じ「含む PR」の範囲）の各々へ書くコメントである。
リリース後テストの記録も同じ PR へ書き、課題を閉じる側は 1 つの PR から両方を読む。

| 記録 | マーカー | 本文の JSON |
| --- | --- | --- |
| リリース記録 | `RECORD_MARK` | `version`（v の前置きの無い版数）・`release`（リリースの PR の番号）・`included`（含めた PR の番号）・単位が 2 つ以上のときだけ `unit`（単位の名前） |
| リリース後テストの記録 | `VERIFICATION_MARK` | スキーマ `release-verification` の形（`version`・`issues`） |

どちらも、マーカーの行・見出し・中身の分かる行・JSON のコードブロック（` ```json `）の順で書く。機械が読むのは
JSON のコードブロックだけで、見出しや本文の文から読まない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ndf import identity
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR
from ndf.core.release_record import RECORD_MARK, Record, payload_of, records

# 記録を扱う呼び手がここからも引く（読めない記録の例外と、1 件の読み取り）
from ndf.core.release_record import UnreadableRecord as UnreadableRecord
from ndf.core.release_record import parse_record as parse_record

_NS = identity.FIXED_WORDS["workflow_namespace"]
# リリース後テストの記録のマーカー（リリース記録のマーカーと読み取りは `ndf.core.release_record`）
VERIFICATION_MARK = f"<!-- {_NS}:release-verification -->"
# 組み立てがリリースのコミットへ書く、承認資料の「含む PR」の行（git のトレーラーの形。値は `#<番号>` の空白区切りか `none`）
INCLUDED_TRAILER = "Ndf-Included-Pulls"
_INCLUDED_LINE = re.compile(rf"^{INCLUDED_TRAILER}: (?P<value>.*)$", re.MULTILINE)
# 単位が 2 つ以上のとき、同じコミットへ単位ごとに書く行（`<単位> <バージョン> #<番号>…` か `<単位> <バージョン> none`）
UNIT_TRAILER = "Ndf-Unit-Pulls"
_UNIT_LINE = re.compile(rf"^{UNIT_TRAILER}: (?P<unit>\S+) (?P<version>\S+) (?P<value>none|#\d+(?: #\d+)*)$", re.MULTILINE)


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


def has_record_of(found: Iterable[Record], version: str) -> bool:
    """リリース記録の並びに、バージョン `version` の記録があるか。"""
    return any(r.version == version for r in found)


def has_version(bodies: Iterable[str], version: str, unit: str | None = None) -> bool:
    """本文の並びに、単位 `unit`（単位が 1 つなら None）のバージョン `version` のリリース記録があるか（打ち直しで同じ記録を重ねない。
    2 つの単位を同じバージョンへ上げたとき、別の単位の記録を記録済みと読まない）。"""
    return any(r.version == version and r.unit == unit for r in records(bodies))


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


def subjects_after_approved(commits: Sequence[Mapping[str, Any]], head: str) -> list[str] | None:
    """組み立てのリリースのコミット（`_approved_message`）より後に first-parent へ積んだコミットの件名（新しい順）。
    「含む PR」の行を持つコミットが無ければ None。"""
    out: list[str] = []
    for message in _first_parent_messages(commits, head):
        if included_from_message(message) is not None:
            return out
        out.append(message.partition("\n")[0])
    return None


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
