"""`claude -p` の費用のジャーナル（statusline の合算が読む）。"""

from __future__ import annotations

import json

import pytest

from ndf.adapters import run_log


def _rows(tmp_path):
    path = tmp_path / "context" / run_log.COST_JOURNAL_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_records_the_cost_from_the_last_line_of_stream_json(tmp_path):
    env = {"NDF_CONTEXT_DIR": str(tmp_path / "context"), "NDF_RELAY_NAME": "p12-lctx"}
    out = '{"type":"assistant"}\n{"type":"result","total_cost_usd":0.1559}\n'
    assert run_log.record_cost(out, env)
    [row] = _rows(tmp_path)
    assert (row["usd"], row["relay"]) == (0.1559, "p12-lctx")


@pytest.mark.parametrize("out", ["", "途中の出力", '{"total_cost_usd":"x"}', '{"total_cost_usd":true}', "[1]"])
def test_skips_output_without_a_cost(tmp_path, out):
    assert not run_log.record_cost(out, {"NDF_CONTEXT_DIR": str(tmp_path / "context")})
    assert _rows(tmp_path) == []


def test_an_unwritable_journal_is_skipped(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    assert not run_log.record_cost('{"total_cost_usd":1}', {"NDF_CONTEXT_DIR": str(blocked / "context")})


def test_an_unreadable_output_file_is_skipped(tmp_path):
    assert not run_log.record_cost_file(tmp_path / "missing.out")
