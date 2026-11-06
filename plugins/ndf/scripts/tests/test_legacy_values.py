"""旧い値の読み替え（#2620）。旧い値を持つ記録が、新しい値を持つ記録と同じに読まれること。

この文書と `scripts/data/legacy-values.json` だけが、書かれなくなった値をそのまま持つ（`.ndf/glossary.json` の外す範囲）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import legacy_values  # noqa: E402
from step_result import EXIT_UNREADABLE, StepError  # noqa: E402

OLD_WORD = "配布"


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("stage", "配布", "デプロイ"),
        ("record_heading", "## 配布の記録", "## リリース記録"),
        ("phase", "配布（開発版）", "開発版のデプロイ"),
        ("phase", "配布（本番）", "リリース"),
        ("phase", "配布の依頼", "リリースの依頼"),
        ("lap_outcome", "配布待ち", "リリース待ち"),
        ("failure_kind", "配布", "リリース"),
        ("failure_kind", "配布の失敗", "リリースの失敗"),
        ("pipeline_stage", "配布", "デプロイ"),
    ],
)
def test_old_value_reads_as_the_new_value_and_the_new_value_stays(name, old, new):
    assert legacy_values.normalize(name, old) == new
    assert legacy_values.normalize(name, new) == new


def test_record_stage_converts_the_prefix_and_keeps_the_reason():
    got = legacy_values.normalize("record_stage_prefix", "配布なし（本番の記録なし）")
    assert got == "デプロイなし（本番の記録なし）"
    assert legacy_values.normalize("record_stage_prefix", "本番") == "本番"


def test_a_value_outside_the_table_passes_through():
    assert legacy_values.normalize("stage", "実装") == "実装"
    assert legacy_values.normalize("stage", None) is None


def test_unknown_set_stops():
    with pytest.raises(StepError) as e:
        legacy_values.normalize("無い集合", "a")
    assert e.value.code == EXIT_UNREADABLE


@pytest.mark.parametrize(
    "text",
    [
        '{"version": 1, "sets": {"a": {"x": "y", "x": "z"}}}',  # 旧い値が 2 つの新しい値へ変換される
        '{"version": 1, "sets": {"a": {"x": "y", "y": "z"}}}',  # 新しい値が旧い値の欄にある
        '{"version": 2, "sets": {}}',
        "読めない",
    ],
)
def test_a_broken_table_stops(text):
    with pytest.raises(StepError) as e:
        legacy_values.parse(text)
    assert e.value.code == EXIT_UNREADABLE


def test_new_values_of_every_set_do_not_carry_the_old_word():
    for name, pairs in legacy_values.table().items():
        assert all(OLD_WORD not in new for new in pairs.values()), name


def test_the_shipped_table_is_json_with_every_set_of_the_design():
    raw = json.loads(legacy_values.TABLE.read_text(encoding="utf-8"))
    assert set(raw["sets"]) == {"stage", "record_heading", "record_stage_prefix", "phase", "lap_outcome", "failure_kind", "pipeline_stage"}
