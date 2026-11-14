"""リレーハブ: 受付箱・起動の確かめ・起こし直しの順・版の混ざり・待ちの一覧（#2563・#2583・#2753・3341-2・決定 86）。

tmux は偽物に差し替え、ラッパーの代わりに錠を持つ。時計は進めるだけの偽物で、眠らない。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from ndf.domains.relay import hub, registry
from ndf.domains.relay.tmux import Done, Tmux

VERSION = "11.0.0-dev.4"


class FakeTmux:
    """セッションを作ると、ラッパーの代わりに錠を持ち状態を書く（`boots` が偽なら何もしない）。"""

    def __init__(self, state_dir: Path, hold, boots: bool = True):
        self.state_dir = state_dir
        self.hold = hold
        self.boots = boots
        self.sessions: dict[str, int] = {}
        self.started: list[tuple[str, dict]] = []

    def has(self, name):
        return name in self.sessions

    def start(self, name, cwd, command, env):
        self.started.append((name, dict(env)))
        if self.boots:
            d = registry.wrapper_dir(self.state_dir, name)
            fd = self.hold(d / registry.LOCK_FILE)
            registry.write_state(d, {"version": VERSION, "state": "running", "reason": "区間 1 を始めた"})
            self.sessions[name] = fd
        return Done(True, "")

    def kill(self, name):
        fd = self.sessions.pop(name, None)
        if fd is not None:
            os.close(fd)
        return Done(True, "")

    def close(self):
        for name in list(self.sessions):
            self.kill(name)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def ctx(tmp_path, hold_lock):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "ndf").write_text("#!/bin/sh\n", encoding="utf-8")
    clock = Clock()
    tmux = FakeTmux(tmp_path / "state", hold_lock)
    c = hub.Context(
        state_dir=tmp_path / "state",
        plugin_root=root,
        version=VERSION,
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "GIT_ASKPASS": "/vscode"},
        tmux=tmux,
        max_named=2,
        max_starts=3,
        started=hub.Wait(1.0, 0.1),
        stop_wait=hub.Wait(1.0, 0.1),
        hub_wait=hub.Wait(5.0, 0.1),
        pending=lambda: {"runs": [{"id": "r1"}], "note": ""},
        sleep=clock.sleep,
        monotonic=clock,
    )
    yield c
    tmux.close()


def test_spawn_confirms_the_wrapper_is_up_and_registers_it(ctx):
    got = hub.submit(ctx, {"kind": "spawn", "cwd": "/w", "role": "lane", "args": ["--model", "x"]})
    assert got.ok and got.items == [{"name": "t1"}]
    name, env = ctx.tmux.started[0]
    assert (env["NDF_RELAY_NAME"], env["NDF_RELAY_ROLE"], env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"]) == ("t1", "lane", "false")
    assert "GIT_ASKPASS" not in env
    rows = hub.listing(ctx)
    assert [(r["name"], r["running"], r["state"], r["version"]) for r in rows] == [("t1", True, "running", VERSION)]
    assert not list((hub.hub_dir(ctx.state_dir) / "requests").glob("*.json"))
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).cause == "refused"


def test_spawn_that_never_comes_up_is_not_reported_as_started(ctx):
    # ラッパーが立ち上がらなければ「起動した」を返さない（#2563）
    ctx.tmux.boots = False
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    assert not got.ok and "立ち上がらなかった" in got.summary
    assert hub.read_registry(ctx.state_dir) == {}


def test_missing_wrapper_place_says_how_to_recover(ctx):
    # プラグインの更新で版の置き場が消えたら、理由と打ち直しを返す（#2583）
    (ctx.plugin_root / "bin" / "ndf").unlink()
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    assert (got.ok, got.cause) == (False, "precondition") and "ndf relay install" in got.summary


def test_restart_at_the_limit_goes_through_and_lists_pending_runs(ctx):
    # 上限 2 で 2 本が動いているとき、そのうち 1 本の restart は通る（#2753）。待ちの一覧を載せる（決定 86）
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    assert hub.submit(ctx, {"kind": "spawn", "name": "t2"}).ok
    assert hub.submit(ctx, {"kind": "spawn", "name": "t3"}).cause == "refused"
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.ok and {"pending": [{"id": "r1"}]} in got.items and "1 件" in got.summary
    assert hub.read_registry(ctx.state_dir)["t1"]["starts"]["count"] == 1


def test_restart_refused_by_the_limit_does_not_stop_the_target(ctx):
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    ctx.max_starts = 0
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.cause == "refused"
    assert ctx.tmux.has("t1") and [r.name for r in registry.running(ctx.state_dir)] == ["t1"]


def test_old_ndf_wrapper_blocks_spawn_and_restart(ctx, tmp_path, hold_lock):
    # 旧い版のラッパーが動いていれば、新しい版で起動しない（3341-2）
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    fd = hold_lock(tmp_path / "old" / "relay" / "20261113T1" / registry.LOCK_FILE)
    try:
        spawn = hub.submit(ctx, {"kind": "spawn", "name": "t2"})
        restart = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    finally:
        os.close(fd)
    assert spawn.cause == restart.cause == "precondition"
    assert "旧い NDF" in spawn.summary and ctx.tmux.has("t1")


def test_stop_and_unknown_requests(ctx):
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    assert [r["running"] for r in hub.listing(ctx)] == [False]
    assert hub.submit(ctx, {"kind": "restart", "name": "nope"}).cause == "invalid"
    assert hub.submit(ctx, {"kind": "fly"}).cause == "invalid"


def test_busy_hub_times_out_without_leaving_the_request(ctx, hold_lock):
    base = hub.hub_dir(ctx.state_dir)
    fd = hold_lock(base / hub.LOCK_FILE)
    try:
        got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    finally:
        os.close(fd)
    assert got.cause == "wait_timeout" and not list((base / "requests").glob("*.json"))


def test_secret_values_never_reach_the_tmux_arguments():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs.get("env")))
        return type("D", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    env = {"GH_TOKEN": "ghp_secret", "ANTHROPIC_API_KEY": "sk-secret", "AWS_SECRET_ACCESS_KEY": "aws", "NDF_RELAY_NAME": "t1"}
    assert Tmux({}, 1.0, runner=runner).start("t1", Path("/w"), ["ndf", "relay", "run"], env).ok
    argv, passed = calls[0]
    joined = " ".join(argv)
    assert "NDF_RELAY_NAME=t1" in joined and not any(v in joined for v in ("ghp_secret", "sk-secret", "aws"))
    assert passed == env


def test_tmux_runner_failures_are_reported():
    def boom(*a, **k):
        raise OSError("no tmux")

    t = Tmux({"NDF_RELAY_TMUX": "/x/tmux"}, 1.0, runner=boom)
    assert t.start("t1", Path("/w"), ["ndf"], {}) == Done(False, "tmux を打てない: no tmux")
    assert not t.has("t1")


# 現状固定（I-004）: 起こし直しの回数は登録簿の starts が今日の日付のときだけ数え、ほかは 0 回から数える
def _register(ctx, starts):
    path = hub.hub_dir(ctx.state_dir) / hub.REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entries": {"t1": {"cwd": None, "args": [], "starts": starts}}}), encoding="utf-8")


@pytest.mark.parametrize(
    ("starts", "count"),
    [
        ({"date": "2000-01-01", "count": 9}, 1),
        ("壊れた値", 1),
        (None, 1),
        ({"date": "TODAY"}, 1),
        ({"date": "TODAY", "count": "2"}, 3),
    ],
)
def test_restart_counts_only_todays_starts(ctx, starts, count):
    today = date.today().isoformat()
    if isinstance(starts, dict):
        starts = {k: (today if v == "TODAY" else v) for k, v in starts.items()}
    _register(ctx, starts)
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.ok, got.summary
    assert hub.read_registry(ctx.state_dir)["t1"]["starts"] == {"date": today, "count": count}


def test_restart_refuses_when_todays_starts_reach_the_limit(ctx):
    today = date.today().isoformat()
    _register(ctx, {"date": today, "count": 3})
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert (got.ok, got.cause) == (False, "refused") and "3 回" in got.summary
    assert hub.read_registry(ctx.state_dir)["t1"]["starts"] == {"date": today, "count": 3}
    assert ctx.tmux.started == []


def test_spawn_registers_zero_starts_for_today(ctx):
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    assert hub.read_registry(ctx.state_dir)["t1"]["starts"] == {"date": date.today().isoformat(), "count": 0}


def test_a_broken_registry_answers_invalid_and_does_not_block_later_requests(ctx):
    # 依頼 1 本の例外で、後ろの依頼が答えを待ち切れなくならない
    path = hub.hub_dir(ctx.state_dir) / hub.REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れた", encoding="utf-8")
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    assert (got.ok, got.cause) == (False, "invalid") and "登録簿を読めない" in got.summary
    assert not list((hub.hub_dir(ctx.state_dir) / "requests").glob("*.json"))
    path.unlink()
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok


def test_an_unreadable_request_file_gets_an_invalid_result(ctx):
    requests = hub.hub_dir(ctx.state_dir) / "requests"
    requests.mkdir(parents=True)
    (requests / "0-bad.json").write_text("[1", encoding="utf-8")
    assert hub.drain(ctx)
    result = json.loads((hub.hub_dir(ctx.state_dir) / "results" / "0-bad.json").read_text(encoding="utf-8"))
    assert (result["ok"], result["cause"]) == (False, "invalid") and not (requests / "0-bad.json").exists()


def test_a_request_removed_by_its_timed_out_submitter_is_skipped(ctx, monkeypatch):
    # 頼む側が期限切れで消した依頼に結果を書かず、後ろの依頼も処理する
    requests = hub.hub_dir(ctx.state_dir) / "requests"
    requests.mkdir(parents=True)
    for rid in ("0-a", "1-gone", "2-c"):
        (requests / f"{rid}.json").write_text(json.dumps({"kind": "stop", "name": "t1"}), encoding="utf-8")
    real = hub.handle

    def removing(c, req):
        (requests / "1-gone.json").unlink(missing_ok=True)
        return real(c, req)

    monkeypatch.setattr(hub, "handle", removing)
    assert hub.drain(ctx)
    results = hub.hub_dir(ctx.state_dir) / "results"
    assert sorted(p.name for p in results.glob("*.json")) == ["0-a.json", "2-c.json"]
    assert not list(requests.glob("*.json"))


@pytest.mark.parametrize("name", ["a b", "x" * 65])
def test_spawn_with_a_malformed_name_starts_no_tmux_session(ctx, name):
    # 名前の形を tmux を起動する前に確かめ、止める手段の無いセッションを残さない
    got = hub.submit(ctx, {"kind": "spawn", "name": name})
    assert (got.ok, got.cause) == (False, "invalid") and ctx.tmux.started == []
