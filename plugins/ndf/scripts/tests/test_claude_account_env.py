"""アカウントの設定ディレクトリを子の `CLAUDE_CONFIG_DIR` にする部品のテスト（#1576）。

`lib/claude_accounts.py` の用意（`prepare`）・子の環境（`account_env`）・書き戻し（`settle`）・登録し直し・登録の削除・
認証の失敗の観測を、偽のアカウントの置き場（`account_fake.py`）で縛る。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_account_dir as ad  # noqa: E402
import claude_accounts as ca  # noqa: E402
from account_fake import accounts, window  # noqa: E402,F401
from relay_lib import switch as relay_switch  # noqa: E402


def _base(accounts, **extra):
    return {**accounts.env(), "PATH": "/bin", **extra}


def _w(path: Path, data) -> None:
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, 0o600)


def _r(path: Path):
    return json.loads(path.read_text())


def _share(accounts, *items):
    for n in items:
        (accounts.shared / n).mkdir(exist_ok=True)


# ---------------------------------------------------------------- 子の環境（受け入れ条件 2・10・16・21、I1・I2・I4・I17）


def test_account_env_points_config_dir_to_account(accounts):
    """受け入れ条件 2・I1・I17: CLAUDE_CONFIG_DIR はアカウントの設定ディレクトリ、トークンとスコープの変数は無い。"""
    tok = accounts.add("a")
    env = ca.account_env("a", _base(accounts, CLAUDE_CODE_OAUTH_TOKEN="old", CLAUDE_CODE_OAUTH_SCOPES="user:inference"))
    assert env[ca.CONFIG_ENV] == str(accounts.root / "a") and env[ca.NAME_ENV] == "a"
    assert ca.TOKEN_ENV not in env and ca.SCOPES_ENV not in env and tok not in json.dumps(env)
    assert env[ca.SHARED_ENV] == str(accounts.shared) and env[ca.PLUGIN_CACHE_ENV] == str(accounts.shared / "plugins")
    kept = ca.account_env("a", _base(accounts, CLAUDE_CODE_PLUGIN_CACHE_DIR="/mine"))
    assert kept[ca.PLUGIN_CACHE_ENV] == "/mine"


def test_account_env_without_config_dir_records_empty(accounts, monkeypatch, tmp_path):
    """元の環境に CLAUDE_CONFIG_DIR が無ければ、NDF_SHARED_CONFIG_DIR は空文字で、共有側は ~/.claude になる。"""
    accounts.add("a")
    home = Path(os.environ["HOME"])
    env = ca.account_env("a", {"PATH": "/bin"})
    assert env[ca.SHARED_ENV] == "" and os.readlink(accounts.root / "a" / "projects") == str(home / ".claude" / "projects")
    m = ca.account_env(ca.METERED, env)
    assert ca.CONFIG_ENV not in m and ca.SHARED_ENV not in m and ca.PLUGIN_CACHE_ENV not in m


def test_metered_from_account_session_restores_config_dir(accounts):
    """受け入れ条件 10・I2: アカウントのセッションの中から従量の接続へ移ると、CLAUDE_CONFIG_DIR は元の値へ戻る。"""
    accounts.add("a")
    inside = ca.account_env("a", _base(accounts))
    m = ca.account_env(ca.METERED, {**inside, ca.FALLBACK_ENV: "CLAUDE_CODE_USE_BEDROCK=1"})
    assert m[ca.CONFIG_ENV] == str(accounts.shared) and m[ca.NAME_ENV] == "metered"
    assert ca.SHARED_ENV not in m and ca.PLUGIN_CACHE_ENV not in m and ca.TOKEN_ENV not in m and ca.SCOPES_ENV not in m


def test_nested_account_links_point_to_shared(accounts, monkeypatch):
    """受け入れ条件 16・I4: A のセッションの中から B を用意すると、B の symlink は共有側を指し、置き場は同じ実体。"""
    _share(accounts, "plugins", "agents")
    accounts.add("a")
    accounts.add("b")
    inside = ca.account_env("a", _base(accounts))
    store = ca.store_dir()
    monkeypatch.setattr(ca.os, "environ", inside)
    env = ca.account_env("b", inside)
    for n in ("plugins", "agents", "projects", "settings.json"):
        assert os.readlink(accounts.root / "b" / n) == str(accounts.shared / n)
    assert env[ca.SHARED_ENV] == str(accounts.shared) and ca.shared_dir() == str(accounts.shared) and ca.store_dir() == store


def test_wrong_symlink_is_relinked(accounts, tmp_path):
    """I4: 参照先の違う symlink は付け替わる。"""
    _share(accounts, "plugins")
    accounts.add("a")
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.symlink(other, accounts.root / "a" / "plugins")
    ca.account_env("a", _base(accounts))
    assert os.readlink(accounts.root / "a" / "plugins") == str(accounts.shared / "plugins")


def test_store_dir_follows_shared_env(accounts, monkeypatch):
    """I4: NDF_SHARED_CONFIG_DIR があれば、CLAUDE_CONFIG_DIR ではなくそこから置き場を求める。"""
    monkeypatch.delenv("NDF_ACCOUNTS_DIR")
    monkeypatch.setenv(ca.CONFIG_ENV, str(accounts.root / "a"))
    monkeypatch.setenv(ca.SHARED_ENV, str(accounts.shared))
    assert ca.store_dir() == str(accounts.shared / "ndf" / "accounts")
    monkeypatch.setenv(ca.SHARED_ENV, "")
    assert ca.store_dir() == str(Path(os.environ["HOME"]) / ".claude" / "ndf" / "accounts")


# ---------------------------------------------------------------- 用意（受け入れ条件 5・9・13・17・18・19、I5〜I7・I13・I15・I19）


def test_old_store_is_migrated_once(accounts):
    """受け入れ条件 9・13・I7: 今の形の置き場で環境が組み立ち、古い排他ファイルが消え、2 回目は何も足さない。"""
    _share(accounts, "plugins", "ndf")
    accounts.add("a")
    old = accounts.root / "a.lock"
    old.touch()
    os.utime(old, (time.time() - 120, time.time() - 120))
    fresh = accounts.root / "nyle.lock"
    fresh.touch()  # 60 秒より新しいものは古い版のラッパーが使っている
    rows = []
    assert ca.account_env("a", _base(accounts), rows.append) is not None
    assert not old.exists() and fresh.exists() and rows[0]["added"] > 0
    assert (accounts.root / ".locks" / "a.lock").exists()
    before = {p.name: os.lstat(p).st_mtime_ns for p in (accounts.root / "a").iterdir() if p.is_symlink()}
    rows.clear()
    ca.account_env("a", _base(accounts), rows.append)
    ca.usage("a")
    ca.note_limit("a", "five_hour", None)
    assert rows == [] and not (accounts.root / "a.lock").exists()
    assert {p.name: os.lstat(p).st_mtime_ns for p in (accounts.root / "a").iterdir() if p.is_symlink()} == before
    for f in (".credentials.json", "account.json", "usage.json"):
        assert (accounts.root / "a" / f).is_file() and not (accounts.root / "a" / f).is_symlink()


def test_shared_credentials_never_linked(accounts):
    """受け入れ条件 7・I3: 共有側の .credentials.json と固有の項目は symlink にしない。"""
    for n in ("policy-limits.json", "remote-settings.json", ".claude.json"):
        _w(accounts.shared / n, {})
    accounts.add("a")
    ca.account_env("a", _base(accounts))
    for n in (".credentials.json", "policy-limits.json", "remote-settings.json"):
        assert not (accounts.root / "a" / n).is_symlink()
    assert accounts.creds("a")["accessToken"] == "a-access-SECRET"


def test_entities_are_kept_and_reported(accounts):
    """受け入れ条件 17・I5: 同じ名前の実体は残して skipped に、共有側に無い実体は local_only に出す。"""
    _share(accounts, "agents")
    accounts.add("a")
    (accounts.root / "a" / "agents").mkdir()
    (accounts.root / "a" / "agents" / "x.md").write_text("mine")
    (accounts.root / "a" / "plans").mkdir()
    _w(accounts.root / "a" / "CLAUDE.md", "local")
    rows = []
    ca.account_env("a", _base(accounts), rows.append)
    assert rows[0]["skipped"] == ["agents"] and rows[0]["local_only"] == ["CLAUDE.md", "plans"]
    assert (accounts.root / "a" / "agents" / "x.md").read_text() == "mine" and (accounts.root / "a" / "CLAUDE.md").read_text() == "local"


def test_projects_not_shared_moves_to_next_candidate(accounts):
    """受け入れ条件 18・I6: projects が実体のアカウントは使わず、次の候補の環境が返り、理由が残る。"""
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    (accounts.root / "a" / "projects").mkdir()
    rows = []
    assert ca.account_env("a", _base(accounts), rows.append) is None
    assert (rows[-1]["account"], rows[-1]["ok"], rows[-1]["reason"]) == ("a", False, "projects_not_shared")
    c, env = ca.choose_env(exclude=(), base=_base(accounts), note=rows.append)
    assert c.name == "b" and env[ca.CONFIG_ENV] == str(accounts.root / "b")
    assert os.readlink(accounts.root / "b" / "projects") == str(accounts.shared / "projects")


@pytest.mark.parametrize("content", ['{"projects": ', ""])
def test_unreadable_account_config_is_not_started(accounts, content):
    """I19: アカウント側の .claude.json が読めなければ起動しない。ファイルは変えず、symlink を足さず、控えを消す。"""
    accounts.add("a")
    _w(accounts.root / "a" / ".claude.json", content)
    _w(accounts.root / "a" / ad.BASE_FILE, {"version": 1, "projects": {}, "mcpServers": {}})
    rows = []
    assert ca.account_env("a", _base(accounts), rows.append) is None
    assert rows[-1]["reason"] == "account_unreadable" and "壊れている" in ca.prepare_reason_text("a", "account_unreadable")
    assert (accounts.root / "a" / ".claude.json").read_text() == content and not (accounts.root / "a" / ad.BASE_FILE).exists()
    assert not any(p.is_symlink() for p in (accounts.root / "a").iterdir())
    (accounts.root / "a" / ".claude.json").unlink()
    assert ca.account_env("a", _base(accounts)) is not None


def test_identity_mismatch_needs_relogin(accounts):
    """I13: oauthAccount のメールアドレスが account.json と違えば起動せず、再登録が要る。無ければ通す。"""
    accounts.add("a")
    assert ca.account_env("a", _base(accounts)) is not None
    _w(accounts.root / "a" / ".claude.json", {"oauthAccount": {"emailAddress": "other@example.com"}})
    rows = []
    assert ca.account_env("a", _base(accounts), rows.append) is None
    assert rows[-1]["reason"] == "identity_mismatch" and accounts.account("a")["needs_relogin"] is True


def test_identity_compares_org_when_both_known(accounts):
    accounts.add("a", org_id="org-1", org_name="O")
    _w(accounts.root / "a" / ".claude.json", {"oauthAccount": {"emailAddress": "A@example.com", "organizationUuid": "org-1"}})
    assert ca.account_env("a", _base(accounts)) is not None
    _w(accounts.root / "a" / ".claude.json", {"oauthAccount": {"emailAddress": "a@example.com", "organizationUuid": "org-2"}})
    assert ca.account_env("a", _base(accounts)) is None


def test_prepare_syncs_shared_part_and_settle_writes_back(accounts):
    """受け入れ条件 19・I10: 用意で共有側の projects と mcpServers が入り、oauthAccount は変わらない。書き戻しで共有側へ入る。"""
    accounts.add("a")
    shared = {"projects": {"/p": {"hasTrustDialogAccepted": True}}, "mcpServers": {"m": {"command": "x"}}, "hasCompletedOnboarding": True}
    _w(accounts.shared / ".claude.json", shared)
    _w(accounts.root / "a" / ".claude.json", {"oauthAccount": {"emailAddress": "a@example.com"}, "userID": "u"})
    ca.account_env("a", _base(accounts))
    got = _r(accounts.root / "a" / ".claude.json")
    assert got["projects"] == shared["projects"] and got["mcpServers"] == shared["mcpServers"]
    assert got["oauthAccount"] == {"emailAddress": "a@example.com"} and got["hasCompletedOnboarding"] is True
    got["projects"]["/new"] = {"hasTrustDialogAccepted": True}
    _w(accounts.root / "a" / ".claude.json", got)
    ca.settle("a", _base(accounts))
    after = _r(accounts.shared / ".claude.json")
    assert after["projects"]["/new"] == {"hasTrustDialogAccepted": True} and "oauthAccount" not in after and "userID" not in after


def test_shared_config_file_follows_claude_rule(accounts, monkeypatch):
    """共有の .claude.json は、元の CLAUDE_CONFIG_DIR の下、無ければ ~/.claude.json（.config.json があればそれ）。"""
    home = Path(os.environ["HOME"])
    assert ca.shared_config_file({"CLAUDE_CONFIG_DIR": str(accounts.shared)}) == str(accounts.shared / ".claude.json")
    assert ca.shared_config_file({}) == str(home / ".claude.json")
    assert ca.shared_config_file({ca.SHARED_ENV: "", ca.CONFIG_ENV: "/x"}) == str(home / ".claude.json")
    _w(accounts.shared / ".config.json", {})
    assert ca.shared_config_file({"CLAUDE_CONFIG_DIR": str(accounts.shared)}) == str(accounts.shared / ".config.json")


def test_permissions_after_prepare_and_sync(accounts):
    """I15: 用意と同期の後、ディレクトリは 0700、実体のファイルは 0600。"""
    accounts.add("a")
    _w(accounts.shared / ".claude.json", {"projects": {"/p": {"t": 1}}})
    ca.account_env("a", _base(accounts))
    d = accounts.root / "a"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700 and stat.S_IMODE(accounts.root.stat().st_mode) == 0o700
    for p in d.iterdir():
        if not p.is_symlink() and p.is_file():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600, p


def test_account_dir_row_carries_no_values(accounts):
    """受け入れ条件 22・I14: 記録の行にトークン・.claude.json の値・userID・参照先のパスが出ない。"""
    tok = accounts.add("a")
    _w(accounts.shared / ".claude.json", {"projects": {"/secret-project": {"t": 1}}})
    _w(accounts.root / "a" / ".claude.json", {"userID": "uid-SECRET"})
    (accounts.root / "a" / "projects").mkdir()
    rows = []
    ca.account_env("a", _base(accounts), rows.append)
    ca.settle("a", _base(accounts), rows.append)
    text = json.dumps(rows)
    for v in (tok, "uid-SECRET", "/secret-project", str(accounts.shared)):
        assert v not in text
    assert set(rows[0]) == {"account", "ok", "reason", "added", "skipped", "local_only", "sync"}


def test_prepare_is_fast_and_offline(accounts):
    """性能: 直下 31 項目・約 164 KB の共有の .claude.json で 1 秒以内に終わり、取得先と宛先を呼ばない。"""
    for i in range(29):
        (accounts.shared / f"item{i}").mkdir()
    projects = {f"/work/p{i}": {"allowedTools": [], "history": ["x" * 200] * 11, "hasTrustDialogAccepted": True} for i in range(70)}
    _w(accounts.shared / ".claude.json", {"projects": projects, "mcpServers": {}})
    accounts.add("a")
    calls = (len(accounts.fake.usage_calls), len(accounts.fake.refresh_calls))
    assert (accounts.shared / ".claude.json").stat().st_size > 150_000
    t = time.monotonic()
    assert ca.account_env("a", _base(accounts)) is not None
    assert time.monotonic() - t < 1.0
    assert (len(accounts.fake.usage_calls), len(accounts.fake.refresh_calls)) == calls


# ---------------------------------------------------------------- 登録し直しと削除（受け入れ条件 17、I5・決定 20）


def _staging(accounts, name, email, claude_json=True):
    st = Path(ca.staging_dir(name))
    _w(st / ".credentials.json", {"claudeAiOauth": {"accessToken": "new-SECRET", "refreshToken": "new-r", "expiresAt": 1}})
    if claude_json:
        _w(st / ".claude.json", {"oauthAccount": {"emailAddress": email}})
    (st / "junk").mkdir()
    return st


def test_reregister_keeps_links_and_entities(accounts):
    """決定 20・I5: 登録し直しは symlink・共有側に無い実体・backups を残し、認証ファイル・.claude.json・account.json だけを替える。"""
    _share(accounts, "plugins")
    accounts.add("a")
    _w(accounts.shared / ".claude.json", {"projects": {"/p": {"t": 1}}, "mcpServers": {"m": {}}})
    ca.account_env("a", _base(accounts))
    d = accounts.root / "a"
    (d / "plans").mkdir()
    (d / "backups").mkdir()
    ca.usage("a")
    assert (d / "usage.json").exists() and (d / ad.BASE_FILE).exists()
    st = _staging(accounts, "a", "a@example.com")
    ca.register("a", str(st), "a@example.com")
    assert (d / "plugins").is_symlink() and (d / "plans").is_dir() and (d / "backups").is_dir()
    assert accounts.creds("a")["accessToken"] == "new-SECRET" and _r(d / ".claude.json")["oauthAccount"]["emailAddress"] == "a@example.com"
    assert accounts.account("a")["needs_relogin"] is False
    assert not (d / "usage.json").exists() and not (d / ad.BASE_FILE).exists() and not st.exists() and not (d / "junk").exists()
    assert ca.account_env("a", _base(accounts)) is not None
    shared = _r(accounts.shared / ".claude.json")
    assert shared["projects"] == {"/p": {"t": 1}} and shared["mcpServers"] == {"m": {}}


def test_reregister_without_claude_json_drops_old_identity(accounts):
    accounts.add("a")
    _w(accounts.root / "a" / ".claude.json", {"oauthAccount": {"emailAddress": "old@example.com"}})
    ca.register("a", str(_staging(accounts, "a", "", claude_json=False)), "a@example.com")
    assert not (accounts.root / "a" / ".claude.json").exists()


def test_detach_and_unregister_keep_shared(accounts):
    """受け入れ条件 17・I5: 登録を外しても、共有の設定ディレクトリの項目は残る。"""
    _share(accounts, "plugins")
    (accounts.shared / "plugins" / "p.txt").write_text("keep")
    accounts.add("a")
    ca.account_env("a", _base(accounts))
    ca.detach("a")
    assert not any(p.is_symlink() for p in (accounts.root / "a").iterdir())
    ca.unregister("a")
    assert (accounts.shared / "plugins" / "p.txt").read_text() == "keep" and (accounts.shared / "projects").is_dir()
    assert (accounts.shared / "settings.json").exists() and (accounts.shared / ".credentials.json").exists()


# ---------------------------------------------------------------- 認証の失敗の観測（受け入れ条件 20、I12）


def test_auth_failed_observation_excludes_until_cleared(accounts):
    """I12: 観測のあるアカウントは候補にならず、needs_relogin は変わらない。後の取得の成功か 1 時間で戻る。"""
    accounts.add("a", util5=10)
    accounts.add("b", util5=30)
    ca.note_auth_failed("a")
    assert ca.choose(keep={"a"}).name == "b" and accounts.account("a")["needs_relogin"] is False
    assert ca.choose(keep={"a"}).name == "b"  # 子が終わる前（keep）の取得では解かない
    assert ca.load_account("a").state(time.time()) == "認証の失敗"
    assert not ca.load_account("a").auth_held(time.time() + ca.AUTH_FAILED_HOLD + 1)
    os.environ["NDF_ACCOUNT_CHECK_INTERVAL"] = "0"
    try:
        ca.usage("a")
    finally:
        del os.environ["NDF_ACCOUNT_CHECK_INTERVAL"]
    assert accounts.account("a").get("auth_failed") is None and ca.choose().name == "a"


# ---------------------------------------------------------------- 再開の文（受け入れ条件 11）


@pytest.mark.parametrize(
    ("reason", "want"),
    [
        ("five_hour", "利用上限でアカウントを替えた。中断したところから続ける"),
        ("spend", "利用上限でアカウントを替えた。中断したところから続ける"),
        ("auth", "認証が通らなかったためアカウントを替えた。中断したところから続ける"),
        ("threshold", "使用率が切り替えの閾値を超えたためアカウントを替えた。中断したところから続ける"),
        ("recovered", "上限が外れたためアカウントへ戻した。中断したところから続ける"),
        ("unusable", "アカウントを替えた。中断したところから続ける"),
    ],
)
def test_resume_text_by_reason(reason, want):
    assert relay_switch.resume_text(reason) == want


def test_limit_start_uses_reason_and_goal(tmp_path, monkeypatch):
    """受け入れ条件 11: 認証の失敗で替えた次の最初の入力は認証の文。未達の /goal があれば理由に依らず /goal。"""
    from relay_lib import claude as rc

    sw = object.__new__(relay_switch.AccountSwitch)
    sw.read_mark = lambda: None
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    m = {"transcript_path": str(tp), "session_id": "s1", "_plan": ("b", "auth", None)}
    assert sw.limit_start(m)[0] == ["--resume", "s1", "認証が通らなかったためアカウントを替えた。中断したところから続ける"]
    monkeypatch.setattr(rc, "unmet_goal", lambda p: "c")
    assert sw.limit_start(m)[0][-1] == "/goal c"
