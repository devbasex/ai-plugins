"""バージョン番号を持たないリポジトリのテストの記録を書く前の 5 つの確かめ（`record._check_unversioned`。#4580）の、
どの確かめで止まるか・確かめの順序・単位による絞り込みを固定する（I-002 の分割の前の振る舞い）。"""

from __future__ import annotations

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import record, record_decide

from ._fakes import FakeHub

VALUE = "main abc1234"
X = "abc1234" + "0" * 33
WEB, WORKER = "web", "worker"
RECORD_PULL, WORK_PULL, PRODUCTION = 11, 12, 20


def _made(environments: tuple[str, ...], unit: str | None = None) -> record_decide.Record:
    included = (RECORD_PULL, WORK_PULL, PRODUCTION)
    confirmation = {e: "cd" for e in environments}
    return record_decide.Record(VALUE, PRODUCTION, included, unit, environments, (WEB, WORKER), X, None, confirmation)


def _hub(*made: record_decide.Record, checks: tuple[dict, ...] = ()) -> FakeHub:
    hub = FakeHub()
    for n in (RECORD_PULL, WORK_PULL, PRODUCTION):
        hub.comments_of[n] = [Authored("octocat", "OWNER", record_decide.render_record(m)) for m in made]
    hub.comments_of[RECORD_PULL] += [Authored("octocat", "OWNER", record_decide.render_verification(c)) for c in checks]
    return hub


def _check(environments: tuple[str, ...], result: str = "passed", issue: int = 7, retest: str | None = None, **more) -> dict:
    row = {"number": issue, "result": result, "evidence": "画面で確かめた"} | ({"retest_reason": retest} if retest else {})
    return {"version": VALUE, "environments": list(environments), "issues": [row], **more}


def _written(hub: FakeHub, number: int = RECORD_PULL) -> list[dict]:
    return record_decide.verifications(c.body for c in hub.comments_of[number])


def _refused(hub: FakeHub, number: int, payload: dict) -> record.RecordError:
    before = {n: list(c) for n, c in hub.comments_of.items()}
    with pytest.raises(record.RecordError) as caught:
        record.write_verification(hub, number, payload, unversioned=True)
    assert hub.comments_of == before  # 止まるときは何も書かない
    return caught.value


def test_empty_environments_stop_at_the_first_step_even_without_a_record() -> None:
    # 1. environments が無ければ、記録の有無より先に止まる（空の一覧はスキーマで止まる）
    hub = _hub()
    payload = {k: v for k, v in _check(()).items() if k != "environments"}
    error = _refused(hub, RECORD_PULL, payload)
    assert type(error) is record.RecordError and "environments（テストした届け先）が要る" in str(error)


def test_a_missing_unit_stops_before_the_record_lookup() -> None:
    # 2a. 値が単位を持つのに unit が無ければ、単位の名前を示して止まる（単位が 1 つでも）
    hub = _hub(_made((WEB,), unit="a"))
    error = _refused(hub, RECORD_PULL, _check((WEB,)))
    assert type(error) is record.RecordError and "unit" in str(error) and "a" in str(error)


@pytest.mark.parametrize(
    ("made", "payload", "where"),
    [
        ((), _check((WEB,)), f"値 {VALUE}の"),
        ((_made((WEB,)),), _check((WEB,), unit="b"), f"値 {VALUE}・単位 bの"),
        ((_made((WEB,)),), _check((WEB,)) | {"version": "main def5678"}, "値 main def5678の"),
    ],
)
def test_a_missing_record_names_the_value_and_unit(made: tuple, payload: dict, where: str) -> None:
    # 2b. 同じ値・同じ単位のリリース記録が無ければ、値と単位を示して record --version を促す
    error = _refused(_hub(*made), RECORD_PULL, payload)
    assert type(error) is record.RecordError
    assert where in str(error) and "release-steps record --version" in str(error)


def test_another_included_pull_stops_before_the_target_check() -> None:
    # 3. 記録の PR でなければ、届け先の確かめ（4）より先に NotRecordPull で止まる
    hub = _hub(_made((WEB,)))
    error = _refused(hub, WORK_PULL, _check((WEB, WORKER, "db")))
    assert isinstance(error, record.NotRecordPull) and isinstance(error, record.RecordElsewhere)
    assert error.holders == (RECORD_PULL,) and f"#{RECORD_PULL}" in str(error)


@pytest.mark.parametrize(
    ("environments", "outside"),
    [
        ((WEB, "db"), "db"),  # 正の一覧に無い届け先
        ((WORKER, WEB), WORKER),  # 一覧にあるが反映の記録が無い届け先
        (("db", WORKER), "db, worker"),  # 当たる届け先を payload の順に並べる
    ],
)
def test_targets_without_a_reflection_are_listed(environments: tuple[str, ...], outside: str) -> None:
    # 4. 正の一覧に無い・反映の記録が無い届け先を並べて止まる
    error = _refused(_hub(_made((WEB,))), RECORD_PULL, _check(environments))
    assert type(error) is record.RecordError and f"（{outside}）" in str(error)


def test_the_target_check_comes_before_the_retest_check() -> None:
    # 4 と 5 の両方に当たるときは 4 で止まる
    hub = _hub(_made((WEB,)), checks=(_check((WEB,), "failed"),))
    error = _refused(hub, RECORD_PULL, _check((WEB, WORKER)))
    assert type(error) is record.RecordError and WORKER in str(error)


def test_retest_pairs_are_listed_per_target_then_issue() -> None:
    # 5. 不合格の組を届け先ごと・課題の行の順に並べる
    failed = _check((WEB, WORKER), "failed") | {
        "issues": [
            {"number": 7, "result": "failed", "evidence": "e"},
            {"number": 8, "result": "failed", "evidence": "e"},
        ]
    }
    hub = _hub(_made((WEB, WORKER)), checks=(failed,))
    retry = failed | {"issues": [{"number": 8, "result": "passed", "evidence": "e"}, {"number": 7, "result": "passed", "evidence": "e"}]}
    error = _refused(hub, RECORD_PULL, retry)
    assert isinstance(error, record.RetestWithoutReason)
    assert error.items == [
        {"kind": "delivery", "name": WEB, "number": 8, "result": "failed"},
        {"kind": "delivery", "name": WEB, "number": 7, "result": "failed"},
        {"kind": "delivery", "name": WORKER, "number": 8, "result": "failed"},
        {"kind": "delivery", "name": WORKER, "number": 7, "result": "failed"},
    ]


def test_retest_judgement_reads_only_the_same_unit() -> None:
    # 5. 判定は同じ単位のテストの記録だけで決める（別の単位の不合格は理由を要しない）
    hub = _hub(_made((WEB,), unit="a"), _made((WEB,), unit="b"), checks=(_check((WEB,), "failed", unit="b"),))
    record.write_verification(hub, RECORD_PULL, _check((WEB,), unit="a"), unversioned=True)
    with pytest.raises(record.RetestWithoutReason):
        record.write_verification(hub, RECORD_PULL, _check((WEB,), unit="b"), unversioned=True)
    assert _written(hub)[-1] == _check((WEB,), unit="a")


def test_a_passed_or_reasoned_row_passes_all_five_steps() -> None:
    # 5 つを通れば記録の PR へだけ書く
    hub = _hub(_made((WEB, WORKER)), checks=(_check((WEB,), "failed"),))
    payload = _check((WEB, WORKER), retest="検証環境の接続を直した")
    record.write_verification(hub, RECORD_PULL, payload, unversioned=True)
    assert _written(hub)[-1] == payload
    assert _written(hub, WORK_PULL) == [] and _written(hub, PRODUCTION) == []
