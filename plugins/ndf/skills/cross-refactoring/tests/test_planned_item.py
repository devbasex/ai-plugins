"""共通化前の計画項目の値を固定する現状固定テスト。"""

import pytest

from crossref_helpers import planned_item


@pytest.mark.parametrize("item_id, rank, symbol", [("I-001", 1, "add"), ("I-002", 2, "total"), ("I-001", 1, "total")])
def test_planned_item_preserves_the_original_test_inputs(item_id, rank, symbol):
    """judged の _item と最終ゲートの直書きの辞書を、そのまま期待値として残す。"""
    assert planned_item(item_id, rank, symbol=symbol, command=["pytest", "-q", "tests/test_calc.py"]) == {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": symbol,
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": "2099-01-01T00:00:00+00:00",
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
    }
