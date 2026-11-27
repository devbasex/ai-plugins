"""閉じる手順の読み取り（`read_release`。#4580）と、その候補の選び方（`candidates`）。

記録の PR のコメントからリリース記録・テストの記録・訂正の記録の候補を選び、今回のリリースに当たる候補のコメントと
本文の書き手だけを照会する。照会できない書き手がいれば記録を選ばずに `PermissionUnknown` で止まる（権限が無いとは扱わない）。
手順は `candidates` の説明と設計書の「閉じる手順の読み取り」の手順 1〜6 のとおり。書く側（`record`・`record_unversioned`）も
`candidates` で確かめる。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.adapters.github import Authored, GitHub
from ndf.adapters.github_pulls import closing_keyword_issues
from ndf.domains.release import record_decide


class Selection(NamedTuple):
    """`records` の絞り込みの引数（渡さなかったものは None）。`release` は本番の PR の番号、`unit` はリリースの単位の名前。"""

    version: Optional[str]
    release: Optional[int]
    unit: Optional[str]

    def takes(self, found: record_decide.Record) -> bool:
        """リリース記録が、渡したもののすべてに一致するか。"""
        return (
            (self.version is None or found.version == self.version)
            and (self.release is None or found.release == self.release)
            and (self.unit is None or found.unit == self.unit)
        )

    def takes_check(self, payload: Mapping[str, Any]) -> bool:
        """テストの記録が、渡した `version`・`unit` に一致するか（テストの記録は本番の PR を持たない）。"""
        return (self.version is None or payload.get("version") == self.version) and (self.unit is None or payload.get("unit") == self.unit)


class Candidates(NamedTuple):
    """`candidates` の結果。`releases` は引数で絞った有効なリリース記録（古い順）、`authoritative` は値ごとの正の対象の一覧
    （絞る前の有効な記録と訂正で決めた）、`corrections` は信用する書き手の組に合う訂正、`verifications` は絞ったテストの記録。"""

    releases: list[record_decide.Record]
    authoritative: dict[str, tuple[str, ...]]
    corrections: list[record_decide.Correction]
    verifications: list[dict[str, Any]]
    # 引数で絞る前の有効なリリース記録（古い順。値とコミットの組の確かめと重ねない判定が使う）
    valid: list[record_decide.Record] = []


class _Found(NamedTuple):
    """候補のコメント 1 件（`at` はコメントの古い順の位置）。"""

    at: int
    authored: Authored
    value: Any


def _read_marked(body: str, parse: Callable[[str], Any]) -> Any:
    """マーカーのある本文を読む。JSON が壊れていれば候補にしない（照会せずに除く）。"""
    try:
        return parse(body)
    except record_decide.UnreadableRecord:  # noqa: NDF401 利用者の入力: 壊れた記録は候補にせず、書き手も照会しない（読み取りの手順 2）
        return None


def _check_of(body: str) -> Optional[dict[str, Any]]:
    got = _read_marked(body, lambda b: record_decide.payload_of(b, record_decide.VERIFICATION_MARK))
    return got if got is not None and isinstance(got.get("version"), str) and isinstance(got.get("issues"), list) else None


class _Sorted(NamedTuple):
    """コメントの分類（どれも古い順）。`versioned`・`unversioned` はリリース記録（コミットの有無で分ける）、`checks` はテストの記録、`fixes` は訂正の記録。"""

    versioned: list[_Found]
    unversioned: list[_Found]
    checks: list[_Found]
    fixes: list[_Found]


def _sorted_comments(comments: Iterable[Authored]) -> _Sorted:
    """手順 1・2。マーカーと JSON の形でコメントを分け、位置を保つ（書き手は照会しない）。"""
    got = _Sorted([], [], [], [])
    for at, c in enumerate(comments):
        made = _read_marked(c.body, record_decide.parse_record)
        if made is not None:
            (got.versioned if made.commit is None else got.unversioned).append(_Found(at, c, made))
            continue
        check = _check_of(c.body)
        if check is not None:
            got.checks.append(_Found(at, c, check))
            continue
        fix = _read_marked(c.body, record_decide.parse_correction)
        if fix is not None:
            got.fixes.append(_Found(at, c, fix))
    return got


def _bridged(hub: GitHub, unversioned: Sequence[_Found], seeds: Sequence[_Found]) -> list[_Found]:
    """手順 3 の 2・3。起点の書き手を照会し、信用した記録と値かコミットを共有する記録へだけ広げる（古い順）。"""
    asked: set[int] = set()
    kept: list[_Found] = []
    values: set[Any] = set()
    commits: set[Any] = set()
    frontier = list(seeds)
    while frontier:
        for f in frontier:
            asked.add(f.at)
            if hub.trusts_strict(f.authored):
                kept.append(f)
                values.add(f.value.version)
                commits.add(f.value.commit)
        frontier = [f for f in unversioned if f.at not in asked and (f.value.version in values or f.value.commit in commits)]
    return sorted(kept, key=lambda f: f.at)


def candidates(
    hub: GitHub, number: int, select: Selection, commit: Optional[str] = None, comments: Optional[Sequence[Authored]] = None
) -> Candidates:
    """設計書の「閉じる手順の読み取り」の手順 1〜4。照会できない書き手がいれば `PermissionUnknown`。

    1. 記録の PR のコメントを 1 回だけ取得し、リリース記録・テストの記録・訂正の記録のマーカーを持つコメントを候補にする
    2. JSON の形が誤った候補を照会せずに除く。バージョン番号を持つリポジトリの記録とテストの記録は、渡したものが一致する候補の書き手だけを照会する
    3. バージョン番号を持たないリポジトリの記録は、起点（`--version` の値、無ければ `--release`・`--unit` の一致、どちらも無ければすべて）から
       信用を確かめながら値かコミットの共有で広げ、値とコミットの組で無効な記録を除き、値ごとの正の一覧を決めてから引数で絞る
    4. 訂正は、値と組のコミットに合うものの書き手だけを照会し、信用する書き手の最後の訂正でその値の正の一覧を置き換える

    `commit` を渡すと、そのコミットの記録も起点にする（書く側の確かめ 6 が、同じコミットの別の値の記録を見るため）。
    `comments` を渡すと、PR のコメントを取得せずにそれを読む。
    """
    versioned, unversioned, checks, fixes = _sorted_comments(hub.comments(number) if comments is None else comments)  # 手順 1・2
    kept_versioned = [f for f in versioned if select.takes(f.value) and hub.trusts_strict(f.authored)]
    kept_checks = [f.value for f in checks if select.takes_check(f.value) and hub.trusts_strict(f.authored)]
    # 手順 3
    by_value = select._replace(release=None, unit=None) if select.version is not None else select._replace(version=None)
    kept = _bridged(hub, unversioned, [f for f in unversioned if by_value.takes(f.value) or f.value.commit == commit])
    pairs, valid = record_decide.value_pairs([f.value for f in sorted([*kept_versioned, *kept], key=lambda f: f.at)])
    # 手順 4
    matching = [f for f in fixes if pairs.get(f.value.version) == f.value.commit and select.version in (None, f.value.version)]
    corrections = [f.value for f in matching if hub.trusts_strict(f.authored)]
    authoritative = {v: t for v in pairs if (t := record_decide.authoritative_targets(valid, corrections, v)) is not None}
    return Candidates([r for r in valid if select.takes(r)], authoritative, corrections, kept_checks, valid)


def _body_view(hub: GitHub, number: int) -> dict[str, Any]:
    """PR `number` の本文と、本文の書き手を信用するか（照会できなければ `PermissionUnknown`）。"""
    got = hub.issue_body(number)
    return {"number": number, "author": got.author, "trusted": hub.trusts_strict(got), "body": got.body}


def _records_outputs(select: Selection, **got: Any) -> dict[str, Any]:
    """`records` の新しい `outputs`（`command`・`selection`・`release`・`releases`・`targets`・`verifications`・`bodies`・`to_test`）。"""
    base: dict[str, Any] = {
        "release": None,
        record_decide.RELEASES: [],
        "targets": None,
        "verifications": [],
        "bodies": None,
        "to_test": None,
    }
    return {"command": "records", "selection": select._asdict(), **base, **got}


def read_release(hub: GitHub, number: int, select: Selection, bodies: bool, issues: Optional[Sequence[int]]) -> dict[str, Any]:
    """記録の PR `number` から、閉じる判定に使うリリース記録・テストの記録と、要れば含めた PR の本文とテストする組を読む。

    手順 5: 代表は、渡した値（無ければ最後の有効な記録の値）の記録のうち、対象の一覧を持つものは正の一覧と同じものだけから、最後の 1 件。
    手順 6: `bodies` なら代表の `included` の PR ごとに本文を読み、書き手を照会して `trusted` を付ける。代表が無ければ読まない。
    `to_test` は `bodies` か `issues` のときだけ返す。課題は `issues`、無ければ信用する書き手の本文の closing keywords から集める。
    照会できない書き手が候補か本文にいれば、何も選ばずに `PermissionUnknown`。"""
    got = candidates(hub, number, select)
    version = select.version if select.version is not None else (got.releases[-1].version if got.releases else None)
    targets = got.authoritative.get(version) if version is not None else None
    rep = record_decide.representative([r for r in got.releases if r.version == version], targets)
    if rep is None:  # 正の一覧の記録が無い（後の正でない記録だけ・負けた側の記録だけ）。正の一覧は返す
        return _records_outputs(select, targets=list(targets) if targets is not None else None, verifications=got.verifications)
    releases = [r for r in got.releases if (r.version, r.unit) == (rep.version, rep.unit)]
    views = [_body_view(hub, n) for n in rep.included] if bodies else []
    pairs = None
    if bodies or issues is not None:
        numbers = list(issues) if issues is not None else _issues_of(hub, views)
        checks = record_decide.verifications_of(got.verifications, rep.version, rep.unit)
        pairs = record_decide.to_test(_released_targets(got.releases, rep, targets), numbers, checks)
    return _records_outputs(
        select,
        release=rep.as_json(),
        releases=_unique([r.as_json() for r in releases]),
        targets=list(targets) if rep.targets is not None and targets is not None else None,
        verifications=got.verifications,
        bodies=views if bodies else None,
        to_test=pairs,
    )


def _unique(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ記録が 2 件以上残っていても 1 件にする（古い順を保つ）。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        if row not in out:
            out.append(row)
    return out


def _released_targets(
    releases: Sequence[record_decide.Record], rep: record_decide.Record, targets: Optional[tuple[str, ...]]
) -> list[Optional[str]]:
    """反映の記録がある届け先（正の一覧の順）。バージョン番号を持つリポジトリの記録は届け先を持たないので `[None]`（届け先を問わない）。"""
    if rep.targets is None or targets is None:
        return [None]
    _complete, missing = record_decide.complete_of(releases, rep, targets)
    return [t for t in targets if t not in missing]


def _issues_of(hub: GitHub, views: Sequence[Mapping[str, Any]]) -> list[int]:
    """信用する書き手の本文の closing keywords が指す、このリポジトリの課題（出た順・重複なし）。"""
    return list(dict.fromkeys(n for v in views if v["trusted"] for n in closing_keyword_issues(v["body"], hub.repo)))


def pr_body_only(hub: GitHub, number: int) -> dict[str, Any]:
    """`--pr-body-only`: リリース記録を選ばず記録の PR のコメントも読まずに、PR `number` の本文だけを `trusted` つきで返す。"""
    return _records_outputs(Selection(None, None, None), bodies=[_body_view(hub, number)])
