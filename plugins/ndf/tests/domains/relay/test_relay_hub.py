"""リレーハブ: 受付箱・起動の確かめ・再起動の順・バージョンの混ざり・待ちの一覧（#2563・#2583・#2753・3341-2・決定 86）。

再起動は名前つきのラッパーの数だけで断り、1 日の起動の回数を持たない（#3864 の決定 13・I15）。

tmux は偽物に差し替え、ラッパーの代わりに錠を持つ。時計は進めるだけの偽物で、眠らない。
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from ndf.contracts import limits, named_cap
from ndf.domains.relay import hub, registry, switch_decide
from ndf.domains.relay.tmux import Done, Tmux, new_session_args

VERSION = "11.0.0-dev.4"


class FakeTmux:
    """セッションを作ると、ラッパーの代わりに錠を持ち状態を書く（`boots` が偽なら何もしない）。"""

    def __init__(self, state_dir: Path, hold, boots: bool = True):
        self.state_dir = state_dir
        self.hold = hold
        self.boots = boots
        self.sessions: dict[str, int] = {}
        self.started: list[tuple[str, dict]] = []
        self.commands: list[list[str]] = []

    def has(self, name):
        return name in self.sessions

    def start(self, name, cwd, command, env):
        self.started.append((name, dict(env)))
        self.commands.append(list(command))
        if self.boots:
            d = registry.wrapper_dir(self.state_dir, name)
            fd = self.hold(d / registry.LOCK_FILE)
            registry.write_state(d, {"version": VERSION, "state": "running", "reason": "セグメント 1 を始めた"})
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
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "GIT_ASKPASS": "/vscode", "NDF_RELAY_MAX_NAMED": "2"},
        tmux=tmux,
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
    got = hub.submit(ctx, {"kind": "spawn", "cwd": str(ctx.plugin_root), "role": "lane", "args": ["--model", "x"]})
    assert got.ok and got.items == [{"name": "t1"}]
    name, env = ctx.tmux.started[0]
    assert (env["NDF_RELAY_NAME"], env["NDF_RELAY_ROLE"], env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"]) == ("t1", "lane", "false")
    assert "GIT_ASKPASS" not in env
    rows = hub.listing(ctx)
    assert [(r["name"], r["running"], r["state"], r["version"]) for r in rows] == [("t1", True, "running", VERSION)]
    assert not list((hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR).glob("*.json"))
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).cause == "refused"


def _new_install(ctx, tmp_path):
    new_root = tmp_path / "new"
    (new_root / "bin").mkdir(parents=True)
    (new_root / "bin" / "ndf").write_text("#!/bin/sh\n", encoding="utf-8")
    return dataclasses.replace(ctx, plugin_root=new_root, version="11.0.1")


def test_an_old_hub_does_not_see_requests_that_carry_the_install(ctx, tmp_path, monkeypatch):
    # 更新前の実装の drain は `requests/*.json` を自分の ctx で処理する（導入を読まない）。
    # 新しいリクエストはそこに置かないので、旧版がリレーハブの間に書いても旧版の bin/ndf では起動されない（#3909）
    new = _new_install(ctx, tmp_path)
    seen: list[str] = []
    real = hub.drain

    def old_drain(_c):
        legacy = hub.hub_dir(ctx.state_dir) / "requests"
        seen.extend(p.name for p in legacy.glob("*.json"))
        return real(new)

    monkeypatch.setattr(hub, "drain", old_drain)
    assert hub.submit(new, {"kind": "spawn", "name": "t1"}).ok
    assert seen == [] and ctx.tmux.commands[0][0] == str(new.plugin_root / "bin" / "ndf")


def test_a_hub_on_the_old_install_launches_with_the_requesters_install(ctx, tmp_path, monkeypatch):
    # 更新前に起動したコマンド（ctx）がリレーハブでも、頼んだ側の新しい導入の bin/ndf とバージョンで起動する（#3909）
    new = _new_install(ctx, tmp_path)
    new_root = new.plugin_root
    real = hub.drain
    monkeypatch.setattr(hub, "drain", lambda _c: real(ctx))
    assert hub.submit(new, {"kind": "spawn", "name": "t1"}).ok
    assert ctx.tmux.commands[0][0] == str(new_root / "bin" / "ndf")
    assert registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]["version"] == "11.0.1"
    assert hub.submit(new, {"kind": "restart", "name": "t1"}).ok
    assert ctx.tmux.commands[1][0] == str(new_root / "bin" / "ndf")


@pytest.mark.parametrize("req", [{}, {"plugin_root": "", "version": "x"}, {"plugin_root": "/p", "version": 1}])
def test_a_request_without_a_usable_install_uses_the_hubs_own(ctx, req):
    got = hub.requester(ctx, req)
    assert (got.plugin_root, got.version, got.fallback_cap) == (ctx.plugin_root, ctx.version, None)


def test_spawn_that_never_comes_up_is_not_reported_as_started(ctx):
    # ラッパーが立ち上がらなければ「起動した」を返さない（#2563）
    ctx.tmux.boots = False
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    assert not got.ok and "立ち上がらなかった" in got.summary
    assert registry.read_registry(hub.hub_dir(ctx.state_dir)) == {}


def test_missing_wrapper_location_says_how_to_recover(ctx):
    # プラグインの更新でバージョンの保存先が消えたら、理由と打ち直しを返す（#2583）
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
    assert "starts" not in registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]


def _fill(ctx, names):
    for name in names:
        assert hub.submit(ctx, {"kind": "spawn", "name": name}).ok


def test_i15_restart_of_a_running_target_at_the_named_limit_goes_through(ctx):
    # 決定 13・I15: 上限 4 本で 4 本が動いているとき、動いている対象の restart は進める（対象を除いて 3 本、足して 4 本）
    named_cap.write(ctx.state_dir, 4)
    _fill(ctx, ("t1", "t2", "t3", "t4"))
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.ok, got.summary
    assert len(ctx.tmux.started) == 5 and ctx.tmux.has("t1")


def test_i15_restart_of_a_stopped_target_over_the_named_limit_is_refused(ctx):
    # 決定 13・I15: 上限 4 本で対象を除く 4 本が動き、対象が止まっているとき、対象の restart は断る（足して 5 本）
    named_cap.write(ctx.state_dir, 5)
    _fill(ctx, ("t1", "t2", "t3", "t4", "t5"))
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    named_cap.write(ctx.state_dir, 4)
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert (got.ok, got.cause) == (False, "refused") and "4 本" in got.summary
    assert not ctx.tmux.has("t1") and len(ctx.tmux.started) == 5


def test_i15_restart_30_times_a_day_never_hits_a_start_limit(ctx):
    # 決定 13・I15: 同じ名前を 1 日に 30 回 restart しても、名前つきのラッパーの数の上限に当たらなければ進める
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    for _ in range(30):
        got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
        assert got.ok, got.summary
    assert len(ctx.tmux.started) == 31 and ctx.tmux.has("t1")


@pytest.mark.parametrize("raw", ["abc", "-1"])
def test_restart_with_an_invalid_spin_keeps_the_target_running(ctx, raw):
    # 決定 13: 後継へ渡す NDF_RELAY_SPIN が不正なら、動いているレーンを止める前に precondition で断る（spawn も起動しない）
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    ctx.environ = {**ctx.environ, "NDF_RELAY_SPIN": raw}
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert (got.ok, got.cause) == (False, "precondition") and "NDF_RELAY_SPIN" in got.summary
    assert ctx.tmux.has("t1") and len(ctx.tmux.started) == 1
    assert hub.submit(ctx, {"kind": "spawn", "name": "t2"}).cause == "precondition" and len(ctx.tmux.started) == 1


def test_spin_is_passed_to_tmux_even_when_unset(ctx):
    # 決定 13: 未設定でも NDF_RELAY_SPIN= を -e で渡し、tmux サーバーのグローバル環境の値を後継に読ませない
    assert "NDF_RELAY_SPIN" not in ctx.environ
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    env = ctx.tmux.started[0][1]
    assert env["NDF_RELAY_SPIN"] == ""
    assert "NDF_RELAY_SPIN=" in new_session_args("t1", ctx.state_dir, ["ndf"], env)
    assert hub.spawn_env({"NDF_RELAY_SPIN": "30"}, ctx.state_dir, "t1", {})["NDF_RELAY_SPIN"] == "30"


def test_old_ndf_wrapper_blocks_spawn_and_restart(ctx, tmp_path, hold_lock):
    # 旧いバージョンのラッパーが動いていれば、新しいバージョンで起動しない（3341-2）
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
    assert got.cause == "wait_timeout" and not list((base / hub.REQUESTS_DIR).glob("*.json"))


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


def _register(ctx, entry):
    path = hub.hub_dir(ctx.state_dir) / registry.REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entries": {"t1": entry}}), encoding="utf-8")


def test_i15_restart_drops_the_starts_of_an_older_registry_entry(ctx):
    # 決定 13: 前のバージョンの starts を持つ登録簿の項目を restart すると、書き戻した項目に starts が無い（上限の回数でも断らない）
    _register(ctx, {"cwd": None, "args": [], "starts": {"date": "2026-11-21", "count": 99}})
    assert [r["name"] for r in hub.listing(ctx)] == ["t1"] and "starts" not in hub.listing(ctx)[0]
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.ok, got.summary
    assert "starts" not in registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]


def test_spawn_registers_no_starts(ctx):
    # 決定 13: spawn は登録簿の項目に starts を書かない（hub ls の項目にも出ない）
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1"}).ok
    assert "starts" not in registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]
    assert "starts" not in hub.listing(ctx)[0]


def test_the_hub_context_has_no_start_limit_or_clock_for_the_day():
    # 決定 13: リレーハブは 1 日の起動の上限と日付を持たない
    fields = {f.name for f in dataclasses.fields(hub.Context)}
    assert not fields & {"max_starts", "today"}
    assert not {f.name for f in dataclasses.fields(hub.hub_decide.Lanes)} & {"starts_today", "max_starts"}
    assert not hasattr(registry, "Starts")


@pytest.mark.parametrize(
    ("environ", "seconds"), [({}, 120.0), ({"NDF_RELAY_SPIN": ""}, 120.0), ({"NDF_RELAY_SPIN": "0"}, 0.0), ({"NDF_RELAY_SPIN": "30"}, 30.0)]
)
def test_spin_of_reads_the_seconds_from_the_env(environ, seconds):
    # 決定 13: 秒は NDF_RELAY_SPIN（無いか空なら relay.spin の秒、0 は検出を切る）。NDF_RELAY_MAX_STARTS は読まない
    assert hub.spin_of({**environ, "NDF_RELAY_MAX_STARTS": "abc"}) == switch_decide.Spin(3, seconds)


@pytest.mark.parametrize("raw", ["abc", "-1"])
def test_spin_of_refuses_a_broken_value(raw):
    with pytest.raises(limits.EnvError, match="NDF_RELAY_SPIN"):
        hub.spin_of({"NDF_RELAY_SPIN": raw})


def test_a_broken_registry_answers_invalid_and_does_not_block_later_requests(ctx):
    # リクエスト 1 本の例外で、後ろのリクエストが回答を待ち切れなくならない
    path = hub.hub_dir(ctx.state_dir) / registry.REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れた", encoding="utf-8")
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1"})
    assert (got.ok, got.cause) == (False, "invalid") and "登録簿を読めない" in got.summary
    assert not list((hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR).glob("*.json"))
    path.unlink()
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok


def test_an_unreadable_request_file_gets_an_invalid_result(ctx):
    requests = hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR
    requests.mkdir(parents=True)
    (requests / "0-bad.json").write_text("[1", encoding="utf-8")
    assert hub.drain(ctx)
    result = json.loads((hub.hub_dir(ctx.state_dir) / "results" / "0-bad.json").read_text(encoding="utf-8"))
    assert (result["ok"], result["cause"]) == (False, "invalid") and not (requests / "0-bad.json").exists()


def test_a_request_removed_by_its_timed_out_submitter_is_skipped(ctx, monkeypatch):
    # 頼む側が期限切れで消したリクエストに結果を書かず、後ろのリクエストも処理する
    requests = hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR
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


# --- 上限の値と置き換えの起動（#3989 の決定 3・4・7） ---------------------------------------------------


def _spawned(ctx, *names):
    for name in names:
        assert hub.submit(ctx, {"kind": "spawn", "name": name}).ok


def _replace(name, seat="lane:x:r1", seated=False, **extra):
    return {"kind": hub.REPLACE, "name": name, "seat": seat, "seated": seated, **extra}


def test_a_cap_refusal_names_the_count_and_the_cap(ctx):
    _spawned(ctx, "t1", "t2")
    got = hub.submit(ctx, {"kind": "spawn", "name": "t3"})
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 2}]


def test_the_machine_setting_is_read_at_each_decision(ctx):
    """マシンの設定を書くと次の判定から使い、下げても動いているラッパーは止めない（受け入れ条件 10）。"""
    _spawned(ctx, "t1", "t2")
    named_cap.write(ctx.state_dir, 3)
    assert hub.submit(ctx, {"kind": "spawn", "name": "t3"}).ok
    named_cap.write(ctx.state_dir, 1)
    assert hub.submit(ctx, {"kind": "spawn", "name": "t4"}).items[0]["cap"] == 1
    assert sorted(r.name for r in registry.running(ctx.state_dir)) == ["t1", "t2", "t3"]


def test_the_requesters_environment_decides_when_the_machine_setting_is_absent(ctx):
    """ハブを兼ねたプロセスの環境に変数が無くても、要求に載った頼む側の値（8）で判定する（受け入れ条件 7）。"""
    requester = dataclasses.replace(ctx, environ={**ctx.environ, "NDF_RELAY_MAX_NAMED": "8"})
    hub_env = {k: v for k, v in ctx.environ.items() if k != "NDF_RELAY_MAX_NAMED"}
    hub_ctx = dataclasses.replace(ctx, environ=hub_env)
    for i in range(6):
        hub.signals.replace_json(
            hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR / f"{i:03d}.json",
            {"kind": "spawn", "name": f"t{i + 1}", "cap": {"value": 8, "source": "env"}},
        )
    assert hub.drain(hub_ctx)
    real = hub.drain
    hub.drain = lambda _c: real(hub_ctx)
    try:
        assert hub.submit(requester, {"kind": "spawn", "name": "t7"}).ok
    finally:
        hub.drain = real


def test_a_cleared_machine_setting_falls_back_to_the_requesters_value_not_the_cleared_one(ctx, monkeypatch):
    """マシンの設定 6・頼む側の環境 2 で要求を書き、drain の前に消すと、ハブは 2 で判定する（受け入れ条件 10）。"""
    _spawned(ctx, "t1", "t2")
    named_cap.write(ctx.state_dir, 6)
    assert hub.submit(ctx, {"kind": "spawn", "name": "t3"}).ok
    real = hub.drain
    calls = []

    def late_drain(c):
        if not calls:
            calls.append(1)
            named_cap.write(ctx.state_dir, None)
        return real(c)

    monkeypatch.setattr(hub, "drain", late_drain)
    got = hub.submit(ctx, {"kind": "spawn", "name": "t4"})
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 3, "cap": 2}]


def test_the_request_carries_the_requesters_fallback_and_not_the_machine_setting(ctx, monkeypatch):
    named_cap.write(ctx.state_dir, 6)
    seen = []
    monkeypatch.setattr(
        hub,
        "drain",
        lambda c: seen.extend(json.loads(p.read_text()) for p in (hub.hub_dir(c.state_dir) / hub.REQUESTS_DIR).glob("*.json")) or False,
    )
    hub.submit(dataclasses.replace(ctx, hub_wait=hub.Wait(0.0, 0.1)), {"kind": "spawn", "name": "t1"})
    assert seen[0]["cap"] == {"value": 2, "source": "env"}


def test_a_broken_requester_env_is_used_only_without_the_machine_setting(ctx):
    broken = dataclasses.replace(ctx, environ={**ctx.environ, "NDF_RELAY_MAX_NAMED": "1.5"})
    got = hub.submit(broken, {"kind": "spawn", "name": "t1"})
    assert got.cause == "invalid" and "NDF_RELAY_MAX_NAMED" in got.summary
    named_cap.write(ctx.state_dir, 6)
    assert hub.submit(broken, {"kind": "spawn", "name": "t1"}).ok


def test_a_broken_machine_setting_answers_invalid_and_later_requests_are_handled(ctx):
    _spawned(ctx, "t1")
    path = named_cap.path(ctx.state_dir)
    path.write_text("[]", encoding="utf-8")
    requests = hub.hub_dir(ctx.state_dir) / hub.REQUESTS_DIR
    hub.signals.replace_json(requests / "001.json", {"kind": "spawn", "name": "t2"})
    hub.signals.replace_json(requests / "002.json", {"kind": "stop", "name": "t1"})
    assert hub.drain(ctx)
    results = hub.hub_dir(ctx.state_dir) / "results"
    first = json.loads((results / "001.json").read_text())
    second = json.loads((results / "002.json").read_text())
    assert (first["ok"], first["cause"]) == (False, "invalid") and second["ok"] and not list(requests.glob("*.json"))


def test_a_replacing_start_is_judged_before_the_target_is_stopped(ctx):
    """上限 2 で 3 本が動くと置き換えは対象を止めずに断り、上限 3 なら対象を止めてから起動する（受け入れ条件 7）。"""
    named_cap.write(ctx.state_dir, 3)
    _spawned(ctx, "t1", "t2", "t3")
    named_cap.write(ctx.state_dir, 2)
    got = hub.submit(ctx, _replace("t1"))
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 2}]
    assert ctx.tmux.has("t1") and len(ctx.tmux.started) == 3
    named_cap.write(ctx.state_dir, 3)
    assert hub.submit(ctx, _replace("t1")).ok
    assert len(ctx.tmux.started) == 4 and registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]["seat"] == "lane:x:r1"


def test_a_continuing_start_keeps_its_seat_even_over_the_cap(ctx):
    """3 本が動く間に上限を 2 へ下げても、同じ席の続きの起動は通り、元を止めてから起動して 3 本のまま（受け入れ条件 7）。"""
    named_cap.write(ctx.state_dir, 3)
    _spawned(ctx, "t2", "t3")
    assert hub.submit(ctx, _replace("t1")).ok
    named_cap.write(ctx.state_dir, 2)
    assert hub.submit(ctx, _replace("t1", seated=True)).ok
    assert len(registry.running(ctx.state_dir)) == 3 and len(ctx.tmux.started) == 4
    # 止まっていても登録簿に席があれば通る。壊れた上限の設定でも続きの起動は上限を読まない
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    named_cap.path(ctx.state_dir).write_text("broken", encoding="utf-8")
    assert hub.submit(ctx, _replace("t1", seated=True)).ok


@pytest.mark.parametrize(("name", "seat"), [("t9", "lane:x:r1"), ("t1", "lane:x:r2")])
def test_a_continuing_start_without_the_same_seat_is_refused(ctx, name, seat):
    assert hub.submit(ctx, _replace("t1")).ok
    got = hub.submit(ctx, _replace(name, seat=seat, seated=True))
    assert got.cause == "refused" and "席" in got.summary and len(ctx.tmux.started) == 1


def test_a_replacing_start_registers_no_starts(ctx):
    # 決定 13: 置き換えの起動も登録簿の項目に 1 日の起動の回数を書かない
    assert hub.submit(ctx, _replace("t1")).ok
    assert hub.submit(ctx, _replace("t1")).ok
    assert "starts" not in registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]


def test_a_replacing_start_to_a_missing_directory_keeps_the_target(ctx, tmp_path):
    assert hub.submit(ctx, _replace("t1")).ok
    got = hub.submit(ctx, _replace("t1", cwd=str(tmp_path / "nowhere")))
    assert got.cause == "precondition" and "作業ディレクトリ" in got.summary and ctx.tmux.has("t1")


def test_a_replacing_start_excludes_the_target_from_the_version_check(ctx, tmp_path):
    assert hub.submit(ctx, _replace("t1")).ok
    registry.write_state(registry.wrapper_dir(ctx.state_dir, "t1"), {"version": "10.9.0", "state": "running"})
    assert hub.submit(ctx, _replace("t1")).ok
    _spawned(ctx, "t2")
    registry.write_state(registry.wrapper_dir(ctx.state_dir, "t2"), {"version": "10.9.0", "state": "running"})
    got = hub.submit(ctx, _replace("t1"))
    assert got.cause == "precondition" and ctx.tmux.has("t1")


def test_listing_shows_a_wrapper_outside_the_registry(ctx, hold_lock):
    _spawned(ctx, "t1")
    fd = hold_lock(registry.wrapper_dir(ctx.state_dir, "bare") / registry.LOCK_FILE)
    try:
        rows = {r["name"]: r for r in hub.listing(ctx)}
    finally:
        os.close(fd)
    assert rows["bare"]["running"] and rows["bare"]["registered"] is False
    assert rows["t1"]["registered"] is True and rows["t1"]["version"] == VERSION


# 現状固定（I-002）: 上限の断りの項目の running は対象を除いた本数。対象が止まっていれば除かない
def test_a_restart_refused_by_the_cap_names_the_count_without_the_running_target(ctx):
    named_cap.write(ctx.state_dir, 3)
    _spawned(ctx, "t1", "t2", "t3")
    named_cap.write(ctx.state_dir, 2)
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 2}]
    assert ctx.tmux.has("t1") and len(ctx.tmux.started) == 3


def test_a_restart_of_a_stopped_target_counts_every_running_wrapper(ctx):
    named_cap.write(ctx.state_dir, 3)
    _spawned(ctx, "t1", "t2", "t3")
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    named_cap.write(ctx.state_dir, 1)
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 1}]
    named_cap.write(ctx.state_dir, 3)
    assert hub.submit(ctx, {"kind": "restart", "name": "t1"}).ok


def test_a_replacing_start_of_a_name_not_running_counts_every_running_wrapper(ctx):
    _spawned(ctx, "t1", "t2")
    got = hub.submit(ctx, _replace("t3"))
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 2}]
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    named_cap.write(ctx.state_dir, 1)
    got = hub.submit(ctx, _replace("t1"))
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 1, "cap": 1}]


def test_a_replacing_start_counts_the_seats_the_admission_held(ctx):
    """許可が数えたラッパーの止まった稼働中のレーン（held）もハブが数える。許可の後に手のラッパーが空きに入れば、置き換えは対象を
    止めずに断る（#3989 の決定 3・7）。動いているラッパーと同じ名前は 1 回だけ数える。"""
    assert hub.submit(ctx, _replace("t1")).ok
    _spawned(ctx, "manual")
    got = hub.submit(ctx, _replace("t1", held=["stopped-lane"]))
    assert got.cause == "refused" and got.items == [{"refusal": "max-named", "running": 2, "cap": 2}] and ctx.tmux.has("t1")
    assert hub.submit(ctx, _replace("t1", held=["manual", "t1"])).ok


def test_a_running_row_from_before_the_seats_continues_and_gets_its_seat(ctx):
    """席を書く前のバージョンが書いた行（seat のキーが無い）は、役と計画が同じ続きの起動を通して席を書く。役か計画が違えば断る。"""
    assert hub.submit(ctx, {"kind": "spawn", "name": "t1", "role": "lane", "plan": "p6"}).ok
    entries = registry.read_registry(hub.hub_dir(ctx.state_dir))
    assert entries["t1"]["seat"] is None
    entries["t1"].pop("seat")
    registry.save_registry(hub.hub_dir(ctx.state_dir), entries)
    assert hub.submit(ctx, _replace("t1", seated=True, role="lane", plan="other")).cause == "refused"
    assert hub.submit(ctx, _replace("t1", seated=True, role="lane", plan="p6")).ok
    assert registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]["seat"] == "lane:x:r1"
    # 席を持たずに今のバージョンが起動した行（seat が null）は、続きの起動に使えない
    assert hub.submit(ctx, {"kind": "spawn", "name": "t2", "role": "lane", "plan": "p6"}).ok
    assert hub.submit(ctx, _replace("t2", seated=True, role="lane", plan="p6")).cause == "refused"


def test_a_replace_answered_by_an_older_hub_is_written_again_until_this_hub_handles_it(ctx, monkeypatch):
    """種別を知らない旧版のハブが「知らないリクエスト」で断ったら、新しい要求 ID で書き直し、対応版のハブが処理して起動する。"""
    real = hub.drain
    calls = []

    def older_then_real(c):
        if not calls:
            calls.append(1)
            requests = hub.hub_dir(c.state_dir) / hub.REQUESTS_DIR
            for path in requests.glob("*.json"):
                kind = json.loads(path.read_text())["kind"]
                body = {"ok": False, "summary": f"知らないリクエスト: {kind}", "cause": "invalid", "items": []}
                hub.signals.replace_json(hub.hub_dir(c.state_dir) / "results" / path.name, body)
                path.unlink()
            return True
        return real(c)

    monkeypatch.setattr(hub, "drain", older_then_real)
    assert hub.submit(ctx, _replace("t1")).ok and len(calls) == 1 and ctx.tmux.has("t1")


def test_the_late_delete_by_an_older_hub_does_not_remove_the_rewritten_request(ctx, monkeypatch):
    """旧版のハブが結果を書いてから要求を消すまでの間に書き直しても、再送は別の要求 ID なので消されずに処理される。"""
    real = hub.drain
    answered: list = []

    def older_then_real(c):
        requests = hub.hub_dir(c.state_dir) / hub.REQUESTS_DIR
        if not answered:
            for path in requests.glob("*.json"):
                body = {"ok": False, "summary": "知らないリクエスト: replace", "cause": "invalid", "items": []}
                hub.signals.replace_json(hub.hub_dir(c.state_dir) / "results" / path.name, body)
                answered.append(path)  # 要求のファイルはまだ消さない
            return True
        for path in answered:
            path.unlink(missing_ok=True)  # 旧版のハブの遅れた削除
        return real(c)

    monkeypatch.setattr(hub, "drain", older_then_real)
    assert hub.submit(ctx, _replace("t1")).ok and ctx.tmux.has("t1")


def test_the_listing_reads_each_wrapper_state_once(ctx, monkeypatch):
    _spawned(ctx, "t1", "t2")
    reads = []
    real = registry.read_state
    monkeypatch.setattr(registry, "read_state", lambda d: reads.append(d.name) or real(d))
    rows = hub.listing(ctx)
    assert [r["name"] for r in rows] == ["t1", "t2"] and sorted(reads) == ["t1", "t2"]


def test_4036_the_listing_carries_the_session_cwd_and_pid(ctx, hold_lock):
    # 会話の記録を組む材料（session_id・cwd）とラッパーの pid を一覧の行に載せる（#4036）
    _spawned(ctx, "t1")
    registry.write_state(
        registry.wrapper_dir(ctx.state_dir, "t1"),
        {"version": VERSION, "state": "running", "session_id": "s1", "cwd": "/w", "pid": 42},
    )
    registry.write_state(registry.wrapper_dir(ctx.state_dir, "bare"), {"state": "running", "session_id": "s2", "cwd": "/b", "pid": 7})
    fd = hold_lock(registry.wrapper_dir(ctx.state_dir, "bare") / registry.LOCK_FILE)
    try:
        rows = {r["name"]: r for r in hub.listing(ctx)}
    finally:
        os.close(fd)
    assert (rows["t1"]["session_id"], rows["t1"]["cwd"], rows["t1"]["pid"]) == ("s1", "/w", 42)
    assert (rows["bare"]["session_id"], rows["bare"]["cwd"], rows["bare"]["pid"]) == ("s2", "/b", 7)


def test_4036_a_row_without_the_state_file_carries_none(ctx):
    _spawned(ctx, "t1")
    (registry.wrapper_dir(ctx.state_dir, "t1") / registry.STATE_FILE).unlink()
    row = hub.listing(ctx)[0]
    assert (row["session_id"], row["cwd"], row["pid"]) == (None, None, None)


def test_4036_a_running_row_carries_the_conductor_activity(ctx, hold_lock, tmp_path, monkeypatch):
    """動いている行に conductor の活動を載せる。会話の記録は末尾の hook.transcript-tail の大きさだけ読む。"""
    from ndf.domains.relay import observe

    environ = {**ctx.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
    ctx = dataclasses.replace(ctx, environ=environ)
    _spawned(ctx, "t1")
    registry.write_state(registry.wrapper_dir(ctx.state_dir, "t1"), {"state": "running", "session_id": "s1", "cwd": "/w", "pid": 42})
    transcript = observe.transcript_path(environ, Path("/w"), "s1")
    transcript.parent.mkdir(parents=True)
    ended = {"type": "assistant", "timestamp": "2026-11-23T00:00:00Z", "message": {"stop_reason": "end_turn", "content": []}}
    transcript.write_text(json.dumps(ended) + "\n", encoding="utf-8")
    sizes = []
    real = observe.turn_done
    monkeypatch.setattr(observe, "turn_done", lambda path, size: sizes.append(size) or real(path, size))
    row = hub.listing(ctx)[0]
    assert row["turn_done"] is True and row["moved"] == observe._time("2026-11-23T00:00:00Z")
    # 活動の記録が無いので背景の作業の数は確かめられない
    assert row["background"] is None
    assert sizes == [int(limits.value("hook.transcript-tail", "count"))]


def test_4036_a_stopped_row_carries_no_activity(ctx):
    _spawned(ctx, "t1")
    ctx.tmux.kill("t1")
    row = hub.listing(ctx)[0]
    assert row["running"] is False and (row["turn_done"], row["moved"], row["background"]) == (None, None, None)
