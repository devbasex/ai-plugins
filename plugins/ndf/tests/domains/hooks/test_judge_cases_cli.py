"""`ndf hooks judge-cases` の結果（#4000 の決定 9・受け入れ条件 3）。

実際の軽いモデルは打たない（偽の `run_cases` で、結果の形と停止理由だけを縛る）。
"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks import judge_child
from ndf.domains.hooks import cli

NO_MISS = {"items": [], "metrics": {"cases": 9, "missed": 0, "calls": 27, "min_seconds": 0.7, "max_seconds": 1.5}}
MISSED = {
    "items": [{"id": "miss-1", "expect": "wait", "got": ["report"]}],
    "metrics": {"cases": 9, "missed": 1, "calls": 27, "min_seconds": 0.7, "max_seconds": 1.5},
}


def _emitted(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_judge_cases_reports_ok_when_no_case_misses(capsys):
    seen: list[tuple] = []

    def run(path, times):
        seen.append((path, times))
        return NO_MISS

    assert cli._judge_cases(None, None, run) == 0
    assert seen[0][0] == judge_child.cases_path() and seen[0][1] == 3
    got = _emitted(capsys)
    assert got["status"] == "ok" and got["metrics"]["calls"] == 27


def test_judge_cases_takes_the_set_and_the_rounds_from_the_arguments(tmp_path, capsys):
    seen: list[tuple] = []

    def run(path, times):
        seen.append((path, times))
        return NO_MISS

    assert cli._judge_cases(str(tmp_path / "c.json"), 1, run) == 0
    assert seen == [(tmp_path / "c.json", 1)]
    assert _emitted(capsys)["status"] == "ok"


def test_judge_cases_stops_when_a_case_misses(capsys):
    assert cli._judge_cases(None, None, lambda path, times: MISSED) == 1
    got = _emitted(capsys)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "check_failed"
    assert got["items"] == MISSED["items"]


def test_judge_cases_passes_the_reason_of_a_refusal_through(capsys):
    def run(path, times):
        raise judge_child.JudgeRefused("中継の確かめが断った: 認証を上書きする設定がある")

    assert cli._judge_cases(None, None, run) == 1
    got = _emitted(capsys)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused"
    assert got["cause"]["detail"] == "中継の確かめが断った: 認証を上書きする設定がある"


def test_judge_cases_stops_with_invalid_when_the_set_cannot_be_read(capsys):
    def run(path, times):
        raise ValueError("判定の例の組の形が違う: /tmp/c.json")

    assert cli._judge_cases(None, None, run) == 2
    got = _emitted(capsys)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


def test_judge_cases_is_a_subcommand_of_ndf_hooks(capsys):
    def run(path, times):
        return NO_MISS

    import ndf.domains.hooks.cli as mod

    mod_run = mod.judge_child.run_cases
    try:
        mod.judge_child.run_cases = run
        assert cli.main(["judge-cases", "--rounds", "1"]) == 0
    finally:
        mod.judge_child.run_cases = mod_run
    assert _emitted(capsys)["status"] == "ok"


@pytest.mark.parametrize("given", [0, -1])
def test_judge_cases_refuses_rounds_that_are_not_positive(capsys, given):
    seen: list[int] = []

    def run(path, times):
        seen.append(times)
        return NO_MISS

    assert cli._judge_cases(None, given, run) == 2
    assert seen == []  # 1 回も打たずに全件合格としない
    got = _emitted(capsys)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


@pytest.mark.parametrize("value", [0, True, "3", 1.5])
def test_judge_cases_refuses_rounds_of_the_setting_that_are_not_a_positive_integer(monkeypatch, capsys, value):
    monkeypatch.setattr(judge_child, "setting", lambda key: {"rounds": value}[key])
    seen: list[int] = []

    def run(path, times):
        seen.append(times)
        return NO_MISS

    assert cli._judge_cases(None, None, run) == 2
    assert seen == []
    assert _emitted(capsys)["cause"]["kind"] == "invalid"
