"""リレーハブ: 受付箱・起動の確かめ・起こし直しの順・版の混ざり・待ちの一覧（#2563・#2583・#2753・3341-2・決定 86）。

tmux は偽物に差し替え、ラッパーの代わりに錠を持つ。時計は進めるだけの偽物で、眠らない。
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from ndf.domains.relay import hub, registry
from ndf.domains.relay.tmux import Done, Tmux

VERSION = "11.0.0-dev.4"


class FakeTmux:
    """セッションを作ると、ラッパーの代わりに錠を持ち状態を書く（`boots` が偽なら何もしない）。"""

    def __init__(self, state_dir: Path, boots: bool = True):
        self.state_dir = state_dir
        self.boots = boots
        self.sessions: dict[str, int] = {}
        self.started: list[tuple[str, dict]] = []

    def has(self, name):
        return name in self.sessions

    def start(self, name, cwd, command, env):
        self.started.append((name, dict(env)))
        if self.boots:
            d = registry.wrapper_dir(self.state_dir, name)
            d.mkdir(parents=True, exist_ok=True)
            fd = os.open(d / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
            fcntl.flock(fd, fcntl.LOCK_EX)
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
def ctx(tmp_path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "ndf").write_text("#!/bin/sh\n", encoding="utf-8")
    clock = Clock()
    tmux = FakeTmux(tmp_path / "state")
    c = hub.Context(
        state_dir=tmp_path / "state", plugin_root=root, version=VERSION,
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "GIT_ASKPASS": "/vscode"},
        tmux=tmux, max_named=2, max_starts=3,
        started=hub.Wait(1.0, 0.1), stop_wait=hub.Wait(1.0, 0.1), hub_wait=hub.Wait(5.0, 0.1),
        pending=lambda: {"runs": [{"id": "r1"}], "note": ""},
        sleep=clock.sleep, monotonic=clock,
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


def test_old_ndf_wrapper_blocks_spawn_and_restart(ctx, tmp_path):
    # 旧い版のラッパーが動いていれば、新しい版で起動しない（3341-2）
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    old = tmp_path / "old" / "relay" / "20261113T1"
    old.mkdir(parents=True)
    fd = os.open(old / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
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


def test_busy_hub_times_out_without_leaving_the_request(ctx):
    base = hub.hub_dir(ctx.state_dir)
    base.mkdir(parents=True)
    fd = os.open(base / hub.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    finally:
        os.close(fd)
    assert got.cause == "wait_timeout" and not list((base / "requests").glob("*.json"))


def test_tmux_runner_failures_are_reported():
    def boom(*a, **k):
        raise OSError("no tmux")

    t = Tmux({"NDF_RELAY_TMUX": "/x/tmux"}, 1.0, runner=boom)
    assert t.start("t1", Path("/w"), ["ndf"], {}) == Done(False, "tmux を打てない: no tmux")
    assert not t.has("t1")
