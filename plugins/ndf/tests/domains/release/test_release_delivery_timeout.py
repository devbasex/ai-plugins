"""届け先の `result.timeout_seconds` の読み方（判定の上限 `_timeout_of` と保存先を残す秒 `_share_age`）の今の振る舞い。

スキーマが許す値（正の整数・null・省略）と、`result` が無い・形が違う届け先で、2 つの読み手が同じ値を返すことを固定する。
"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.contracts import limits
from ndf.domains.release import delivery_confirm, record_unversioned


def _default() -> float:
    return limits.seconds(delivery_confirm.WAIT_LIMIT)


def _row(result: Any = None, **more: Any) -> dict:
    row = {"target": "web", "kind": "auto", "trigger": "deploy", "versioned": False, **more}
    if result is not None:
        row["result"] = result
    return row


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"workflow": "deploy.yml", "timeout_seconds": 50}, 50.0),
        ({"workflow": "deploy.yml", "timeout_seconds": 1}, 1.0),
        ({"workflow": "deploy.yml"}, None),
        ({"workflow": "deploy.yml", "timeout_seconds": None}, None),
        ({"workflow": "deploy.yml", "timeout_seconds": "50"}, None),
        ("deploy.yml", None),
        (None, None),
    ],
)
def test_timeout_and_share_age_read_one_delivery_alike(result: Any, expected: float | None) -> None:
    """届け先 1 つなら、判定の上限と保存先を残す秒は同じ値（無いか読めない値なら待ちの上限）。"""
    want = _default() if expected is None else expected
    row = _row(result)
    assert delivery_confirm._timeout_of(row) == want
    assert record_unversioned._share_age({"deliveries": [row]}) == want


def test_share_age_takes_the_longest_timeout_of_the_deliveries() -> None:
    rows = [
        _row({"workflow": "a.yml", "timeout_seconds": 30}),
        _row({"workflow": "b.yml", "timeout_seconds": 900}),
        _row({"workflow": "c.yml"}),
        _row(kind="manual"),
        "not a row",
    ]
    assert record_unversioned._share_age({"deliveries": rows}) == 900.0


@pytest.mark.parametrize("release", [{}, {"deliveries": None}, {"deliveries": {}}, {"deliveries": []}])
def test_share_age_falls_back_to_the_wait_limit_without_timeouts(release: dict) -> None:
    assert record_unversioned._share_age(release) == _default()
