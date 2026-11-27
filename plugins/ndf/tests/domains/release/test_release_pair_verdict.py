"""`record_decide.pair_verdict` の再テストの境界と結果の優先順位（I-003 の書き換え前の振る舞いを固定する）。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.domains.release import record_decide

WEB, WORKER = "web", "worker"


def _row(number: int, result: Any, retest: Any = None, *, with_result: bool = True) -> dict:
    row: dict[str, Any] = {"number": number, "evidence": "確かめた"}
    if with_result:
        row["result"] = result
    if retest is not None:
        row["retest_reason"] = retest
    return row


def _check(environments: Any, *rows: Any) -> dict:
    return {"version": "main abc1234", "environments": environments, "issues": list(rows)}


@pytest.mark.parametrize(
    ("rows", "verdict"),
    [
        # 最後の再テストの行が境界。それより前は（前の再テストの行も）捨てる
        ([_row(1, "failed"), _row(1, "passed", "1 回目"), _row(1, "failed"), _row(1, "not_run", "2 回目")], "not_run"),
        ([_row(1, "passed", "1 回目"), _row(1, "failed", "2 回目")], "failed"),
        # 境界の行そのものは残り、後ろの行と合わせる
        ([_row(1, "failed"), _row(1, "passed", "やり直し"), _row(1, "failed")], "failed"),
        ([_row(1, "failed"), _row(1, "not_run", "やり直し"), _row(1, "passed")], "passed"),
        ([_row(1, "failed"), _row(1, "passed", "やり直し"), _row(1, "not_run")], "passed"),
        # 空の理由は境界にならない
        ([_row(1, "failed"), _row(1, "passed", "")], "failed"),
        # 再テストの行が無ければ全部を合わせる
        ([_row(1, "passed"), _row(1, "not_run"), _row(1, "passed")], "passed"),
        ([_row(1, "not_run"), _row(1, "not_run")], "not_run"),
    ],
)
def test_last_retest_row_is_the_boundary(rows: list[dict], verdict: str) -> None:
    checks = [_check([WEB], row) for row in rows]
    assert record_decide.pair_verdict(checks, WEB, 1) == verdict
    # 1 件の記録にまとめて並べても同じ
    assert record_decide.pair_verdict([_check([WEB], *rows)], WEB, 1) == verdict


def test_retest_rows_of_other_issues_or_environments_are_not_boundaries() -> None:
    checks = [
        _check([WEB], _row(1, "failed")),
        _check([WEB], _row(2, "passed", "ほかの課題")),
        _check([WORKER], _row(1, "passed", "ほかの届け先")),
    ]
    assert record_decide.pair_verdict(checks, WEB, 1) == "failed"
    assert record_decide.pair_verdict(checks, WORKER, 1) == "passed"
    assert record_decide.pair_verdict(checks, WEB, 2) == "passed"


def test_environment_none_reads_rows_of_every_environment() -> None:
    checks = [_check([WEB], _row(1, "failed")), _check([WORKER], _row(1, "passed", "やり直し"))]
    assert record_decide.pair_verdict(checks, None, 1) == "passed"
    # 届け先の欄が壊れていても None なら数える
    assert record_decide.pair_verdict([_check("web", _row(1, "failed"))], None, 1) == "failed"
    assert record_decide.pair_verdict([_check(None, _row(1, "not_run"))], None, 1) == "not_run"


@pytest.mark.parametrize(
    ("rows", "verdict"),
    [
        # 知らない結果や結果の無い行だけなら保留になる
        ([_row(1, "unknown")], "not_run"),
        ([_row(1, None, with_result=False)], "not_run"),
        ([_row(1, None)], "not_run"),
        # 知らない結果は合格・不合格を打ち消さない
        ([_row(1, "unknown"), _row(1, "passed")], "passed"),
        ([_row(1, "passed"), _row(1, "unknown"), _row(1, "failed")], "failed"),
        # 知らない結果の行も再テストの境界になる
        ([_row(1, "failed"), _row(1, "unknown", "やり直し")], "not_run"),
    ],
)
def test_unknown_results_fall_back_to_not_run(rows: list[dict], verdict: str) -> None:
    assert record_decide.pair_verdict([_check([WEB], *rows)], WEB, 1) == verdict


def test_malformed_entries_are_skipped() -> None:
    checks = [
        _check("web", _row(1, "failed")),  # 届け先が一覧でない
        _check(None, _row(1, "failed")),
        {"environments": [WEB], "issues": "1"},  # 課題が一覧でない
        {"environments": [WEB]},
        _check([WEB], "1", None, ["x"]),  # 行が dict でない
        _check([WEB], {"number": "1", "result": "failed"}),  # 番号の型が違う
    ]
    assert record_decide.pair_verdict(checks, WEB, 1) is None
    assert record_decide.pair_verdict([*checks, _check([WEB], _row(1, "passed"))], WEB, 1) == "passed"


def test_no_rows_is_none() -> None:
    assert record_decide.pair_verdict([], WEB, 1) is None
    assert record_decide.pair_verdict([], None, 1) is None
    assert record_decide.pair_verdict([_check([WEB], _row(2, "failed"))], WEB, 1) is None
