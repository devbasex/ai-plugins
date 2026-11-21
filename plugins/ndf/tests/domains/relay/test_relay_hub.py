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

from ndf.contracts import limits
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
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "GIT_ASKPASS": "/vscode"},
        tmux=tmux,
        max_named=2,
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
    assert hub.requester(ctx, req) is ctx


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
    ctx.max_named = 4
    _fill(ctx, ("t1", "t2", "t3", "t4"))
    got = hub.submit(ctx, {"kind": "restart", "name": "t1"})
    assert got.ok, got.summary
    assert len(ctx.tmux.started) == 5 and ctx.tmux.has("t1")


def test_i15_restart_of_a_stopped_target_over_the_named_limit_is_refused(ctx):
    # 決定 13・I15: 上限 4 本で対象を除く 4 本が動き、対象が止まっているとき、対象の restart は断る（足して 5 本）
    ctx.max_named = 5
    _fill(ctx, ("t1", "t2", "t3", "t4", "t5"))
    assert hub.submit(ctx, {"kind": "stop", "name": "t1"}).ok
    ctx.max_named = 4
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


def test_i15_spawn_again_resumes_from_the_saved_values_and_keeps_the_registered_args(ctx):
    # 決定 13・I15（検出）: 再起動ループの案内の spawn --again は、再開の値の作業ディレクトリ・役割・計画で後継を --again で
    # 起動し、引数を tmux のコマンドに載せない。登録簿の引数（最初の作業指示）は上書きせず、後の restart が元の指示で起動する
    first = ["--model", "x", "/goal 最初の作業"]
    _register(ctx, {"cwd": "/w", "args": first, "role": "worker", "plan": "p1", "version": VERSION})
    secret = ["--mcp-config", '{"headers":{"Authorization":"Bearer sk-secret"}}']
    saved = {"session_id": "s1", "args": secret, "account": "work2", "cwd": "/w2", "role": "worker", "plan": "p1"}
    registry.write_again(registry.wrapper_dir(ctx.state_dir, "t1"), saved)
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1", "again": True})
    assert got.ok, got.summary
    assert ctx.tmux.commands[0][-4:] == ["run", "--name", "t1", "--again"]
    assert "sk-secret" not in " ".join(ctx.tmux.commands[0])
    _, env = ctx.tmux.started[0]
    assert (env["NDF_RELAY_ROLE"], env["NDF_RELAY_PLAN"]) == ("worker", "p1")
    assert registry.read_registry(hub.hub_dir(ctx.state_dir))["t1"]["args"] == first
    assert hub.submit(ctx, {"kind": "restart", "name": "t1"}).ok
    assert ctx.tmux.commands[1][-len(first) - 1 :] == ["--", *first]


def test_i15_spawn_again_without_saved_values_is_invalid(ctx):
    # 決定 13: 再開の値が無い名前の spawn --again は起動せずに断る
    got = hub.submit(ctx, {"kind": "spawn", "name": "t1", "again": True})
    assert (got.ok, got.cause) == (False, "invalid") and not ctx.tmux.started
