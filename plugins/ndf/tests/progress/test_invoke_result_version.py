"""作業の結果の形のバージョンの確かめ（`invoke.read_report` と Skill の回答。#2339 の受け入れ条件 1）。"""

from __future__ import annotations

import json
import subprocess

import pytest

from ndf.adapters import claude
from ndf.contracts import result
from ndf.progress import invoke


def _report(**extra):
    return {**result.result("plan", "ok", "済んだ", outputs={"pr": 3}), **extra}


def _run(kind, args):
    return invoke.StageRun(kind, "x", {"issues": [5]}, ("pr",), "none", "s", args)


def _of_process(report, tmp_path):
    line = json.dumps(report) + "\n"
    run = _run("script", {"argv": ["x"]})
    return invoke.invoke(run, tmp_path, tmp_path, runner=lambda argv, cwd, env: subprocess.CompletedProcess(argv, 0, line, ""))


def _of_skill(report, tmp_path):
    reply = claude.ClaudeReply(ok=True, answer=report, raw="", cost=0.0)
    return invoke.invoke(_run("skill", {"skill": "x"}), tmp_path, tmp_path, call_claude=lambda *a, **k: reply)


@pytest.fixture(autouse=True)
def _installed_plugins(monkeypatch):
    """Skill の起動の引数を、導入済みのプラグインを CLI に聞かずに組む。"""
    monkeypatch.setattr(claude, "list_plugins", lambda **k: [])


@pytest.mark.parametrize("extra", [{}, {"version": 1}])
def test_read_report_takes_a_result_of_the_current_version(extra):
    assert invoke.read_report(json.dumps(_report(**extra))) == _report(**extra)


def test_read_report_turns_a_result_of_a_newer_version_into_stopped():
    got = invoke.read_report(json.dumps(_report(version=2)))
    assert got is not None and got["status"] == "stopped" and got["cause"]["kind"] == "precondition" and "outputs" not in got


@pytest.mark.parametrize("of", [_of_process, _of_skill])
@pytest.mark.parametrize(("version", "kind"), [(2, "precondition"), ("1", "bad_report")])
def test_a_result_of_a_newer_version_is_told_from_the_wrong_shape(tmp_path, of, version, kind):
    got = of(_report(version=version), tmp_path)
    assert got.cause is not None and got.cause["kind"] == kind and got.outputs == {}


@pytest.mark.parametrize("of", [_of_process, _of_skill])
def test_a_result_of_the_current_version_passes_its_outputs(tmp_path, of):
    assert of(_report(version=1), tmp_path).outputs == {"pr": 3}
