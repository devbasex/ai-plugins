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


def test_outputs_of_a_stopped_report_are_not_handed_on(tmp_path):
    # できなかった作業の outputs は、必須の名前がそろっていても後の工程へ渡さない
    report = {"status": "stopped", "summary": "途中", "outputs": OUTPUTS}
    checked, outputs, _ = worker_outcome.require_outputs(report, list(OUTPUTS))
    got = worker_outcome.outcome_of("worktree", tmp_path / "r.json", checked, [], {"attempts": 1}, outputs=outputs)
    assert outputs is None and "outputs" not in got and got["status"] == "stopped" and result.errors(got) == []


def test_a_report_without_outputs_and_no_required_names_stays_ok(tmp_path):
    report = {"status": "ok", "summary": "済んだ"}
    checked, outputs, _ = worker_outcome.require_outputs(report, [])
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
    checked, got, _ = worker_outcome.require_outputs({"status": "ok", "outputs": outputs}, ["worktree"])
    assert checked["gaps"] == [_absent("worktree")] and got is None


def test_a_report_without_the_outputs_key_is_one_absent_gap():
    checked, got, _ = worker_outcome.require_outputs({"status": "ok"}, ["worktree"])
    assert checked == {"status": "ok", "gaps": [_absent("worktree")]} and got is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("worktree", ""), ("worktree", None), ("branch", 3), ("branch", ["b"]), ("unknown", "v")],
)
def test_a_misshapen_required_value_is_one_gap_with_the_schema_errors(name, value):
    # 値が null でも名前はあるので「無い」ではなく「形が違う」になる。形の定義に無い名前も「形が違う」になる
    checked, got, _ = worker_outcome.require_outputs({"status": "ok", "outputs": {name: value}}, [name])
    assert checked["gaps"] == [_misshapen(name, value)] and got is None


def test_gaps_follow_the_order_of_the_required_names_after_the_existing_gaps():
    # 合う名前は欠けを作らず、形の違いと無い名前は必須の名前の並びのまま、元からある欠けの後に付く
    existing = {"kind": "criterion", "name": "3", "reason": "確かめられない"}
    report = {"status": "ok", "gaps": [existing], "outputs": {"worktree": "/r/w", "branch": 3}}
    checked, got, _ = worker_outcome.require_outputs(report, ["pr", "worktree", "branch"])
    assert checked == {**report, "gaps": [existing, _absent("pr"), _misshapen("branch", 3)]} and got is None
    assert report["gaps"] == [existing]  # 元の結果ファイルの値は書き換えない


def test_values_outside_the_required_names_are_neither_checked_nor_handed_on():
    report = {"status": "ok", "outputs": {"worktree": "/r/w", "branch": 3, "unknown": "v"}}
    checked, got, _ = worker_outcome.require_outputs(report, ["worktree"])
    assert checked is report and got == {"worktree": "/r/w"}


# #4513: product_version の形は結果 JSON の定義でなく ndf worker run が確かめる（バージョン番号を持たないリポジトリでも終える）
RELEASE = ["delivery", "product_version"]
DELIVERY = {"kind": "manual", "target": "本番", "trigger": "手で出す", "channel": "production", "versioned": False}


def _release(value, **extra):
    return {"status": "ok", "summary": "出した", "outputs": {"delivery": DELIVERY, "product_version": value}, **extra}


def test_a_short_hash_value_passes_when_the_version_number_is_not_required():
    checked, got, missing = worker_outcome.require_outputs(_release("main abc1234"), RELEASE)
    assert got == {"delivery": DELIVERY, "product_version": "main abc1234"} and missing == () and "gaps" not in checked


@pytest.mark.parametrize("value", ["ndf v11.0.0", "main abc1234"])
def test_a_value_that_is_not_a_version_number_is_a_gap_when_the_version_number_is_required(value):
    checked = worker_outcome.require_outputs(_release(value), RELEASE, version_number=True)
    assert checked.outputs is None and checked.missing == ("product_version",)
    (gap,) = checked.report["gaps"]
    assert gap["name"] == "product_version" and "バージョン番号" in gap["reason"]


def test_a_version_number_passes_when_the_version_number_is_required():
    checked = worker_outcome.require_outputs(_release("11.0.0"), RELEASE, version_number=True)
    assert checked.outputs == {"delivery": DELIVERY, "product_version": "11.0.0"} and checked.missing == ()


@pytest.mark.parametrize("value", ["main\nabc1234", "a\r", "", "  "])
def test_a_multi_line_or_blank_distribution_id_is_a_gap_even_without_the_version_number(value):
    checked = worker_outcome.require_outputs(_release(value), RELEASE)
    assert checked.outputs is None and checked.missing == ("product_version",)


def test_a_delivery_that_does_not_fit_the_context_is_a_gap():
    # 結果 JSON の $defs/outputs は delivery をオブジェクトとしか見ない。後の工程が読む文脈の形（versioned が要る）でも確かめる
    report = {"status": "ok", "outputs": {"delivery": {"kind": "manual"}, "product_version": "1.0.0"}}
    checked = worker_outcome.require_outputs(report, RELEASE)
    assert checked.outputs is None and checked.missing == ("delivery",)


# --- #4513 の U3: 判断を求めた結果は、確かめを通った値を渡し、通らなかった名前を metrics.missing_outputs に載せる ---------------


def _gate(outputs):
    return {"status": "gate", "summary": "判断を求めた", "outputs": outputs}


def test_a_gate_report_with_every_value_hands_them_on_without_missing_names(tmp_path):
    checked = worker_outcome.require_outputs(_gate({"delivery": DELIVERY, "product_version": "main abc1234"}), RELEASE)
    assert checked.report["status"] == "gate" and checked.missing == ()
    got = worker_outcome.checked_outcome("release", tmp_path / "r.json", checked, [], {"attempts": 1})
    assert got["status"] == "gate" and got["outputs"] == checked.outputs and "missing_outputs" not in got["metrics"]
    assert result.errors(got) == []


@pytest.mark.parametrize(
    ("outputs", "version_number", "kept", "missing"),
    [
        ({"delivery": DELIVERY}, False, {"delivery": DELIVERY}, ("product_version",)),
        ({"delivery": DELIVERY, "product_version": "ndf v11.0.0"}, True, {"delivery": DELIVERY}, ("product_version",)),
        ({"delivery": {"kind": "manual"}, "product_version": "1.2.3"}, True, {"product_version": "1.2.3"}, ("delivery",)),
        (None, False, None, ("delivery", "product_version")),
    ],
)
def test_a_gate_report_stays_gate_and_names_the_values_that_did_not_pass(tmp_path, outputs, version_number, kept, missing):
    checked = worker_outcome.require_outputs(_gate(outputs), RELEASE, version_number=version_number)
    assert "gaps" not in checked.report and checked.outputs == kept and checked.missing == missing
    got = worker_outcome.checked_outcome("release", tmp_path / "r.json", checked, [], {"attempts": 1})
    assert got["status"] == "gate" and got["metrics"]["missing_outputs"] == list(missing) and result.errors(got) == []
