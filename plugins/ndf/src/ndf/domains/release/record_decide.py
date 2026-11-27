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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from ndf import identity
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR

_NS = identity.FIXED_WORDS["workflow_namespace"]
# リリース記録のマーカーと、リリース後テストの記録のマーカー
RECORD_MARK = f"<!-- {_NS}:release-record -->"
VERIFICATION_MARK = f"<!-- {_NS}:release-verification -->"
CORRECTION_MARK = f"<!-- {_NS}:release-targets-correction -->"
# 実環境の確認で反映を確かめた届け先の `confirmation` の値（この届け先だけが `evidence` を持つ）
OBSERVED = "observed"
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
    if got is None:
        return None
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


def authoritative_targets(found: Sequence[Record], corrections: Sequence[Correction], version: str) -> tuple[str, ...] | None:
    """値 `version` の正の対象の一覧。最後の訂正の一覧、無ければ同じ値の最初の記録の一覧。記録が無ければ None。

    `found` は引数で絞る前の有効な記録（`value_pairs`）、`corrections` は信用する書き手の、組のコミットに合う訂正（古い順）。"""
    fixed = [c for c in corrections if c.version == version]
    if fixed:
        return fixed[-1].targets
    return next((r.targets for r in found if r.version == version and r.targets is not None), None)


def representative(found: Sequence[Record], targets: tuple[str, ...] | None) -> Record | None:
    """閉じる判定に使う 1 件。対象の一覧を持つ記録は `targets`（正の一覧）と同じものだけから、持たない記録は絞らずに、最後の 1 件。"""
    return next((r for r in reversed(found) if r.targets is None or r.targets == targets), None)


def complete_of(releases: Sequence[Record], representative: Record, targets: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    """代表と `version`・`release`・`unit`・`commit` が同じで `targets` が正の一覧の記録の `environments` が、
    `targets` を覆うかと、まだ反映の記録が無い届け先（`targets` の順）。"""
    key = (representative.version, representative.release, representative.unit, representative.commit)
    covered = {e for r in releases if (r.version, r.release, r.unit, r.commit) == key and r.targets == targets for e in r.environments}
    missing = tuple(t for t in targets if t not in covered)
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
    retested = [i for i, r in enumerate(rows) if r.get("retest_reason")]
    results = {r.get("result") for r in rows[retested[-1] if retested else 0 :]}
    if not results:
        return None
    return next(v for v in ("failed", "passed", "not_run") if v in results or v == "not_run")


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
