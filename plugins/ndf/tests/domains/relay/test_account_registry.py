"""アカウントの登録の保存先と OAuth の登録（`ndf relay account add`・`list`・`capacity`・`remove`。#3569）。"""

from __future__ import annotations

import json
import shutil
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import psutil
import pytest

from ndf.contracts import causes, facts
from ndf.domains.relay import account_decide, account_login, account_metered, cli, session_env
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
    # 旧い NDF の名前（ndf）の下を指し、今のプラグイン名（plugin.json の name）から求めない
    assert reg.store_dir({"NDF_ACCOUNTS_DIR": str(tmp_path)}) == tmp_path
    assert reg.store_dir({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / "ndf" / "accounts"
    shared = {"NDF_SHARED_CONFIG_DIR": str(tmp_path / "s"), "CLAUDE_CONFIG_DIR": str(tmp_path / "acc")}
    assert reg.store_dir(shared) == tmp_path / "s" / "ndf" / "accounts"
    assert reg.store_dir({"NDF_SHARED_CONFIG_DIR": "", "CLAUDE_CONFIG_DIR": "x"}) == Path.home() / ".claude" / "ndf" / "accounts"


# 認証の 13 変数（子の環境の表の account-drop。設計の「子の環境の変数」）と提供元の選択の 6 つ
AUTH_13 = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
}
PROVIDERS = {n for n in AUTH_13 if n.startswith("CLAUDE_CODE_USE_")}
BEDROCK = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p", "AWS_REGION": "us-east-1"}


def test_3604_2_i4_env_table_rows_for_account_children():
    """#3604 の 2・I4: 表の account-drop が認証の 13 変数、provider が提供元の選択の 6 つ、account-set が 5 変数。"""
    assert set(facts.ids("env", child="account-drop")) == AUTH_13
    assert set(reg.PROVIDER_ENV) == PROVIDERS and set(facts.ids("env", provider=True)) == PROVIDERS
    assert set(facts.ids("env", child="account-set")) == {
        "CLAUDE_CONFIG_DIR",
        "NDF_SHARED_CONFIG_DIR",
        "CLAUDE_CODE_PLUGIN_CACHE_DIR",
        "NDF_ACCOUNT_USAGE_FILE",
        "NDF_RELAY_PIN",
    }
    # 秘密の値の変数は表の secret の列が真
    assert facts.value("env", "CLAUDE_CODE_OAUTH_TOKEN", "secret") and facts.value("env", "ANTHROPIC_AUTH_TOKEN", "secret")


def test_shared_config_dir_prefers_the_kept_shared_dir(tmp_path):
    assert reg.shared_config_dir({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path
    # アカウントの子の中では元の設定ディレクトリを NDF_SHARED_CONFIG_DIR が持つ（空なら ~/.claude）
    assert reg.shared_config_dir({"NDF_SHARED_CONFIG_DIR": str(tmp_path / "s"), "CLAUDE_CONFIG_DIR": "acc"}) == tmp_path / "s"
    assert reg.shared_config_dir({"NDF_SHARED_CONFIG_DIR": "", "CLAUDE_CONFIG_DIR": "acc"}) == Path.home() / ".claude"
    assert reg.shared_config_dir({}) == Path.home() / ".claude"


def test_generation_reads_the_registered_generation(tmp_path):
    _account(tmp_path, "work1", generation="g1")
    _account(tmp_path, "work2")
    assert reg.generation(tmp_path, "work1") == "g1"
    assert reg.generation(tmp_path, "none") == ""
    # 世代を持たない旧い登録は、登録簿ごとに違う世代を持つ（同じ状態の保存先を共有しても記録が重ならない）
    legacy = reg.generation(tmp_path, "work2")
    assert legacy == reg.legacy_generation(tmp_path, "work2") and reg.GEN_RE.fullmatch(legacy)
    assert reg.load(tmp_path, "work2").generation == legacy
    _account(tmp_path / "other", "work2")
    assert reg.generation(tmp_path / "other", "work2") not in ("", "_", legacy)
    (tmp_path / "work2" / reg.ACCOUNT_FILE).write_text("{broken")
    with pytest.raises(reg.AccountError):
        reg.generation(tmp_path, "work2")
    # 世代はパスの要素になるので、1 つの安全な要素の形でなければ壊れた登録として扱う
    for bad in ("../x", "/abs", "..", "a/b", 7):
        _account(tmp_path, "work3", generation=bad)
        with pytest.raises(reg.AccountError):
            reg.generation(tmp_path, "work3")
        with pytest.raises(reg.AccountError):
            reg.load(tmp_path, "work3")
        shutil.rmtree(tmp_path / "work3")


def test_register_issues_a_new_generation_and_settles_inside_the_lock(tmp_path):
    store = tmp_path / "store"
    seen = []

    def settle(old, new):
        # 登録の排他の中で呼ばれる（ほかの書き手は同じ名前の排他を取れない）
        with pytest.raises(reg.AccountLockTimeout):
            with reg.locked(store, "work1", wait=0):
                pass
        seen.append((old, new, reg.generation(store, "work1")))

    gens = []
    for i in range(2):
        staging = tmp_path / f"staging{i}"
        staging.mkdir()
        (staging / reg.CRED_FILE).write_text("{}")
        gens.append(reg.register(store, "work1", staging, reg.Identity("a@example.com", "", ""), float(i), settle))
    assert gens[0] != gens[1] and all(reg.GEN_RE.fullmatch(g) for g in gens)
    assert seen == [(None, gens[0], gens[0]), (gens[0], gens[1], gens[1])]
    assert reg.load(store, "work1").generation == gens[1]
    gone = []
    reg.unregister(store, "work1", gone.append)
    assert gone == [gens[1]] and reg.names(store) == []


def test_unregister_keeps_the_generation_of_a_partial_removal_and_retries(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _account(store, "work1", generation="g1")
    real = reg.shutil.rmtree

    def partial(path, ignore_errors=False):  # account.json だけを消して、ほかの項目で失敗する（権限・使用中）
        (Path(path) / reg.ACCOUNT_FILE).unlink(missing_ok=True)

    monkeypatch.setattr(reg.shutil, "rmtree", partial)
    gone = []
    with pytest.raises(OSError, match="打ち直す"):
        reg.unregister(store, "work1", gone.append)
    assert gone == ["g1"] and reg.names(store) == [] and reg.removing(store, "work1")
    monkeypatch.setattr(reg.shutil, "rmtree", real)
    reg.unregister(store, "work1", gone.append)  # 同じ削除の打ち直しで、残った世代の後始末と削除をやり直す
    assert gone == ["g1", "g1"] and not reg.removing(store, "work1") and not (store / reg.REMOVING_DIR).exists()


def test_remove_account_rerun_clears_leftovers_first_and_keeps_a_new_registration(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _account(store, "work1", generation="g1")
    real = reg.shutil.rmtree

    def partial(path, ignore_errors=False):  # account.json だけを消して、ほかの項目で失敗する
        (Path(path) / reg.ACCOUNT_FILE).unlink(missing_ok=True)

    monkeypatch.setattr(reg.shutil, "rmtree", partial)
    with pytest.raises(OSError):
        reg.unregister(store, "work1")
    monkeypatch.setattr(reg.shutil, "rmtree", real)
    _account(store, "work1", generation="g2")  # 打ち直しの前に、別の端末が同じ名前で登録し直した
    released, gone = [], []
    got = reg.remove_account(store, "work1", lambda: released.append(1) or True, gone.append)
    # 案内どおりの打ち直しは残りだけを消し、今の登録をログアウトも削除もしない
    assert got == reg.Removal(leftovers=True, kept=True) and released == [] and gone == ["g1"] and not reg.removing(store, "work1")
    assert reg.names(store) == ["work1"] and reg.generation(store, "work1") == "g2"
    got = reg.remove_account(store, "work1", lambda: released.append(1) or True, gone.append)  # 残りが無くなった後の削除で今の登録を外す
    assert got == reg.Removal(leftovers=False, kept=False, logged_out=True) and released == [1] and gone == ["g1", "g2"]
    assert reg.names(store) == []
    with pytest.raises(reg.NotRegistered):
        reg.remove_account(store, "work1", lambda: True)


def test_remove_account_rerun_keeps_a_new_registration_when_leftovers_vanish_while_waiting(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _account(store, "work1", generation="g1")
    real = reg.shutil.rmtree

    def partial(path, ignore_errors=False):
        (Path(path) / reg.ACCOUNT_FILE).unlink(missing_ok=True)

    monkeypatch.setattr(reg.shutil, "rmtree", partial)
    with pytest.raises(OSError):
        reg.unregister(store, "work1")
    monkeypatch.setattr(reg.shutil, "rmtree", real)
    _account(store, "work1", generation="g2")
    real_locked = reg.locked

    def other_terminal_first(s, n, **kw):  # 排他を待つ間に、別の端末の打ち直しが残りを片付けた
        reg._finish_removal(s, n, None)
        return real_locked(s, n, **kw)

    monkeypatch.setattr(reg, "locked", other_terminal_first)
    released = []
    got = reg.remove_account(store, "work1", lambda: released.append(1) or True)
    assert got == reg.Removal(leftovers=True, kept=True) and released == []
    assert reg.names(store) == ["work1"] and reg.generation(store, "work1") == "g2"


def test_remove_account_logs_out_and_unregisters_inside_the_register_lock(tmp_path):
    store = tmp_path / "store"
    _account(store, "work1", generation="g1")
    seen = []

    def release():
        # ログアウトの間も登録の排他を持つ（別の端末の登録し直しは、登録を外し終えるまで待つ）
        with pytest.raises(reg.AccountLockTimeout):
            with reg.locked(store, "work1", wait=0):
                pass
        seen.append(reg.generation(store, "work1"))
        return False

    gone = []
    got = reg.remove_account(store, "work1", release, gone.append)
    assert got == reg.Removal(leftovers=False, kept=False, logged_out=False) and seen == ["g1"] and gone == ["g1"]
    assert reg.names(store) == []


def test_r1_3_remove_account_refuses_a_directory_that_links_to_another_account(tmp_path):
    """アカウントのディレクトリが別のアカウントへの symlink なら、ログアウトもリンクの解除もせず登録も残す。"""
    store = tmp_path / "store"
    _account(store, "work1", generation="g1")
    (store / "work2").symlink_to(store / "work1")
    called = []
    with pytest.raises(reg.AccountError):
        reg.remove_account(store, "work2", lambda: called.append(1) or True)
    assert called == [] and (store / "work2").is_symlink() and (store / "work1" / reg.ACCOUNT_FILE).is_file()


def test_unregister_without_a_registration_does_not_settle(tmp_path):
    gone = []
    reg.unregister(tmp_path / "store", "work1", gone.append)
    assert gone == []


def test_3604_9_mark_relogin_sets_the_flag_and_keeps_other_items(tmp_path):
    _account(tmp_path, "work1", generation="g1", org_id="o1", capacity={"seven_day": 900})
    before = json.loads((tmp_path / "work1" / reg.ACCOUNT_FILE).read_text())
    with reg.locked(tmp_path, "work1"):
        reg.mark_relogin(tmp_path, "work1")
    after = json.loads((tmp_path / "work1" / reg.ACCOUNT_FILE).read_text())
    assert after == {**before, "needs_relogin": True}
    assert stat.S_IMODE((tmp_path / "work1" / reg.ACCOUNT_FILE).stat().st_mode) == 0o600
    rows, _ = reg.list_rows(tmp_path, {}, None, lambda _: [])
    assert {r["name"]: r for r in rows}["work1"]["state"] == "再登録が要る"


def test_mark_relogin_refuses_a_broken_or_missing_registration(tmp_path):
    _account(tmp_path, "work1")
    (tmp_path / "work1" / reg.ACCOUNT_FILE).write_text("{broken")
    with pytest.raises(reg.AccountError):
        reg.mark_relogin(tmp_path, "work1")
    with pytest.raises(reg.AccountError):
        reg.mark_relogin(tmp_path, "none")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On", " on "])
def test_provider_on_is_true_for_the_truthy_values(value):
    assert reg.provider_on(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "False", "no", "off", "OFF"])
def test_provider_on_is_false_for_the_falsy_values(value):
    assert reg.provider_on(value) is False


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_provider_on_is_none_for_an_unreadable_value(value):
    assert reg.provider_on(value) is None


def test_metered_check_reads_the_provider_like_claude_code():
    """接続確認の提供元の判定が provider_on と同じ（false を有効と読まない）。"""
    base = {"AWS_PROFILE": "p", "AWS_REGION": "us-east-1", "ANTHROPIC_MODEL": "m"}
    assert account_metered.from_env({**base, "CLAUDE_CODE_USE_BEDROCK": "false"}) is None
    assert account_metered.from_env({**base, "CLAUDE_CODE_USE_BEDROCK": "maybe"}) is None
    assert account_metered.from_env({**base, "CLAUDE_CODE_USE_BEDROCK": "TRUE"}) == account_metered.Target("p", "us-east-1", "m")


def test_secret_and_checked_env_cover_the_provider_secrets():
    secret = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "ANTHROPIC_AWS_API_KEY",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
        "ANTHROPIC_CUSTOM_HEADERS",
    }
    assert set(reg.SECRET_ENV) == secret
    checked = set(reg.METERED_CHECKED)
    assert len(checked) == len(reg.METERED_CHECKED)
    assert PROVIDERS | secret | {"AWS_PROFILE", "ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL", "AWS_REGION"} <= checked


def test_check_decl_accepts_one_provider():
    reg.check_decl("bedrock", BEDROCK, {"region": "us-east-1"})
    reg.check_decl("bedrock", {**BEDROCK, "CLAUDE_CODE_USE_VERTEX": "0"}, {"region": "us-east-1"})


@pytest.mark.parametrize(
    "env",
    [
        {**BEDROCK, "AWS_BEARER_TOKEN_BEDROCK": "x"},
        {**BEDROCK, "ANTHROPIC_FOUNDRY_API_KEY": "x"},
        {**BEDROCK, "ANTHROPIC_CUSTOM_HEADERS": "x"},
        {**BEDROCK, "CLAUDE_CONFIG_DIR": "/tmp/x"},
        {**BEDROCK, "NDF_CLAUDE_ACCOUNT": "work1"},
        {**BEDROCK, "NDF_ANYTHING": "1"},
        {"AWS_REGION": "us-east-1"},
        {"CLAUDE_CODE_USE_BEDROCK": "0", "AWS_REGION": "us-east-1"},
        {**BEDROCK, "CLAUDE_CODE_USE_VERTEX": "1"},
        {**BEDROCK, "CLAUDE_CODE_USE_VERTEX": "maybe"},
    ],
)
def test_check_decl_refuses_secrets_wrapper_variables_and_unclear_providers(env):
    with pytest.raises(ValueError) as exc:
        reg.check_decl("bedrock", env, {"region": "us-east-1"})
    assert "/tmp/x" not in str(exc.value) and "work1" not in str(exc.value)  # 誤りの文に値を出さない


@pytest.mark.parametrize(
    "other",
    [{"ANTHROPIC_AUTH_TOKEN": "t"}, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}, {"ANTHROPIC_PROFILE": "p"}],
)
def test_check_decl_refuses_an_api_key_with_another_credential(other):
    """API キーの設定に別の認証の値を書くと拒む（キーと別の資格情報を子が選べる組み合わせを残さない）。"""
    with pytest.raises(ValueError) as exc:
        reg.check_decl("api-key", {"ANTHROPIC_API_KEY": "sk-ant-test-decl", **other}, {"key": "main"})
    assert "sk-ant-test-decl" not in str(exc.value)  # 誤りの文に値を出さない


def test_check_decl_refuses_every_variable_the_wrapper_fixes():
    """保存した設定が拒む変数と、従量の子でラッパーが確定する変数（session_env.METERED_FIXED）が同じ。"""
    for name in session_env.METERED_FIXED:
        with pytest.raises(ValueError):
            reg.check_decl("bedrock", {**BEDROCK, name: "x"}, {"region": "us-east-1"})


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


def _registered_then_staged(tmp_path):
    store = tmp_path / "store"
    first = tmp_path / "first"
    first.mkdir()
    (first / reg.CRED_FILE).write_text('{"old": 1}')
    (first / reg.CONFIG_FILE).write_text('{"oauthAccount": {"emailAddress": "a@example.com"}}')
    reg.register(store, "work1", first, reg.Identity("a@example.com", "", ""), 0.0)
    again = tmp_path / "again"
    again.mkdir()
    (again / reg.CRED_FILE).write_text('{"new": 1}')
    (again / reg.CONFIG_FILE).write_text('{"oauthAccount": {"emailAddress": "b@example.com"}}')
    return store, again


def test_r1_1_relogin_interrupted_after_the_credentials_leaves_the_old_row_needing_relogin(tmp_path, monkeypatch):
    """認証を置き換えた直後に止まっても、旧い本人・世代の登録は再登録が要る印を持つ（起動と使用率の取得が飛ばす）。"""
    store, again = _registered_then_staged(tmp_path)
    old_gen = reg.generation(store, "work1")
    real = os.replace
    calls = []

    def stop_after_credentials(src, dst):
        calls.append(Path(dst).name)
        real(src, dst)
        if Path(dst).name == reg.CRED_FILE:
            raise OSError("中断")

    monkeypatch.setattr(reg.os, "replace", stop_after_credentials)
    with pytest.raises(OSError):
        reg.register(store, "work1", again, reg.Identity("b@example.com", "", ""), 1.0)
    got = reg.load(store, "work1")
    assert json.loads((store / "work1" / reg.CRED_FILE).read_text()) == {"new": 1}
    assert got.needs_relogin and got.generation == old_gen and got.email == "a@example.com"


def test_r1_8_relogin_moves_a_legacy_config_to_backups(tmp_path):
    """旧形式の .config.json は登録し直しで backups/ へ移り、次の起動は新しい .claude.json を読む。"""
    from ndf.domains.relay import account_dir as ad

    store, again = _registered_then_staged(tmp_path)
    final = store / "work1"
    (final / reg.LEGACY_CONFIG_FILE).write_text('{"oauthAccount": {"emailAddress": "a@example.com"}}')
    reg.register(store, "work1", again, reg.Identity("b@example.com", "", ""), 7.0)
    assert not (final / reg.LEGACY_CONFIG_FILE).exists()
    assert (final / reg.BACKUPS_DIR / f"{reg.LEGACY_CONFIG_FILE}.7").is_file()
    assert ad.config_file(final, True) == final / reg.CONFIG_FILE
    assert not reg.load(store, "work1").needs_relogin


def test_r2_2_relogin_keeps_an_existing_backup_of_the_same_name(tmp_path):
    """同じ秒の名前のバックアップが既にあっても上書きせず、両方の中身が backups/ に残る。"""
    store, again = _registered_then_staged(tmp_path)
    final = store / "work1"
    backups = final / reg.BACKUPS_DIR
    backups.mkdir()
    (backups / f"{reg.LEGACY_CONFIG_FILE}.7").write_text("earlier")
    (final / reg.LEGACY_CONFIG_FILE).write_text("now")
    reg.register(store, "work1", again, reg.Identity("b@example.com", "", ""), 7.0)
    assert not (final / reg.LEGACY_CONFIG_FILE).exists()
    assert (backups / f"{reg.LEGACY_CONFIG_FILE}.7").read_text() == "earlier"
    assert (backups / f"{reg.LEGACY_CONFIG_FILE}.7-1").read_text() == "now"


def test_r3_2_relogin_moves_a_legacy_config_without_hard_links(tmp_path, monkeypatch):
    """ハードリンク非対応のファイルシステムでも、既存のバックアップを残したまま旧形式の設定を退避して登録し直せる。"""
    store, again = _registered_then_staged(tmp_path)
    final = store / "work1"
    backups = final / reg.BACKUPS_DIR
    backups.mkdir()
    (backups / f"{reg.LEGACY_CONFIG_FILE}.7").write_text("earlier")
    (final / reg.LEGACY_CONFIG_FILE).write_text("now")

    def no_link(*_a, **_k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(reg.os, "link", no_link)
    reg.register(store, "work1", again, reg.Identity("b@example.com", "", ""), 7.0)
    assert not (final / reg.LEGACY_CONFIG_FILE).exists()
    assert (backups / f"{reg.LEGACY_CONFIG_FILE}.7").read_text() == "earlier"
    assert (backups / f"{reg.LEGACY_CONFIG_FILE}.7-1").read_text() == "now"
    assert not reg.load(store, "work1").needs_relogin


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
    usage = [account_decide.Usage(name="work1", percent=40.0, five=40.0, seven=None, seven_resets_at=None, limited=False)]
    rows, warnings = reg.list_rows(
        tmp_path, {"NDF_SUPERVISE_CLAUDE_FALLBACK": "AWS_REGION=us-east-1 AWS_PROFILE=p"}, "work1", lambda _: usage
    )
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


def _live_lease(store: Path, name: str) -> None:
    d = reg.lease_dir(store, name)
    d.mkdir(parents=True, exist_ok=True)
    me = {"pid": os.getpid(), "start": reg.proc_start(os.getpid())}
    (d / "l1.json").write_text(json.dumps({"generation": "g1", "state": "running", "owner": me, "child": None}))


def test_add_of_a_name_in_use_stops_before_the_login_and_before_passing_the_code(tmp_path, fake_claude):
    store = tmp_path / "store"
    env = dict(os.environ)
    _live_lease(store, "work1")
    with pytest.raises(reg.AccountInUse):
        account_login.add(store, "work1", None, env)  # ログインを始めない
    assert not (store / ".pending-work1").exists()
    (reg.lease_dir(store, "work1") / "l1.json").unlink()
    assert account_login.add(store, "work1", None, env).needs_code
    _live_lease(store, "work1")
    with pytest.raises(reg.AccountInUse):
        account_login.add(store, "work1", "good", env)  # 認可コードを渡さず、途中の状態を残す
    assert (store / ".pending-work1" / account_login.PENDING_FIFO).exists() and reg.names(store) == []
    (reg.lease_dir(store, "work1") / "l1.json").unlink()
    assert not account_login.add(store, "work1", "good", env).needs_code


def test_add_holds_the_lock_from_passing_the_code_to_registering(tmp_path, fake_claude, monkeypatch):
    """認可コードを渡してから登録を終えるまで、使用の届けを書く排他を他のレーンが取れない（コードを使った後に使用中で拒まない）。"""
    store = tmp_path / "store"
    env = dict(os.environ)
    account_login.add(store, "work1", None, env)
    real = account_login._wait_exit
    tried: list[str] = []

    def wait_and_try_the_lock(p):
        try:
            with reg.locked(store, "work1", wait=0.1):
                tried.append("got")
        except reg.AccountLockTimeout:
            tried.append("busy")
        return real(p)

    monkeypatch.setattr(account_login, "_wait_exit", wait_and_try_the_lock)
    assert not account_login.add(store, "work1", "good", env).needs_code
    assert tried == ["busy"] and reg.names(store) == ["work1"]


@pytest.mark.parametrize(
    "error, kept",
    [(psutil.AccessDenied, True), (psutil.NoSuchProcess, False), (psutil.ZombieProcess, False)],
    ids=["denied", "gone", "zombie"],
)
def test_a_lease_whose_process_cannot_be_checked_is_kept_and_stops_changes(tmp_path, monkeypatch, error, kept):
    """確かめられないプロセスの届けは消さずに使用中とみなす。消えたと確かめられた届けだけを消す。"""
    store = tmp_path / "store"
    _live_lease(store, "work1")

    def fails(pid):
        raise error(pid)

    monkeypatch.setattr(reg.psutil, "Process", fails)
    assert bool(reg.live_leases(store, "work1")) is kept
    assert (reg.lease_dir(store, "work1") / "l1.json").exists() is kept
    if kept:
        with pytest.raises(reg.AccountInUse):
            reg.ensure_free(store, "work1")


def test_login_env_does_not_carry_the_parents_provider_or_auth_profile(tmp_path):
    """登録用の子は、子の環境の表の account-drop の変数（提供元の選択・認証のプロファイルを含む）を親から継がない。"""
    drop = facts.ids("env", child="account-drop")
    parent = {"PATH": "/bin", **{name: "1" for name in drop}, "NDF_CLAUDE_ACCOUNT": "work9"}
    got = account_login.auth_env(parent, tmp_path / "config")
    assert {"CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE", "ANTHROPIC_PROFILE"} <= set(drop)
    assert got == {"PATH": "/bin", "CLAUDE_CONFIG_DIR": str(tmp_path / "config")}


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
    reg.save_metered(tmp_path, "bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1"}, {"region": "us-east-1"}, 0.0)
    book = {
        "version": 1,
        "use": "off",
        "keys": {"k2": {"key": "sk-2", "account": "x"}, "k1": {"key": "sk-1", "monthly_usd": 0}, "k3": "not a dict"},
    }
    reg.write(reg.book_path(tmp_path), book)
    usage = [
        account_decide.Usage(name="c-limited", percent=100.0, five=100.0, seven=None, seven_resets_at=None, limited=True),
        account_decide.Usage(name="a-relogin", percent=5.0, five=5.0, seven=None, seven_resets_at=None, limited=False),
    ]
    rows, warnings = reg.list_rows(tmp_path, {}, None, lambda _: usage)
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
    rows, warnings = reg.list_rows(tmp_path, {}, None, lambda _: [])
    assert rows == [{"name": "metered", "kind": "metered", "source": "saved", "state": "壊れている（設定なしとして扱う）"}]
    assert len(warnings) == 1 and warnings[0].endswith("（API キーを使わずに動く）")
    rows, _ = reg.list_rows(tmp_path, {"NDF_SUPERVISE_CLAUDE_FALLBACK": ""}, None, lambda _: [])
    assert rows == []
    assert reg.list_rows(tmp_path / "none", {}, "x", lambda _: []) == ([], [])


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


def test_sweep_skips_a_pending_login_whose_code_is_being_passed(tmp_path):
    """認可コードを渡してログインの終わりを待つ間（登録の排他を持つ）は、期限を過ぎても別の登録が始めに打つ掃除が止めない。"""
    proc = _session_proc("sleep 30")
    try:
        d = _pending(tmp_path, "work1", {"pid": proc.pid, "pid_start": account_login._start_time(proc.pid), "expires": 10.0})
        with reg.locked(tmp_path, "work1"):
            assert account_login.sweep_pending(tmp_path, 20.0) == set()
            assert d.exists() and proc.poll() is None
        assert account_login.sweep_pending(tmp_path, 20.0) == {"work1"}
        assert not d.exists() and proc.wait(timeout=5) in (-signal.SIGTERM, -signal.SIGKILL)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_r3_5_mark_relogin_keeps_0600_under_a_strict_umask(tmp_path):
    _account(tmp_path, "work1")
    old = os.umask(0o477)  # 所有者の読み取りも落とす
    try:
        with reg.locked(tmp_path, "work1"):
            reg.mark_relogin(tmp_path, "work1")
    finally:
        os.umask(old)
    path = tmp_path / "work1" / reg.ACCOUNT_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and json.loads(path.read_text())["needs_relogin"] is True


FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"


@pytest.mark.parametrize(
    "env, kind",
    [
        ({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p"}, "CLAUDE_CODE_USE_BEDROCK"),
        ({"ANTHROPIC_API_KEY": FAKE_KEY}, reg.API_KEY_KIND),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_BASE_URL": "https://gw.example.com"}, reg.API_KEY_KIND),
        ({"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "0"}, "CLAUDE_CODE_USE_BEDROCK"),
        ({"CLAUDE_CODE_USE_BEDROCK": "0", "ANTHROPIC_API_KEY": FAKE_KEY}, reg.API_KEY_KIND),
        # 別冊の「None になる組み合わせ」の 4 行と、空のキー・解けない提供元の選択
        ({"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_KEY": ""}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_AUTH_TOKEN": "fake"}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "CLAUDE_CODE_OAUTH_TOKEN": "fake"}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_PROFILE": "p"}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "AWS_PROFILE": "p"}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_CUSTOM_HEADERS": "Authorization: x"}, None),
        ({"ANTHROPIC_API_KEY": "  "}, None),
        ({"ANTHROPIC_API_KEY": FAKE_KEY, "CLAUDE_CODE_USE_VERTEX": "maybe"}, None),
        ({"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1"}, None),
        ({}, None),
    ],
)
def test_metered_kind_settles_the_provider_or_the_api_key(env, kind):
    # 別冊の「設定の提供元の確定」: 提供元の選択が 1 つか、API キーの設定か、どちらでもなければ None
    assert reg.metered_kind(env) == kind
