"""`record_read.candidates` がコメントをリリース記録・テストの記録・訂正の記録へ分ける振る舞い（I-007 の切り出しの前に固定する）。

分け方（手順 1・2）: リリース記録として読めればリリース記録（`commit` の有無でバージョン番号の有無を分ける）、読めなければ
テストの記録、それも読めなければ訂正の記録として読む。壊れた JSON・形の違う記録は候補にせず書き手を照会しない。
位置（`at`）は渡したコメント全体の古い順で、マーカーの無いコメントも数える。
"""

from __future__ import annotations

import json

import pytest

from ndf.adapters.github import Authored, PermissionUnknown
from ndf.domains.release import record_decide, record_read

from ._fakes import FakeHub
from .test_release_record import TARGETS, WEB, WORKER, X, Y, _check, _row, _unversioned

VALUE = "main abc1234"


def _c(writer: str, body: str) -> Authored:
    return Authored(writer, "MEMBER", body)


def _rec(made: record_decide.Record, writer: str = "octocat") -> Authored:
    return _c(writer, record_decide.render_record(made))


def _verify(payload: dict, writer: str = "octocat") -> Authored:
    return _c(writer, record_decide.render_verification(payload))


def _fix(fix: record_decide.Correction, writer: str = "octocat") -> Authored:
    return _c(writer, record_decide.render_correction(fix))


def _broken(mark: str) -> str:
    return f"{mark}\n```json\n{{壊れた\n```\n"


def _hub(*comments: Authored, unknown: tuple[str, ...] = ()) -> FakeHub:
    hub = FakeHub(unknown=set(unknown))
    hub.comments_of[11] = list(comments)
    return hub


def _get(hub: FakeHub, version: str | None = VALUE, release: int | None = None, unit: str | None = None, **kw) -> record_read.Candidates:
    return record_read.candidates(hub, 11, record_read.Selection(version, release, unit), **kw)


def test_each_kind_goes_to_its_own_list() -> None:
    made = _unversioned()
    check = _check((WEB,), _row(1, "passed"))
    fix = record_decide.Correction(VALUE, X, (WEB,), TARGETS, "worker は配らない")
    got = _get(_hub(_c("octocat", "ただの会話"), _rec(made), _verify(check), _fix(fix)))
    assert got.releases == [made] and got.valid == [made]
    assert got.verifications == [check]
    assert got.corrections == [fix] and got.authoritative == {VALUE: (WEB,)}


def test_a_body_read_as_a_record_is_not_read_as_a_check_or_a_correction() -> None:
    made = _unversioned()
    check = _check((WEB,), _row(1, "passed"))
    fix = record_decide.Correction(VALUE, X, (WEB,), TARGETS, "訂正")
    both = _c(
        "octocat", record_decide.render_record(made) + record_decide.render_verification(check) + record_decide.render_correction(fix)
    )
    got = _get(_hub(both))
    assert got.releases == [made] and got.verifications == [] and got.corrections == []


def test_a_body_read_as_a_check_is_not_read_as_a_correction() -> None:
    check = _check((WEB,), _row(1, "passed"))
    fix = record_decide.Correction(VALUE, X, (WEB,), TARGETS, "訂正")
    both = _c("octocat", record_decide.render_verification(check) + record_decide.render_correction(fix))
    got = _get(_hub(_rec(_unversioned()), both))
    assert got.verifications == [check] and got.corrections == [] and got.authoritative == {VALUE: TARGETS}


def test_a_broken_record_falls_through_to_the_check_after_it() -> None:
    check = _check((WEB,), _row(1, "passed"))
    body = _broken(record_decide.RECORD_MARK) + record_decide.render_verification(check)
    got = _get(_hub(_c("octocat", body)))
    assert got.releases == [] and got.verifications == [check]


@pytest.mark.parametrize("mark", [record_decide.RECORD_MARK, record_decide.VERIFICATION_MARK, record_decide.CORRECTION_MARK])
def test_broken_json_is_not_a_candidate_and_its_writer_is_not_asked(mark: str) -> None:
    hub = _hub(_c("stranger", _broken(mark)), _rec(_unversioned()), unknown=("stranger",))
    got = _get(hub)
    assert got.releases == [_unversioned()] and got.verifications == [] and got.corrections == []
    assert "stranger" not in hub.asked


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "environments": [WEB], "issues": []},
        {"version": VALUE, "environments": [WEB], "issues": {}},
        {"environments": [WEB], "issues": []},
    ],
)
def test_a_check_without_a_string_version_or_an_issue_list_is_not_a_candidate(payload: dict) -> None:
    body = f"{record_decide.VERIFICATION_MARK}\n```json\n{json.dumps(payload)}\n```\n"
    hub = _hub(_c("stranger", body), unknown=("stranger",))
    got = _get(hub, version=None)
    assert got.verifications == [] and "stranger" not in hub.asked


def test_checks_keep_the_order_and_only_trusted_matching_writers() -> None:
    first, second = _check((WEB,), _row(1, "passed")), _check((WORKER,), _row(2, "failed"))
    other = _check((WEB,), _row(3, "passed"), version="main def5678")
    hub = _hub(_verify(first), _verify(other, "someone"), _verify(_check((WEB,)), "reader"), _verify(second), unknown=("someone",))
    got = _get(hub)
    assert got.verifications == [first, second]
    # 値の違うテストの記録の書き手は照会しない
    assert "someone" not in hub.asked and "reader" in hub.asked


def test_valid_records_keep_the_comment_order_across_versioned_and_unversioned() -> None:
    a = _unversioned()
    versioned = record_decide.Record(VALUE, 50, (11,))
    b = _unversioned(environments=(WORKER,))
    hub = _hub(_c("octocat", "会話"), _rec(a), _c("octocat", "会話"), _rec(versioned), _rec(b))
    got = _get(hub)
    assert got.valid == [a, versioned, b] and got.releases == [a, versioned, b]


def test_the_first_unversioned_pair_wins_by_comment_position() -> None:
    first, clash = _unversioned(), _unversioned(commit=Y)
    got = _get(_hub(_rec(first), _rec(clash)))
    assert got.valid == [first]
    got = _get(_hub(_rec(clash), _rec(first)))
    assert got.valid == [clash] and got.authoritative == {VALUE: TARGETS}


def test_trust_is_asked_in_the_order_versioned_checks_unversioned_corrections() -> None:
    versioned = record_decide.Record(VALUE, 50, (11,))
    fix = record_decide.Correction(VALUE, X, (WEB,), TARGETS, "訂正")
    hub = _hub(
        _fix(fix, "d"),
        _rec(_unversioned(), "c"),
        _verify(_check((WEB,)), "b"),
        _rec(versioned, "a"),
    )
    hub.trusted = {"a", "b", "c", "d"}
    got = _get(hub)
    assert hub.asked == ["a", "b", "c", "d"]
    assert got.corrections == [fix] and got.verifications == [_check((WEB,))]


def test_an_unknown_writer_of_a_matching_candidate_stops() -> None:
    hub = _hub(_rec(_unversioned(), "someone"), unknown=("someone",))
    with pytest.raises(PermissionUnknown):
        _get(hub)


def test_given_comments_are_read_instead_of_the_pull() -> None:
    made = _unversioned()
    hub = _hub(_rec(_unversioned(environments=(WORKER,))))
    got = _get(hub, comments=[_rec(made)])
    assert got.releases == [made]


def test_commit_seeds_another_value_of_the_same_commit() -> None:
    other = _unversioned(version="main def5678", commit=Y)
    hub = _hub(_rec(_unversioned()), _rec(other))
    assert _get(hub).valid == [_unversioned()]
    assert _get(hub, commit=Y).valid == [_unversioned(), other]
