"""#2944: `ndf models pick`（値と出所を結果 JSON か 1 行 1 引数で返す）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli, identity
from ndf.contracts import causes


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".ndf").mkdir()
    arms = {"design": {"arms": {"claude": [{"model": "opus", "effort": "high"}]}}}
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {"stages": arms}}), encoding="utf-8")
    monkeypatch.chdir(root)
    for key in ("launch-model", "launch-effort", "launch-codex-model", "launch-codex-effort", "claude-effort-level"):
        monkeypatch.delenv(identity.env_name(key), raising=False)
    return root


def test_pick_returns_the_values_and_sources_as_a_result(repo, capsys, last_result):
    assert cli.main(["models", "pick", "--layer", "worker", "--name", "design", "--unit", "r:design"]) == 0
    report = last_result(capsys.readouterr().out)
    assert report["status"] == "ok"
    (item,) = report["items"]
    assert item["args"] == ["--model", "opus", "--effort", "high"] and item["stage"] == "design"
    assert (item["model_source"], item["unit"], item["raised"]) == ("base", "r:design", False)


def test_pick_as_args_prints_one_argument_per_line(repo, capsys):
    argv = ["models", "pick", "--layer", "seat", "--name", "review", "--runtime", "codex", "--args"]
    assert cli.main(argv) == 0
    assert capsys.readouterr().out == "-c\nmodel_reasoning_effort=low\n"


def test_pick_as_args_prints_an_accepted_model_name_as_written(repo, capsys, monkeypatch):
    """バックスラッシュを含むモデル名も、変えずに 1 行で出す。"""
    monkeypatch.setenv(identity.env_name("launch-model"), r"m\1\nx")
    assert cli.main(["models", "pick", "--layer", "seat", "--name", "fix", "--args"]) == 0
    assert capsys.readouterr().out == "--model\n" + "m\\1\\nx\n"


def test_pick_as_args_without_a_choice_prints_nothing(repo, capsys):
    assert cli.main(["models", "pick", "--layer", "seat", "--name", "fix", "--args"]) == 0
    assert capsys.readouterr().out == ""


def test_a_failure_as_args_writes_only_to_stderr(repo, capsys):
    (repo / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {"allocation": {"min_units": 1}}}))
    code = cli.main(["models", "pick", "--layer", "worker", "--name", "impl", "--args"])
    out = capsys.readouterr()
    assert code == causes.exit_code("precondition") and out.out == "" and "min_units" in out.err


def test_a_failure_as_json_is_a_stopped_result(repo, capsys, last_result):
    code = cli.main(["models", "pick", "--layer", "lane", "--name", "impl"])
    report = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("precondition") and report["cause"]["kind"] == "precondition"


def test_a_usage_error_as_args_writes_nothing_to_stdout(repo, capsys):
    code = cli.main(["models", "pick", "--name", "impl", "--args"])
    out = capsys.readouterr()
    assert code != 0 and out.out == ""
