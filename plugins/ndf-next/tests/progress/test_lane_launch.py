"""レーンの conductor を起こす最初の入力と、名前つきのラッパーでの起動（#3097）。"""

from __future__ import annotations

import json
import subprocess

import pytest

from ndf.progress.lane import LaneRow, launch


def _row(args=()):
    return LaneRow(key="r2", input={"kind": "sprint", "issues": [5, 6], "args": list(args)}, state="pending")


def test_start_commands_create_the_sprint_under_the_lane_then_start_the_row():
    new, start = launch.start_commands("p6", "a", "lane-id", _row())
    assert new["argv"][1:] == ["progress", "new", "p6-a-r2", "--mode", "standard", "--issues", "5", "6", "--parent", "lane-id"]
    assert start["argv"][1:] == ["lanes", "row", "start", "p6", "a", "r2", "--sprint"] and start["ask"] == ["sprint"]


def test_start_commands_keep_the_mode_given_by_the_row():
    new, _ = launch.start_commands("p6", "a", "lane-id", _row(["--mode", "fast"]))
    assert new["argv"].count("--mode") == 1 and new["argv"][-2:] == ["--mode", "fast"]


def test_sprint_names_of_two_plans_with_the_same_lane_do_not_collide():
    assert launch.sprint_name("p6", "a", "r1") != launch.sprint_name("p7", "a", "r1")


def test_a_loop_row_is_refused_instead_of_failing_on_missing_issues():
    row = LaneRow(key="r1", input={"kind": "loop", "loop": "backlog"}, state="pending")
    with pytest.raises(launch.Unsupported, match="loop"):
        launch.start_commands("p6", "a", "lane-id", row)


def test_the_wrapper_name_differs_by_repo_and_by_the_plan_lane_boundary():
    from ndf.domains.relay import signals

    name = launch.wrapper_name("o/r", "p6", "a")
    assert name.startswith("p6-a-") and signals.NAME_RE.match(name)
    assert name != launch.wrapper_name("o/other", "p6", "a")
    assert launch.wrapper_name("o/r", "a-b", "c") != launch.wrapper_name("o/r", "a", "b-c")
    assert signals.NAME_RE.match(launch.wrapper_name("o/r", "p" * 60, "l" * 60))


def test_cancel_commands_cancel_the_running_sprint_then_end_the_lane_row():
    cancel, end = launch.cancel_commands("s1")
    assert cancel["argv"][1:4] == ["progress", "cancel", "s1"]
    assert end["argv"][1:] == ["lanes", "row", "end", "s1", "--outcome", "cancelled"]


def test_stop_stops_the_wrapper_of_the_lane_and_reads_the_result():
    run, calls = _runner(stdout=json.dumps({"status": "ok", "summary": "p6-a を止めた"}))
    assert launch.stop("o/r", "p6", "a", run=run) == {"ok": True, "detail": "p6-a を止めた"}
    assert calls[0][1:] == ["relay", "stop", launch.wrapper_name("o/r", "p6", "a")]
    run, _ = _runner(stdout=json.dumps({"status": "stopped", "summary": "終わらない"}), code=1)
    assert launch.stop("o/r", "p6", "a", run=run) == {"ok": False, "detail": "終わらない"}


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
    got = launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)
    assert got == {"ok": True, "detail": "起こした"}
    argv = calls[0]
    assert argv[1:3] == ["relay", "spawn"] and argv[argv.index("--name") + 1] == launch.wrapper_name("o/r", "p6", "a") and argv[-1] == "入力"
    assert argv[argv.index("--role") + 1] == "conductor" and argv[argv.index("--cwd") + 1] == str(tmp_path)


def test_spawn_reports_a_refusal_or_a_broken_output_as_a_failure(tmp_path):
    run, _ = _runner(stdout=json.dumps({"status": "stopped", "summary": "p6-a は既に動いている"}), code=1)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "p6-a は既に動いている"}
    run, _ = _runner(stdout="not json", stderr="壊れた", code=2)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "壊れた"}
    run, _ = _runner(error=FileNotFoundError("ndf"))
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)["ok"] is False


# --- spawn の現状固定（リファクタリングの前に今の出力を記録する） ----------------------------------------


def test_spawn_passes_the_whole_argv_and_the_run_options(tmp_path):
    from ndf import identity

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ログ\n" + json.dumps({"status": "ok", "summary": "起こした"}) + "\n", "")

    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": True, "detail": "起こした"}
    argv, kwargs = calls[0]
    assert argv == [
        str(identity.entrypoint(identity.ROOT)),
        "relay",
        "spawn",
        "--name",
        launch.wrapper_name("o/r", "p6", "a"),
        "--cwd",
        str(tmp_path),
        "--role",
        "conductor",
        "--plan",
        "p6",
        "入力",
    ]
    assert kwargs == {"capture_output": True, "text": True, "check": False}


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "got"),
    [
        # 出力が空で stderr も空なら終了コードを書く
        ("", "", 0, {"ok": False, "detail": "終了コード 0"}),
        ("", "", 3, {"ok": False, "detail": "終了コード 3"}),
        # 最後の行の JSON だけを読む。summary が無ければ detail は None
        (json.dumps({"status": "ok"}), "", 0, {"ok": True, "detail": None}),
        (json.dumps({"status": "stopped"}), "エラー", 1, {"ok": False, "detail": None}),
        # 空の JSON は壊れた出力と同じ扱い（stderr か終了コード）
        (json.dumps({}), "", 0, {"ok": False, "detail": "終了コード 0"}),
        # stderr は末尾の 300 文字だけを残す
        ("not json", "x" * 100 + "e" * 300, 1, {"ok": False, "detail": "e" * 300}),
    ],
)
def test_spawn_reads_the_last_line_as_it_is_now(tmp_path, stdout, stderr, code, got):
    run, _ = _runner(stdout=stdout, stderr=stderr, code=code)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == got


def test_spawn_reports_the_os_error_in_the_detail(tmp_path):
    run, _ = _runner(error=PermissionError("denied"))
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "ラッパーを起こせない: denied"}


def test_spawn_uses_subprocess_run_when_no_runner_is_given(tmp_path, monkeypatch):
    run, calls = _runner(stdout=json.dumps({"status": "ok", "summary": "起こした"}))
    monkeypatch.setattr(subprocess, "run", run)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力") == {"ok": True, "detail": "起こした"}
    assert calls[0][-1] == "入力"
