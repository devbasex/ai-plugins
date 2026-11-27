"""解析の書き出しが、手で足した `test.protected_files`・`test.local_total_minutes` を消さない（#4566 の Task 2）。"""

from __future__ import annotations

from ndf.domains.project import write

SUITES = [{"name": "unit", "runner": "pytest", "command": "pytest -q"}]


def test_the_analysis_keeps_the_keys_written_by_hand_into_the_test_section():
    analyzed = {"suites": SUITES}
    by_hand = {"suites": SUITES, "protected_files": ["package-lock.json"], "local_total_minutes": 240}
    assert write.item_problem("test", by_hand) is None
    value, rows, ours = write.merge_item("test", (True, by_hand), analyzed, "測った", {"test": write.value_digest(analyzed)})
    assert value == by_hand
    assert not ours
    assert rows == [{"kind": "mismatch", "key": "test", "declared": by_hand, "analyzed": analyzed}]


def test_a_non_positive_total_is_refused_by_the_schema():
    assert write.item_problem("test", {"suites": SUITES, "local_total_minutes": 0}) is not None
