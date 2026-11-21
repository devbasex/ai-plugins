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
from ndf.contracts import causes, limits
from ndf.domains.relay import accounts, cli, cli_account, hub, markers, pending, registry, switch_decide, wrapper


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
        "limit_pick": {"kind": "switch", "account": "main", "reason": "上限"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    assert cli.main(["decide"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["kind"] == "account"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"unknown": 1}'))
    assert cli.main(["decide"]) != 0


def test_spin_end_exits_with_the_child_exit_code(capsys):
    # 決定 13: 再起動ループの検出で子が終わった後に止まったら、子の終了コードで終わる
    assert cli._ended(wrapper.Ended("spin", f"子が終了コード 3 で終わった。{switch_decide.SPIN_REASON}", code=3)) == 3
    assert switch_decide.SPIN_REASON in capsys.readouterr().err


@pytest.mark.parametrize(("raw", "seconds"), [(None, 120.0), ("", 120.0), ("30", 30.0), ("0", 0.0)])
def test_wrapper_settings_read_the_spin_seconds_from_the_env(tmp_path, monkeypatch, raw, seconds):
    # 決定 13: ラッパーの設定は NDF_RELAY_SPIN で秒を替え、0 で検出を切る（無いか空なら上限の設定の値）
    monkeypatch.chdir(tmp_path)
    if raw is None:
        monkeypatch.delenv("NDF_RELAY_SPIN", raising=False)
    else:
        monkeypatch.setenv("NDF_RELAY_SPIN", raw)
    monkeypatch.setenv("NDF_RELAY_MAX_STARTS", "abc")  # 読まないので誤りにならない
    assert cli.wrapper_settings("w1", []).spin == switch_decide.Spin(3, seconds)


@pytest.mark.parametrize("raw", ["abc", "-1"])
def test_wrapper_settings_refuse_a_broken_spin_env(tmp_path, monkeypatch, raw):
    # 決定 13: NDF_RELAY_SPIN が数でない・負なら、ラッパーを作る前に EnvError（main が invalid の 1 行にする）
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NDF_RELAY_SPIN", raw)
    with pytest.raises(limits.EnvError, match="NDF_RELAY_SPIN"):
        cli.wrapper_settings("w1", [])


def test_spin_end_after_a_signal_exits_with_128_plus_the_signal(tmp_path, capsys):
    # 決定 13: シグナル（SIGTERM）で終わった子が検出に当たったら、CLI は 128 + シグナルの番号で終わる
    from relay_children import FakeChild, run_wrapper

    children = [FakeChild([], 1), FakeChild([], 1), FakeChild([], None, signal=15)]
    ended, spawn, _ = run_wrapper(tmp_path, children, spin=switch_decide.Spin(3, 120.0))
    assert (ended.cause, ended.code) == ("spin", 143) and len(spawn.calls) == 3
    assert cli._ended(ended) == 143


def test_mark_sets_the_relay_mark(capsys):
    assert cli.main(["mark", "s-9"]) == 0
    assert markers.take(identity.load().state_dir, "s-9") is True
    assert cli.main(["mark", "../bad"]) != 0


def test_ls_with_nothing_registered(capsys, last_result):
    assert cli.main(["ls"]) == 0
    assert last_result(capsys.readouterr().out)["items"] == []


def test_ls_of_an_older_registry_entry_has_no_starts(capsys, last_result):
    # 決定 13: hub ls の項目は starts を持たない。前の版の登録簿に starts が残っていても、結果の形の誤りにしない
    path = hub.hub_dir(identity.load().state_dir) / registry.REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"cwd": None, "args": [], "role": None, "plan": None, "version": "1", "starts": {"date": "2026-11-21", "count": 3}}
    path.write_text(json.dumps({"version": 1, "entries": {"t1": entry}}), encoding="utf-8")
    assert cli.main(["ls"]) == 0
    rows = last_result(capsys.readouterr().out)["items"]
    assert [r["name"] for r in rows] == ["t1"] and "starts" not in rows[0]


def test_hub_context_has_no_start_limit():
    # 決定 13: hub_context は NDF_RELAY_MAX_STARTS を読まず、設定されていても誤りにしない
    ctx = cli.hub_context({"NDF_RELAY_MAX_STARTS": "abc"})
    assert not hasattr(ctx, "max_starts")


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


def _root_with_manifest(tmp_path, monkeypatch, text: str):
    """`plugin.json` を `text` で置いた根を自分の根（`identity.ROOT`）にする。本物の `identity.load` がそこを読む。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(identity, "ROOT", tmp_path)


@pytest.mark.parametrize(("version", "expected"), [('"1.2.3"', "1.2.3"), ("7", "7"), ("1.5", "1.5"), ("null", "None")])
def test_hub_and_wrapper_read_the_plugin_version(tmp_path, monkeypatch, version, expected):
    """現状固定（I-001）: ハブとラッパーの設定は plugin.json の version を文字列にして持つ。"""
    _root_with_manifest(tmp_path, monkeypatch, f'{{"name": "ndf", "version": {version}}}')
    assert cli.hub_context({}).version == expected
    assert cli.wrapper_settings("w1", []).version == expected
    assert cli.plugin_version(tmp_path) == expected


@pytest.mark.parametrize(("text", "error"), [('{"name": "ndf"}', KeyError), ("{broken", json.JSONDecodeError), ('["ndf"]', TypeError)])
def test_plugin_version_raises_when_the_manifest_lacks_a_version(tmp_path, monkeypatch, text, error):
    """現状固定（I-001）: version が無い・壊れた・オブジェクトでない plugin.json では例外をそのまま上げる。"""
    _root_with_manifest(tmp_path, monkeypatch, text)
    with pytest.raises(error):
        cli.plugin_version(tmp_path)
    if error is KeyError:
        with pytest.raises(KeyError):
            cli.hub_context({})
        with pytest.raises(KeyError):
            cli.wrapper_settings("w1", [])


def test_plugin_version_raises_when_the_manifest_is_missing(tmp_path):
    """現状固定（I-001）: plugin.json が無ければ OSError を上げる。"""
    with pytest.raises(FileNotFoundError):
        cli.plugin_version(tmp_path)


@pytest.mark.parametrize(
    ("argv", "rest", "own"),
    [
        (["run", "--dangerously-skip-permissions"], ["--dangerously-skip-permissions"], {}),
        (["run", "--resume", "abc", "--name", "x"], ["--resume", "abc", "--name", "x"], {"name": None}),
        (["run", "--name", "w1", "--prompt", "hi", "--model", "opus"], ["--model", "opus"], {"name": "w1", "prompt": "hi"}),
        (["run", "--name=w1", "-c"], ["-c"], {"name": "w1"}),
        (["run", "--", "--name", "x"], ["--name", "x"], {"name": None}),
        (["run", "foo", "--x"], ["foo", "--x"], {}),
        (["run"], [], {}),
        (["spawn", "--cwd", "/w", "--name", "l1", "--", "--resume", "s"], ["--resume", "s"], {"cwd": "/w", "name": "l1"}),
        (["spawn", "--role", "worker", "--verbose"], ["--verbose"], {"role": "worker"}),
    ],
)
def test_run_and_spawn_pass_claude_options_through(argv, rest, own):
    # alias の `claude --dangerously-skip-permissions` がラッパーを通っても止まらない。
    # ndf のオプションは先頭にあるものだけを読み、残りはすべて claude へ渡す
    args = cli._parse(argv)
    assert args.rest == rest
    assert {key: getattr(args, key) for key in own} == own


def test_run_says_nothing_when_the_child_simply_ends_and_one_line_when_it_stops(capsys):
    # 人が端末で打った claude の終わりに結果 JSON を出さない。止まったときだけ理由を標準エラーへ 1 行出す
    from ndf.contracts import causes
    from ndf.domains.relay import cli as relay_cli, wrapper

    assert relay_cli._ended(wrapper.Ended("", "子が次のセグメントのブロックを書かずに終わった")) == 0
    out, err = capsys.readouterr()
    assert (out, err) == ("", "")
    assert relay_cli._ended(wrapper.Ended("refused", "t1 のラッパーは既に動いている")) == causes.exit_code("refused")
    out, err = capsys.readouterr()
    assert out == "" and err == "ndf-relay: t1 のラッパーは既に動いている\n"


# --- 作業ディレクトリが消えたシェルで ndf relay run を打ったとき（#3842） ---


def test_cwd_reads_the_work_dir_as_path_cwd_while_it_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._cwd({}) == (tmp_path.resolve(), None)


def _run_in_a_vanished_dir(tmp_path, monkeypatch, capsys, recreate=False, proc_readable=True, pwd=None):
    """作業ディレクトリへ入ってから消し、偽の起動と偽の更新で `ndf relay run` を打つ。返すのは終了コード・標準エラー・呼ばれた手・ラッパーの保存先。"""
    from ndf.domains.relay import claude_args, plugin_update, wrapper

    gone = (tmp_path / "gone").resolve()
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    if recreate:
        gone.mkdir()
    if not proc_readable:
        monkeypatch.setattr(cli, "PROC_CWD", str(tmp_path / "no-proc"))
    if pwd is None:
        monkeypatch.delenv("PWD", raising=False)
    else:
        monkeypatch.setenv("PWD", pwd)
    called: list[str] = []

    class FakeUpdater:
        def __init__(self, **kw):
            pass

        def __call__(self):
            called.append("update")

        def confirm(self):
            called.append("confirm")

    real_wrapper, real_parse = wrapper.Wrapper, claude_args.parse
    monkeypatch.setattr(plugin_update, "Updater", FakeUpdater)
    monkeypatch.setattr(claude_args, "parse", lambda args, environ, tty: real_parse(args, environ, True))
    monkeypatch.setattr(wrapper, "Wrapper", lambda s, **kw: real_wrapper(s, spawn=lambda *a: called.append("spawn"), **kw))
    capsys.readouterr()
    code = cli.main(["run", "--name", "t1"])
    return code, capsys.readouterr().err, called, registry.wrapper_dir(identity.load().state_dir, "t1"), gone


def _assert_launch_refused(code, err, called, d, label):
    from ndf.domains.relay import wrapper

    reason = wrapper.LAUNCH_FAILED.format(why=wrapper.NO_CWD.format(cwd=label))
    assert code == causes.exit_code("precondition") == 3
    assert err == f"ndf-relay: {reason}\n"  # トレースバックを出さず、1 行だけ
    assert called == []
    last = json.loads((d / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert (last["kind"], last["cause"], last["reason"]) == ("end", "precondition", reason)
    state = registry.read_state(d)
    assert (state["state"], state["reason"]) == ("stopped", reason)


def test_run_in_a_vanished_work_dir_stops_with_one_line_and_exit_code_3(tmp_path, monkeypatch, capsys):
    # 受け入れ条件 1・2・3: 設定の生成から通しても、更新も spawn も呼ばずに 1 行と 3 で終わる
    code, err, called, d, gone = _run_in_a_vanished_dir(tmp_path, monkeypatch, capsys)
    _assert_launch_refused(code, err, called, d, str(gone))


def test_run_in_a_vanished_work_dir_says_unknown_when_the_path_cannot_be_read(tmp_path, monkeypatch, capsys):
    code, err, called, d, _ = _run_in_a_vanished_dir(tmp_path, monkeypatch, capsys, proc_readable=False)
    _assert_launch_refused(code, err, called, d, "（不明）")


def test_run_in_a_vanished_work_dir_does_not_launch_in_a_dir_recreated_with_the_same_name(tmp_path, monkeypatch, capsys):
    # 同じ名前で作り直したディレクトリでも、Path.cwd() は失敗したままなので起動しない
    code, err, called, d, gone = _run_in_a_vanished_dir(tmp_path, monkeypatch, capsys, recreate=True)
    _assert_launch_refused(code, err, called, d, str(gone))


def test_run_in_a_vanished_work_dir_does_not_launch_in_the_dir_pwd_names(tmp_path, monkeypatch, capsys):
    # /proc を読めず PWD が別の実在するディレクトリを指しても、そこで起動しない（停止理由には PWD の値が出る）
    other = tmp_path / "other"
    other.mkdir()
    code, err, called, d, _ = _run_in_a_vanished_dir(tmp_path, monkeypatch, capsys, proc_readable=False, pwd=str(other))
    _assert_launch_refused(code, err, called, d, str(other))
