"""危険フラグが立ったときの全体のテストの扱い（#933 決定 22・#3048）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 最終ゲートへ寄せる | どの戦略でも、検証の巡の中で全体テストを走らせず、立ったフラグと項目を `whole_test.deferred` に残す |
| 基準を示す | 報告と改修計画に着手前の全体のテストの結果と HEAD が出る |
"""

from __future__ import annotations

import argparse
import sys

import pytest

from crossref_helpers import (
    TEST_TOTAL,
    build_git_flow,
    commit_existing_tests,
    implement_items,
    read_state,
    strategy_state,
    write_state,
    planned_item,
    emitted,
    write_file,
)


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, symbol="add"):
    return planned_item(item_id, rank, symbol=symbol, command=["pytest", "-q", "tests/test_calc.py"])


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _implement(flow, cmd_setup, cmd_phases, cmd_implement, changes, late=False, **state_overrides):
    """項目ごとに `changes[id](work)` を当てて 1 コミットずつ積み、取り込む。"""
    items = [_item(item_id, rank) for rank, item_id in enumerate(changes, start=1)]
    implement_items(flow, cmd_phases, cmd_implement, items, changes, late=late, **state_overrides)


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _touch_other(name):
    """項目の `path` の外のファイルを触る（D1 が立つ）。"""
    return lambda w: write_file(w, f"src/{name}.py", "X = 1\n")


@pytest.mark.parametrize("strategy", ["local-full", "local-scoped-ci-whole"])
def test_every_strategy_defers_the_whole_test_to_the_final_gate(
    flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys, patch_lib, strategy
):
    """#3048 — どの戦略でも、危険フラグが立っても検証の中で全体テストを走らせず、最終ゲートへ寄せる。"""
    whole_runs: list = []
    original = sys.modules["refactor_lib.targets"].run_with_timeout

    def spy_run(command, cwd, timeout, grace=5.0, output=None):
        if command == strategy_state(strategy)["suites"][0]["command"]:
            whole_runs.append(command)
        return original(command, cwd, timeout, grace, output)

    patch_lib("run_with_timeout", spy_run)
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other")}, strategy=strategy_state(strategy))
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert not record.get("ran") and record["resolution"] == "deferred"
    assert record["deferred"] == {"flags": ["D1"], "items": ["I-001"]}
    assert whole_runs == [], "全体テストのコマンドを巡の中で起動しない"
    assert _items(flow)["I-001"]["status"] == "verified"


def test_the_report_and_the_plan_show_the_baseline(flow, cmd_report, plan, capsys):
    state = read_state(flow["path"])
    state["baseline_test"].update({"head": "0123456789abcdef", "seconds": 12.5})
    write_state(flow["path"], state)

    _call(cmd_report, "cmd_report", metrics=False)
    out = capsys.readouterr().out
    body = plan.format_plan(read_state(flow["path"]))

    for text in (out, body):
        assert "0123456" in text and "12.5" in text
