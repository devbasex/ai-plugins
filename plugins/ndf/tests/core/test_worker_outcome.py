"""worker が書いた結果ファイルの outputs を `ndf worker run` の結果へ渡すところ（`core/worker_outcome.py`。#3908）。

確かめた値を後の工程へ渡すことは `tests/worker/test_delegate.py` が見る。ここは ok でない結果ファイルの境界と、必須の名前の欠けの中身
（種類・名前・理由・並び・元からある欠けとの順）を見る。
"""

from __future__ import annotations

import pytest

from ndf import contracts
from ndf.contracts import result
from ndf.core import worker_outcome

OUTPUTS = {"worktree": "/r/.worktrees/b", "branch": "b"}


@pytest.mark.parametrize("status", ["gate", "stopped"])
def test_outputs_of_a_report_that_is_not_ok_are_not_handed_on(tmp_path, status):
    # 判断を求めた・できなかった作業の outputs は、必須の名前がそろっていても後の工程へ渡さない
    report = {"status": status, "summary": "途中", "outputs": OUTPUTS}
    checked, outputs = worker_outcome.require_outputs(report, list(OUTPUTS))
    got = worker_outcome.outcome_of("worktree", tmp_path / "r.json", checked, [], {"attempts": 1}, outputs=outputs)
    assert outputs is None and "outputs" not in got and got["status"] == status and result.errors(got) == []


def test_a_report_without_outputs_and_no_required_names_stays_ok(tmp_path):
    report = {"status": "ok", "summary": "済んだ"}
    checked, outputs = worker_outcome.require_outputs(report, [])
    got = worker_outcome.outcome_of("impl", tmp_path / "r.json", checked, [], {"attempts": 1}, outputs=outputs)
    assert got["status"] == "ok" and "outputs" not in got and result.errors(got) == []


def _absent(name):
    return {"kind": "output", "name": name, "reason": "作業の結果ファイルの outputs に無い"}


def _misshapen(name, value):
    errs = contracts.schema_errors({name: value}, worker_outcome.OUTPUTS_SCHEMA)
    assert errs
    return {"kind": "output", "name": name, "reason": f"作業の結果ファイルの outputs の値の形が違う: {'; '.join(errs)}"}


@pytest.mark.parametrize("outputs", [None, [], ["worktree"], "worktree", 3, {}, {"branch": "b"}])
def test_a_required_name_not_in_outputs_is_one_absent_gap(outputs):
    # outputs がオブジェクトでないとき（名前を含む配列・文字列も）は、名前が無いのと同じ欠けにする
    checked, got = worker_outcome.require_outputs({"status": "ok", "outputs": outputs}, ["worktree"])
    assert checked["gaps"] == [_absent("worktree")] and got is None


def test_a_report_without_the_outputs_key_is_one_absent_gap():
    checked, got = worker_outcome.require_outputs({"status": "ok"}, ["worktree"])
    assert checked == {"status": "ok", "gaps": [_absent("worktree")]} and got is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("worktree", ""), ("worktree", None), ("branch", 3), ("branch", ["b"]), ("unknown", "v")],
)
def test_a_misshapen_required_value_is_one_gap_with_the_schema_errors(name, value):
    # 値が null でも名前はあるので「無い」ではなく「形が違う」になる。形の定義に無い名前も「形が違う」になる
    checked, got = worker_outcome.require_outputs({"status": "ok", "outputs": {name: value}}, [name])
    assert checked["gaps"] == [_misshapen(name, value)] and got is None


def test_gaps_follow_the_order_of_the_required_names_after_the_existing_gaps():
    # 合う名前は欠けを作らず、形の違いと無い名前は必須の名前の並びのまま、元からある欠けの後に付く
    existing = {"kind": "criterion", "name": "3", "reason": "確かめられない"}
    report = {"status": "ok", "gaps": [existing], "outputs": {"worktree": "/r/w", "branch": 3}}
    checked, got = worker_outcome.require_outputs(report, ["pr", "worktree", "branch"])
    assert checked == {**report, "gaps": [existing, _absent("pr"), _misshapen("branch", 3)]} and got is None
    assert report["gaps"] == [existing]  # 元の結果ファイルの値は書き換えない


def test_values_outside_the_required_names_are_neither_checked_nor_handed_on():
    report = {"status": "ok", "outputs": {"worktree": "/r/w", "branch": 3, "unknown": "v"}}
    checked, got = worker_outcome.require_outputs(report, ["worktree"])
    assert checked is report and got == {"worktree": "/r/w"}
