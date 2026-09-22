"""再開の反映（`statefile.apply_resume_args`）のテスト（#727 / #648）。

この関数は出力せず、標準エラーへ出す行の一覧を返す。予約語 `none` の正規化は
呼び出し側が済ませてから渡すので、値はそのまま `!=` で比べる。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

STATEFILE = Path(__file__).resolve().parents[1] / "lib" / "statefile.py"


@pytest.fixture(scope="module")
def statefile():
    spec = importlib.util.spec_from_file_location("ndf_lib_statefile_resume", STATEFILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def frozen_now(statefile, monkeypatch):
    monkeypatch.setattr(statefile, "now", lambda: "2026-09-19T10:00:00")
    return "2026-09-19T10:00:00"


def _args(**values):
    return argparse.Namespace(**values)


def test_replace_writes_the_value_and_records_the_change(statefile, frozen_now):
    state = {"max_rounds": 12, "resume_changes": []}
    spec = [statefile.ResumeField("max_rounds", "max_rounds", "replace")]

    lines = statefile.apply_resume_args(state, _args(max_rounds=20), spec)

    assert lines == ["↻ max_rounds: 12 → 20"]
    assert state["max_rounds"] == 20
    assert state["resume_changes"] == [
        {"at": frozen_now, "field": "max_rounds", "from": 12, "to": 20},
    ]


def test_notify_returns_a_line_and_leaves_the_state_alone(statefile):
    state = {"host": "claude", "resume_changes": []}
    spec = [statefile.ResumeField("host", "host", "notify")]

    lines = statefile.apply_resume_args(state, _args(host="codex"), spec)

    assert lines == ["ℹ --host は再開では反映しません（状態: claude / 指定: codex）"]
    assert state["host"] == "claude"
    assert state["resume_changes"] == []


def test_notify_uses_the_dashed_argument_name(statefile):
    state = {"baseline_test": "pytest -q", "resume_changes": []}
    spec = [statefile.ResumeField("baseline_test", "baseline_test", "notify")]

    lines = statefile.apply_resume_args(state, _args(baseline_test="make test"), spec)

    assert lines == ["ℹ --baseline-test は再開では反映しません（状態: pytest -q / 指定: make test）"]


def test_same_value_yields_no_line_and_no_record(statefile):
    state = {"max_rounds": 12, "host": "claude", "resume_changes": []}
    spec = [
        statefile.ResumeField("max_rounds", "max_rounds", "replace"),
        statefile.ResumeField("host", "host", "notify"),
    ]

    lines = statefile.apply_resume_args(state, _args(max_rounds=12, host="claude"), spec)

    assert lines == []
    assert state == {"max_rounds": 12, "host": "claude", "resume_changes": []}


def test_unspecified_argument_does_nothing(statefile):
    """未指定（`None`）と属性そのものが無い場合の両方で何もしない。"""
    state = {"max_rounds": 12, "only": "codex", "resume_changes": []}
    spec = [
        statefile.ResumeField("max_rounds", "max_rounds", "replace"),
        statefile.ResumeField("only", "only", "replace"),
        statefile.ResumeField("host", "host", "notify"),
    ]

    lines = statefile.apply_resume_args(state, _args(max_rounds=None, only=None), spec)

    assert lines == []
    assert state == {"max_rounds": 12, "only": "codex", "resume_changes": []}


def test_replace_creates_resume_changes_when_missing(statefile, frozen_now):
    """この変更の前に始めた実行の状態ファイルにも積める（`resume_changes` が無い）。"""
    state = {"only": "codex"}
    spec = [statefile.ResumeField("only", "only", "replace")]

    lines = statefile.apply_resume_args(state, _args(only="kiro"), spec)

    assert lines == ["↻ only: codex → kiro"]
    assert state["resume_changes"] == [
        {"at": frozen_now, "field": "only", "from": "codex", "to": "kiro"},
    ]


def test_replace_compares_values_as_given(statefile, frozen_now):
    """`none` の正規化は呼び出し側の責務。正規化済みの `[]` と `None` をそのまま比べる。"""
    state = {"verify_commands": ["pytest -q"], "only": "codex", "resume_changes": []}
    spec = [
        statefile.ResumeField("verify_commands", "verify_commands", "replace"),
        statefile.ResumeField("only", "only", "replace"),
    ]

    lines = statefile.apply_resume_args(state, _args(verify_commands=[], only="codex"), spec)

    assert lines == ["↻ verify_commands: ['pytest -q'] → []"]
    assert state["verify_commands"] == []
    assert state["only"] == "codex"


def test_several_fields_are_handled_in_one_call_in_spec_order(statefile, frozen_now):
    state = {"max_rounds": 12, "rotate_after": 8, "host": "claude", "resume_changes": []}
    spec = [
        statefile.ResumeField("max_rounds", "max_rounds", "replace"),
        statefile.ResumeField("rotate_after", "rotate_after", "replace"),
        statefile.ResumeField("host", "host", "notify"),
    ]

    lines = statefile.apply_resume_args(
        state, _args(max_rounds=20, rotate_after=4, host="codex"), spec,
    )

    assert lines == [
        "↻ max_rounds: 12 → 20",
        "↻ rotate_after: 8 → 4",
        "ℹ --host は再開では反映しません（状態: claude / 指定: codex）",
    ]
    assert (state["max_rounds"], state["rotate_after"], state["host"]) == (20, 4, "claude")
    assert [c["field"] for c in state["resume_changes"]] == ["max_rounds", "rotate_after"]


def test_resume_field_is_a_named_tuple(statefile):
    f = statefile.ResumeField("max_rounds", "max_rounds", "replace")
    assert (f.arg, f.key, f.mode) == ("max_rounds", "max_rounds", "replace")
    assert tuple(f) == ("max_rounds", "max_rounds", "replace")
