"""Codex の相手役の費用と、使用量ログの行（#2944）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ndf.adapters import experiment_rows
from ndf.adapters import model_choice as mc
from ndf.adapters.agents import CodexUsage
from ndf.domains.records import jsonl, usage

# codex-cli 0.172.0 の実測（2026-11-27）。cached_input_tokens は input_tokens の内数
MEASURED = CodexUsage(input_tokens=32578, cached_input_tokens=13952, cache_write_input_tokens=0, output_tokens=5)
DEFAULT = experiment_rows.codex_prices()["default_model"]
PRICE = experiment_rows.codex_prices()["models"][DEFAULT]


def test_the_cached_and_written_tokens_are_priced_apart_from_the_rest_of_the_input():
    used = MEASURED._replace(cache_write_input_tokens=1000, output_tokens=100)
    plain = 32578 - 13952 - 1000
    total = plain * PRICE["input"] + 13952 * PRICE["cached_input"] + 1000 * PRICE["cache_write"] + 100 * PRICE["output"]
    assert usage.codex_cost(used, None) == (DEFAULT, pytest.approx(total / 1_000_000))


def test_a_model_outside_the_price_table_has_no_cost():
    assert usage.codex_cost(MEASURED, "no-such-model") == ("no-such-model", None)


def test_every_price_row_has_the_four_prices():
    table = experiment_rows.codex_prices()
    assert DEFAULT in table["models"] and table["source"].startswith("https://") and table["fetched"]
    assert all(set(p) == {"input", "cached_input", "cache_write", "output"} for p in table["models"].values())


def _written() -> list[dict]:
    return jsonl.read_rows(usage.usage_log_path(), kind="usage")[0]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    return mc.records_dir()


def _unpriced_rows(store) -> list[dict]:
    return [r for r in mc.read_experiments(mc.utc_now() - timedelta(days=1), store) if r["kind"] == "unpriced"]


META = {"name": "review", "assign_id": "a1"}


def _choice(store, model=None) -> mc.Choice:
    source = mc.Source.EXPERIMENT
    return mc.Choice("seat", "review", "codex", "review", None, model, None, source, source, assign_id="a1", store=store)


def test_a_codex_row_has_the_converted_cost_and_usage(store):
    usage.record_codex(MEASURED, META, _choice(store))
    (row,) = _written()
    assert (row["caller"], row["session_seq"], bool(row["launch_id"])) == ("codex-exec", 0, True)
    assert row["session_cost_usd"] == pytest.approx(usage.codex_cost(MEASURED, None)[1])
    assert row["usage"] == {
        "input_tokens": 32578 - 13952,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 13952,
    }
    assert _unpriced_rows(store) == []


def test_an_unknown_price_leaves_the_cost_out_and_adds_an_unpriced_row(store):
    usage.record_codex(MEASURED, META, _choice(store, "no-such-model"))
    (row,) = _written()
    assert "session_cost_usd" not in row
    assert [(r["assign_id"], r["model"]) for r in _unpriced_rows(store)] == [("a1", "no-such-model")]


def test_a_failed_run_of_a_priced_model_is_unknown_without_an_unpriced_row(store):
    usage.record_codex(None, META, _choice(store))
    assert [(r["usage"], "session_cost_usd" in r) for r in _written()] == [("unknown", False)]
    assert _unpriced_rows(store) == []


def test_an_unpriced_row_of_a_model_that_is_priced_now_is_not_counted():
    """単価表に後から載ったモデルは、割り付けの `uniform` の判定と report の表示に数えない。"""
    where = mc.Where("r", "review", "codex", "k")
    now = mc.utc_now()
    assign = {"kind": "assign", "at": now.isoformat(), "assign_id": "a1", **where.__dict__}
    rows = [assign, *({"kind": "unpriced", "assign_id": "a1", "model": m} for m in (DEFAULT, "no-such-model"))]
    assert experiment_rows.unpriced_models(rows, where, now - timedelta(days=1)) == ["no-such-model"]
