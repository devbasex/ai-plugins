"""`ndf hooks judge-cases` の結果（#4000 の決定 9・受け入れ条件 3）。

実際の軽いモデルは打たない（偽の `run_cases` で、結果の形と停止理由だけを縛る）。
"""

from __future__ import annotations

import json

import pytest

from ndf.adapters import reply_judge
from ndf.domains.hooks import cli

NO_MISS = {"items": [], "metrics": {"cases": 9, "missed": 0, "calls": 27, "min_seconds": 0.7, "max_seconds": 1.5}}
MISSED = {
    "items": [{"id": "miss-1", "expect": "wait", "got": ["report"]}],
    "metrics": {"cases": 9, "missed": 1, "calls": 27, "min_seconds": 0.7, "max_seconds": 1.5},
}


@pytest.fixture(autouse=True)
def rounds(monkeypatch):
    """設定の周の数（`limits.json` の判定の行はまだ無い）。"""
    monkeypatch.setattr(reply_judge, "setting", lambda key: {"rounds": 3}[key])


def _emitted(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_judge_cases_reports_ok_when_no_case_misses(capsys):
    seen: list[tuple] = []

    def run(path, times):
        seen.append((path, times))
        return NO_MISS

    assert cli._judge_cases(None, None, run) == 0
    assert seen[0][0] == reply_judge.cases_path() and seen[0][1] == 3
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
        raise reply_judge.JudgeRefused("中継の確かめが断った: 認証を上書きする設定がある")

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

    mod_run = mod.reply_judge.run_cases
    try:
        mod.reply_judge.run_cases = run
        assert cli.main(["judge-cases", "--rounds", "1"]) == 0
    finally:
        mod.reply_judge.run_cases = mod_run
    assert _emitted(capsys)["status"] == "ok"
