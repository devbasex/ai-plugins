"""レーンの conductor を起こす最初の入力と、名前つきのラッパーでの起動（#3097）。"""

from __future__ import annotations

import json
import subprocess

from ndf.progress.lane import LaneRow, launch


def _row(args=()):
    return LaneRow(key="r2", input={"kind": "sprint", "issues": [5, 6], "args": list(args)}, state="pending")


def test_start_commands_create_the_sprint_under_the_lane_then_start_the_row():
    new, start = launch.start_commands("p6", "a", "lane-id", _row())
    assert new["argv"][1:] == ["progress", "new", "a-r2", "--mode", "standard", "--issues", "5", "6", "--parent", "lane-id"]
    assert start["argv"][1:] == ["lanes", "row", "start", "p6", "a", "r2", "--sprint"] and start["ask"] == ["sprint"]


def test_start_commands_keep_the_mode_given_by_the_row():
    new, _ = launch.start_commands("p6", "a", "lane-id", _row(["--mode", "fast"]))
    assert new["argv"].count("--mode") == 1 and new["argv"][-2:] == ["--mode", "fast"]


def test_cancel_commands_cancel_the_running_sprint():
    (cmd,) = launch.cancel_commands("s1")
    assert cmd["argv"][1:4] == ["progress", "cancel", "s1"]


def test_prompt_carries_the_commands_as_json():
    cmds = launch.cancel_commands("s1")
    text = launch.prompt("p6", "a", cmds)
    assert json.loads(text[text.index("[") :]) == cmds


def _runner(stdout="", stderr="", code=0, error=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if error is not None:
            raise error
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    return run, calls


def test_spawn_names_the_wrapper_after_the_plan_and_the_lane(tmp_path):
    run, calls = _runner(stdout=json.dumps({"tool": "relay", "status": "ok", "summary": "起こした"}) + "\n")
    got = launch.spawn("p6", "a", tmp_path, "入力", run=run)
    assert got == {"ok": True, "detail": "起こした"}
    argv = calls[0]
    assert argv[1:3] == ["relay", "spawn"] and argv[argv.index("--name") + 1] == "p6-a" and argv[-1] == "入力"
    assert argv[argv.index("--role") + 1] == "conductor" and argv[argv.index("--cwd") + 1] == str(tmp_path)


def test_spawn_reports_a_refusal_or_a_broken_output_as_a_failure(tmp_path):
    run, _ = _runner(stdout=json.dumps({"status": "stopped", "summary": "p6-a は既に動いている"}), code=1)
    assert launch.spawn("p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "p6-a は既に動いている"}
    run, _ = _runner(stdout="not json", stderr="壊れた", code=2)
    assert launch.spawn("p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "壊れた"}
    run, _ = _runner(error=FileNotFoundError("ndf"))
    assert launch.spawn("p6", "a", tmp_path, "入力", run=run)["ok"] is False
