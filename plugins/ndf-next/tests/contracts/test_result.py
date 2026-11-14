from __future__ import annotations

import io
import json

import pytest

from ndf import contracts
from ndf.contracts import exit_codes, result

GAP = {"kind": "record", "name": "x", "reason": "書けなかった"}


def _stopped(kind="test_failed", **extra):
    return result.result("t", "stopped", "止まった", cause={"kind": kind, "detail": "理由"}, **extra)


@pytest.mark.parametrize(
    ("obj", "code"),
    [
        (result.result("t", "ok", "済んだ"), 0),
        (_stopped(), 1),
        (_stopped("deps_missing"), 69),
        (_stopped("incomplete", gaps=[GAP]), 6),
        (result.result("t", "gate", "承認を待つ", routes={"approved": "ndf x", "rejected": None}), 10),
        (result.result("t", "gate", "判断を待つ", routes={"a": "ndf y"}), 20),
        (_stopped("fix_limit", routes={"fixed": "ndf retry"}), 1),
    ],
)
def test_valid_results(obj, code):
    assert result.errors(obj, code) == []


@pytest.mark.parametrize(
    ("obj", "code"),
    [
        (result.result("t", "stopped", "理由なし"), 1),
        (result.result("t", "ok", "済んだ", cause={"kind": "unknown", "detail": "x"}), 0),
        (result.result("t", "gate", "承認を待つ", next="ndf x"), 10),
        (result.result("t", "ok", "済んだ", routes={"a": "x"}), 0),
        (_stopped(gaps=[GAP]), 1),
        (_stopped("incomplete"), 6),
        (_stopped("no_such_kind"), 1),
        (_stopped("base_conflict"), 1),
        (_stopped(), 10),
        (result.result("t", "ok", "済んだ"), 1),
        ({**result.result("t", "ok", "済んだ"), "extra": 1}, 0),
        ({"tool": "t", "status": "ok", "summary": "s", "items": []}, 0),
        (_stopped(process=[{"run": " ", "result": "x"}]), 1),
        ("not an object", None),
    ],
)
def test_wrong_results(obj, code):
    assert result.errors(obj, code)


def test_code_for_takes_stopped_code_from_kind():
    assert result.code_for(_stopped("precondition")) == 3
    assert result.code_for(result.result("t", "gate", "g", routes={"a": None})) == 10
    assert result.code_for(result.result("t", "stopped", "s")) is None


def test_stopped_helper_builds_cause():
    obj = result.stopped("t", "push_rejected", "hook が断った", instance="pre-push")
    assert obj["summary"] == "hook が断った"
    assert obj["cause"] == {"kind": "push_rejected", "detail": "hook が断った", "instance": "pre-push"}
    assert result.errors(obj) == []


def test_emit_prints_one_line_and_returns_the_kind_code():
    out = io.StringIO()
    assert result.emit(_stopped("base_conflict"), stream=out) == 4
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["cause"]["kind"] == "base_conflict"


def test_emit_refuses_wrong_shape(capsys):
    out = io.StringIO()
    assert result.emit(result.result("t", "stopped", "理由なし"), stream=out) == 2
    assert out.getvalue() == ""
    assert "結果の形の誤り" in capsys.readouterr().err


@pytest.mark.parametrize(("version", "ok"), [("10.27.57", True), ("11.0.0-dev.2", True), ("ndf v10.27.57", False), ("v10.27.57", False)])
def test_release_verification_version_is_bare(version, ok):
    # #3115: 対象の版は前置きを持たない版数だけで、本番の版と文字列のまま照合できる
    record = {"version": version, "issues": [{"number": 3115, "result": "passed", "evidence": "PR #3102"}]}
    assert (contracts.schema_errors(record, contracts.schema_path("release-verification")) == []) is ok


# 現状固定（I-009）: exit-codes の区分の範囲の読み方（始点・終点・範囲外・last の省略・範囲内の非整数）。
def _stopped_range(data):
    data["rows"] = [{**r, "last": 89} if r["id"] == "deps_missing" else r for r in data["rows"]]
    return data


def test_stopped_range_is_read_from_code_to_last(contracts_copy):
    contracts_copy("facts/exit-codes.json", _stopped_range)
    assert exit_codes.codes("stopped") == frozenset([1, 2, 3, 4, 5, 6, *range(69, 90), 75])
    assert [result.code_matches("stopped", c) for c in (68, 69, 80, 89, 90, 75, 7)] == [False, True, True, True, False, True, False]
    assert [result.code_matches("gate", c) for c in (9, 10, 19, 20, 29, 30)] == [False, True, True, True, True, False]
    assert result.code_matches("ok", 0) and not result.code_matches("ok", 1)


def test_code_matches_compares_non_integers_by_range():
    assert result.code_matches("gate", 10.5)
    assert not result.code_matches("stopped", 3.5)
    assert not result.code_matches("ok", 0.5)
