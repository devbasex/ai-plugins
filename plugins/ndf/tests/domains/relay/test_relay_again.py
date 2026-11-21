"""`--again` の再開（#3604 #3864 の決定 13）: 錠の後に読む再開の値・保存した作業ディレクトリ・役割・計画・断る条件。

再起動ループの案内（`--again` の 1 行）を出す側は `test_relay_wrapper.py` にある。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ndf.domains.relay import cli as relay_cli
from ndf.domains.relay import registry, signals
from relay_children import FakeChild, make_settings, run_wrapper

LIMITED = ("--disallowedTools", "Bash", "--permission-mode", "plan", "--settings", "/tmp/a b.json")


def _loader(loaded: list, **changes):
    """錠を取った後に再開の値を受け取る手（`Settings.again_load`）。受け取った値を `loaded` に残す。"""

    def load(saved):
        loaded.append(saved)
        return make_settings(Path(saved["cwd"]), again=True, first_account=saved["account"], **changes)

    return load


@pytest.mark.parametrize("account", ["work2", None])
def test_i15_again_starts_on_the_saved_account_without_choosing_again(tmp_path, account):
    # 決定 13・I15（検出）: --again の最初のセグメントは環境変数・account use の指定から選び直さず、再開の値のアカウント
    # （None は既定のログイン）で起動し、起動できたら再開の値を消す（1 回だけ使う）
    s = make_settings(tmp_path)
    saved = {"session_id": "s", "args": [], "account": account, "cwd": str(tmp_path)}
    registry.write_again(registry.wrapper_dir(s.state_dir, s.name), saved)
    env = {**s.environ, "NDF_CLAUDE_ACCOUNT": "limited"}
    loaded: list = []
    load = _loader(loaded, state_dir=s.state_dir, environ=env)
    ended, spawn = run_wrapper(tmp_path, [FakeChild([], 0)], environ=env, again_load=load)[:2]
    assert ended.cause == "" and loaded == [saved]
    assert spawn.calls[0][1].get("NDF_CLAUDE_ACCOUNT") == account
    assert not (registry.wrapper_dir(s.state_dir, s.name) / registry.AGAIN_FILE).exists()


def test_i15_again_reads_the_saved_values_only_after_taking_the_lock(tmp_path):
    # 決定 13: 同じ名前のラッパーが動いていれば、再開の値を読まず・消さずに断る（錠の前に読んだ値で後から起動しない）
    s = make_settings(tmp_path)
    d = registry.wrapper_dir(s.state_dir, s.name)
    registry.write_again(d, {"session_id": "s", "args": [], "account": None, "cwd": str(tmp_path)})
    loaded: list = []
    with signals.held(d / registry.LOCK_FILE, 0):
        ended = run_wrapper(tmp_path, [FakeChild([], 0)], again_load=_loader(loaded, state_dir=s.state_dir))[0]
    assert ended.cause == "refused" and loaded == [] and (d / registry.AGAIN_FILE).exists()


def test_i15_again_keeps_saved_values_written_after_it_read_them(tmp_path):
    # 決定 13: 起動に使った再開の値だけを消し、読んだ後に書き直された値は残す
    s = make_settings(tmp_path)
    d = registry.wrapper_dir(s.state_dir, s.name)
    registry.write_again(d, {"session_id": "s", "args": [], "account": None, "cwd": str(tmp_path)})
    newer = {"session_id": "s2", "args": [], "account": None, "cwd": str(tmp_path)}
    loaded: list = []
    first = _loader(loaded, state_dir=s.state_dir)

    def load(saved):
        got = first(saved)
        registry.write_again(d, newer)
        return got

    assert run_wrapper(tmp_path, [FakeChild([], 0)], again_load=load)[0].cause == ""
    assert registry.read_again(d)["session_id"] == "s2"


def test_i15_again_settings_use_the_saved_cwd_role_and_plan(tmp_path, monkeypatch):
    # 決定 13: run --again は打った場所でなく再開の値の作業ディレクトリ・役割・計画で起動し、計画への質問の経路も組む
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NDF_RELAY_ROLE", raising=False)
    monkeypatch.setenv("NDF_RELAY_PLAN", "other")
    saved = {"session_id": "s1", "args": list(LIMITED), "account": "work2", "cwd": str(work), "role": "lane", "plan": "p1"}
    s = relay_cli.again_settings("t1", saved, True)
    assert (s.cwd, s.cwd_lost, s.environ["NDF_RELAY_ROLE"], s.environ["NDF_RELAY_PLAN"]) == (work, None, "lane", "p1")
    assert (s.again, s.first_account, s.first_session, s.first_resume, s.args) == (True, "work2", "s1", True, LIMITED)
    assert s.ask_plan is not None
    gone = relay_cli.again_settings("t1", {**saved, "cwd": str(tmp_path / "gone"), "plan": None}, True)
    assert gone.cwd_lost == str(tmp_path / "gone") and "NDF_RELAY_PLAN" not in gone.environ


@pytest.mark.parametrize("relay, on_tty", [("0", True), (None, False)])
def test_i15_run_again_refuses_where_the_wrapper_cannot_run(tmp_path, monkeypatch, capsys, relay, on_tty):
    # 決定 13: NDF_RELAY=0 や端末でないときの run --again は claude を直接起動せず（保存したアカウントを使えない）、起動前に断る
    if relay is None:
        monkeypatch.delenv("NDF_RELAY", raising=False)
    else:
        monkeypatch.setenv("NDF_RELAY", relay)
    monkeypatch.setattr(relay_cli.os, "execvp", lambda *a: pytest.fail("claude を直接起動した"))
    args = relay_cli._parse(["run", "--name", "t1", "--again"])
    code = relay_cli._run_again(args, on_tty)
    assert code != 0 and json.loads(capsys.readouterr().out)["cause"]["kind"] == "precondition"
