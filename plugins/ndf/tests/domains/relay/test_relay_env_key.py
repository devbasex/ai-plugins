"""中継の変数の名前をコントラクトのキーから引き、子の会話の ID を渡す（#4049 の Task 4・#4126 の決定 3・4）。"""

from __future__ import annotations

import pytest
from env_contract import use_env_contract, without_key
from relay_children import FakeChild, Spawner, clock_ticks, make_settings, register, run_wrapper

from ndf import identity
from ndf.contracts import causes
from ndf.domains.relay import account_login, accounts, claude_args, cli, registry, signals, wrapper

KEYS = ("relay-dir", "relay-session")


@pytest.mark.parametrize("env", [{}, {"NDF_RELAY_SESSION": ""}], ids=["unset", "empty"])
def test_relay_session_is_none_without_a_value(env):
    assert signals.relay_session(env) is None


@pytest.mark.parametrize("account", [None, "work1"], ids=["default-login", "named"])
def test_each_child_gets_its_own_session_id(tmp_path, monkeypatch, account):
    # 既定のログイン・名前つきの起動先・起動の前の下限の確かめのどれも、その子の会話の ID を環境に持つ
    # （既定のログインで始めるときは登録しない。登録があれば最初の起動はそのアカウントになる）
    register(tmp_path, *([] if account is None else [account]))
    checked = []
    monkeypatch.setattr(wrapper.cli_floor, "require", lambda name, argv0, env, cwd: checked.append(signals.relay_session(env)))
    prepare = None if account is None else lambda _dir, state: accounts.use(state, account)
    _, spawn, _ = run_wrapper(tmp_path, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], prepare, {0: "/goal 次"})
    sessions = [argv[2] for argv, _ in spawn.calls]
    assert [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls] == [account, account]
    assert [signals.relay_session(env) for _, env in spawn.calls] == sessions == checked
    assert len(set(sessions)) == 2


def _run(tmp_path, monkeypatch):
    """偽の子で `ndf relay run` を打つ。返すのは終了コードと起動の記録。"""
    spawn = Spawner([FakeChild([], 0)])
    real_wrapper, real_parse = wrapper.Wrapper, claude_args.parse
    monkeypatch.setattr(claude_args, "parse", lambda args, environ, tty: real_parse(args, environ, True))
    monkeypatch.setattr(cli, "wrapper_settings", lambda name, rest, parsed: make_settings(tmp_path))
    monkeypatch.setattr(wrapper.cli_floor, "require", lambda *a, **k: None)  # 実の `claude --version` を打たない
    monkeypatch.setattr(wrapper, "Wrapper", lambda s, **kw: real_wrapper(s, spawn=spawn, clock=clock_ticks(), **kw))
    try:
        return cli.main(["run", "--name", "t1"]), spawn.calls
    finally:
        for c in spawn.made + spawn.children:
            c.close()


def test_run_starts_the_child_with_the_shipped_contract(tmp_path, monkeypatch):
    code, calls = _run(tmp_path, monkeypatch)
    assert code == 0 and len(calls) == 1


@pytest.mark.parametrize("key", KEYS)
def test_run_stops_before_the_launch_when_a_key_is_missing(tmp_path, monkeypatch, capsys, last_result, key):
    use_env_contract(monkeypatch, tmp_path / "root", without_key(key))
    code, calls = _run(tmp_path, monkeypatch)
    got = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("config_broken") and got["status"] == "stopped" and key in got["summary"]
    assert calls == [] and not (registry.wrapper_dir(tmp_path / "state", "t1") / signals.LAUNCH_FILE).exists()


def test_a_missing_key_stops_the_login_but_not_the_other_commands(tmp_path, monkeypatch, capsys):
    use_env_contract(monkeypatch, tmp_path / "root", without_key("relay-dir"))
    assert cli.main(["ls"]) == 0
    with pytest.raises(identity.EnvNameError):
        account_login.auth_env({}, tmp_path / "config")


def test_the_login_child_does_not_inherit_the_relay(tmp_path):
    parent = {"PATH": "/bin", **signals.launch_env(tmp_path, "L1", "s1")}
    got = account_login.auth_env(parent, tmp_path / "config")
    assert signals.relay_dir(got) is None and got["PATH"] == "/bin"
