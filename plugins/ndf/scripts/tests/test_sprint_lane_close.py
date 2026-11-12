"""並列の計画の 1 スプリントのレーンの目録のまとめのステージ（#3058 の A7・B1・B3・B5・I8、決定 7・8）。

依頼の経路では、依頼のプランが承認ゲート（10）で完了し、版の要らないまとめ（課題を閉じない）が本番の後に続き、版の要るまとめ
（リリース後テスト → 課題を閉じる）は目録と同じ置き場へ書いて依頼に載せる。レーンの中で本番を流し終える経路では今のまとめの後に
終わりの記録を足す。1 スプリントのレーンでなければ目録を変えない。レーンの判定は差し替える。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import durable  # noqa: E402
import parallel_external as pe  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_release  # noqa: E402
from supervise_lib import sprint_lane_close as slc  # noqa: E402


@pytest.fixture
def a(tmp_path):
    return argparse.Namespace(
        worktree=str(tmp_path), name="s1", issue=[11, 12], mode="standard", base="develop", out=str(tmp_path / "out"), state=None,
        test_limits={"ci_wait_timeout": 600},
    )  # fmt: skip


@pytest.fixture
def lane(monkeypatch):
    monkeypatch.setattr(slc, "sprint_lane", lambda _repo: ("p1", "la"))
    monkeypatch.setattr(slc, "plan_ends", lambda _lane: True)


def request_waves(a) -> list[dict]:
    req = parallel_release.plan_release_request(a, a.worktree, "p1", "la", ["{queue_prs}"])
    return [
        {"name": "スプリントブランチ", "plans": {"sprint-branch": {"steps": []}}},
        {"name": "本番", "plans": {"release-request": req}, "then_of": "スプリントブランチ"},
    ]


def not_called(_a, _repo):
    raise AssertionError("依頼の経路は今のまとめを組まない")


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def test_the_request_route_closes_without_waiting_and_without_closing_issues(a, lane):
    waves = slc.with_lane_close(a, request_waves(a), not_called)
    last = waves[-1]
    assert (last["name"], last["then_of"], list(last["plans"])) == ("まとめ", "スプリントブランチ", ["lane-close"])
    close = last["plans"]["lane-close"]
    got = ids(close)
    assert got[:3] == ["spec", "pr", "ready"] and got[-1] == "lane-end" and {"retro", "refine", "merge-gate", "merge"} <= set(got)
    assert not slc.closes_issues(close) and "release-verify" not in got  # 版の要らないまとめは課題を閉じない（I8・B3）
    steps = {s["id"]: s for s in close["steps"]}
    assert steps["merge"]["next"] == steps["merge-approved"]["next"] == "lane-end"
    assert steps["lane-end"]["cmd"].endswith("end p1 --lane la --request {queue_state:release-request}/work/request.txt")
    step = waves[1]["plans"]["release-request"]["steps"][0]
    assert step["on_exit"]["10"] == "end"  # 依頼を送ったら承認ゲートで止まらずにまとめへ進む（B1・決定 7）
    verify = Path(a.out).resolve() / "lane-verify.json"
    assert step["cmd"].endswith(f"--verify-plan {verify}")
    plan = json.loads(verify.read_text())
    assert ids(plan)[:3] == ["config-save", "release-verify", "config-check"] and "close" in ids(plan)
    assert slc.closes_issues(plan)  # 課題を閉じるのは版の要るまとめだけ（B3）
    closing = next(s for s in plan["steps"] if s["id"] == "close")
    # 振り返りはリリースより前のまとめで済むため、最終工程はリリース後テストに固定する
    assert "--final-stage リリース後テスト" in closing["cmd"] and "--final-stage-from" not in closing["cmd"]
    assert "--record-pr {queue_record}" in closing["cmd"]


def test_a_lane_that_releases_inside_closes_as_now_and_records_its_end(a, lane):
    stub = {"steps": [{"id": "retro", "next": "close"}, {"id": "close", "type": "run", "next": "end"}]}
    waves = [{"name": "デプロイ", "plans": {"release": {}}}, {"name": "本番", "plans": {"release-prod": {}}}]
    got = slc.with_lane_close(a, waves, lambda _a, _repo: stub)
    close = got[-1]["plans"]["lane-close"]
    assert got[-1]["then_of"] == "本番" and ids(close) == ["retro", "close", "lane-end"]
    assert close["steps"][1]["next"] == "lane-end" and "--request" not in close["steps"][2]["cmd"]
    assert not (Path(a.out) / "lane-verify.json").exists()  # 版の要るまとめは作らない


def test_a_plan_started_before_the_lane_end_mark_keeps_waiting_for_the_answer(a, lane, monkeypatch):
    """印 `parallel-lane-end` の前に始めた計画は終わりの記録も版の要るまとめも扱わないため、依頼の答えを待ってから
    今までのまとめ（課題を閉じる）を流す（決定 10）。"""
    monkeypatch.setattr(slc, "plan_ends", lambda _lane: False)
    seen = []
    waves = slc.with_lane_close(a, request_waves(a), lambda _a, _repo: seen.append(_a) or {"steps": [{"id": "close", "next": "end"}]})
    close = waves[-1]["plans"]["lane-close"]
    assert ids(close) == ["close", "lane-end"] and slc.closes_issues(close)
    # 目録に本番のプラン（release-prod）は無いため、リリース記録は queue が探す
    assert seen[0].record_pr == "{queue_record}" and not hasattr(a, "record_pr")
    step = waves[1]["plans"]["release-request"]["steps"][0]
    assert "on_exit" not in step and "--verify-plan" not in step["cmd"]
    assert not (Path(a.out) / "lane-verify.json").exists()


@pytest.mark.parametrize(
    ("event", "want"), [(True, True), (None, False), (durable.DurableError("無い"), False)], ids=["new", "old", "unreadable"]
)
def test_the_plan_ends_lanes_only_when_its_workflow_set_the_event(tmp_path, monkeypatch, event, want):
    def read(kind, ident, wid, key, timeout, start=None):
        assert (kind, wid, key, start) == (pl.KIND, pl.workflow_id("p1"), pl.ENDING_EVENT, tmp_path)
        if isinstance(event, Exception):
            raise event
        return event

    monkeypatch.setattr(durable, "event_from", read)
    assert slc.plan_ends(pl.LaneRef("p1", "la", tmp_path)) is want
    assert slc.plan_ends(("p1", "la")) is False  # 計画の置き場が分からなければ今までの道


def test_after_a_gate_stage_the_close_runs_on_its_own_command(a, lane):
    stages = [{"name": "本番", "plans": {"promote": {}}}, {"name": "リリース承認ゲート", "gate": "承認を取る"}]
    got = slc.with_lane_close(a, stages, lambda _a, _repo: {"steps": [{"id": "close", "next": "end"}]})
    assert got[-1]["name"] == "まとめ" and "then_of" not in got[-1]


def test_a_stage_list_outside_a_sprint_lane_is_kept(a, monkeypatch):
    monkeypatch.setattr(slc, "sprint_lane", lambda _repo: None)
    waves = request_waves(a)
    got = slc.with_lane_close(a, waves, not_called)
    assert got is waves and "on_exit" not in waves[1]["plans"]["release-request"]["steps"][0]


@pytest.mark.parametrize(
    ("plan", "kind", "want"),
    [("p1", "sprint", ("p1", "la")), ("p1", "loop", None), (pe.EXTERNAL, "sprint", None)],
    ids=["sprint", "loop", "external"],
)
def test_only_a_sprint_lane_of_a_plan_gets_the_close(tmp_path, monkeypatch, plan, kind, want):
    """開発ループのレーンと外のオーガナイザーのレーンには足さない（A8）。別のリポジトリのレーンも 1 スプリントのレーンなら足す。"""
    inp = {"kind": "sprint", "issues": [1]} if kind == "sprint" else {"kind": "loop", "loop": "main"}
    ln = pp.Lane(name="la", input=inp, plans=1, touches=[])
    monkeypatch.setattr(pl, "lane_of", lambda _main, *_a, **_k: pl.LaneRef(plan, ln, tmp_path, tmp_path / "other"))
    got = slc.sprint_lane(str(tmp_path))
    assert got == want and (got is None or got.home == tmp_path)
