"""JSON と設定の形の検証の包み（lib/schema.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import pydantic  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import schema  # noqa: E402


class Step(schema.Shape):
    id: str
    retries: int = 0
    kind: Literal["work", "judge"] = "work"
    severity: schema.lenient_choice(("high", "medium", "low"), "low") = "low"
    next: list[str] = []


def test_errors_become_one_japanese_line_with_the_place():
    with pytest.raises(schema.ShapeError) as e:
        schema.load_shape(Step, {"retries": "x", "kind": "other", "extra": 1, "next": ["a", 2]}, where="steps[0]")
    assert str(e.value) == ("steps[0].id: 必須の項目が無い／steps[0].retries: 整数にする（値: 'x'）／"
                            "steps[0].kind: 次のどれかにする: 'work' / 'judge'（値: 'other'）／"
                            "steps[0].next[1]: 文字列にする（値: 2）／steps[0].extra: 知らない項目")
    assert e.value.problems[0] == ("steps[0].id", "必須の項目が無い")


def test_lenient_choice_lowers_unknown_values_and_round_trips():
    s = schema.load_shape(Step, {"id": "a", "severity": " HIGH "})
    assert s.severity == "high"
    assert schema.load_shape(Step, {"id": "a", "severity": "critical"}).severity == "low"
    assert schema.load_shape(Step, {"id": "a", "severity": 3}).severity == "low"
    assert schema.dump_shape(s) == {"id": "a", "retries": 0, "kind": "work", "severity": "high", "next": []}
    with pytest.raises(ValueError):
        schema.lenient_choice(("a",), "b")


def test_where_defaults_to_the_whole_and_assignment_is_checked():
    with pytest.raises(schema.ShapeError, match=r"^（全体）: オブジェクトにする"):
        schema.load_shape(Step, [1])
    s = schema.load_shape(Step, {"id": "a"})
    with pytest.raises(Exception):
        s.retries = "x"
