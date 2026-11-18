"""`ndf relay` のサブコマンドと、待ちを抱えた実行の一覧の読み取り（#2898・#2464・決定 86・決定 92）。"""

from __future__ import annotations

import fcntl
import io
import json
import os
import subprocess

import pytest

from ndf import cli as top
from ndf import identity
from ndf.contracts import causes
from ndf.domains.relay import accounts, cli, cli_account, markers, pending, registry


def test_relay_is_a_subcommand_of_ndf():
    assert top.COMMANDS["relay"] == cli.__name__


def test_help_lists_every_subcommand(capsys):
    # --help の一覧と実装が食い違わない（#2898）
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli.COMMANDS)
    assert set(cli.COMMANDS) == set(cli.HANDLERS)
    assert cli.main(["account", "--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli_account.ACCOUNT_COMMANDS)
    assert cli.main(["fly"]) != 0


def test_account_use_show_clear(capsys, last_result):
    assert cli.main(["account", "use", "work"]) == 0
    assert accounts.pinned(identity.load().state_dir) == "work"
    assert cli.main(["account", "show"]) == 0
    assert last_result(capsys.readouterr().out)["items"] == [{"account": "work"}]
    assert cli.main(["account", "use", "a b"]) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert cli.main(["account", "clear"]) == 0


def test_decide_reads_the_section_state(monkeypatch, capsys, last_result):
    data = {
        "next_block": "/goal x",
        "question": False,
        "child_alive": True,
        "child_exit": None,
        "starts_today": 0,
        "max_starts": 3,
        "limit_pick": {"kind": "switch", "account": "main", "reason": "上限"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    assert cli.main(["decide"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["kind"] == "account"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"unknown": 1}'))
    assert cli.main(["decide"]) != 0


def test_mark_sets_the_relay_mark(capsys):
    assert cli.main(["mark", "s-9"]) == 0
    assert markers.take(identity.load().state_dir, "s-9") is True
    assert cli.main(["mark", "../bad"]) != 0


def test_ls_with_nothing_registered(capsys, last_result):
    assert cli.main(["ls"]) == 0
    assert last_result(capsys.readouterr().out)["items"] == []


def test_env_override_must_be_a_positive_number(monkeypatch, capsys, last_result):
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "many")
    assert cli.main(["ls"]) != 0
    assert "NDF_RELAY_MAX_NAMED" in last_result(capsys.readouterr().out)["summary"]


class _Done:
    def __init__(self, stdout: str, code: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


def test_pending_reads_the_plan_command_result(tmp_path):
    ok = json.dumps({"tool": "plan", "status": "ok", "summary": "2 件", "items": [{"run": "a"}, {"run": "b"}]})
    got = pending.read(tmp_path, 5, runner=lambda *a, **k: _Done("log\n" + ok + "\n"))
    assert got == pending.Pending([{"run": "a"}, {"run": "b"}], "")


@pytest.mark.parametrize(
    "runner",
    [
        lambda *a, **k: _Done("", 2, "知らないサブコマンド: plan"),
        lambda *a, **k: _Done(json.dumps({"status": "stopped", "summary": "壊れた"})),
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("bin/ndf")),
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ndf", 5)),
    ],
)
def test_pending_reports_why_the_list_could_not_be_read(tmp_path, runner):
    got = pending.read(tmp_path, 5, runner=runner)
    assert got.runs is None and got.note


# 現状固定（I-018）: install・uninstall・status の結果 JSON（要約・項目・警告）と、--replace-old の断り
@pytest.fixture
def locations(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "old"))
    return tmp_path / "claude" / "ndf" / "relay.py", tmp_path / "bin" / "ndf-relay"


def _expect(copy, entry, states, warnings=None, wrote_version=False) -> dict:
    items = [{"path": str(copy), "state": states[0]}, {"path": str(entry), "state": states[1]}]
    if wrote_version:
        # 複製を置き換えたときは relay.version に自分のバージョンを書いた行が足される（#3341 の規則 P3）
        items.append({"path": str(copy.parent / "relay.version"), "version": identity.version_of(identity.load().root)})
    out = {
        "tool": "relay",
        "status": "ok",
        "summary": f"複製: {states[0]}（{copy}）・エントリポイント: {states[1]}（{entry}）",
        "items": items,
        "metrics": {},
    }
    if warnings:
        out["warnings"] = warnings
    return out


def test_install_status_uninstall_report_both_locations(locations, monkeypatch, capsys, last_result):
    copy, entry = locations
    base = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", base)
    assert cli.main(["status"]) == 0
    hint = f'{entry.parent} が PATH に無い。export PATH="{entry.parent}:$PATH" を足す'
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("absent", "absent"), [hint])
    monkeypatch.setenv("PATH", f"{entry.parent}{os.pathsep}{base}")
    assert cli.main(["install"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"), wrote_version=True)
    assert cli.main(["status"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"))
    assert cli.main(["uninstall"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("absent", "absent"))
    assert not copy.exists() and not entry.exists()


def test_install_keeps_an_old_copy_and_replace_old_refuses_while_an_old_wrapper_runs(locations, tmp_path, monkeypatch, capsys, last_result):
    copy, entry = locations
    monkeypatch.setenv("PATH", str(entry.parent))
    copy.parent.mkdir(parents=True)
    copy.write_text("old\n", encoding="utf-8")
    assert cli.main(["install"]) == 0
    warn = "旧い NDF の複製がある。旧いラッパーを止めてから ndf relay install --replace-old で置き換える"
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("foreign", "ours"), [warn])
    old = tmp_path / "old" / "relay" / "20261113T1"
    old.mkdir(parents=True)
    fd = os.open(old / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert cli.main(["install", "--replace-old"]) == causes.exit_code("precondition")
        why = "旧い NDF のラッパーが動いている: 20261113T1。止めてから置き換える"
        assert last_result(capsys.readouterr().out) == {
            "tool": "relay",
            "status": "stopped",
            "summary": why,
            "items": [],
            "metrics": {},
            "cause": {"kind": "precondition", "detail": why},
        }
        assert copy.read_text(encoding="utf-8") == "old\n"
        # --replace-old を付けなければ、旧いラッパーが動いていても断らずに旧い複製を残す
        assert cli.main(["install"]) == 0
        assert last_result(capsys.readouterr().out)["items"][0]["state"] == "foreign"
    finally:
        os.close(fd)
    assert cli.main(["install", "--replace-old"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"), wrote_version=True)


@pytest.mark.parametrize(
    ("value", "allow_zero", "expected"),
    [
        (None, False, None),
        ("5", False, 5.0),
        ("0.5", False, 0.5),
        (" 7 ", False, 7.0),
        ("0", True, None),
        ("0.0", True, None),
        ("3", True, 3.0),
    ],
)
def test_amount_reads_a_positive_number(value, allow_zero, expected):
    """現状固定（I-031）: 月の額は正の有限の数。`allow_zero` なら 0 は設定を外す None。"""
    from ndf.domains.relay import account_metered

    assert account_metered.amount(value, allow_zero=allow_zero) == expected


@pytest.mark.parametrize(
    ("value", "allow_zero"), [("0", False), ("-1", False), ("-1", True), ("abc", True), ("inf", False), ("nan", False), ("", False)]
)
def test_amount_refuses_without_echoing_the_value(value, allow_zero):
    """現状固定（I-031）: 読めない・正でない・無限の額は `invalid` で、入力値をメッセージへ出さない。"""
    from ndf.domains.relay import account_metered

    with pytest.raises(account_metered.MeteredError) as exc:
        account_metered.amount(value, allow_zero=allow_zero)
    assert exc.value.kind == "invalid" and str(exc.value) == "月の額は正の数（ドル）で渡す（入力値は表示しない）"


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("1", 1.0), ("100", 100.0), ("85.5", 85.5)])
def test_weekly_at_reads_a_percentage(value, expected):
    """現状固定（I-031）: 閾値は 1 以上 100 以下の百分率。"""
    from ndf.domains.relay import account_metered

    assert account_metered.weekly_at(value) == expected


@pytest.mark.parametrize("value", ["0", "0.99", "100.1", "x", "nan", ""])
def test_weekly_at_refuses_outside_the_range(value):
    """現状固定（I-031）: 範囲の外・読めない閾値は `invalid`。"""
    from ndf.domains.relay import account_metered

    with pytest.raises(account_metered.MeteredError) as exc:
        account_metered.weekly_at(value)
    assert exc.value.kind == "invalid" and str(exc.value) == "--weekly-at は 1 以上 100 以下の百分率"


def test_cli_api_key_monthly_sets_clears_and_refuses(tmp_path, monkeypatch, capsys):
    """現状固定（I-031）: `api-key-monthly` の額の読み方と終了コード。"""
    from ndf.domains.relay import account_registry

    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path))
    account_registry.make_store(tmp_path)
    account_registry.write(
        account_registry.book_path(tmp_path), {"version": 1, "use": "off", "keys": {"k1": {"key": "sk", "account": "a"}}}
    )

    def monthly() -> object:
        return account_registry.read_book(tmp_path)["keys"]["k1"].get("monthly_usd", "なし")

    assert cli.main(["account", "api-key-monthly", "k1", "12.5"]) == 0
    assert monthly() == 12.5
    for bad in ("abc", "-1", "inf"):
        assert cli.main(["account", "api-key-monthly", "k1", bad]) == 2
    assert monthly() == 12.5
    assert cli.main(["account", "api-key-monthly", "k1", "0"]) == 0
    assert monthly() == "なし"
    assert cli.main(["account", "api-key-monthly", "k2", "5"]) == 1
    out = capsys.readouterr().out
    assert "abc" not in out
