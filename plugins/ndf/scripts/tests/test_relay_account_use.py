"""`relay.py account use <名前>`（#2460）: 動いているラッパーでも、次の区間の起動から使うアカウントを指定する。

指定は置き場のファイルで持ち、区間の起動のたびに読む。ラッパーは擬似端末の偽物（`test_relay.Term`）で動かし、
副命令はサブプロセスで打つ（`test_relay_account.run`）。
"""

from __future__ import annotations

import json
import subprocess
import sys

from account_fake import accounts, window  # noqa: F401
from aws_fake import FakeAws
from test_relay import RELAY, account_term, events, hit_limit, isolated_env, term  # noqa: F401
from test_relay import _bare_relay

sys.path.insert(0, str(RELAY.parent / "lib"))
import claude_accounts as ca  # noqa: E402
from relay_lib import pin  # noqa: E402


def use(tmp_path, accounts, *args):  # noqa: F811
    e = isolated_env(tmp_path, **accounts.env(), **FakeAws(tmp_path).env())
    cmd = [sys.executable, str(RELAY), "account", *args]
    return subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def _picker(cur):
    r = _bare_relay({})
    r.account, r.watch, r.multi = cur, None, True
    return r


# ---------------------------------------------------------------- 区間の起動（受け入れ条件 1・3・8）


def test_use_switches_running_wrapper_at_next_section(term, accounts, tmp_path):  # noqa: F811
    """受け入れ条件 1・8: 動いているラッパーでも、指定の後の区間の起動で替わる。今が閾値未満でも替わる。"""
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    t = account_term(term, accounts)
    t.wait_start(1)
    assert t.starts()[0]["account"] == "a"
    p = use(tmp_path, accounts, "use", "b")
    assert p.returncode == 0, p.stderr
    t.type("mark 次\r")
    t.wait_start(2)
    assert t.starts()[1]["account"] == "b" and t.starts()[1]["config_dir"] == str(accounts.root / "b")
    assert [(r["reason"], r["from"], r["to"]) for r in events(t.rows(), "account")] == [("pinned", "a", "b")]
    t.type("mark 次\r")  # 指定のアカウントは閾値を超えても替えない
    t.wait_start(3)
    assert t.starts()[2]["account"] == "b"


def test_pinned_wins_over_threshold_and_remaining(accounts):  # noqa: F811
    accounts.add("a", util5=10)
    accounts.add("b", util5=95)
    pin.set_pinned("b")
    assert _picker("a").pick(None)[:2] == ("b", "pinned")
    assert _picker("b").pick(None)[:2] == ("b", None)  # 閾値を超えても指定のまま
    assert _picker(None).pick(None)[:2] == ("b", "pinned")  # 起動の直後


def test_pinned_brings_back_from_metered(accounts, monkeypatch):  # noqa: F811
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    pin.set_pinned("b")
    assert _picker("metered").pick(None)[:2] == ("b", "pinned")


def test_clear_returns_to_current_rule(accounts, tmp_path):  # noqa: F811
    """受け入れ条件 3: `--clear` の後は今の選び方（閾値未満なら替えない）に戻る。"""
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    pin.set_pinned("b")
    p = use(tmp_path, accounts, "use", "--clear", "--json")
    assert p.returncode == 0 and json.loads(p.stdout) == {"ok": True, "command": "use", "pinned": None, "cleared": True}
    assert pin.pinned() is None
    assert _picker("a").pick(None)[:2] == ("a", None)
    p = use(tmp_path, accounts, "use", "--clear", "--json")  # 指定が無くても通る
    assert p.returncode == 0 and json.loads(p.stdout)["cleared"] is False


# ---------------------------------------------------------------- 使えない指定（受け入れ条件 2）


def test_pinned_limited_uses_other_then_returns(accounts, monkeypatch):  # noqa: F811
    """受け入れ条件 2: 指定が上限なら今の選び方で別のアカウントへ、上限が外れた後の区間の起動で指定へ戻る。"""
    monkeypatch.setenv("NDF_ACCOUNT_CHECK_INTERVAL", "0")  # 保存した残量を使わず毎回読む
    accounts.add("a", util5=10)
    tb = accounts.add("b", util5=100)
    pin.set_pinned("b")
    assert _picker("a").pick(None)[:2] == ("a", None)
    assert _picker("b").pick("five_hour")[0] == "a"  # 上限の後の選び直しは指定を除く（今と同じ）
    assert pin.pinned() == "b"  # 指定は外さない
    accounts.fake.set_usage(tb, window(10), window(10))
    assert _picker("a").pick(None)[:2] == ("b", "pinned")


def test_pinned_limited_e2e(term, accounts, tmp_path):  # noqa: F811
    accounts.add("a", util5=10)
    tb = accounts.add("b", util5=20)
    pin.set_pinned("b")
    # 保存した残量を使わず毎回読む。1 秒の間隔だと、上限の直後の区間の起動が上限の残量を読み直さずに使う（CI で落ちた）
    t = account_term(term, accounts, NDF_ACCOUNT_CHECK_INTERVAL="0")
    t.wait_start(1)
    assert t.starts()[0]["account"] == "b"
    accounts.fake.set_usage(tb, window(100, 3600), window(5))
    hit_limit(t, 0)
    t.wait_start(2)
    assert t.starts()[1]["account"] == "a"
    accounts.fake.set_usage(tb, window(10), window(5))
    t.type("mark 次\r")
    t.wait_start(3)
    assert t.starts()[2]["account"] == "b"
    assert [(r["reason"], r["from"], r["to"]) for r in events(t.rows(), "account")] == [("five_hour", "b", "a"), ("pinned", "a", "b")]


def test_pinned_needing_relogin_falls_back(accounts):  # noqa: F811
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    pin.set_pinned("b")
    row = accounts.account("b")
    accounts.write(accounts.root / "b" / "account.json", {**row, "needs_relogin": True})
    assert _picker("a").pick(None)[:2] == ("a", None)


def test_pinned_removed_account_is_ignored(accounts):  # noqa: F811
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    pin.set_pinned("b")
    ca.unregister("b")
    assert pin.pinned() is None
    assert _picker("a").pick(None)[:2] == ("a", None)


# ---------------------------------------------------------------- 断る指定（受け入れ条件 4・6）


def test_use_refuses_unknown_and_relogin(accounts, tmp_path):  # noqa: F811
    """受け入れ条件 4: 登録に無い名前・再登録が要る名前は終了コード 1 と理由で断る。"""
    accounts.add("a")
    p = use(tmp_path, accounts, "use", "zzz", "--json")
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == "not_registered"
    p = use(tmp_path, accounts, "use", "zzz")
    assert p.returncode == 1 and "zzz" in p.stderr
    oauth = accounts.creds("a")
    accounts.write(accounts.root / "a" / ".credentials.json", {"claudeAiOauth": {**oauth, "refreshToken": ""}})
    p = use(tmp_path, accounts, "use", "a", "--json")
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == "needs_relogin"
    p = use(tmp_path, accounts, "use", "metered", "--json")
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == "not_registered"
    assert pin.pinned() is None


def test_use_takes_yes_and_json(accounts, tmp_path):  # noqa: F811
    """受け入れ条件 6: 他の副命令と同じく `--yes`・`--json` を受け、足りなければ名前を示して 2。"""
    accounts.add("a")
    p = use(tmp_path, accounts, "use", "a", "--yes", "--json")
    assert p.returncode == 0 and json.loads(p.stdout) == {"ok": True, "command": "use", "name": "a", "pinned": "a"}
    p = use(tmp_path, accounts, "use", "a")
    assert p.returncode == 0 and "a" in p.stdout
    p = use(tmp_path, accounts, "use", "--yes", "--json")
    assert p.returncode == 2 and json.loads(p.stdout)["missing"] == ["<名前>"]
    p = use(tmp_path, accounts, "use", "a", "--clear", "--json")
    assert p.returncode == 2 and json.loads(p.stdout)["ok"] is False


# ---------------------------------------------------------------- 一覧（受け入れ条件 5）


def test_list_marks_pinned(accounts, tmp_path):  # noqa: F811
    accounts.add("a")
    accounts.add("b")
    assert use(tmp_path, accounts, "use", "b").returncode == 0
    rows = json.loads(use(tmp_path, accounts, "list", "--json").stdout)
    assert {r["name"]: r["pinned"] for r in rows} == {"a": False, "b": True}
    table = use(tmp_path, accounts, "list").stdout.splitlines()
    assert [ln.split()[0] for ln in table[1:]] == ["a", "b（指定）"]


def test_ls_shows_pinned(accounts, monkeypatch, tmp_path, capsys):  # noqa: F811
    from relay_lib import hub_client as hc

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NDF_RELAY_TMUX", "ndf-no-tmux")
    accounts.add("a")
    assert hc.cmd_ls(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["pinned_account"] is None
    pin.set_pinned("a")
    assert hc.cmd_ls(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["pinned_account"] == "a"
    assert hc.cmd_ls([]) == 0
    assert "a" in capsys.readouterr().out.splitlines()[-1]
