from __future__ import annotations

import importlib.util
import json
import pathlib


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


def _load_monitor_outcome():
    spec = importlib.util.spec_from_file_location(
        "ndf_lib_monitor_outcome_unit", LIB / "monitor_outcome.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_read_journal_returns_empty_when_file_is_missing(tmp_path):
    assert _load_monitor_outcome().read_journal(tmp_path) == []


def test_read_journal_ignores_malformed_and_non_object_rows(tmp_path):
    valid = {"status": "OK"}
    (tmp_path / "monitor-outcomes.jsonl").write_text(
        json.dumps(valid) + "\n{not json\n" + json.dumps(["not", "an", "object"]) + "\n",
        encoding="utf-8",
    )

    assert _load_monitor_outcome().read_journal(tmp_path) == [valid]
