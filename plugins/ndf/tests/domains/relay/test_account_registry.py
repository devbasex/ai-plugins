"""アカウントの登録の保存先と OAuth の登録（`ndf relay account add`・`list`・`capacity`・`remove`。#3569）。"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

from ndf.contracts import causes
from ndf.domains.relay import account_decide, account_login, cli
from ndf.domains.relay import account_registry as reg

FAKE_CLAUDE = """#!/bin/sh
# claude auth の代わり。login は URL を出して認可コードの 1 行を待ち、資格情報を書く。status は本人を出す
if [ "$2" = "login" ]; then
  echo "Open https://claude.ai/oauth/authorize?x=1"
  read code
  [ "$code" = "good" ] || exit 1
  echo '{"claudeAiOauth": {"accessToken": "t"}}' > "$CLAUDE_CONFIG_DIR/.credentials.json"
  exit 0
fi
if [ "$2" = "status" ]; then
  if [ -f "$CLAUDE_CONFIG_DIR/.credentials.json" ]; then
    echo '{"loggedIn": true, "email": "a@example.com", "orgId": "o1", "orgName": "Org"}'
  else
    echo '{"loggedIn": false}'
  fi
  exit 0
fi
exit 1
"""


def _account(store: Path, name: str, **extra) -> Path:
    d = store / name
    d.mkdir(parents=True)
    (d / reg.ACCOUNT_FILE).write_text(json.dumps({"name": name, "email": f"{name}@example.com", "needs_relogin": False, **extra}))
    return d


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_store_dir_follows_the_old_rules(tmp_path):
    # 旧い NDF の名前（ndf）の下を指し、今のプラグイン名（入れ替えの前は ndf11）から求めない
    assert reg.store_dir({"NDF_ACCOUNTS_DIR": str(tmp_path)}) == tmp_path
    assert reg.store_dir({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / "ndf" / "accounts"
    shared = {"NDF_SHARED_CONFIG_DIR": str(tmp_path / "s"), "CLAUDE_CONFIG_DIR": str(tmp_path / "acc")}
    assert reg.store_dir(shared) == tmp_path / "s" / "ndf" / "accounts"
    assert reg.store_dir({"NDF_SHARED_CONFIG_DIR": "", "CLAUDE_CONFIG_DIR": "x"}) == Path.home() / ".claude" / "ndf" / "accounts"


def test_register_new_then_relogin_keeps_other_items(tmp_path):
    store = tmp_path / "store"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / reg.CRED_FILE).write_text("{}")
    reg.register(store, "work1", staging, reg.Identity("a@example.com", "o1", "Org"), 0.0)
    final = store / "work1"
    assert json.loads((final / reg.ACCOUNT_FILE).read_text())["org_id"] == "o1"
    assert stat.S_IMODE(final.stat().st_mode) == 0o700 and stat.S_IMODE((final / reg.CRED_FILE).stat().st_mode) == 0o600
    (final / "backups").mkdir()
    (final / reg.USAGE_FILE).write_text("{}")
    again = tmp_path / "again"
    again.mkdir()
    (again / reg.CRED_FILE).write_text('{"new": 1}')
    reg.register(store, "work1", again, reg.Identity("a@example.com", "", ""), 1.0)
    assert (final / "backups").is_dir() and not (final / reg.USAGE_FILE).exists() and not again.exists()
    assert json.loads((final / reg.CRED_FILE).read_text()) == {"new": 1}


def test_set_capacity_declares_and_clears(tmp_path):
    _account(tmp_path, "work1")
    assert reg.set_capacity(tmp_path, "work1", None, 900)["capacity_declared"] == {"five_hour": None, "seven_day": 900.0}
    reg.set_capacity(tmp_path, "work1", None, None)
    assert json.loads((tmp_path / "work1" / reg.ACCOUNT_FILE).read_text())["capacity"] is None
    with pytest.raises(reg.NotRegistered):
        reg.set_capacity(tmp_path, "nobody", 1, 1)


def test_locked_gives_up_when_another_writer_holds_it(tmp_path, hold_lock):
    fd = hold_lock(tmp_path / reg.LOCKS_DIR / "work1.lock")
    try:
        with pytest.raises(reg.AccountLockTimeout), reg.locked(tmp_path, "work1", wait=0.05):
            pass
    finally:
        os.close(fd)


def test_capacity_refuses_names_outside_the_store(tmp_path):
    outside = tmp_path / "outside"
    for name in (str(outside), "../outside", "metered"):
        with pytest.raises(reg.AccountError):
            reg.set_capacity(tmp_path / "store", name, 1, 1)
    assert not outside.exists() and not (tmp_path / "store" / reg.LOCKS_DIR).exists()


def test_cli_lock_timeout_stops_for_a_human_without_retry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path))
    _account(tmp_path, "work1")

    def busy(*_args):
        raise reg.AccountLockTimeout("排他を取れない")

    monkeypatch.setattr(reg, "set_capacity", busy)
    assert cli.main(["account", "capacity", "work1", "1", "1"]) == causes.exit_code("store_in_use")
    got = _last(capsys)
    assert got["cause"]["kind"] == "store_in_use" and causes.policy_of("store_in_use") is None


def test_list_rows_hide_secrets_and_mark_broken(tmp_path):
    _account(tmp_path, "work1", capacity={"seven_day": 900})
    (tmp_path / "work2").mkdir()
    (tmp_path / "work2" / reg.ACCOUNT_FILE).write_text("{broken")
    book = {
        "version": 1,
        "use": "off",
        "keys": {"key1": {"key": "sk-secret", "account": "work1", "added_at": "x", "generation": "g", "monthly_usd": 100}},
    }
    reg.write(reg.book_path(tmp_path), book)
    usage = [account_decide.Usage(name="work1", percent=40.0, limited=False)]
    rows, warnings = reg.list_rows(tmp_path, {"NDF_SUPERVISE_CLAUDE_FALLBACK": "AWS_REGION=us-east-1 AWS_PROFILE=p"}, "work1", usage)
    by = {r["name"]: r for r in rows}
    assert by["work1"]["pinned"] and by["work1"]["percent"] == 40.0 and by["work1"]["state"] == "使える"
    assert by["work2"]["state"] == "broken" and len(warnings) == 1
    assert by["metered"]["keys"] == ["AWS_PROFILE", "AWS_REGION"] and "us-east-1" not in json.dumps(rows)
    assert by["key1"]["monthly_usd"] == 100.0 and "sk-secret" not in json.dumps(rows)


def test_cli_capacity_and_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))  # claude が無い: logout は通らないが登録は外す
    d = _account(tmp_path, "work1")
    shared = tmp_path / "shared.json"
    shared.write_text("{}")
    (d / "settings.json").symlink_to(shared)
    assert cli.main(["account", "capacity", "work1", "x", "1"]) == 2
    assert cli.main(["account", "capacity", "nobody", "1", "1"]) == 1
    capsys.readouterr()
    assert cli.main(["account", "remove", "work1"]) == 0
    got = _last(capsys)
    assert got["warnings"] and not d.exists() and shared.exists()
    assert cli.main(["account", "remove", "metered"]) == 1


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return exe


def test_add_in_two_steps_registers_the_account(tmp_path, fake_claude):
    store = tmp_path / "store"
    env = dict(os.environ)
    first = account_login.add(store, "work1", None, env)
    assert first.needs_code and first.url.startswith("https://claude.ai/oauth")
    assert (store / ".pending-work1" / account_login.PENDING_FIFO).exists()
    done = account_login.add(store, "work1", "good\n", env)
    assert not done.needs_code and (done.email, done.org_name) == ("a@example.com", "Org")
    assert reg.names(store) == ["work1"] and not (store / ".pending-work1").exists()
    with pytest.raises(account_login.LoginError):
        account_login.add(store, "work1", None, env)  # 登録済み
    account_login.add(store, "work2", None, env)
    with pytest.raises(account_login.LoginError, match="同じメールアドレス"):
        account_login.add(store, "work2", "good", env)


def test_add_with_a_wrong_code_does_not_register(tmp_path, fake_claude):
    store = tmp_path / "store"
    account_login.add(store, "work1", None, dict(os.environ))
    with pytest.raises(account_login.LoginError):
        account_login.add(store, "work1", "bad", dict(os.environ))
    assert reg.names(store) == [] and not (store / ".pending-work1").exists()


def test_cli_add_asks_for_the_code_and_refuses_bad_names(tmp_path, fake_claude, monkeypatch, capsys):
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path / "store"))
    assert cli.main(["account", "add", "BAD"]) == 2
    assert cli.main(["account", "add", "metered"]) == 2
    capsys.readouterr()
    assert cli.main(["account", "add", "work1"]) == 3
    got = _last(capsys)
    assert got["cause"]["kind"] == "input_missing" and got["items"][0]["url"].startswith("https://")
    account_login.discard_pending(tmp_path / "store", "work1")


def test_list_rows_states_saved_metered_and_key_rows(tmp_path):
    """現状固定（I-004）: アカウントの状態・保存した従量の設定・API キーの行の形と並び。"""
    _account(tmp_path, "a-relogin", needs_relogin=True, org_name="Org", capacity={"five_hour": 10, "seven_day": 0})
    _account(tmp_path, "b-unknown")
    _account(tmp_path, "c-limited")
    (tmp_path / "d-noaccount").mkdir()  # account.json の無い保存先は並べない
    reg.save_metered(tmp_path, "bedrock", {"AWS_REGION": "us-east-1"}, {"region": "us-east-1"}, 0.0)
    book = {
        "version": 1,
        "use": "off",
        "keys": {"k2": {"key": "sk-2", "account": "x"}, "k1": {"key": "sk-1", "monthly_usd": 0}, "k3": "not a dict"},
    }
    reg.write(reg.book_path(tmp_path), book)
    usage = [
        account_decide.Usage(name="c-limited", percent=100.0, limited=True),
        account_decide.Usage(name="a-relogin", percent=5.0, limited=False),
    ]
    rows, warnings = reg.list_rows(tmp_path, {}, None, usage)
    assert warnings == []
    assert rows == [
        {
            "name": "a-relogin",
            "kind": "oauth",
            "email": "a-relogin@example.com",
            "org_name": "Org",
            "pinned": False,
            "percent": 5.0,
            "capacity_declared": {"five_hour": 10.0, "seven_day": None},
            "state": "再登録が要る",
        },
        {
            "name": "b-unknown",
            "kind": "oauth",
            "email": "b-unknown@example.com",
            "org_name": None,
            "pinned": False,
            "percent": None,
            "capacity_declared": {"five_hour": None, "seven_day": None},
            "state": "残量不明",
        },
        {
            "name": "c-limited",
            "kind": "oauth",
            "email": "c-limited@example.com",
            "org_name": None,
            "pinned": False,
            "percent": 100.0,
            "capacity_declared": {"five_hour": None, "seven_day": None},
            "state": "上限",
        },
        {
            "name": "metered",
            "kind": "metered",
            "source": "saved",
            "provider": "bedrock",
            "details": {"region": "us-east-1"},
            "state": "保存した設定",
        },
        {"name": "k1", "kind": "api_key", "account": None, "monthly_usd": None, "state": "月の額が未設定"},
        {"name": "k2", "kind": "api_key", "account": "x", "monthly_usd": None, "state": "月の額が未設定"},
    ]


def test_list_rows_broken_metered_broken_book_and_empty_env(tmp_path):
    """現状固定（I-004）: 壊れた保存の設定・壊れた登録簿・空の環境変数の設定。"""
    reg.make_store(tmp_path)
    reg.metered_path(tmp_path).write_text("[]")
    reg.book_path(tmp_path).write_text('{"keys": []}')
    rows, warnings = reg.list_rows(tmp_path, {}, None, [])
    assert rows == [{"name": "metered", "kind": "metered", "source": "saved", "state": "壊れている（設定なしとして扱う）"}]
    assert len(warnings) == 1 and warnings[0].endswith("（API キーを使わずに動く）")
    rows, _ = reg.list_rows(tmp_path, {"NDF_SUPERVISE_CLAUDE_FALLBACK": ""}, None, [])
    assert rows == []
    assert reg.list_rows(tmp_path / "none", {}, "x", []) == ([], [])


STATUS_CLAUDE = """#!/bin/sh
# claude auth の代わり。status は資格情報があれば環境変数 FAKE_STATUS をそのまま出す
if [ "$2" = "login" ]; then
  echo "Open https://claude.ai/oauth/authorize?x=1"
  read code
  echo '{"claudeAiOauth": {"accessToken": "t"}}' > "$CLAUDE_CONFIG_DIR/.credentials.json"
  exit 0
fi
if [ "$2" = "status" ]; then
  [ -f "$CLAUDE_CONFIG_DIR/.credentials.json" ] && printf '%s' "$FAKE_STATUS" || echo '{"loggedIn": false}'
  exit 0
fi
exit 1
"""


@pytest.fixture
def status_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "sbin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(STATUS_CLAUDE)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return exe


def _add_with_status(store: Path, name: str, status: str):
    env = dict(os.environ) | {"FAKE_STATUS": status}
    account_login.add(store, name, None, env)
    return account_login.add(store, name, "code", env)


@pytest.mark.parametrize(
    "status",
    ["not json", "[1]", '{"loggedIn": false, "email": "a@example.com"}', '{"loggedIn": true, "email": 1}', '{"loggedIn": true}'],
)
def test_add_refuses_when_status_has_no_identity(tmp_path, status_claude, status):
    """現状固定（I-025）: `claude auth status` から本人を読めなければ登録しない。"""
    store = tmp_path / "store"
    with pytest.raises(account_login.LoginError, match="ログインが通らなかった") as exc:
        _add_with_status(store, "work1", status)
    assert exc.value.kind == "refused"
    assert reg.names(store) == [] and not (store / ".pending-work1").exists()


def test_add_reads_email_and_org_from_status(tmp_path, status_claude):
    """現状固定（I-025）: 組織の ID・名前が文字列でなければ空として扱い、組織の分からない登録は同じ組織とみなす。"""
    store = tmp_path / "store"
    no_org = _add_with_status(store, "work1", '{"loggedIn": true, "email": "b@example.com", "orgId": 5, "orgName": null}')
    assert no_org == account_login.Added("work1", needs_code=False, email="b@example.com", org_name=None)
    row = json.loads((store / "work1" / reg.ACCOUNT_FILE).read_text())
    assert row["email"] == "b@example.com" and "org_id" not in row and "org_name" not in row
    with pytest.raises(account_login.LoginError, match="同じメールアドレスと組織で登録済み: work1"):
        _add_with_status(store, "work2", '{"loggedIn": true, "email": "B@example.com", "orgId": "o2", "orgName": "Org2"}')
    with_org = _add_with_status(store, "work3", '{"loggedIn": true, "email": "c@example.com", "orgId": "o2", "orgName": "Org2"}')
    assert (with_org.email, with_org.org_name) == ("c@example.com", "Org2")
    row = json.loads((store / "work3" / reg.ACCOUNT_FILE).read_text())
    assert (row["org_id"], row["org_name"]) == ("o2", "Org2")
    other_org = _add_with_status(store, "work4", '{"loggedIn": true, "email": "c@example.com", "orgId": "o3", "orgName": ""}')
    assert (other_org.email, other_org.org_name) == ("c@example.com", None)
    assert reg.names(store) == ["work1", "work3", "work4"]


def _pending(store: Path, name: str, row) -> Path:
    d = account_login.pending_dir(store, name)
    (d / account_login.PENDING_CONFIG).mkdir(parents=True)
    (d / account_login.PENDING_FILE).write_text(row if isinstance(row, str) else json.dumps(row))
    return d


def _session_proc(script: str):
    proc = subprocess.Popen(["sh", "-c", script], start_new_session=True)
    for _ in range(50):
        if account_login._start_time(proc.pid) is not None:
            break
        time.sleep(0.02)
    return proc


def test_discard_pending_removes_state_without_a_live_login(tmp_path):
    """現状固定（I-028）: 途中の状態が無い・読めない・プロセスが別物なら、止めずに保存先だけを消す。"""
    account_login.discard_pending(tmp_path, "none")
    broken = _pending(tmp_path, "broken", "{broken")
    account_login.discard_pending(tmp_path, "broken")
    assert not broken.exists()
    proc = _session_proc("sleep 30")
    try:
        stale = _pending(tmp_path, "stale", {"pid": proc.pid, "pid_start": 1.0})
        account_login.discard_pending(tmp_path, "stale")
        assert not stale.exists() and proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("script", ["sleep 30", "trap '' TERM; sleep 30"])
def test_discard_pending_stops_the_waiting_login_group(tmp_path, script):
    """現状固定（I-028）: 生きている待機中のログインはセッションのグループへ SIGTERM、効かなければ SIGKILL で止める。"""
    proc = _session_proc(script)
    try:
        d = _pending(tmp_path, "work1", {"pid": proc.pid, "pid_start": account_login._start_time(proc.pid)})
        account_login.discard_pending(tmp_path, "work1")
        assert not d.exists()
        assert proc.wait(timeout=5) in (-signal.SIGTERM, -signal.SIGKILL)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
