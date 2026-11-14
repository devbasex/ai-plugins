"""レーンの生死の判定（#3097・N3）。判定は純粋な関数なので、材料（ラッパーの一覧・スプリントの待ち）を値で渡して確かめる。

`ndf lanes status` は `opened`（SQLite と PostgreSQL）の進行表に、差し替えた材料を渡して確かめる。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ndf.contracts import result
from ndf.progress import lane, plan, store
from ndf.progress.lane import launch, liveness
from ndf.progress.lane.types import LaneRow
from plan_decls import REPO
from plan_decls import decl as _decl
from plan_decls import doc as _doc

RUNNING_ROW = LaneRow("r1", {"kind": "sprint", "issues": [1]}, "running", sprint_id="s1")
ALIVE = {"name": "w", "running": True, "state": "running", "reason": "区間 1 を始めた"}
AUTH = {"name": "w", "running": True, "state": "stalled", "reason": "認証の失敗で /goal が外れ、子が入力を待っている"}
GONE = {"name": "w", "running": False, "state": "stopped", "reason": None}


def _gate():
    return result.result("progress", result.GATE, "工程 設計 のステップ gate が答えを待っている")


def _working():
    return result.stopped("progress", "wait_timeout", "進行表 s1 を流す作業がまだ動いている（0 秒待った）")


def _idle():
    return result.result("progress", result.OK, "次に流すのは工程 実装（resume）")


@pytest.mark.parametrize(
    ("out", "want"),
    [
        (None, liveness.SPRINT_UNKNOWN),
        ({}, liveness.SPRINT_UNKNOWN),
        ("gate", liveness.SPRINT_GATE),
        ("working", liveness.SPRINT_WORKING),
        ("idle", liveness.SPRINT_IDLE),
        ({"status": "壊れた"}, liveness.SPRINT_UNKNOWN),
    ],
)
def test_sprint_wait_reads_the_wait_result(out, want):
    given = {"gate": _gate, "working": _working, "idle": _idle}[out]() if isinstance(out, str) else out
    assert liveness.sprint_wait(given) == want


@pytest.mark.parametrize(
    ("row", "sprint", "wrapper", "listed", "kind"),
    [
        (None, liveness.SPRINT_IDLE, None, True, liveness.IDLE),
        # 承認ゲートや問いの答えを待つ行は、ラッパーが無くても正常な待ち（止まりとして知らせない）
        (RUNNING_ROW, liveness.SPRINT_GATE, None, True, liveness.WAITING_ANSWER),
        (RUNNING_ROW, liveness.SPRINT_GATE, AUTH, True, liveness.WAITING_ANSWER),
        # 一覧に無いことを止まりと読まない
        (RUNNING_ROW, liveness.SPRINT_IDLE, None, False, liveness.UNKNOWN),
        (RUNNING_ROW, liveness.SPRINT_UNKNOWN, ALIVE, True, liveness.UNKNOWN),
        # 認証の失敗で /goal が外れたラッパーを running のまま扱わない（#3243）
        (RUNNING_ROW, liveness.SPRINT_IDLE, AUTH, True, liveness.STALLED),
        (RUNNING_ROW, liveness.SPRINT_IDLE, ALIVE, True, liveness.ALIVE),
        (RUNNING_ROW, liveness.SPRINT_WORKING, GONE, True, liveness.WORKING),
        # ループを終えたレーンを running に見せない（#2477・#2649）
        (RUNNING_ROW, liveness.SPRINT_IDLE, GONE, True, liveness.ENDED),
        (RUNNING_ROW, liveness.SPRINT_IDLE, None, True, liveness.ENDED),
    ],
)
def test_judge_decides_one_kind(row, sprint, wrapper, listed, kind):
    verdict = liveness.judge(row, sprint, wrapper, listed)
    assert verdict.kind == kind
    assert verdict.stall == (kind in (liveness.STALLED, liveness.ENDED))


def test_a_row_awaiting_next_after_an_answer_is_resuming_not_a_stall():
    row = LaneRow("r1", {"kind": "sprint", "issues": [1]}, "running", sprint_id="s1", answered_seq=5, resumed_seq=0)
    verdict = liveness.judge(row, liveness.SPRINT_IDLE, None, True)
    assert verdict.kind == liveness.RESUMING and not verdict.stall


def test_a_stalled_wrapper_carries_its_reason():
    verdict = liveness.judge(RUNNING_ROW, liveness.SPRINT_IDLE, AUTH, True)
    assert "認証の失敗" in verdict.reason and verdict.row == "r1" and verdict.sprint_id == "s1"


def test_notice_key_changes_with_the_row_and_the_kind():
    ended = liveness.judge(RUNNING_ROW, liveness.SPRINT_IDLE, None, True)
    stalled = liveness.judge(RUNNING_ROW, liveness.SPRINT_IDLE, AUTH, True)
    assert liveness.notice_key("a", ended) == "stall:a/r1:ended"
    assert liveness.notice_key("a", ended) != liveness.notice_key("a", stalled)


class _Probes:
    """材料を差し替える口。読んだ回数を数える。"""

    def __init__(self, wrappers, sprints):
        self.table, self.sprints, self.listed = wrappers, sprints, 0

    def build(self):
        def wrappers():
            self.listed += 1
            return self.table

        return liveness.Probes(wrapper_name=lambda name: f"w-{name}", wrappers=wrappers, sprint=lambda sid: self.sprints.get(sid))


def test_judge_lanes_reads_the_wrapper_list_only_when_a_row_runs():
    probes = _Probes({}, {})
    got = liveness.judge_lanes({"a": [LaneRow("r1", {}, "pending")]}, probes.build())
    assert got["a"].kind == liveness.IDLE and probes.listed == 0


def test_judge_lanes_matches_each_lane_with_its_wrapper():
    rows = {
        "a": [RUNNING_ROW],
        "b": [LaneRow("r1", {}, "running", sprint_id="s2")],
        "c": [LaneRow("r1", {}, "running", sprint_id="s3")],
    }
    probes = _Probes({"w-a": ALIVE, "w-b": AUTH}, {"s1": _idle(), "s2": _idle(), "s3": _gate()})
    got = liveness.judge_lanes(rows, probes.build())
    assert {k: v.kind for k, v in got.items()} == {"a": liveness.ALIVE, "b": liveness.STALLED, "c": liveness.WAITING_ANSWER}
    assert probes.listed == 1


def test_judge_lanes_reads_a_failed_list_as_unknown():
    probes = _Probes(None, {"s1": _idle()})
    assert liveness.judge_lanes({"a": [RUNNING_ROW]}, probes.build())["a"].kind == liveness.UNKNOWN


# --- 材料を読む口 ---------------------------------------------------------------------------------


class _Done:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def test_wrappers_reads_the_relay_list_by_name():
    out = result.result("relay", result.OK, "名前つきのラッパー: 1 本", items=[ALIVE])
    seen = []

    def run(argv, **_kw):
        seen.append(argv[1:])
        return _Done("前置き\n" + __import__("json").dumps(out))

    assert launch.wrappers(run) == {"w": ALIVE}
    assert seen == [["relay", "ls"]]


@pytest.mark.parametrize("stdout", ["", "壊れた", '{"status": "stopped"}'])
def test_wrappers_is_none_when_the_list_cannot_be_read(stdout):
    assert launch.wrappers(lambda argv, **_kw: _Done(stdout)) is None


def test_wrappers_is_none_when_the_command_cannot_run():
    def run(argv, **_kw):
        raise OSError("無い")

    assert launch.wrappers(run) is None


def test_sprint_wait_asks_progress_wait_without_waiting():
    seen = []

    def run(argv, **_kw):
        seen.append(argv[1:])
        return _Done(__import__("json").dumps(_gate()))

    assert launch.sprint_wait("s1", run)["status"] == result.GATE
    assert seen == [["progress", "wait", "s1", "--timeout", "0"]]


# --- ndf lanes status ---------------------------------------------------------------------------


def test_status_shows_liveness_and_warns_only_about_stalls(opened):
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, _doc(_decl("a", [[1]]), _decl("b", [[2]], touches=("src/b",))), True)
        view, _ = lane.read_plan(conn, pid)
        sids = {}
        for name in ("a", "b"):
            sids[name] = store.create_table(conn, ("sprint", REPO, f"{name}-r1"), view.lanes[name].table_id, None, [])
            lane.start_row(conn, pid, name, "r1", sids[name])
    # a は承認ゲートの答えを待ち（正常）、b は conductor が落ちた
    probes = _Probes({}, {sids["a"]: _gate(), sids["b"]: _idle()})
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC), probes.build())
    by_lane = {i["lane"]: i for i in out["items"]}
    assert by_lane["a"]["liveness"]["kind"] == liveness.WAITING_ANSWER
    assert by_lane["b"]["liveness"]["kind"] == liveness.ENDED
    assert out["metrics"]["stalled"] == 1 and len(out["warnings"]) == 1 and "レーン b" in out["warnings"][0]


def test_status_without_probes_has_no_liveness(opened):
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, _doc(_decl("a", [[1]])), True)
        out = plan.status(conn, pid, datetime.now(UTC))
    assert "liveness" not in out["items"][0]
