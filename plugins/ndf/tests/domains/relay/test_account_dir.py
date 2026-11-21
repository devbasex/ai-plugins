"""起動の前の確かめと symlink（`account_dir.py` の U2 の範囲。#3604 #3864）。

設計書の「テスト設計」の行（3604:4・5・8・9・10・12 と I1・I2・I3・I5）と、詳細設計書の「起動の前の用意」の
「単体テストの観点」のうち確かめ・認証の上書き・symlink の部分を縛る。値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ndf.domains.relay import account_dir as ad
from ndf.domains.relay import account_registry as reg

ACCESS = "fake-access-value-3604"
REFRESH = "fake-refresh-value-3604"
KEY = "fake-key-value-3604"
HEADER = "Authorization: Bearer fake-header-value-3604"
SECRETS = (ACCESS, REFRESH, KEY, "fake-header-value-3604")


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """管理の設定の保存先と HOME を一時ディレクトリへ替える（手元と CI の /etc・~ を読まない）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    etc = tmp_path / "etc" / "claude-code"
    etc.mkdir(parents=True)
    monkeypatch.setattr(ad, "MANAGED_FILES", (etc / "managed-settings.json",))
    monkeypatch.setattr(ad, "MANAGED_DIR", etc / "managed-settings.d")
    return etc


def _json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _stamp(path: Path) -> tuple[int, bytes]:
    return path.stat().st_mtime_ns, path.read_bytes()


def _world(tmp_path: Path, name: str = "work1", *, org: str = "o1") -> dict:
    """共有の設定ディレクトリ・登録の保存先・使える登録済みアカウントを 1 つ作る。"""
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    _json(shared / ".claude.json", {"projects": {}})
    _json(shared / ".credentials.json", {"claudeAiOauth": {"accessToken": "shared-access", "refreshToken": "shared-refresh"}})
    store = shared / "ndf" / "accounts"
    acc = store / name
    acc.mkdir(parents=True)
    row = {"name": name, "email": f"{name}@example.com", "needs_relogin": False}
    if org:
        row["org_id"] = org
    _json(acc / reg.ACCOUNT_FILE, row)
    _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": ACCESS, "refreshToken": REFRESH}})
    _json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": f"{name}@example.com", "organizationUuid": "o1"}, "userID": "u"})
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    return {"shared": shared, "store": store, "acc": acc, "cwd": cwd, "shared_file": shared / ".claude.json"}


def _check(w: dict, name: str = "work1", env=None, argv=("claude",)):
    registered = reg.load(w["store"], name)
    launch = ad.Launch(env or {}, w["cwd"], list(argv))
    return ad.check_account(w["store"], w["store"] / name, registered, w["shared"], w["shared_file"], launch)


def _overrides(w: dict, env=None, argv=("claude",), cwd=None):
    return ad.auth_overrides(env or {}, w["acc"], cwd or w["cwd"], list(argv), w["shared"] / "settings.json")


# ---------------------------------------------------------------- symlink（3604:4・5・10、I1・I2・I3）


@pytest.mark.parametrize(
    "item, local",
    [
        (".credentials.json", True),
        (".claude.json.tmp.1", True),
        (".oauth_refresh.lock", True),
        ("account.json", True),
        (".ndf-shared-base.next.json", True),
        (".ndf-oauth-pending.json", True),
        ("remote-settings.json", True),
        ("projects", False),
        ("settings.json", False),
        ("skills", False),
    ],
)
def test_is_local_names_the_account_items(item, local):
    assert ad.is_local(item) is local


def test_3604_4_i1_link_shared_links_every_shared_item_but_the_account_ones(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "skills").mkdir()
    (w["shared"] / "plugins").mkdir()
    links = ad.link_shared(w["shared"], w["acc"])
    # 共有側に projects・settings.json が無ければ作る
    assert (w["shared"] / "projects").is_dir() and json.loads((w["shared"] / "settings.json").read_text()) == {}
    for item in ("projects", "settings.json", "skills", "plugins", "ndf"):
        p = w["acc"] / item
        assert p.is_symlink() and Path(os.readlink(p)) == w["shared"] / item
    # 認証ファイルは symlink にしない（I1）。アカウント側の実体のまま
    assert not (w["acc"] / reg.CRED_FILE).is_symlink() and not (w["acc"] / reg.CONFIG_FILE).is_symlink()
    assert json.loads((w["acc"] / reg.CRED_FILE).read_text())["claudeAiOauth"]["accessToken"] == ACCESS
    # 参照先はどれも共有の設定ディレクトリの直下
    for p in w["acc"].iterdir():
        if p.is_symlink():
            assert Path(os.readlink(p)).parent == w["shared"]
    assert links.added == 5 and links.projects_shared
    # 固定の一覧で張らない: 後から共有側に増えた項目にも張る
    (w["shared"] / "agents").mkdir()
    again = ad.link_shared(w["shared"], w["acc"])
    assert (w["acc"] / "agents").is_symlink() and again.added == 1


def test_link_shared_retargets_a_symlink_to_elsewhere(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "skills").mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    (w["acc"] / "skills").symlink_to(other)
    links = ad.link_shared(w["shared"], w["acc"])
    assert Path(os.readlink(w["acc"] / "skills")) == w["shared"] / "skills"
    assert links.added >= 1 and not list(w["acc"].glob("*.link"))


def test_3604_10_i2_link_shared_keeps_the_account_entities(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "skills").mkdir()
    (w["acc"] / "skills").mkdir()
    (w["acc"] / "skills" / "mine.md").write_text("mine")
    (w["acc"] / "notes").mkdir()
    (w["acc"] / "notes" / "n.txt").write_text("n")
    links = ad.link_shared(w["shared"], w["acc"])
    # 同じ名前の実体は上書きせず飛ばし、アカウント側にだけある実体は名前だけを返して残す
    assert links.skipped == ["skills"] and links.local_only == ["notes"]
    assert (w["acc"] / "skills" / "mine.md").read_text() == "mine" and not (w["acc"] / "skills").is_symlink()
    assert (w["acc"] / "notes" / "n.txt").read_text() == "n"


def test_3604_5_i3_two_accounts_reach_the_same_conversation_file(tmp_path):
    w = _world(tmp_path)
    other = _world(tmp_path, "work2")["acc"]
    ad.link_shared(w["shared"], w["acc"])
    ad.link_shared(w["shared"], other)
    written = w["acc"] / "projects" / "-work" / "s1.jsonl"
    written.parent.mkdir(parents=True)
    written.write_text("{}\n")
    seen = other / "projects" / "-work" / "s1.jsonl"
    assert seen.resolve() == written.resolve() == (w["shared"] / "projects" / "-work" / "s1.jsonl").resolve()


def test_i3_an_account_projects_entity_is_not_shared(tmp_path):
    w = _world(tmp_path)
    (w["acc"] / "projects").mkdir()
    links = ad.link_shared(w["shared"], w["acc"])
    assert not links.projects_shared and "projects" in links.skipped
    assert ad.planned_links(w["shared"], w["acc"])["projects"] == w["acc"] / "projects"


def test_planned_links_point_to_the_shared_items_for_a_first_account(tmp_path):
    w = _world(tmp_path)
    before = sorted(p.name for p in w["acc"].iterdir())
    plan = ad.planned_links(w["shared"], w["acc"])
    assert plan == {"projects": w["shared"] / "projects", "settings.json": w["shared"] / "settings.json"}
    # 書かない
    assert sorted(p.name for p in w["acc"].iterdir()) == before and not (w["shared"] / "projects").exists()


# ---------------------------------------------------------------- 設定ファイルの解決


def test_config_file_prefers_the_legacy_name(tmp_path, iso):
    d = tmp_path / "conf"
    d.mkdir()
    assert ad.config_file(d, True) == d / ".claude.json"
    assert ad.config_file(d, False) == Path.home() / ".claude.json"
    (d / ".config.json").write_text("{}")
    assert ad.config_file(d, True) == d / ".config.json" and ad.config_file(d, False) == d / ".config.json"


def test_shared_config_file_follows_the_parent_environment(tmp_path, iso):
    assert ad.shared_config_file({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / ".claude.json"
    assert ad.shared_config_file({"NDF_SHARED_CONFIG_DIR": str(tmp_path), "CLAUDE_CONFIG_DIR": "acc"}) == tmp_path / ".claude.json"
    # 親に CLAUDE_CONFIG_DIR が無かった（空文字）ときは既定の ~/.claude.json
    assert ad.shared_config_file({"NDF_SHARED_CONFIG_DIR": "", "CLAUDE_CONFIG_DIR": "acc"}) == Path.home() / ".claude.json"
    assert ad.shared_config_file({}) == Path.home() / ".claude.json"
    (Path.home() / ".claude").mkdir()
    (Path.home() / ".claude" / ".config.json").write_text("{}")
    assert ad.shared_config_file({}) == Path.home() / ".claude" / ".config.json"


# ---------------------------------------------------------------- 起動の前の確かめ（3604:8）


def test_check_account_returns_the_checked_local_and_resolved(tmp_path, iso):
    w = _world(tmp_path)
    got = _check(w)
    assert isinstance(got, ad.Checked)
    local = ad.check_local(w["store"], w["acc"], w["shared_file"])
    assert got.local == local and local.account == w["acc"].resolve() and local.config == (w["acc"] / ".claude.json").resolve()
    assert got.resolved == ad.effective_env({}, w["acc"], w["cwd"], ["claude"], w["shared"] / "settings.json")


def test_check_account_compares_only_the_email_without_an_org(tmp_path, iso):
    w = _world(tmp_path, org="")
    _json(w["acc"] / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "work1@example.com", "organizationUuid": "other"}})
    assert isinstance(_check(w), ad.Checked)


def _no_cred(acc):
    (acc / reg.CRED_FILE).unlink()


def _cred_broken(acc):
    (acc / reg.CRED_FILE).write_text("{broken")


def _no_access(acc):
    _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": "", "refreshToken": REFRESH}})


def _no_refresh(acc):
    _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": ACCESS, "refreshToken": ""}})


def _config_broken(acc):
    (acc / reg.CONFIG_FILE).write_text("[1]")


def _other_person(acc):
    _json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "else@example.com", "organizationUuid": "o1"}})


def _legacy(acc):
    _json(acc / ".config.json", {"oauthAccount": {"emailAddress": "work1@example.com", "organizationUuid": "o1"}})


@pytest.mark.parametrize(
    "breaks, reason",
    [
        (_no_cred, "no_credentials"),
        (_cred_broken, "no_credentials"),
        (_no_access, "no_credentials"),
        (_no_refresh, "no_refresh"),
        (_config_broken, "account_unreadable"),
        (_other_person, "identity_mismatch"),
        (_legacy, "legacy_config"),
    ],
)
def test_3604_8_check_account_names_each_reason(tmp_path, iso, breaks, reason):
    w = _world(tmp_path)
    _json(w["acc"] / reg.BASE_FILE, {"version": 1})
    breaks(w["acc"])
    base = _stamp(w["acc"] / reg.BASE_FILE)
    assert _check(w) == reason
    # 読むだけで、同期のバックアップを消さない
    assert _stamp(w["acc"] / reg.BASE_FILE) == base


def test_3604_9_relogin_reasons_are_the_three():
    assert set(ad.RELOGIN_REASONS) == {"no_refresh", "identity_mismatch", "legacy_config"}
    for reason in ("no_credentials", "account_unreadable", "not_account_local", "auth_overridden", "auth_unresolved"):
        assert reason not in ad.RELOGIN_REASONS and reason in ad.REASONS


def _cred_to_shared(w):
    (w["acc"] / reg.CRED_FILE).unlink()
    (w["acc"] / reg.CRED_FILE).symlink_to(w["shared"] / reg.CRED_FILE)
    return w["shared"] / reg.CRED_FILE


def _cred_hardlink_other(w):
    other = _world(w["shared"].parent, "work2")["acc"] / reg.CRED_FILE
    (w["acc"] / reg.CRED_FILE).unlink()
    os.link(other, w["acc"] / reg.CRED_FILE)
    return other


def _cred_hardlink_shared(w):
    (w["acc"] / reg.CRED_FILE).unlink()
    os.link(w["shared"] / reg.CRED_FILE, w["acc"] / reg.CRED_FILE)
    return w["shared"] / reg.CRED_FILE


def _config_to_shared(w):
    (w["acc"] / reg.CONFIG_FILE).unlink()
    (w["acc"] / reg.CONFIG_FILE).symlink_to(w["shared"] / ".claude.json")
    return w["shared"] / ".claude.json"


def _config_to_other(w):
    other = _world(w["shared"].parent, "work2")["acc"] / reg.CONFIG_FILE
    (w["acc"] / reg.CONFIG_FILE).unlink()
    (w["acc"] / reg.CONFIG_FILE).symlink_to(other)
    return other


def _base_to_shared(w):
    target = _json(w["shared"] / "base-copy.json", {"version": 1})
    (w["acc"] / reg.BASE_FILE).symlink_to(target)
    return target


def _dir_to_other(w):
    other = w["shared"].parent / "elsewhere"
    os.replace(w["acc"], other)
    w["acc"].symlink_to(other)
    return other / reg.CRED_FILE


def _shared_is_account(w):
    w["shared_file"].unlink()
    os.link(w["acc"] / reg.CONFIG_FILE, w["shared_file"])
    return w["acc"] / reg.CONFIG_FILE


@pytest.mark.parametrize(
    "breaks",
    [
        _cred_to_shared,
        _cred_hardlink_other,
        _cred_hardlink_shared,
        _config_to_shared,
        _config_to_other,
        _base_to_shared,
        _dir_to_other,
        _shared_is_account,
    ],
)
def test_3604_8_i1_check_account_skips_entities_outside_the_account(tmp_path, iso, breaks):
    w = _world(tmp_path)
    target = breaks(w)
    before = _stamp(target)
    assert _check(w) == "not_account_local"
    # 参照先を開かず書かない
    assert _stamp(target) == before


def test_check_local_refuses_a_shared_file_under_the_store(tmp_path, iso):
    w = _world(tmp_path)
    inner = _json(w["store"] / "work2" / ".claude.json", {})
    w["shared_file"].unlink()
    w["shared_file"].symlink_to(inner)
    assert ad.check_local(w["store"], w["acc"], w["shared_file"]) == "not_account_local"


def test_check_local_allows_a_dotfiles_symlink_for_the_shared_file(tmp_path, iso):
    w = _world(tmp_path)
    dotfiles = _json(tmp_path / "dotfiles" / "claude.json", {})
    w["shared_file"].unlink()
    w["shared_file"].symlink_to(dotfiles)
    local = ad.check_local(w["store"], w["acc"], w["shared_file"])
    assert isinstance(local, ad.Local) and local.shared == dotfiles.resolve()


def test_check_account_skips_an_overriding_shared_settings(tmp_path, iso):
    # 共有の settings.json に apiKeyHelper（planned_links の先で読む）
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": "echo x"})
    assert _check(w) == "auth_overridden"
    # アカウント側の settings.json が実体なら、その中を読む
    _json(w["shared"] / "settings.json", {})
    _json(w["acc"] / "settings.json", {"apiKeyHelper": "echo x"})
    assert _check(w) == "auth_overridden"


def test_check_account_skips_custom_headers_from_the_parent(tmp_path, iso):
    w = _world(tmp_path)
    assert _check(w, env={"ANTHROPIC_CUSTOM_HEADERS": HEADER}) == "auth_overridden"
    assert _check(w, env={"CLAUDE_CODE_USE_VERTEX": "maybe"}) == "auth_unresolved"


# ---------------------------------------------------------------- 認証の上書き


def test_auth_overrides_are_empty_without_overrides(tmp_path, iso):
    w = _world(tmp_path)
    assert _overrides(w) == []
    assert _overrides(w, env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com/"}) == []


@pytest.mark.parametrize(
    "where, data, item",
    [
        ("user", {"apiKeyHelper": "echo x"}, "apiKeyHelper（利用者の設定）"),
        ("user", {"env": {"ANTHROPIC_API_KEY": KEY}}, "ANTHROPIC_API_KEY（利用者の設定）"),
        ("project", {"apiKeyHelper": "echo x"}, "apiKeyHelper（プロジェクトの設定）"),
        ("project", {"env": {"CLAUDE_CODE_USE_ANTHROPIC_AWS": "1"}}, "CLAUDE_CODE_USE_ANTHROPIC_AWS（プロジェクトの設定）"),
        ("local", {"env": {"ANTHROPIC_PROFILE": "p"}}, "ANTHROPIC_PROFILE（プロジェクトのローカルの設定）"),
        ("managed", {"env": {"ANTHROPIC_API_KEY": KEY}}, "ANTHROPIC_API_KEY（管理の設定（managed-settings.json））"),
        ("managed_d", {"apiKeyHelper": "echo x"}, "apiKeyHelper（管理の設定（managed-settings.d/20-x.json））"),
        ("remote", {"env": {"ANTHROPIC_AUTH_TOKEN": KEY}}, "ANTHROPIC_AUTH_TOKEN（管理の設定（remote-settings.json））"),
        ("user", {"env": {"ANTHROPIC_BASE_URL": "https://proxy.example.com"}}, "ANTHROPIC_BASE_URL（利用者の設定）"),
        ("user", {"env": {"ANTHROPIC_CUSTOM_HEADERS": HEADER}}, "ANTHROPIC_CUSTOM_HEADERS（利用者の設定）"),
    ],
)
def test_auth_overrides_name_each_source(tmp_path, iso, where, data, item):
    w = _world(tmp_path)
    path = {
        "user": w["shared"] / "settings.json",
        "project": w["cwd"] / ".claude" / "settings.json",
        "local": w["cwd"] / ".claude" / "settings.local.json",
        "managed": iso / "managed-settings.json",
        "managed_d": iso / "managed-settings.d" / "20-x.json",
        "remote": w["acc"] / "remote-settings.json",
    }[where]
    _json(path, data)
    got = _overrides(w)
    assert got == [item]
    assert not any(s in json.dumps(got, ensure_ascii=False) for s in SECRETS)


def test_auth_overrides_from_the_child_env_and_settings_flag(tmp_path, iso):
    w = _world(tmp_path)
    assert _overrides(w, env={"ANTHROPIC_BASE_URL": "https://proxy.example.com"}) == ["ANTHROPIC_BASE_URL（子の環境）"]
    assert _overrides(w, env={"ANTHROPIC_CUSTOM_HEADERS": HEADER}) == ["ANTHROPIC_CUSTOM_HEADERS（子の環境）"]
    inline = json.dumps({"apiKeyHelper": "echo x"})
    assert _overrides(w, argv=("claude", "--settings", inline)) == ["apiKeyHelper（--settings）"]


def test_settings_file_is_resolved_from_the_given_cwd(tmp_path, iso, monkeypatch):
    w = _world(tmp_path)
    _json(w["cwd"] / "extra.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    elsewhere = tmp_path / "elsewhere"
    _json(elsewhere / "extra.json", {})
    monkeypatch.chdir(elsewhere)
    assert _overrides(w, argv=("claude", "--settings", "extra.json")) == ["ANTHROPIC_API_KEY（--settings）"]
    # 呼び出し元の作業ディレクトリにある同じ名前のファイルは読まない
    _json(w["cwd"] / "extra.json", {})
    _json(elsewhere / "extra.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    assert _overrides(w, argv=("claude", "--settings", "extra.json")) == []


def test_setting_sources_empty_reads_only_managed_and_settings_flag(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": "echo x"})
    _json(w["cwd"] / ".claude" / "settings.json", {"apiKeyHelper": "echo x"})
    assert _overrides(w, argv=("claude", "--setting-sources", "")) == []
    inline = json.dumps({"apiKeyHelper": "echo x"})
    assert _overrides(w, argv=("claude", "--setting-sources", "", "--settings", inline)) == ["apiKeyHelper（--settings）"]
    _json(iso / "managed-settings.json", {"apiKeyHelper": "echo x"})
    assert _overrides(w, argv=("claude", "--setting-sources", "")) == ["apiKeyHelper（管理の設定（managed-settings.json））"]


def test_examples_of_cancelling_and_choosing(tmp_path, iso):
    w = _world(tmp_path)
    user = w["shared"] / "settings.json"
    # 利用者の 1 を管理の "" が打ち消す
    _json(user, {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}})
    _json(iso / "managed-settings.json", {"env": {"CLAUDE_CODE_USE_BEDROCK": ""}})
    assert _overrides(w) == []
    # --settings の 1 が利用者の 0 の上に来る
    (iso / "managed-settings.json").unlink()
    _json(user, {"env": {"CLAUDE_CODE_USE_BEDROCK": "0"}})
    inline = json.dumps({"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}})
    assert _overrides(w, argv=("claude", "--settings", inline)) == ["CLAUDE_CODE_USE_BEDROCK（--settings）"]
    # サーバーから取った写しがポリシーの項目を持てば、managed-settings.d の apiKeyHelper は採らない
    _json(user, {})
    _json(w["acc"] / "remote-settings.json", {"env": {"FOO": "1"}})
    _json(iso / "managed-settings.d" / "20-x.json", {"apiKeyHelper": "echo x"})
    assert _overrides(w) == []
    # 写しに無い env の変数はファイルから埋める
    _json(iso / "managed-settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    assert _overrides(w) == ["ANTHROPIC_API_KEY（管理の設定（managed-settings.json））"]


def test_project_env_does_not_carry_the_ignored_variables(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["cwd"] / ".claude" / "settings.json", {"env": {"CLAUDE_CODE_OAUTH_TOKEN": KEY}})
    assert _overrides(w) == []
    _json(w["shared"] / "settings.json", {"env": {"CLAUDE_CODE_OAUTH_TOKEN": KEY}})
    assert _overrides(w) == ["CLAUDE_CODE_OAUTH_TOKEN（利用者の設定）"]


def test_managed_d_files_override_in_name_order(tmp_path, iso):
    w = _world(tmp_path)
    _json(iso / "managed-settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://proxy.example.com"}})
    _json(iso / "managed-settings.d" / "10-a.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    _json(iso / "managed-settings.d" / "20-b.json", {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}})
    assert _overrides(w) == []


def test_oidc_federation_profile_overrides(tmp_path, iso):
    w = _world(tmp_path)
    conf = Path.home() / ".config" / "anthropic"
    _json(conf / "configs" / "default.json", {"authentication": {"type": "user_oauth"}})
    assert _overrides(w) == []
    _json(conf / "configs" / "fed.json", {"authentication": {"type": "oidc_federation"}})
    (conf / "active_config").write_text("fed\n")
    assert _overrides(w) == ["oidc_federation（Anthropic のプロファイル）"]
    both = _overrides(w, env={"ANTHROPIC_FEDERATION_RULE_ID": "r", "ANTHROPIC_ORGANIZATION_ID": "o"})
    assert set(both) == {
        "oidc_federation（Anthropic のプロファイル）",
        "ANTHROPIC_FEDERATION_RULE_ID・ANTHROPIC_ORGANIZATION_ID（子の環境）",
    }


@pytest.mark.parametrize(
    "setup, argv, item",
    [
        ("managed_broken", ("claude",), "管理の設定（managed-settings.json）"),
        ("none", ("claude", "--settings"), "--settings"),
        ("none", ("claude", "--setting-sources"), "--setting-sources"),
        ("settings_missing", ("claude", "--settings", "nothing.json"), "--settings"),
    ],
)
def test_unreadable_sources_are_unresolved(tmp_path, iso, setup, argv, item):
    w = _world(tmp_path)
    if setup == "managed_broken":
        (iso / "managed-settings.json").write_text("{broken")
    resolved = ad.effective_env({}, w["acc"], w["cwd"], list(argv), w["shared"] / "settings.json")
    assert resolved.unresolved == [item]
    assert _check(w, argv=argv) == "auth_unresolved"


def test_effective_env_stamps_missing_sources(tmp_path, iso):
    w = _world(tmp_path)
    resolved = ad.effective_env({}, w["acc"], w["cwd"], ["claude"], w["shared"] / "settings.json")
    # 探して無かったファイルも None で持ち、managed-settings.d が無ければ親の stat を持つ
    assert resolved.stamps[str(w["cwd"] / ".claude" / "settings.local.json")] is None
    assert resolved.stamps[str(iso / "managed-settings.json")] is None
    assert resolved.stamps[str(iso)] is not None and str(iso / "managed-settings.d") not in resolved.stamps
    assert resolved.helper is None and resolved.helper_origin is None


def test_effective_env_keeps_the_helper_value_apart(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": f"echo {KEY}"})
    resolved = ad.effective_env({}, w["acc"], w["cwd"], ["claude"], w["shared"] / "settings.json")
    assert resolved.helper == f"echo {KEY}" and resolved.helper_origin == "利用者の設定"
    assert KEY not in json.dumps(ad.auth_overrides({}, w["acc"], w["cwd"], ["claude"], w["shared"] / "settings.json"), ensure_ascii=False)


# ---------------------------------------------------------------- 値を出さない（3604:12・I5）


@pytest.mark.parametrize("breaks", [_no_cred, _cred_broken, _no_access, _no_refresh, _config_broken, _other_person, _legacy])
def test_3604_12_i5_reasons_carry_no_secret_and_leave_the_shared_credentials(tmp_path, iso, breaks):
    w = _world(tmp_path)
    shared_cred = _stamp(w["shared"] / reg.CRED_FILE)
    breaks(w["acc"])
    got = _check(w, env={"ANTHROPIC_API_KEY": KEY})
    assert isinstance(got, str) and got in ad.REASONS
    text = got + ad.REASONS[got] + json.dumps(_overrides(w, env={"ANTHROPIC_API_KEY": KEY}), ensure_ascii=False)
    assert not any(s in text for s in SECRETS)
    assert _stamp(w["shared"] / reg.CRED_FILE) == shared_cred


def test_i5_reason_texts_carry_no_values():
    for code, text in ad.REASONS.items():
        assert code and text and not any(s in text for s in SECRETS)


# ---------------------------------------------------------------- レビューの指摘（PR 4027 の r1）


@pytest.mark.parametrize(
    "argv",
    [
        ("claude", "-p", "--", "--setting-sources=", "--settings={}"),
        ("claude", "--append-system-prompt", "--setting-sources=", "-p"),
        ("claude", "--settings", "--setting-sources="),
    ],
)
def test_r1_1_options_after_the_end_or_as_values_are_not_read(tmp_path, iso, argv):
    # `--` の後と、値を取るオプションの値は、利用者の設定を外す指定として読まない
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    got = _overrides(w, argv=argv)
    assert "ANTHROPIC_BASE_URL（利用者の設定）" in got


def test_r1_1_an_unknown_option_before_a_read_flag_is_unresolved(tmp_path, iso):
    w = _world(tmp_path)
    assert _check(w, argv=("claude", "--hidden-option", "--setting-sources=")) == "auth_unresolved"


def test_r1_2_control_keys_alone_do_not_select_the_remote_source(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["acc"] / "remote-settings.json", {"managedSourcesBehavior": "first-wins"})
    _json(iso / "managed-settings.json", {"apiKeyHelper": "echo x"})
    assert _overrides(w) == ["apiKeyHelper（管理の設定（managed-settings.json））"]


def test_r1_3_a_selected_policy_helper_is_unresolved(tmp_path, iso):
    w = _world(tmp_path)
    _json(iso / "managed-settings.json", {"policyHelper": {"path": "/usr/local/bin/p"}})
    assert _check(w) == "auth_unresolved"
    # サーバーから取った写しがポリシーを持っても、起動時の取得で消えたと分かれば helper が走る（r2-3）
    _json(w["acc"] / "remote-settings.json", {"model": "x"})
    assert _check(w) == "auth_unresolved"


def test_r1_4_hidden_managed_files_are_not_read(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    _json(iso / "managed-settings.d" / ".draft.json", {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}})
    assert _overrides(w) == ["ANTHROPIC_BASE_URL（利用者の設定）"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root は権限で列挙を止められない")
def test_r1_5_an_unlistable_managed_dir_is_unresolved(tmp_path, iso):
    w = _world(tmp_path)
    d = iso / "managed-settings.d"
    d.mkdir()
    d.chmod(0o300)
    try:
        assert _check(w) == "auth_unresolved"
    finally:
        d.chmod(0o700)


def test_r1_6_withheld_remote_env_does_not_cancel_a_lower_value(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    _json(w["acc"] / "remote-settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}})
    # 起動の時点では写しの値が保留され、利用者の設定のゲートウェイへ送る
    assert _overrides(w) == ["ANTHROPIC_BASE_URL（利用者の設定）"]
    # 起動の時点にだけ読む選び手は、写しの値で打ち消されない
    _json(w["shared"] / "settings.json", {"env": {"ANTHROPIC_PROFILE": "p"}})
    _json(w["acc"] / "remote-settings.json", {"env": {"ANTHROPIC_PROFILE": ""}})
    assert _overrides(w) == ["ANTHROPIC_PROFILE（利用者の設定）"]


def test_r1_7_a_relative_anthropic_config_dir_follows_the_child_cwd(tmp_path, iso, monkeypatch):
    w = _world(tmp_path)
    _json(w["cwd"] / "anth" / "configs" / "default.json", {"authentication": {"type": "oidc_federation"}})
    monkeypatch.chdir(tmp_path)
    assert "oidc_federation（Anthropic のプロファイル）" in _overrides(w, env={"ANTHROPIC_CONFIG_DIR": "anth"})


def test_r1_8_a_stored_api_key_skips_the_account(tmp_path, iso):
    w = _world(tmp_path)
    _json(
        w["acc"] / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "work1@example.com", "organizationUuid": "o1"}, "primaryApiKey": KEY}
    )
    got = _check(w)
    assert got == "auth_overridden" and got.items == ["primaryApiKey（アカウントの .claude.json）"]
    assert KEY not in repr(got) + json.dumps(got.items)


def test_r1_9_resolved_repr_hides_the_env_and_helper(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": f"echo {KEY}"})
    r = ad.effective_env({"OTHER_SECRET": ACCESS}, w["acc"], w["cwd"], ["claude"], w["shared"] / "settings.json")
    text = repr(r) + repr(ad.Checked(None, r))
    assert KEY not in text and ACCESS not in text


def test_r1_10_the_child_path_stamp_follows_a_retarget(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {})
    got = _check(w)
    ad.link_shared(w["shared"], w["acc"])
    child = str(w["acc"] / "settings.json")
    # link_shared の後の見回りでは変わらない
    assert ad._stamp(w["acc"] / "settings.json") == got.resolved.stamps[child]
    other = _json(tmp_path / "other.json", {"apiKeyHelper": "echo x"})
    os.unlink(w["acc"] / "settings.json")
    (w["acc"] / "settings.json").symlink_to(other)
    assert ad._stamp(w["acc"] / "settings.json") != got.resolved.stamps[child]
    # 付け替えの後は子が開くパスから判定する
    assert ad.auth_overrides({}, w["acc"], w["cwd"], ["claude"]) == ["apiKeyHelper（利用者の設定）"]


def test_r1_11_a_refusal_carries_the_items_and_stamps(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": "echo x"})
    got = _check(w)
    assert isinstance(got, ad.Refused) and got == "auth_overridden"
    assert got.items == ["apiKeyHelper（利用者の設定）"] and str(w["shared"] / "settings.json") in got.stamps
    _no_cred(w["acc"])
    early = _check(w)
    assert early == "no_credentials" and str(w["acc"] / reg.CRED_FILE) in early.stamps


def test_r1_12_a_lost_creation_race_reuses_the_shared_items(tmp_path, monkeypatch):
    w = _world(tmp_path)
    real_lexists = os.path.lexists
    monkeypatch.setattr(ad.os.path, "lexists", lambda p: False if Path(p).parent == w["shared"] else real_lexists(p))
    (w["shared"] / "projects").mkdir()
    _json(w["shared"] / "settings.json", {"model": "x"})
    ad._ensure_shared(w["shared"])
    assert json.loads((w["shared"] / "settings.json").read_text()) == {"model": "x"}


def test_r1_13_relink_keeps_an_item_with_the_old_temporary_name(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "skills").mkdir()
    (w["acc"] / "skills").symlink_to(tmp_path)
    mine = w["acc"] / f"skills.{os.getpid()}.link"
    mine.write_text("mine")
    ad.link_shared(w["shared"], w["acc"])
    assert mine.read_text() == "mine" and Path(os.readlink(w["acc"] / "skills")) == w["shared"] / "skills"


def test_r1_14_an_alias_to_credentials_is_not_linked(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "alias.json").symlink_to(w["shared"] / reg.CRED_FILE)
    (w["shared"] / "other").symlink_to(w["store"] / "work1")
    links = ad.link_shared(w["shared"], w["acc"])
    assert set(links.refused) == {"alias.json", "other"}
    assert not os.path.lexists(w["acc"] / "alias.json") and not os.path.lexists(w["acc"] / "other")


@pytest.mark.parametrize(
    "argv",
    [
        ("claude", "-pc", "--settings", '{"apiKeyHelper": "echo x"}'),
        ("claude", "-pn", "x", "--settings", '{"apiKeyHelper": "echo x"}'),
        ("claude", "-pnx", "--settings", '{"apiKeyHelper": "echo x"}'),
    ],
)
def test_r2_1_combined_short_options_are_split_like_the_cli(tmp_path, iso, argv):
    w = _world(tmp_path)
    got = _check(w, argv=argv)
    assert got == "auth_overridden" and got.items == ["apiKeyHelper（--settings）"]


def test_r2_1_an_attached_short_value_is_not_read_as_an_option(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    # `-n--setting-sources=` は名前の値で、利用者の設定を外さない
    assert _overrides(w, argv=("claude", "-pn--setting-sources=")) == ["ANTHROPIC_BASE_URL（利用者の設定）"]


@pytest.mark.parametrize("argv, env", [(("claude", "-p", "--bare"), {}), (("claude",), {"CLAUDE_CODE_SIMPLE": "1"})])
def test_r2_2_bare_mode_skips_the_account(tmp_path, iso, argv, env):
    w = _world(tmp_path)
    got = _check(w, env=env, argv=argv)
    assert got == "auth_overridden" and got.items in (["--bare（起動引数）"], ["CLAUDE_CODE_SIMPLE（子の環境）"])


def test_r2_2_restricted_mode_ignores_user_project_and_local_settings(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["shared"] / "settings.json", {"apiKeyHelper": "echo x"})
    _json(w["cwd"] / ".claude" / "settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}})
    assert isinstance(_check(w, argv=("claude", "--restricted")), ad.Checked)
    # 管理の設定と --settings は効く
    got = _check(w, argv=("claude", "--restricted", "--settings", '{"apiKeyHelper": "echo x"}'))
    assert got == "auth_overridden" and got.items == ["apiKeyHelper（--settings）"]


@pytest.mark.parametrize("k", ["ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR"])
def test_r2_4_startup_only_selectors_are_not_taken_from_the_remote_cache(tmp_path, iso, k):
    w = _world(tmp_path)
    _json(w["acc"] / "remote-settings.json", {"env": {k: "p"}})
    assert isinstance(_check(w), ad.Checked)


def test_r2_4_federation_selectors_are_not_taken_from_the_remote_cache(tmp_path, iso):
    w = _world(tmp_path)
    _json(w["acc"] / "remote-settings.json", {"env": {"ANTHROPIC_FEDERATION_RULE_ID": "r", "ANTHROPIC_ORGANIZATION_ID": "o"}})
    assert isinstance(_check(w), ad.Checked)


def test_r2_5_a_refused_shared_settings_is_absent_for_the_check(tmp_path, iso):
    w = _world(tmp_path)
    other = _json(w["store"] / "work2" / "settings.json", {"apiKeyHelper": "echo x"})
    (w["shared"] / "settings.json").symlink_to(other)
    assert ad.planned_links(w["shared"], w["acc"])["settings.json"] is None
    got = _check(w)
    assert isinstance(got, ad.Checked) and got.resolved.stamps[str(w["acc"] / "settings.json")] is None
    links = ad.link_shared(w["shared"], w["acc"])
    assert "settings.json" in links.refused and not os.path.lexists(w["acc"] / "settings.json")


def test_r2_6_a_credentials_symlink_in_the_middle_is_refused(tmp_path):
    w = _world(tmp_path)
    secret = _json(tmp_path / "anything.bin", {"claudeAiOauth": {"accessToken": ACCESS}})
    (w["shared"] / reg.CRED_FILE).unlink()
    (w["shared"] / reg.CRED_FILE).symlink_to(secret)
    (w["shared"] / "alias.json").symlink_to(w["shared"] / reg.CRED_FILE)
    assert "alias.json" in ad.link_shared(w["shared"], w["acc"]).refused


def test_r2_6_a_real_dir_with_a_symlink_to_another_account_is_refused(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "skills" / "deep").mkdir(parents=True)
    (w["shared"] / "skills" / "deep" / "x").symlink_to(w["store"] / "work1" / reg.CRED_FILE)
    (w["shared"] / "agents").mkdir()
    (w["shared"] / "agents" / "ok.md").write_text("x")
    links = ad.link_shared(w["shared"], w["acc"])
    assert "skills" in links.refused and "agents" not in links.refused
    assert not os.path.lexists(w["acc"] / "skills") and (w["acc"] / "agents").is_symlink()


def test_r2_9_each_source_is_stat_once(tmp_path, iso, monkeypatch):
    w = _world(tmp_path)
    path = _json(w["cwd"] / ".claude" / "settings.json", {})
    calls = []
    real = os.stat
    monkeypatch.setattr(ad.os, "stat", lambda p, *a, **k: (calls.append(str(p)), real(p, *a, **k))[1])
    ad._layer(path, "x", {}, [])
    assert calls == [str(path)]


def test_r3_1_a_symlinked_parent_component_to_another_account_is_refused(tmp_path):
    w = _world(tmp_path)
    _json(w["store"] / "work2" / "settings.json", {"apiKeyHelper": "echo x"})
    (tmp_path / "account-alias").symlink_to(w["store"] / "work2")
    (w["shared"] / "alias").symlink_to(tmp_path / "account-alias" / "settings.json")
    # `..` は symlink を解いた後の親へ上がる（hop/.. は work2 で、共有側ではない）
    (w["store"] / "work2" / "sub").mkdir()
    (w["shared"] / "hop").symlink_to(tmp_path / "account-alias" / "sub")
    (w["shared"] / "up").symlink_to("hop/../settings.json")
    links = ad.link_shared(w["shared"], w["acc"])
    assert {"alias", "hop", "up"} <= set(links.refused)
    assert not any(os.path.lexists(w["acc"] / n) for n in ("alias", "hop", "up"))


def test_r3_2_an_alias_to_the_shared_dir_or_a_hard_link_is_refused(tmp_path):
    w = _world(tmp_path)
    (w["shared"] / "alias").symlink_to(w["shared"])
    os.link(w["shared"] / reg.CRED_FILE, w["shared"] / "copy.json")
    (w["shared"] / "skills" / "deep").mkdir(parents=True)
    os.link(w["store"] / "work1" / reg.CRED_FILE, w["shared"] / "skills" / "deep" / "x")
    (w["shared"] / "agents" / "old").mkdir(parents=True)
    (w["shared"] / "agents" / "old" / reg.CRED_FILE).write_text("{}")
    (w["shared"] / "plugins").mkdir()
    (w["shared"] / "plugins" / "ok.md").write_text("x")
    links = ad.link_shared(w["shared"], w["acc"])
    assert {"alias", "copy.json", "skills", "agents"} <= set(links.refused) and "plugins" not in links.refused
    assert not os.path.lexists(w["acc"] / "alias") and (w["acc"] / "plugins").is_symlink()


def test_r3_3_a_kept_account_symlink_to_a_refused_target_stops_the_launch(tmp_path, iso):
    w = _world(tmp_path)
    other = _json(w["store"] / "work2" / "settings.json", {})
    (w["shared"] / "settings.json").symlink_to(other)
    mine = w["acc"] / "settings.json"
    mine.symlink_to(os.path.relpath(w["shared"] / "settings.json", w["acc"]))
    got = _check(w)
    assert got == ad.NOT_LOCAL and got.items == ["settings.json（アカウントの symlink）"]
    links = ad.link_shared(w["shared"], w["acc"])
    assert links.unsafe == ["settings.json"] and mine.is_symlink()
    # 自分の設定ディレクトリの中を指す利用者の symlink は届かない
    mine.unlink()
    _json(w["acc"] / "my-settings.json", {})
    mine.symlink_to("my-settings.json")
    assert isinstance(_check(w), ad.Checked) and ad.link_shared(w["shared"], w["acc"]).unsafe == []


def _scanned(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = os.scandir
    monkeypatch.setattr(ad.os, "scandir", lambda p=".": (calls.append(str(p)), real(p))[1])
    return calls


def test_r3_4_check_account_does_not_walk_the_shared_history(tmp_path, iso, monkeypatch):
    w = _world(tmp_path)
    (w["shared"] / "projects" / "-work").mkdir(parents=True)
    (w["shared"] / "projects" / "-work" / "s1.jsonl").write_text("{}\n")
    calls = _scanned(monkeypatch)
    assert isinstance(_check(w), ad.Checked)
    assert not [c for c in calls if "projects" in c]


def test_r3_6_the_walk_stops_at_the_budget_and_the_depth(tmp_path, monkeypatch):
    w = _world(tmp_path)
    deep = w["shared"] / "skills"
    for i in range(ad._DEPTH + 2):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    calls = _scanned(monkeypatch)
    ad.link_shared(w["shared"], w["acc"])
    assert len([c for c in calls if "/skills" in c]) == ad._DEPTH
    monkeypatch.setattr(ad, "_BUDGET", 5)
    hist = w["shared"] / "projects" / "-work"
    hist.mkdir(parents=True)
    for i in range(50):
        (hist / f"s{i}").mkdir()
    calls.clear()
    ad.link_shared(w["shared"], w["acc"])
    assert len([c for c in calls if "/projects" in c]) <= 5
