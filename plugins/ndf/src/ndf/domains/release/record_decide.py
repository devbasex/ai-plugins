"""リリース記録とリリース後テストの記録を組み、読む（純粋な関数。#3487）。

リリース記録は、本番ブランチへ入れたバージョンに含めた PR（承認資料と同じ「含む PR」の範囲）の各々へ書くコメントである。
リリース後テストの記録も同じ PR へ書き、課題を閉じる側は 1 つの PR から両方を読む。

| 記録 | マーカー | 本文の JSON |
| --- | --- | --- |
| リリース記録 | `RECORD_MARK` | `version`（v の前置きの無い版数）・`release`（リリースの PR の番号）・`included`（含めた PR の番号）・単位が 2 つ以上のときだけ `unit`（単位の名前） |
| リリース後テストの記録 | `VERIFICATION_MARK` | スキーマ `release-verification` の形（`version`・`issues`） |
| 対象の一覧の訂正の記録 | `CORRECTION_MARK` | `version`・`commit`・`targets`（新しい一覧）・`previous`（前の一覧）・`reason`（#4580） |

バージョン番号を持たないリポジトリのリリース記録（#4580）は、`version` に出したものを指す値を持ち、加えて `environments`
（反映を確かめた届け先）・`targets`（今回の対象の届け先）・`commit`（反映したコミット）・`confirmation`（届け先ごとの反映の確かめ方）と、
読めたときだけ `push`（反映したコミットを入れた push）・`evidence`（実環境の確認の証跡）を持つ。`commit` と `targets` を持たない
記録がバージョン番号を持つリポジトリの記録である。

どちらも、マーカーの行・見出し・中身の分かる行・JSON のコードブロック（` ```json `）の順で書く。機械が読むのは
JSON のコードブロックだけで、見出しや本文の文から読まない。
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from ndf import identity
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR
from ndf.core.production_merge import DELIVERIES

_NS = identity.FIXED_WORDS["workflow_namespace"]
# リリース記録のマーカーと、リリース後テストの記録のマーカー
RECORD_MARK = f"<!-- {_NS}:release-record -->"
VERIFICATION_MARK = f"<!-- {_NS}:release-verification -->"
CORRECTION_MARK = f"<!-- {_NS}:release-targets-correction -->"
# 実環境の確認で反映を確かめた届け先の `confirmation` の値（この届け先だけが `evidence` を持つ）
OBSERVED = "observed"
# `release-steps records` の outputs の、代表と同じ値・リリースの単位の有効なリリース記録の並びのキー
RELEASES = "releases"
# CD の結果で反映を確かめた届け先の `confirmation` の値（ほかの区分は閉じる報告に「CD で確かめていない」と載る）
CD = "cd"
# 値の 16 進の語と SHA の一致に要る最小の長さ（確かめ 2 と実環境の確認の両方が読む）
SHA_PREFIX = 7
# JSON Pointer が指す値が無いこと（null の値と見分ける）
MISSING: Any = object()
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


class Push(NamedTuple):
    """反映したコミットを本番ブランチへ入れた 1 回の push（更新の前と後のコミット）。"""

    before: str
    after: str


class Evidence(NamedTuple):
    """実環境の確認の証跡 1 つ。`url` は読んだ URL、`value` はそこで読んだ値。"""

    url: str
    value: str


class Record(NamedTuple):
    """リリース記録 1 件。`included` は含めた PR の番号（小さい順）。`unit` は単位が 2 つ以上のリリースの単位の名前（1 つなら None）。

    `commit` 以降はバージョン番号を持たないリポジトリの記録だけが持つ（#4580。モジュールの説明の表の後）。"""

    version: str
    release: int
    included: tuple[int, ...]
    unit: str | None = None
    environments: tuple[str, ...] = ()
    targets: tuple[str, ...] | None = None
    commit: str | None = None
    push: Push | None = None
    confirmation: Mapping[str, str] | None = None
    evidence: Mapping[str, Evidence] | None = None

    def as_json(self) -> dict[str, Any]:
        got: dict[str, Any] = {"version": self.version, "release": self.release, "included": list(self.included)}
        if self.unit is not None:
            got["unit"] = self.unit
        if self.commit is not None:
            got.update(environments=list(self.environments), commit=self.commit)
            if self.targets is not None:
                got["targets"] = list(self.targets)
            if self.confirmation is not None:
                got["confirmation"] = dict(self.confirmation)
            if self.push is not None:
                got["push"] = self.push._asdict()
            if self.evidence is not None:
                got["evidence"] = {name: e._asdict() for name, e in self.evidence.items()}
        return got


class Correction(NamedTuple):
    """対象の一覧の訂正の記録 1 件。`targets` は新しい一覧、`previous` は訂正の前の一覧。"""

    version: str
    commit: str
    targets: tuple[str, ...]
    previous: tuple[str, ...]
    reason: str


def _json_block(payload: Mapping[str, Any]) -> str:
    return f"```json\n{json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)}\n```"


def render_record(record: Record) -> str:
    """リリース記録のコメントの本文。"""
    listed = ", ".join(f"#{n}" for n in record.included)
    return (
        f"{RECORD_MARK}\n## リリース記録\n\n対象のバージョン: {record.version}\nリリースの PR: #{record.release}\n"
        f"含めた PR: {listed}\n\n{_json_block(record.as_json())}\n"
    )


def render_correction(correction: Correction) -> str:
    """対象の一覧の訂正の記録のコメントの本文。"""
    payload = {**correction._asdict(), "targets": list(correction.targets), "previous": list(correction.previous)}
    return (
        f"{CORRECTION_MARK}\n## 対象の一覧の訂正\n\n対象の値: {correction.version}\n"
        f"対象の届け先: {', '.join(correction.targets)}（前: {', '.join(correction.previous)}）\n\n{_json_block(payload)}\n"
    )


def render_verification(payload: Mapping[str, Any]) -> str:
    """リリース後テストの記録のコメントの本文（`payload` はスキーマ `release-verification` で確かめたもの）。"""
    return f"{VERIFICATION_MARK}\n## リリース後テストの記録\n\n対象のバージョン: {payload['version']}\n\n{_json_block(payload)}\n"


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


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _name_list(value: Any) -> tuple[str, ...] | None:
    """空でない文字列の空でない配列。違えば None。"""
    if not isinstance(value, list) or not value or not all(_filled(v) for v in value):
        return None
    return tuple(value)


def _push_of(value: Any) -> Push | None:
    if not isinstance(value, dict) or not _filled(value.get("before")) or not _filled(value.get("after")):
        return None
    return Push(value["before"], value["after"])


def _evidence_of(value: Any, confirmation: Mapping[str, str]) -> dict[str, Evidence] | None:
    """`observed` の届け先にちょうど 1 つずつある証跡。形が違えば None。"""
    observed = {name for name, kind in confirmation.items() if kind == OBSERVED}
    if value is None:
        return {} if not observed else None
    if not isinstance(value, dict) or set(value) != observed:
        return None
    out = {}
    for name, item in value.items():
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not isinstance(item.get("value"), str):
            return None
        out[name] = Evidence(item["url"], item["value"])
    return out


def _unversioned_of(got: Mapping[str, Any], base: Record) -> Record | None:
    """バージョン番号を持たないリポジトリの記録の項目を `base` に足す。形が違えば None。"""
    environments, targets, commit = _name_list(got.get("environments")), _name_list(got.get("targets")), got.get("commit")
    confirmation = got.get("confirmation")
    if environments is None or targets is None or not _filled(commit) or not isinstance(confirmation, dict):
        return None
    if not all(_filled(k) and _filled(v) for k, v in confirmation.items()):
        return None
    push = _push_of(got["push"]) if "push" in got else None
    evidence = _evidence_of(got.get("evidence"), confirmation)
    if ("push" in got and push is None) or evidence is None:
        return None
    return base._replace(
        environments=environments,
        targets=targets,
        commit=commit,
        push=push,
        confirmation=dict(confirmation),
        evidence=evidence if "evidence" in got else None,
    )


def parse_record(body: str) -> Record | None:
    """コメント 1 件をリリース記録として読む。リリース記録でない・形が違えば None。"""
    got = payload_of(body, RECORD_MARK)
    return None if got is None else parse_record_json(got)


def parse_record_json(got: Mapping[str, Any]) -> Record | None:
    """リリース記録の JSON（コメントの本文か `Record.as_json` の出力）を読む。形が違えば None。"""
    version, release, included = got.get("version"), got.get("release"), _numbers(got.get("included"))
    unit = got["unit"] if "unit" in got else None
    if not isinstance(version, str) or not version or not _is_int(release) or included is None:
        return None
    if unit is not None and (not isinstance(unit, str) or not unit):
        return None
    made = Record(version, release, included, unit)
    if "commit" in got or "targets" in got:
        return _unversioned_of(got, made)
    return made


def parse_correction(body: str) -> Correction | None:
    """コメント 1 件を対象の一覧の訂正の記録として読む。訂正の記録でない・形が違えば None。"""
    got = payload_of(body, CORRECTION_MARK)
    if got is None:
        return None
    targets, previous = _name_list(got.get("targets")), _name_list(got.get("previous"))
    fields = (got.get("version"), got.get("commit"), got.get("reason"))
    if targets is None or previous is None or not all(_filled(v) for v in fields):
        return None
    return Correction(fields[0], fields[1], targets, previous, fields[2])


def records(bodies: Iterable[str]) -> list[Record]:
    """本文の並び（古い順）から読めたリリース記録（古い順）。"""
    return [r for r in (parse_record(b) for b in bodies) if r is not None]


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


def verifications_of(verifications: Iterable[Mapping[str, Any]], version: Any, unit: Any) -> list[Mapping[str, Any]]:
    """テストの記録のうち、`version`・`unit` が渡した値と同じもの（古い順を保つ）。"""
    return [v for v in verifications if (v.get("version"), v.get("unit")) == (version, unit)]


def value_pairs(found: Sequence[Record]) -> tuple[dict[str, str], list[Record]]:
    """値とコミットの組（値 → コミット）と、有効な記録（古い順）。`found` は信用する書き手の記録の古い順。

    古い順にたどり、値もコミットもまだ組になっていなければ組にする。値とコミットがそのまま 1 つの組の記録だけが有効で、
    どちらかが別の相手と組になった記録は無効にする。バージョン番号を持つリポジトリの記録（`commit` が無い）は常に有効。"""
    by_value: dict[str, str] = {}
    by_commit: dict[str, str] = {}
    valid = []
    for r in found:
        if r.commit is None:
            valid.append(r)
            continue
        if r.version not in by_value and r.commit not in by_commit:
            by_value[r.version], by_commit[r.commit] = r.commit, r.version
        if by_value.get(r.version) == r.commit:
            valid.append(r)
    return by_value, valid


def value_commit_conflict(found: Sequence[Record], commit: str, version: str) -> tuple[str, str] | None:
    """値とコミットの組（`value_pairs`）で、今回のコミットが別の値と組か、今回の値が別のコミットと組なら、その組の (値, コミット)。"""
    by_value, _valid = value_pairs(found)
    if version in by_value and by_value[version] != commit:
        return version, by_value[version]
    return next(((v, c) for v, c in by_value.items() if c == commit and v != version), None)


def has_unversioned(found: Sequence[Record], version: str, environments: tuple[str, ...], targets: tuple[str, ...]) -> bool:
    """同じ値・同じ `environments`・正の `targets`（`targets`）の記録が既にあるか（重ねて書かない）。`found` は有効な記録。"""
    return any(r.version == version and r.environments == environments and r.targets == targets for r in found)


def authoritative_targets(found: Sequence[Record], corrections: Sequence[Correction], version: str) -> tuple[str, ...] | None:
    """値 `version` の正の対象の一覧。最後の訂正の一覧、無ければ同じ値の最初の記録の一覧。記録が無ければ None。

    `found` は引数で絞る前の有効な記録（`value_pairs`）、`corrections` は信用する書き手の、組のコミットに合う訂正（古い順）。"""
    fixed = next((c for c in reversed(corrections) if c.version == version), None)
    if fixed is not None:
        return fixed.targets
    return next((r.targets for r in found if r.version == version and r.targets is not None), None)


def representative(found: Sequence[Record], targets: tuple[str, ...] | None) -> Record | None:
    """閉じる判定に使う 1 件。対象の一覧を持つ記録は `targets`（正の一覧）と同じものだけから、持たない記録は絞らずに、最後の 1 件。"""
    return next((r for r in reversed(found) if r.targets is None or r.targets == targets), None)


def counted(releases: Sequence[Record], representative: Record, targets: tuple[str, ...]) -> list[Record]:
    """反映がそろったかを数える記録。代表と `version`・`release`・`unit`・`commit` が同じで `targets` が正の一覧の記録（古い順）。"""
    key = (representative.version, representative.release, representative.unit, representative.commit)
    return [r for r in releases if (r.version, r.release, r.unit, r.commit) == key and r.targets == targets]


def missing_in(counted_records: Sequence[Record], targets: tuple[str, ...]) -> tuple[str, ...]:
    """`counted` で選んだ記録の `environments` が覆わない届け先（`targets` の順）。"""
    covered = {e for r in counted_records for e in r.environments}
    return tuple(t for t in targets if t not in covered)


def listed_names(names: Sequence[str] | None) -> str:
    """届け先の名前を `, ` でつないだ文。空か `None` なら「無し」。"""
    return ", ".join(names) if names else "無し"


def complete_of(releases: Sequence[Record], representative: Record, targets: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    """`counted` の記録の `environments` が `targets` を覆うかと、まだ反映の記録が無い届け先（`targets` の順）。"""
    missing = missing_in(counted(releases, representative, targets), targets)
    return not missing, missing


def _pair_rows(verifications: Sequence[Mapping[str, Any]], environment: str | None, issue: int) -> list[Mapping[str, Any]]:
    """届け先 `environment`（None は届け先を問わない）と課題 `issue` の組の行（古い順）。"""
    rows = []
    for v in verifications:
        environments = v.get("environments")
        if environment is not None and (not isinstance(environments, list) or environment not in environments):
            continue
        issues = v.get("issues")
        rows.extend(r for r in (issues if isinstance(issues, list) else ()) if isinstance(r, dict) and r.get("number") == issue)
    return rows


def pair_verdict(verifications: Sequence[Mapping[str, Any]], environment: str | None, issue: int) -> str | None:
    """届け先と課題の組の判定（`failed`・`passed`・`not_run`、行が無ければ None）。

    最後に `retest_reason` を持つ行より前の行を捨て、残りを投稿の順によらずに合わせる。不合格が 1 つでもあれば不合格、
    無ければ合格が 1 つでもあれば合格（保留は合格を打ち消さない）、保留だけなら保留。"""
    rows = _pair_rows(verifications, environment, issue)
    start = max((i for i, r in enumerate(rows) if r.get("retest_reason")), default=0)
    results = {r.get("result") for r in rows[start:]}
    if not results:
        return None
    if "failed" in results:
        return "failed"
    if "passed" in results:
        return "passed"
    return "not_run"


def to_test(released: Sequence[str | None], issues: Sequence[int], verifications: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """テストを始めてよい届け先と課題の組（行が無い組と保留の組。届け先の順・課題の順）。`released` は反映の記録がある届け先。"""
    return [{"environment": e, "issue": n} for e in released for n in issues if pair_verdict(verifications, e, n) in (None, "not_run")]


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


class Confirmation(NamedTuple):
    """届け先 1 つの反映の確かめ。`state` は `ok`・`failed`・`pending`、`kind` は `ok` のときの `cd`・`observed`・`attested`。
    `reason` は書かない届け先の理由、`evidence` は `observed` のときの証跡。"""

    state: str
    kind: str | None = None
    reason: str | None = None
    evidence: Evidence | None = None


_HEX_WORD = re.compile(r"[0-9A-Za-z]+")


def value_matches(value: str, sha: str) -> bool:
    """値の中に、SHA `sha` の先頭 `SHA_PREFIX` 文字以上と一致する 16 進の語があるか（大文字と小文字を問わない）。"""
    sha = sha.lower()
    words = (w.lower() for w in _HEX_WORD.findall(value))
    return any(len(w) >= SHA_PREFIX and sha.startswith(w) and all(c in "0123456789abcdef" for c in w) for w in words)


def declared_deliveries(release: Mapping[str, Any]) -> list[Any] | None:
    """設定の `release` の届け先の行（`deliveries`）。無いか配列でなければ None（届け先の設定が無いリポジトリ）。"""
    rows = release.get(DELIVERIES)
    return rows if isinstance(rows, list) else None


def production_deliveries(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """届け先の設定の行のうち本番の届け先（`channel` が `production`・省略・null で、`production` が false でない。手動も含む）。"""
    return [r for r in rows if r.get("channel") in (None, "production") and r.get("production") is not False]


# git の wildmatch が括弧の中で受ける POSIX の文字クラス（git の ctype と同じく ASCII だけ）
_CLASSES = {
    "alnum": string.ascii_letters + string.digits,
    "alpha": string.ascii_letters,
    "blank": " \t",
    "cntrl": "".join(chr(c) for c in (*range(32), 127)),
    "digit": string.digits,
    "graph": "".join(chr(c) for c in range(33, 127)),
    "lower": string.ascii_lowercase,
    "print": "".join(chr(c) for c in range(32, 127)),
    "punct": string.punctuation,
    "space": " \t\n\r",
    "upper": string.ascii_uppercase,
    "xdigit": string.hexdigits,
}


def _class_at(pattern: str, i: int) -> tuple[str, int] | None:
    """`pattern[i]` の `[:` で始まる文字クラスの文字と、閉じの `]` の位置。`:]` で閉じていなければ ("[", i)（`[` を文字に）。
    閉じの `]` が無いか、知らない文字クラスなら None（git はパターンの誤りとして、どのパスにも当てない）。"""
    close = pattern.find("]", i + 2)
    if close < 0:
        return None
    if close - 1 < i + 2 or pattern[close - 1] != ":":
        return "[", i
    chars = _CLASSES.get(pattern[i + 2 : close - 1])
    return (chars, close) if chars is not None else None


def _bracket_item(pattern: str, i: int, prev: str | None) -> tuple[str, str | None, int] | None:
    """括弧の中の `pattern[i]` からの 1 項目。(文字の集合の正規表現の断片, 範囲の始まりにできる文字, 項目の最後の位置)。
    `\\` は次の 1 文字をそのまま、範囲の端でない `-` は文字そのもの、逆向きの範囲は空。パターンの誤りは None。"""
    n, c = len(pattern), pattern[i]
    if c == "\\":
        return (re.escape(pattern[i + 1]), pattern[i + 1], i + 1) if i + 1 < n else None
    if c == "-" and prev is not None and i + 1 < n and pattern[i + 1] != "]":
        j = i + 2 if pattern[i + 1] == "\\" else i + 1
        if j >= n:
            return None
        return (f"{re.escape(prev)}-{re.escape(pattern[j])}" if prev <= pattern[j] else ""), None, j
    if c == "[" and i + 1 < n and pattern[i + 1] == ":":
        got = _class_at(pattern, i)
        if got is None:
            return None
        chars, end = got
        return (re.escape("["), "[", i) if end == i else ("".join(re.escape(ch) for ch in chars), None, end)
    return re.escape(c), c, i


def _bracket(pattern: str, start: int) -> tuple[str, int] | None:
    """`pattern[start]` の `[` で始まる括弧の正規表現と、閉じ括弧の次の位置（git の wildmatch と同じ読み方）。先頭の `]` は
    文字そのもの。括弧は `/` に当たらない。閉じ括弧が無いなどのパターンの誤りは None。"""
    i, n = start + 1, len(pattern)
    negate = i < n and pattern[i] in "!^"
    i += negate
    parts: list[str] = []
    prev: str | None = None
    while i < n and (not parts or pattern[i] != "]"):
        got = _bracket_item(pattern, i, prev)
        if got is None:
            return None
        part, prev, last = got
        parts.append(part)
        i = last + 1
    if i >= n:
        return None
    body = "".join(parts)
    if negate:
        return (f"[^/{body}]" if body else "[^/]"), i + 1
    return (f"(?!/)[{body}]" if body else "(?!)"), i + 1


def _stars(pattern: str, i: int) -> tuple[str, int]:
    """`pattern[i]` から続く `*` の正規表現と、次の位置。末尾の `**` と、`/` に挟まれた `**/` は `/` を越え、ほかは越えない。"""
    n, j = len(pattern), i
    while j < n and pattern[j] == "*":
        j += 1
    double = j - i >= 2
    if double and j == n:
        return ".*", j
    if double and (i == 0 or pattern[i - 1] == "/") and pattern[j] == "/":
        return "(?:[^/]*/)*", j + 1
    return "[^/]*", j


def _glob_regex(pattern: str) -> str | None:
    """git の `:(glob)` のパターンの正規表現（`*`・`?`・括弧は `/` を越えない。`\\` は次の 1 文字をそのまま）。
    パターンの誤り（閉じない括弧・知らない文字クラス・末尾の `\\`）は None で、git と同じくどのパスにも当てない。"""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            got = _stars(pattern, i)
        elif c == "[":
            got = _bracket(pattern, i)
        elif c == "\\":
            got = (re.escape(pattern[i + 1]), i + 2) if i + 1 < n else None
        else:
            got = ("[^/]" if c == "?" else re.escape(c)), i + 1
        if got is None:
            return None
        out.append(got[0])
        i = got[1]
    return "".join(out)


def _bytewise(text: str) -> str:
    """`text` の UTF-8 のバイト列を、1 バイトを 1 文字（U+0000〜U+00FF）にした文字列（git と同じ単位で照らすため）。"""
    return text.encode("utf-8", "surrogateescape").decode("latin-1")


def glob_matches(pattern: str, path: str) -> bool:
    """パス `path` が git の pathspec の `:(glob)` のパターン `pattern` に当たるか（大文字と小文字を区別する）。

    git と同じく、先にパターンを文字そのままとして比べ、同じパスと、そのディレクトリの下のパスに当てる。ワイルドカード
    （`*`・`?`・`[`・`\\`）があれば、続けて wildmatch で照らす。括弧は POSIX の文字クラス（`[[:alpha:]]` など）も受ける。

    git の wildmatch は 1 バイトずつ照らすので、パターンもパスも UTF-8 のバイト列にして、1 バイトを 1 文字として照らす
    （`?` と括弧は日本語の 1 文字でなく 1 バイトに当たる）。"""
    pattern, path = _bytewise(pattern), _bytewise(path)
    if path == pattern or (path.startswith(pattern) and (pattern.endswith("/") or path[len(pattern)] == "/")):
        return True
    if not any(c in pattern for c in "*?[\\"):
        return False
    regex = _glob_regex(pattern)
    return regex is not None and re.fullmatch(regex, path) is not None


def targets_of(rows: Sequence[Mapping[str, Any]], pr_paths: frozenset[str], push_paths: frozenset[str]) -> tuple[str, ...]:
    """今回の対象の届け先（設定の順）。本番の届け先のうち、`paths` を持たないものと、本番の PR の差分と push の差分の
    両方に出るパスが `paths` のどれかに当たるもの。"""
    changed = pr_paths & push_paths
    picked = []
    for row in production_deliveries(rows):
        paths = row.get("paths")
        if paths is None or any(glob_matches(p, path) for p in paths for path in changed):
            picked.append(row["target"])
    return tuple(picked)


def push_of(activities: Iterable[Mapping[str, Any]], landed: frozenset[str]) -> Push | None:
    """反映したコミットを本番ブランチへ入れた push。`activities` はブランチの更新の新しい順、`landed` は反映したコミットから
    本番ブランチの HEAD までの第一親のコミット。`after` が `landed` にあり `before` が無い最初の更新を返し、`after` が
    `landed` の外の更新（反映したコミットより前）に当たれば None。"""
    for activity in activities:
        before, after = activity.get("before"), activity.get("after")
        if after not in landed:
            return None
        if before not in landed:
            return Push(str(before), str(after))
    return None


def json_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 の JSON Pointer `pointer` が `doc` の中で指す 1 つの値。無ければ `MISSING`。"""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        return MISSING
    for token in (t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")):
        if isinstance(doc, dict) and token in doc:
            doc = doc[token]
        elif isinstance(doc, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(doc):
            doc = doc[int(token)]
        else:
            return MISSING
    return doc


def confirm(delivery: Mapping[str, Any], run: Mapping[str, Any] | None, observed: Any, after: str) -> Confirmation:
    """届け先 1 つの反映を何で確かめたか（設計書の届け先ごとの反映の確かめの表を 1 回評価する）。

    `run` は push の `after` の CD の結果（`gh run list` の `status`・`conclusion` の形。まだ無ければ None）、`observed` は
    実環境の確認で `observe.pointer` が指した値（確かめを指定していないか、読めなければ `MISSING`）、`after` は push の `after`。"""
    result = delivery.get("result")
    if not isinstance(result, Mapping) or not (result.get("workflow") or result.get("command")):
        return Confirmation("ok", "attested")
    if run is not None and run.get("status") == "completed":
        if run.get("conclusion") == "success":
            return Confirmation("ok", CD)
        return Confirmation("failed", reason=f"CD の結果が失敗（{run.get('conclusion')}）")
    observe = delivery.get("observe")
    if not isinstance(observe, Mapping):
        return Confirmation("pending", reason="CD の結果が未確定で、届け先の設定に実環境の確認（observe）が無い")
    if observed is MISSING:
        return Confirmation("pending", reason="CD の結果が未確定で、実環境の確認を指定していないか、値を読めなかった")
    if not isinstance(observed, str) or not value_matches(observed, after):
        return Confirmation("pending", reason=f"CD の結果が未確定で、実環境の値が push の after（{after}）と一致しない")
    return Confirmation("ok", OBSERVED, evidence=Evidence(str(observe.get("url")), observed))
