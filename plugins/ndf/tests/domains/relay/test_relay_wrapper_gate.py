"""関所の起動と候補を試す起動の失敗の扱い（U8 の cross-review の r1-1〜r1-10・sv-1。#3604 #3864）。

- 関所の子が claude になれなかった名前（`execvp` の失敗・知らせが来ない・`attach` の失敗）は飛ばして次の候補へ進む
- 関所の失敗の後は子の終わりを確かめてから届けを解除し、確かめられなければ届けを残す
- 同じ会話を続ける起動し直し・引継ぎは今の起動先に固定し、用意できなければ既定のログインへ落とさずに止まる
- 上限の後の判断は名前・世代別の取得の記録を読む
値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import errno
import io
import json
import math
import os
import time

import pytest
from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_dir, accounts, launch_inputs, launch_target, registry, stop_signal, wrapper
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay.account_decide import Usage
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, make_settings, no_work, put_usages, register, rows_of


def _run(tmp_path, children, prepare=None, markers=None, spawner=None, **changes):
    s = make_settings(tmp_path, **changes)
    spawn = spawner if spawner is not None else Spawner(children, markers)
    spawn.children = list(children)
    spawn.markers = dict(markers or {})
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    if prepare:
        prepare(s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode()


def _envs(spawn) -> list:
    return [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls]


def _leases(store, name) -> list:
    return list(reg.lease_dir(store, name).glob("*.json"))


def _env_work1(tmp_path):
    return {**make_settings(tmp_path).environ, "NDF_CLAUDE_ACCOUNT": "work1"}


class GateSpawner(Spawner):
    """`fail` の名前の関所の子を、起動の知らせに errno を書いて claude になれなくする（`execvp` の失敗）。"""

    def __init__(self, fail=()):
        super().__init__([])
        self.fail = set(fail)

    def __call__(self, argv, env, cwd, pass_fds=()):
        status = int(argv[5]) if pass_fds else None
        child = super().__call__(argv, env, cwd, pass_fds)
        if status is not None and env.get("NDF_CLAUDE_ACCOUNT") in self.fail:
            dup = os.dup(status)
            os.write(dup, str(errno.ENOENT).encode())
            os.close(dup)
        return child


def _short_gate(monkeypatch):
    real = limits.seconds
    monkeypatch.setattr(limits, "seconds", lambda key: 0.05 if key in ("relay.gate-exec", "relay.child-exit") else real(key))


# ---------------------------------------------------------------- 関所の失敗は次の候補へ（r1-2・r1-10・sv-1）


@pytest.mark.parametrize("how", ["errno", "attach"])
def test_a_gate_failure_skips_to_the_next_candidate(tmp_path, monkeypatch, how):
    # 環境変数の work1 の関所の子が claude になれなければ、子を止めて届けを解除し、1 行を出して指定の work2 で起動する
    store = register(tmp_path, "work1", "work2")
    _short_gate(monkeypatch)
    spawn = GateSpawner({"work1"} if how != "attach" else ())
    if how == "attach":
        real = account_dir.attach
        monkeypatch.setattr(account_dir, "attach", lambda st, n, *a: "lock_timeout" if n == "work1" else real(st, n, *a))
    ended, spawn, w, shown = _run(
        tmp_path,
        [FakeChild([], 0), FakeChild([], 0)],
        lambda state: accounts.use(state, "work2"),
        spawner=spawn,
        environ=_env_work1(tmp_path),
    )
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert spawn.made[0].alive is False and _leases(store, "work1") == []
    code = {"errno": f"launch_failed errno {errno.ENOENT}", "attach": "lock_timeout"}[how]
    assert code in [r["reason"] for r in rows_of(w, "account_dir") if r["account"] == "work1"]
    assert shown.count("ndf-relay: アカウント work1 で起動しない") == 1
    row = rows_of(w, "account")[0]
    assert (row["account"], row["skipped"]) == ("work2", ["work1"])


def test_a_gate_failure_of_the_only_name_falls_back_to_the_default_login(tmp_path, monkeypatch):
    # 名前が尽きたら、最初の起動の扱い（既定のログイン）に合流する。届けは残らない
    store = register(tmp_path, "work1")
    spawn = GateSpawner({"work1"})
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0), FakeChild([], 0)], spawner=spawn, environ=_env_work1(tmp_path))
    assert _envs(spawn) == ["work1", None] and spawn.gated == [0] and ended.cause == ""
    assert _leases(store, "work1") == [] and rows_of(w, "account")[0]["skipped"] == ["work1"]


def test_gate_failures_of_the_limit_target_and_the_current_account_stop_without_the_default_login(tmp_path, monkeypatch):
    # 上限の後に替え先 main と今の team の関所がどちらも落ちれば、既定のログインを起動せずに account_unavailable で止まる
    store = register(tmp_path, "team", "main")
    attached = []
    real = account_dir.attach

    def attach(st, n, *a):
        attached.append(n)
        return real(st, n, *a) if len(attached) == 1 else "lock_timeout"

    monkeypatch.setattr(account_dir, "attach", attach)

    def setup(state):
        rows = [Usage("team", 50.0, 50.0, None, None, False), Usage("main", 5.0, 5.0, None, None, False)]
        put_usages(store, rows, fetched_at=time.time() - 3600)
        accounts.use(state, "team")

    children = [FakeChild([LIMIT_LINE], None), FakeChild([], 0), FakeChild([], 0)]
    ended, spawn, w, _ = _run(tmp_path, children, setup)
    assert attached == ["team", "main", "team"] and ended.cause == "account_unavailable"
    assert _envs(spawn) == ["team", "main", "team"] and spawn.gated == [0, 1, 2]
    assert _leases(store, "main") == [] and _leases(store, "team") == []


# ---------------------------------------------------------------- 失敗の経路でも子を追跡する（r1-8）


def _gated_target(store, name="work1"):
    lease = account_dir.acquire(store, name, store / name, "g1")
    return launch_target.Target(name, store / name, "g1", "first", (), lease, {}, ["claude"], None)


def _fake_spawn(child):
    def spawn(argv, env, cwd, pass_fds=()):
        child.gate, child.pid = os.dup(int(argv[4])), os.getpid()  # 関所の読み口を子の代わりに開けておく
        return child

    return spawn


def test_an_io_error_after_the_gate_opened_reaps_the_child_before_the_release(tmp_path, monkeypatch):
    store = register(tmp_path, "work1")
    child = FakeChild([], None)
    monkeypatch.setattr(launch_target.process_group, "read_status", lambda fd, s: (_ for _ in ()).throw(OSError(errno.EIO, "io")))
    try:
        with pytest.raises(launch_target.GateFailed) as got:
            launch_target.spawn(_fake_spawn(child), _gated_target(store), tmp_path, store)
    finally:
        child.close()
    assert got.value.code == f"gate_io errno {errno.EIO}" and child.alive is False and _leases(store, "work1") == []


class Stubborn(FakeChild):
    """強制終了しても終わらない子。"""

    def terminate(self, force=False):
        return False


# ---------------------------------------------------------------- 子が終わらないときは書き戻さない（r1-3）


# ---------------------------------------------------------------- 起動の直前の停止要求（r1-9）


def test_a_stop_requested_while_preparing_does_not_start_the_child(tmp_path, monkeypatch):
    store = register(tmp_path, "work1")
    real = account_dir.prepare
    s = make_settings(tmp_path)

    def prepare(*a):
        got = real(*a)
        stop_signal.location_stop(registry.wrapper_dir(s.state_dir, s.name))
        return got

    monkeypatch.setattr(account_dir, "prepare", prepare)
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)], lambda state: accounts.use(state, "work1"))
    assert ended == wrapper.Ended("", wrapper.STOP_REASON) and spawn.calls == [] and _leases(store, "work1") == []


# ---------------------------------------------------------------- 同じ会話は今の起動先に固定する（r1-5）


def _fail_after_first(monkeypatch):
    calls = []
    real = account_dir.prepare

    def prepare(store, name, *a):
        calls.append(name)
        return real(store, name, *a) if len(calls) == 1 else account_dir.Prepared(False, "lock_timeout", store / name)

    monkeypatch.setattr(account_dir, "prepare", prepare)
    return calls


@pytest.mark.parametrize("kind", ["restart", "handover"])
def test_the_same_conversation_does_not_move_to_the_default_login(tmp_path, monkeypatch, kind):
    # 起動し直し・引継ぎで今のアカウントを用意できなければ、既定のログインで --resume せずに理由を出して止まる
    register(tmp_path, "work1", "work2")
    calls = _fail_after_first(monkeypatch)
    if kind == "handover":
        monkeypatch.setattr(wrapper.observe, "context_tokens", lambda path, size: 900_000)
        first = no_work(FakeChild([b"working\r\n"], None))
    else:
        first = FakeChild([], 1)
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], lambda state: accounts.use(state, "work1"))
    assert calls == ["work1", "work1"] and len(spawn.calls) == 1
    assert ended.cause == "account_unavailable" and "work1" in ended.reason


# ---------------------------------------------------------------- 環境変数の名前が落ちたら指定（r1-6）


def test_an_env_account_that_fails_to_prepare_falls_to_the_pin(tmp_path, monkeypatch):
    register(tmp_path, "work1", "work2")
    real = account_dir.prepare
    monkeypatch.setattr(
        account_dir, "prepare", lambda st, n, *a: account_dir.Prepared(False, "lock_timeout", st / n) if n == "work1" else real(st, n, *a)
    )
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)], lambda state: accounts.use(state, "work2"), environ=_env_work1(tmp_path))
    assert _envs(spawn) == ["work2"] and rows_of(w, "account")[0]["skipped"] == ["work1"]


# ---------------------------------------------------------------- 上限の後は今の世代の取得の記録で決める（r1-4）


def test_a_limited_candidate_in_the_generation_records_is_not_picked_after_the_limit(tmp_path):
    store = register(tmp_path, "team", "main", "gmail")
    rows = [
        Usage("team", 50.0, 50.0, None, None, False),
        Usage("main", 100.0, 100.0, None, None, True),
        Usage("gmail", 5.0, 5.0, None, None, False),
    ]

    def setup(state):
        put_usages(store, rows, fetched_at=time.time() - 3600)
        accounts.use(state, "team")

    ended, spawn, w, _ = _run(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], setup)
    assert _envs(spawn) == ["team", "gmail"]


# ---------------------------------------------------------------- 登録と外した間は変わったときだけ読む（r1-7）


def test_launch_inputs_read_the_registry_and_the_holds_only_when_they_change(tmp_path, monkeypatch):
    store = register(tmp_path, "work1", "work2")
    loads, holds = [], []
    real_load, real_held = account_choice._load, accounts.held
    monkeypatch.setattr(account_choice, "_load", lambda st, n: (loads.append(n), real_load(st, n))[1])
    # 外した間だけを読む呼び出し（`-inf` まで）を数える。使用率の読み直しの呼び出しは数えない
    monkeypatch.setattr(accounts, "held", lambda st, now, g: (holds.append(1) if now == -math.inf else None, real_held(st, now, g))[1])
    s = make_settings(tmp_path)
    r = launch_inputs.Reader(store, s.state_dir, s.environ, tmp_path, [wrapper.CLAUDE])
    now = time.time()
    for _ in range(5):
        got = r.read(now, True)
    assert sorted(loads) == ["work1", "work2"] and len(holds) == 1 and got.candidates() == ["work1", "work2"]
    path = store / "work1" / reg.ACCOUNT_FILE
    path.write_text(json.dumps({**json.loads(path.read_text()), "needs_relogin": True}))
    got = r.read(now, True)
    assert sorted(loads) == ["work1", "work1", "work2", "work2"] and got.candidates() == ["work2"]
    accounts.hold(s.state_dir, "work2", "g1", now + 60, now)
    assert r.read(now, True).candidates() == [] and len(holds) == 2
    assert r.read(now + 120, True).candidates() == ["work2"] and len(holds) == 2


def test_the_real_pty_child_is_not_looked_up_with_the_parent_path(tmp_path, monkeypatch):
    # 子の PATH に無い実行ファイルは、親の PATH にあっても起動しない（ptyprocess に親の PATH で探し直させない）
    work = (tmp_path / "work").resolve()
    work.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    exe = parent / "ndf-probe-exe"
    exe.write_text("#!/bin/sh\necho parent\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(FileNotFoundError):
        wrapper.spawn_pty(["ndf-probe-exe"], {"PATH": "/usr/bin:/bin"}, work)
