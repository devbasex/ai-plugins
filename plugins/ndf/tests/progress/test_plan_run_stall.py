"""止まりの知らせの中身（#4036 の受け入れ条件 4）。判定の材料は値で渡し、時刻はテストが決める。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ndf.domains.relay import observe
from ndf.progress import lane, plan, store
from ndf.progress.lane import launch, liveness
from ndf.progress.lane.types import LaneRow
from ndf.progress.plan import run, run_decide
from plan_decls import REPO
from plan_decls import decl as _decl
from plan_decls import doc as _doc
from ndf.progress.plan.run_decide import LANE_STALLED, RunState, Stall

ALIVE = {"name": "w-a", "running": True, "state": "running", "reason": None}
GONE = {"name": "w-b", "running": False, "state": "stopped", "reason": None}
IDLE_WAIT = {"tool": "progress", "status": "ok", "summary": "次に流すのは工程 実装"}
ROWS = {"a": [LaneRow("r1", {}, "running", sprint_id="s1")], "b": [LaneRow("r1", {}, "running", sprint_id="s2")]}
MOVED = {"a": "2026-11-23T00:00:00+00:00", "b": "2026-11-23T00:01:00+00:00"}


def _found(activity):
    return liveness.Probes(
        wrapper_name=lambda name: f"w-{name}",
        wrappers=lambda: {"w-a": ALIVE, "w-b": GONE},
        sprint=lambda _sid: IDLE_WAIT,
        activity=lambda name: activity if name == "a" else None,
    )


def test_an_idle_conductor_stall_carries_its_moved_time_and_no_background():
    quiet = liveness.Activity(turn_done=True, moved=1000.0, background=0)
    got = run._judge(ROWS, run_decide.RUNNING, _found(quiet), MOVED)
    by_lane = {s[0]: s for s in got.stalls}
    assert by_lane["a"][1].endswith(liveness.IDLE_CONDUCTOR) and by_lane["a"][3:] == (1000.0, "r1", 0)
    # 活動を見ずに決まる止まり（ended）は今までどおりの記録の時刻と None
    assert by_lane["b"][3:] == (MOVED["b"], "r1", None)


def test_a_stall_without_a_recorded_time_has_none():
    got = run._judge({"b": ROWS["b"]}, run_decide.RUNNING, _found(None), {})
    assert got.stalls[0][3] is None and got.stalls[0][5] is None


def test_a_unix_moved_time_becomes_iso_in_the_lane_stalled_request():
    report = run_decide._stall_report(RunState(), Stall("a", "stall:a/r1:idle_conductor", "理由", 0.0, "r1", 0))
    facts = dict(report.facts)
    assert report.kind == LANE_STALLED
    assert facts["moved"] == "1970-01-01T00:00:00+00:00" and facts["background"] == 0
    assert "1970-01-01T00:00:00+00:00" in report.detail


def test_an_iso_moved_time_and_an_unknown_background_pass_through():
    facts = dict(run_decide._stall_report(RunState(), Stall("b", "k", "理由", MOVED["b"], "r1")).facts)
    assert facts["moved"] == MOVED["b"] and facts["background"] is None


def test_a_five_item_stall_from_an_older_snapshot_still_reads():
    old = Stall(*["b", "k", "理由", MOVED["b"], "r1"])
    assert old.background is None
    report = run_decide._stall_report(RunState(), old)
    assert dict(report.facts)["background"] is None


# --- 活動を読む（`ndf relay ls` の行を写す） ---------------------------------------------------------


def _row(turn_done=True, moved=1000.0, background=0):
    return {"name": "w", "running": True, "state": "running", "turn_done": turn_done, "moved": moved, "background": background}


def test_lane_activity_copies_the_relay_row():
    assert run.lane_activity(_row()) == liveness.Activity(True, 1000.0, 0)
    assert run.lane_activity(_row(background=None)) == liveness.Activity(True, 1000.0, None)


@pytest.mark.parametrize("row", [None, _row(turn_done=None), {"name": "w", "running": True}], ids=["unlisted", "unread", "older-relay"])
def test_lane_activity_is_none_when_the_row_has_no_activity(row):
    """一覧に無い・活動を読めない・活動を載せない古いバージョンのラッパーの行は、活動を読まない（判定は今までどおり）。"""
    assert run.lane_activity(row) is None


def test_lanes_status_shows_an_idle_conductor_through_the_run_probes(opened, monkeypatch):
    """`ndf lanes status` の経路（`plan.status` に `run.probes` を渡す）で、何もしていない conductor が止まりと出る。"""
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, _doc(_decl("a", [[1]])), True)
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_machine(conn, ("sprint", REPO, "a-r1"), view.lanes["a"].machine_id, None, [])
        lane.start_row(conn, pid, "a", "r1", sid)
    name = launch.wrapper_name(REPO, view.name, "a")
    monkeypatch.setattr(launch, "wrappers", lambda run=None, activity=False: {name: {**_row(moved=0.0), "name": name}})
    monkeypatch.setattr(launch, "sprint_wait", lambda sprint_id, run=None: IDLE_WAIT)
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC), run.probes(REPO, view.name))
    got = out["items"][0]["liveness"]
    assert got["kind"] == liveness.IDLE_CONDUCTOR and got["stall"] and got["background"] == 0 and got["moved_at"] == 0.0
    assert out["metrics"]["stalled"] == 1


# --- 実ファイルから読んだ活動で判定する（`observe.conductor_activity` → 一覧の行 → `lane_activity` → `judge`） -------


def _said(at):
    return {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}}


def _jsonl(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _stamp(text):
    return datetime.fromisoformat(text).timestamp()


CONDUCTOR_AT = "2026-11-23T00:00:00+00:00"


def _judged(tmp_path, tasks, *, subagent_at=None, live=0, output_at=None, now_after=0.0, activity_at=CONDUCTOR_AT):
    """会話の記録・活動の記録・/proc を一時ディレクトリに組み、conductor の最後の応答から `now_after` 秒後に判定する。"""
    environ = {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"), "CLAUDE_CODE_TMPDIR": str(tmp_path / "t")}
    cwd = Path("/w")
    transcript = observe.transcript_path(environ, cwd, "sid")
    _jsonl(transcript, _said(CONDUCTOR_AT))
    if subagent_at is not None:
        _jsonl(observe.subagent_transcript_path(transcript, "a1"), _said(subagent_at))
    if output_at is not None:
        out = observe.shell_output_path(environ, cwd, "sid", "b1")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("x", encoding="utf-8")
        os.utime(out, (_stamp(output_at),) * 2)
    wrapper = tmp_path / "wrap"
    wrapper.mkdir()
    (wrapper / "activity.json").write_text(json.dumps({"at": activity_at, "session_id": "sid", "tasks": tasks}), encoding="utf-8")
    proc = tmp_path / "proc"
    for pid, ppid, args in [(10, 1, ["ndf"]), (11, 10, ["claude"]), *((20 + n, 11, SHELL) for n in range(live))]:
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        (proc / str(pid) / "stat").write_text(f"{pid} ({Path(args[0]).name}) S {ppid} 0", encoding="utf-8")
    now = _stamp(CONDUCTOR_AT) + now_after
    look = observe.Look(now, 1 << 16, lambda: observe.process_parents(proc), proc)
    row = {"name": "w", "running": True, "state": "running", "session_id": "sid", "cwd": str(cwd), "pid": 10}
    paths = {"transcript": str(transcript), "shell_outputs": str(observe.shell_output_dir(environ, cwd, "sid"))}
    seen = observe.conductor_activity({**row, **paths}, wrapper, look)
    return liveness.judge(RUN_ROW, liveness.SPRINT_IDLE, row, True, run.lane_activity({**row, **seen}), now)


SHELL = ["/bin/bash", "-c", "source /home/u/.claude/shell-snapshots/snapshot-bash-1-x.sh && eval 'sleep 45'"]
RUN_ROW = LaneRow("r1", {}, "running", sprint_id="s1")
SUBAGENT = [{"id": "a1", "type": "subagent"}]
SHELL_TASK = [{"id": "b1", "type": "shell"}]


def test_a_conductor_idle_after_its_subagent_finished_is_a_stall(tmp_path):
    """lracct の形: 背景のサブエージェントが終わり、そこから閾値を過ぎた（受け入れ条件 1・決定 2）。"""
    later = "2026-11-23T00:01:00+00:00"
    got = _judged(tmp_path, SUBAGENT, subagent_at=later, now_after=60 + liveness.idle_after())
    assert got.kind == liveness.IDLE_CONDUCTOR and got.moved_at == _stamp(later)


def test_a_conductor_idle_after_its_shell_finished_is_a_stall(tmp_path):
    """背景のシェルが終わって出力のファイルの更新時刻から閾値を過ぎた（受け入れ条件 1 のシェルの取りこぼし）。"""
    got = _judged(tmp_path, SHELL_TASK, output_at="2026-11-23T00:02:00+00:00", now_after=120 + liveness.idle_after())
    assert got.kind == liveness.IDLE_CONDUCTOR


def test_a_long_subagent_that_just_finished_is_not_a_stall(tmp_path):
    """conductor の最後の応答は 30 分前でも、サブエージェントの最後の応答が 1 分前なら止まりにしない。"""
    got = _judged(tmp_path, SUBAGENT, subagent_at="2026-11-23T00:29:00+00:00", now_after=1800)
    assert got.kind == liveness.ALIVE


@pytest.mark.parametrize(
    "given",
    [
        {"tasks": SHELL_TASK, "live": 1},  # 生きているシェルが残る（受け入れ条件 2）
        {"tasks": SUBAGENT},  # 会話の記録を読めないサブエージェントは走っているとみなす
        {"tasks": [], "activity_at": "2026-11-22T23:59:00+00:00"},  # 前の Stop の空の一覧だけ
    ],
    ids=["live-shell", "unread-subagent", "previous-stop"],
)
def test_a_conductor_with_running_or_unproven_work_stays_alive_past_the_threshold(tmp_path, given):
    tasks = given.pop("tasks")
    assert _judged(tmp_path, tasks, now_after=10 * liveness.idle_after(), **given).kind == liveness.ALIVE
