"""レーンの conductor を起動する最初の入力と、名前つきのラッパーでの起動（#3097）。"""

from __future__ import annotations

import functools
import json
import os
import subprocess

import pytest

from ndf import identity
from ndf.adapters import claude
from ndf.progress.lane import LaneRow, launch


@pytest.fixture(autouse=True)
def _no_claude(tmp_path, monkeypatch):
    """`spawn` が導入の一覧を読む `claude` を、手元に入っていても打たない（無いコマンドにして一覧を読めない形にする）。"""
    monkeypatch.setattr(claude, "CLAUDE", str(tmp_path / "no-claude"))


def _fake_run(stdout="", stderr="", code=0, error=None):
    """偽の run（呼ばれた argv を calls に積み、決まった出力を返すか error を上げる）。"""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if error is not None:
            raise error
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    return run, calls


def _row(args=()):
    return LaneRow(key="r2", input={"kind": "sprint", "issues": [5, 6], "args": list(args)}, state="pending")


def test_start_commands_create_the_sprint_under_the_lane_then_start_the_row():
    new, start, going = launch.start_commands("p6", "a", "lane-id", _row())
    assert new["argv"][1:] == [
        "progress",
        "new",
        launch.sprint_name("p6", "a", "r2"),
        "--mode",
        "standard",
        "--issues",
        "5",
        "6",
        "--parent",
        "lane-id",
    ]
    assert start["argv"][1:] == ["lanes", "row", "start", "p6", "a", "r2", "--sprint"] and start["ask"] == ["sprint"]
    assert going == {"argv": [launch.result.ENTRY, "progress", "next"], "ask": ["sprint"]}


def test_the_start_prompt_sets_the_goal_and_goes_on_past_starting_the_row():
    # #3858: /goal が無いと conductor は行を始めるコマンドを打って止まる。1 行目は hook が conductor の起動と読む形にする
    from ndf.domains.hooks.guards import unattended

    text = launch.start_prompt("p6", "a", "lane-id", _row())
    head = text.splitlines()[0]
    assert head.startswith("/goal /ndf:development-workflow #5 #6 -- ") and unattended.is_target(text)
    assert "ndf lanes row end" in head[head.index(" -- ") :]
    cmds = json.loads(text[text.index("[") :])
    assert [c["argv"][1:3] for c in cmds] == [["progress", "new"], ["lanes", "row"], ["progress", "next"]]
    loop = LaneRow(key="r1", input={"kind": "loop", "loop": "backlog"}, state="pending")
    text = launch.start_prompt("p6", "lr", "lane-id", loop)
    assert text.startswith("/goal /ndf:development-workflow --loop lr -- ") and unattended.is_target(text)
    assert json.loads(text[text.index("[") :])[-1]["argv"][1:] == ["loop", "get", "lr"]


def test_start_commands_keep_the_mode_given_by_the_row():
    new, *_ = launch.start_commands("p6", "a", "lane-id", _row(["--mode", "fast"]))
    assert new["argv"].count("--mode") == 1 and new["argv"][-2:] == ["--mode", "fast"]


def test_sprint_names_of_two_plans_with_the_same_lane_do_not_collide():
    assert launch.sprint_name("p6", "a", "r1") != launch.sprint_name("p7", "a", "r1")
    assert launch.sprint_name("dev-api", "tests", "r1") != launch.sprint_name("dev", "api-tests", "r1")
    assert launch.sprint_name("p6", "a", "r1").startswith("p6-a-r1-")


def test_a_loop_row_starts_the_loop_named_after_the_lane():
    # #3489・#3558: 開発ループの行もレーンの子のステートマシンを作り、その ID で行を実行中にして、ループをその行に結び付ける
    row = LaneRow(key="r1", input={"kind": "loop", "loop": "backlog"}, state="pending")
    new, row_start, start, going = launch.start_commands("p6", "lr-tail", "lane-id", row)
    assert new["argv"][1:] == ["progress", "new", launch.sprint_name("p6", "lr-tail", "r1"), "--loop", "lr-tail", "--parent", "lane-id"]
    assert row_start["argv"][1:] == ["lanes", "row", "start", "p6", "lr-tail", "r1", "--sprint"] and row_start["ask"] == ["sprint"]
    assert start["argv"][1:] == ["loop", "start", "lr-tail", "--row"] and start["ask"] == ["sprint", "session"]
    assert going["argv"][1:] == ["loop", "get", "lr-tail"]


def test_a_loop_row_is_cancelled_after_halting_the_loop_and_goes_on_by_reading_the_loop():
    # #3558: 止めた計画では先にループを止め、続けるときはステートマシンへ next を打たない（工程の無いステートマシンは next で完了になる）
    halt, cancel, end = launch.cancel_commands("s1", "lr")
    assert halt["argv"][1:] == ["loop", "stop", "lr", "--by", "organizer", "--reason", launch.STOP_REASON, "--now"]
    assert cancel["argv"][1:3] == ["progress", "cancel"] and end["argv"][-2:] == ["--outcome", "cancelled"]
    assert launch.resume_commands("s1", "lr")[1]["argv"][1:] == ["loop", "get", "lr"]
    text = launch.goal_prompt("p6", "lr", "s1", "lr")
    assert text.startswith("/goal /ndf:development-workflow --loop lr -- ")
    assert json.loads(text[text.index("[") :]) == [{"argv": [launch.result.ENTRY, "loop", "get", "lr"]}]


def test_a_row_of_an_unknown_kind_is_refused_instead_of_failing_on_missing_issues():
    row = LaneRow(key="r1", input={"kind": "other"}, state="pending")
    with pytest.raises(launch.Unsupported, match="other"):
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


def test_resume_commands_mark_the_next_then_run_next_on_the_sprint():
    mark, nxt = launch.resume_commands("s1")
    assert mark["argv"][1:] == ["lanes", "row", "resumed", "s1"]
    assert nxt["argv"][1:] == ["progress", "next", "s1"]


def test_stop_stops_the_wrapper_of_the_lane_and_reads_the_result():
    run, calls = _fake_run(stdout=json.dumps({"status": "ok", "summary": "p6-a を止めた"}))
    assert launch.stop("o/r", "p6", "a", run=run) == {"ok": True, "detail": "p6-a を止めた"}
    assert calls[0][1:] == ["relay", "stop", launch.wrapper_name("o/r", "p6", "a")]
    run, _ = _fake_run(stdout=json.dumps({"status": "stopped", "summary": "終わらない"}), code=1)
    assert launch.stop("o/r", "p6", "a", run=run) == {"ok": False, "detail": "終わらない"}


def test_prompt_carries_the_commands_as_json():
    cmds = launch.cancel_commands("s1")
    text = launch.prompt("p6", "a", cmds)
    assert json.loads(text[text.index("[") :]) == cmds


def test_spawn_names_the_wrapper_after_the_plan_and_the_lane(tmp_path):
    run, calls = _fake_run(stdout=json.dumps({"tool": "relay", "status": "ok", "summary": "起動した"}) + "\n")
    got = launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)
    assert got == {"ok": True, "detail": "起動した"}
    argv = calls[0]
    assert (
        argv[1:3] == ["relay", "spawn"] and argv[argv.index("--name") + 1] == launch.wrapper_name("o/r", "p6", "a") and argv[-1] == "入力"
    )
    assert argv[argv.index("--role") + 1] == "conductor" and argv[argv.index("--cwd") + 1] == str(tmp_path)


def test_spawn_reports_a_refusal_or_a_broken_output_as_a_failure(tmp_path):
    run, _ = _fake_run(stdout=json.dumps({"status": "stopped", "summary": "p6-a は既に動いている"}), code=1)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "p6-a は既に動いている"}
    run, _ = _fake_run(stdout="not json", stderr="壊れた", code=2)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "壊れた"}
    run, _ = _fake_run(error=FileNotFoundError("ndf"))
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)["ok"] is False


# --- spawn の現状固定（リファクタリングの前に今の出力を記録する） ----------------------------------------


def test_spawn_passes_the_whole_argv_and_the_run_options(tmp_path):
    from ndf import identity

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ログ\n" + json.dumps({"status": "ok", "summary": "起動した"}) + "\n", "")

    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": True, "detail": "起動した"}
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
    run, _ = _fake_run(stdout=stdout, stderr=stderr, code=code)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == got


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "got"),
    [
        # 複数行は最後の行だけを JSON として読む（途中の行が JSON でも読まない）
        (
            json.dumps({"status": "ok", "summary": "前"}) + "\n" + json.dumps({"status": "stopped", "summary": "後"}),
            "",
            1,
            {"ok": False, "detail": "後"},
        ),
        ("ログ\n" + json.dumps({"status": "ok", "summary": "起動した"}) + "\n\n", "", 0, {"ok": True, "detail": "起動した"}),
        # 最後の行が壊れていれば、前の行が読めても壊れた出力と同じ扱い
        (json.dumps({"status": "ok", "summary": "前"}) + "\n壊れた", "エラー", 1, {"ok": False, "detail": "エラー"}),
        # 空白だけの出力は空の出力と同じ扱い
        ("  \n\t\n", "", 4, {"ok": False, "detail": "終了コード 4"}),
    ],
)
def test_read_spawn_reads_only_the_last_line_as_it_is_now(stdout, stderr, code, got):
    assert launch.read_spawn(stdout, stderr, code) == got


@pytest.mark.parametrize("stdout", ["[1]", "null", "3", '"ok"'])
def test_read_spawn_raises_on_a_last_line_that_is_json_but_not_an_object_as_it_is_now(stdout):
    """辞書でない JSON の最後の行は、今は AttributeError で落ちる（現状の記録。直すのは別の変更）。"""
    with pytest.raises(AttributeError):
        launch.read_spawn(stdout, "", 0)


@pytest.mark.parametrize(
    ("stdout", "got"),
    [
        ("", None),
        ("  \n", None),
        ("壊れた", None),
        ("[1]", None),
        ("null", None),
        ("{}", {}),
        ('{"status": "ok"}\n壊れた', None),
        ('壊れた\n{"status": "gate"}\n', {"status": "gate"}),
    ],
)
def test_probe_reads_only_the_last_line_as_an_object_as_it_is_now(stdout, got):
    run, calls = _fake_run(stdout=stdout)
    assert launch.sprint_wait("s1", run=run) == got
    assert calls[0][1:] == ["progress", "wait", "s1", "--timeout", "0"]


def test_spawn_reports_the_os_error_in_the_detail(tmp_path):
    run, _ = _fake_run(error=PermissionError("denied"))
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run) == {"ok": False, "detail": "ラッパーを起動できない: denied"}


def test_spawn_uses_subprocess_run_when_no_runner_is_given(tmp_path, monkeypatch):
    run, calls = _fake_run(stdout=json.dumps({"status": "ok", "summary": "起動した"}))
    monkeypatch.setattr(subprocess, "run", run)
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力") == {"ok": True, "detail": "起動した"}
    assert calls[0][-1] == "入力"


def _version(base, name, mtime):
    entry = base / name / "bin" / "ndf"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    entry.chmod(0o755)
    import os

    os.utime(entry, (mtime, mtime))
    return base / name


def test_a_removed_version_dir_launches_with_the_newest_version_and_leaves_a_line(tmp_path, monkeypatch, capsys):
    """起動したバージョンの保存先がプラグインの更新で消えても、同じ親の下の最も新しいバージョンでレーンを起動し、切り替えた 1 行を
    残す（#2583 の条件 17・18。更新を 2 回行った後を模す）。"""
    from ndf import identity

    _version(tmp_path, "11.0.1", 1_000)
    newest = _version(tmp_path, "11.0.2", 2_000)
    monkeypatch.setattr(identity, "ROOT", tmp_path / "11.0.0")
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "ok", "summary": "起動した"}) + "\n", "")

    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)["ok"]
    assert seen[0][0] == str(newest / "bin" / "ndf")
    err = capsys.readouterr().err
    assert "11.0.0" in err and "11.0.2" in err and len(err.strip().splitlines()) == 1


def test_a_present_version_dir_is_used_as_it_is(tmp_path, monkeypatch, capsys):
    from ndf import identity

    own = _version(tmp_path, "11.0.0", 1_000)
    _version(tmp_path, "11.0.2", 2_000)
    monkeypatch.setattr(identity, "ROOT", own)
    assert launch.entry() == own / "bin" / "ndf" and capsys.readouterr().err == ""


# --- _probe 経由のサブコマンドの読みの現状固定（正常・status 違い・末尾が JSON でない・打てない） ------------------

_LISTED = {"name": "w", "running": True}


@pytest.mark.parametrize(
    ("kwargs", "got"),
    [
        ({"stdout": json.dumps({"status": "ok", "items": [_LISTED, {"x": 1}, "s"]})}, {"w": _LISTED}),
        ({"stdout": json.dumps({"status": "stopped", "items": [_LISTED]}), "code": 1}, None),
        ({"stdout": "壊れた"}, None),
        ({"error": OSError("無い")}, None),
    ],
)
def test_wrappers_reads_the_relay_list_as_it_is_now(kwargs, got):
    run, calls = _fake_run(**kwargs)
    assert launch.wrappers(run) == got
    assert calls[0][1:] == ["relay", "ls"]


@pytest.mark.parametrize(
    ("kwargs", "got"),
    [
        ({"stdout": json.dumps({"status": "ok"})}, True),
        ({"stdout": json.dumps({"status": "stopped"}), "code": 1}, False),
        ({"stdout": "壊れた"}, False),
        ({"error": OSError("無い")}, False),
    ],
)
def test_account_ok_reads_the_relay_account_as_it_is_now(kwargs, got):
    run, calls = _fake_run(**kwargs)
    assert launch.account_ok(run) is got
    assert calls[0][1:] == ["relay", "account", "show"]


@pytest.mark.parametrize(
    ("kwargs", "got"),
    [
        ({"stdout": json.dumps({"status": "ok", "summary": "x"})}, {"status": "ok", "summary": "x"}),
        # status を見ずに結果 JSON をそのまま返す
        ({"stdout": json.dumps({"status": "stopped"}), "code": 1}, {"status": "stopped"}),
        ({"stdout": "壊れた"}, None),
        ({"error": OSError("無い")}, None),
    ],
)
def test_sprint_wait_reads_the_wait_as_it_is_now(kwargs, got):
    run, calls = _fake_run(**kwargs)
    assert launch.sprint_wait("s1", run) == got
    assert calls[0][1:] == ["progress", "wait", "s1", "--timeout", "0"]


# --- installed_entry・spawn: 今の有効な導入の bin/ndf でラッパーを起動する（#3909 の決定 4・受け入れ条件 5） ---------


def _cache_root(tmp_path, version):
    """`CLAUDE_CONFIG_DIR` を `tmp_path/"claude"` に向けたときのキャッシュの `ai-plugins/ndf/<version>` のパス（作らない）。"""
    return tmp_path / "claude" / "plugins" / "cache" / "ai-plugins" / "ndf" / version


def _cached_version(tmp_path, version, mtime=1_000, executable=True):
    """`CLAUDE_CONFIG_DIR` を `tmp_path/"claude"` に向けたときのキャッシュの `ai-plugins/ndf/<version>` に、bin/ndf と plugin.json を置く。"""
    root = _cache_root(tmp_path, version)
    entry = root / "bin" / "ndf"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    entry.chmod(0o755 if executable else 0o644)
    os.utime(entry, (mtime, mtime))
    identity.manifest_path(root).parent.mkdir(parents=True)
    identity.manifest_path(root).write_text(json.dumps({"name": "ndf", "version": version}), encoding="utf-8")
    return root


def _own_root(tmp_path, monkeypatch, version="11.0.9", present=True):
    """自分の根をキャッシュのバージョン `version` のディレクトリにする（`present` が偽なら作らない＝消えた後）。"""
    root = _cached_version(tmp_path, version) if present else _cache_root(tmp_path, version)
    monkeypatch.setattr(identity, "ROOT", root)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return root


def _listed(*roots, market="ai-plugins"):
    """`claude plugin list --json` の偽。`roots` の各根を有効な user スコープの行にして返す（`roots` が None なら読めない）。"""
    rows = [{"id": f"ndf@{market}", "enabled": True, "scope": "user", "installPath": str(r), "version": r.name} for r in roots]

    def listed(cwd=None):
        return rows

    return listed


def _unreadable(cwd=None):
    return None


def _one_line(err):
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    return lines[0]


def test_3909_installed_entry_picks_the_listed_version_under_the_same_parent(tmp_path, monkeypatch, capsys):
    _own_root(tmp_path, monkeypatch, "11.0.9")
    moved = _cached_version(tmp_path, "11.0.10")
    got = launch.installed_entry(tmp_path, listed=_listed(moved), version="11.0.9")
    assert got == moved / "bin" / "ndf" and capsys.readouterr().err == ""


def test_3909_installed_entry_follows_the_list_after_the_own_root_was_removed(tmp_path, monkeypatch, capsys):
    """自分の根が消えても、一覧の 1 件を選び、一覧に無く mtime の新しい根（mtime の選び方の候補）へ落ちない。"""
    _own_root(tmp_path, monkeypatch, "11.0.9", present=False)
    moved = _cached_version(tmp_path, "11.0.10", mtime=1_000)
    _cached_version(tmp_path, "11.0.11", mtime=9_000)
    got = launch.installed_entry(tmp_path, listed=_listed(moved), version="11.0.9")
    assert got == moved / "bin" / "ndf" and capsys.readouterr().err == ""


def test_3909_installed_entry_of_the_own_version_is_the_same_as_entry(tmp_path, monkeypatch, capsys):
    own = _own_root(tmp_path, monkeypatch, "11.0.9")
    assert launch.installed_entry(tmp_path, listed=_listed(own), version="11.0.9") == launch.entry()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("unreadable", "導入の一覧を読めない"),
        ("none", "0 件"),
        ("two", "2 件"),
        ("not_executable", "実行できない"),
    ],
)
def test_3909_installed_entry_falls_back_to_entry_with_one_line(tmp_path, monkeypatch, capsys, case, reason):
    own = _own_root(tmp_path, monkeypatch, "11.0.9")
    moved = _cached_version(tmp_path, "11.0.10", executable=case != "not_executable")
    listed = {
        "unreadable": _unreadable,
        "none": _listed(),
        "two": _listed(moved, _cached_version(tmp_path, "11.0.11")),
        "not_executable": _listed(moved),
    }[case]
    got = launch.installed_entry(tmp_path, listed=listed, version="11.0.9")
    line = _one_line(capsys.readouterr().err)
    assert got == launch.entry() == own / "bin" / "ndf"
    assert line.startswith("ndf lanes: ") and reason in line and line.endswith("。11.0.9 のバージョンで打つ")


def test_3909_installed_entry_says_the_reason_and_the_switched_version_in_one_line(tmp_path, monkeypatch, capsys):
    own = _own_root(tmp_path, monkeypatch, "11.0.9", present=False)
    other = _cached_version(tmp_path, "11.0.10")
    got = launch.installed_entry(tmp_path, listed=_unreadable, version="11.0.9")
    line = _one_line(capsys.readouterr().err)
    assert got == other / "bin" / "ndf"
    assert line == f"ndf lanes: 導入の一覧を読めない。バージョンの保存先 {own} が無いので 11.0.10 のバージョンで打つ"


def test_3909_installed_entry_returns_the_own_path_when_nothing_is_usable(tmp_path, monkeypatch, capsys):
    own = _own_root(tmp_path, monkeypatch, "11.0.9", present=False)
    got = launch.installed_entry(tmp_path, listed=_unreadable, version="11.0.9")
    line = _one_line(capsys.readouterr().err)
    assert got == own / "bin" / "ndf"
    assert line == f"ndf lanes: 導入の一覧を読めない。{own} のバージョンで打つ"


def _spawn_with(monkeypatch, listed, version):
    monkeypatch.setattr(launch, "installed_entry", functools.partial(launch.installed_entry, listed=listed, version=version))


def _ok_run(seen):
    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "ok", "summary": "起動した"}) + "\n", "")

    return run


def test_3909_spawn_launches_the_wrapper_with_the_listed_version(tmp_path, monkeypatch):
    _own_root(tmp_path, monkeypatch, "11.0.9")
    moved = _cached_version(tmp_path, "11.0.10")
    _spawn_with(monkeypatch, _listed(moved), "11.0.9")
    seen = []
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=_ok_run(seen))["ok"]
    assert seen[0][0] == str(moved / "bin" / "ndf") and seen[0][1:3] == ["relay", "spawn"]


def test_3909_spawn_reads_the_list_in_the_lane_dir(tmp_path, monkeypatch):
    _own_root(tmp_path, monkeypatch, "11.0.9")
    asked = []

    def listed(cwd=None):
        asked.append(cwd)
        return None

    _spawn_with(monkeypatch, listed, "11.0.9")
    launch.spawn("o/r", "p6", "a", tmp_path / "lane", "入力", run=_ok_run([]))
    assert asked == [tmp_path / "lane"]


def test_3909_spawn_launches_with_the_own_version_when_the_list_is_unreadable(tmp_path, monkeypatch):
    own = _own_root(tmp_path, monkeypatch, "11.0.9")
    _cached_version(tmp_path, "11.0.10")
    _spawn_with(monkeypatch, _unreadable, "11.0.9")
    seen = []
    assert launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=_ok_run(seen))["ok"]
    assert seen[0][0] == str(own / "bin" / "ndf")


def test_3909_spawn_fails_to_launch_when_no_root_is_left(tmp_path, monkeypatch):
    _own_root(tmp_path, monkeypatch, "11.0.9", present=False)
    _spawn_with(monkeypatch, _unreadable, "11.0.9")
    run, _ = _fake_run(error=FileNotFoundError("ndf"))
    got = launch.spawn("o/r", "p6", "a", tmp_path, "入力", run=run)
    assert got["ok"] is False and got["detail"].startswith("ラッパーを起動できない")


def test_3909_stop_still_uses_entry(tmp_path, monkeypatch):
    own = _own_root(tmp_path, monkeypatch, "11.0.9")
    moved = _cached_version(tmp_path, "11.0.10")
    _spawn_with(monkeypatch, _listed(moved), "11.0.9")
    seen = []
    launch.stop("o/r", "p6", "a", run=_ok_run(seen))
    assert seen[0][0] == str(own / "bin" / "ndf")
