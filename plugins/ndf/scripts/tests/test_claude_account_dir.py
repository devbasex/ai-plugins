"""アカウントの設定ディレクトリの共有物 `lib/claude_account_dir.py` のテスト（#1576）。"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import claude_account_dir as ad  # noqa: E402


@pytest.fixture()
def dirs(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    account = tmp_path / "acc"
    account.mkdir(mode=0o700)
    return shared, account


def _w(path: Path, data) -> None:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    os.chmod(path, 0o600)


def _r(path: Path):
    return json.loads(path.read_text())


# ---------------------------------------------------------------- symlink


def test_link_shared_links_everything_but_local(dirs):
    shared, account = dirs
    for n in ("plugins", "agents", "ndf"):
        (shared / n).mkdir()
    for n in (".credentials.json", ".claude.json", "settings.json", "policy-limits.json", ".ndf-retention-checked"):
        _w(shared / n, {})
    links = ad.link_shared(str(shared), str(account))
    linked = {p.name for p in account.iterdir() if p.is_symlink()}
    assert linked == {"plugins", "agents", "ndf", "projects", "settings.json", ".ndf-retention-checked"}
    for n in linked:
        assert os.readlink(account / n) == str(shared / n)
    assert links.added == 6 and links.skipped == [] and links.projects_shared
    assert not (account / ".credentials.json").exists()  # 共有の認証ファイルは symlink にしない（I3）


def test_link_shared_creates_projects_and_settings_on_shared(dirs):
    shared, account = dirs
    ad.link_shared(str(shared), str(account))
    assert (shared / "projects").is_dir() and _r(shared / "settings.json") == {}
    assert stat.S_IMODE((shared / "settings.json").stat().st_mode) == 0o600


def test_link_shared_second_run_adds_nothing(dirs):
    shared, account = dirs
    (shared / "plugins").mkdir()
    ad.link_shared(str(shared), str(account))
    before = {p.name: os.lstat(p).st_mtime_ns for p in account.iterdir()}
    again = ad.link_shared(str(shared), str(account))
    assert again.added == 0 and {p.name: os.lstat(p).st_mtime_ns for p in account.iterdir()} == before


def test_link_shared_relinks_wrong_target_and_keeps_entities(dirs, tmp_path):
    shared, account = dirs
    (shared / "plugins").mkdir()
    (shared / "agents").mkdir()
    other = tmp_path / "other"
    other.mkdir()
    os.symlink(other, account / "plugins")
    (account / "agents").mkdir()
    (account / "agents" / "mine.md").write_text("x")
    (account / "plans").mkdir()  # 共有側に無い名前の実体
    links = ad.link_shared(str(shared), str(account))
    assert os.readlink(account / "plugins") == str(shared / "plugins")
    assert (account / "agents" / "mine.md").read_text() == "x"
    assert links.skipped == ["agents"] and links.local_only == ["plans"]
    assert (account / "plans").is_dir()


def test_local_only_excludes_local_items_and_links(dirs):
    shared, account = dirs
    _w(account / ".credentials.json", {})
    _w(account / "account.json", {})
    (account / "backups").mkdir()
    _w(account / "CLAUDE.md", "x")
    links = ad.link_shared(str(shared), str(account))
    assert links.local_only == ["CLAUDE.md"]


def test_projects_entity_is_not_shared(dirs):
    shared, account = dirs
    (account / "projects").mkdir()
    links = ad.link_shared(str(shared), str(account))
    assert not links.projects_shared and links.skipped == ["projects"]


def test_unlink_shared_keeps_targets(dirs):
    shared, account = dirs
    (shared / "plugins").mkdir()
    (shared / "plugins" / "p").write_text("x")
    ad.link_shared(str(shared), str(account))
    _w(account / ".credentials.json", {})
    ad.unlink_shared(str(account))
    assert not any(p.is_symlink() for p in account.iterdir())
    assert (shared / "plugins" / "p").read_text() == "x" and (account / ".credentials.json").exists()


# ---------------------------------------------------------------- 読めるか・識別


@pytest.mark.parametrize("content", ['{"projects": {', ""])
def test_unreadable_account_config_drops_base_and_keeps_file(dirs, content):
    _, account = dirs
    _w(account / ".claude.json", content)
    _w(account / ad.BASE_FILE, {"version": 1, "projects": {}, "mcpServers": {}})
    assert not ad.readable(str(account))
    assert (account / ".claude.json").read_text() == content and not (account / ad.BASE_FILE).exists()


def test_missing_account_config_is_readable(dirs):
    assert ad.readable(str(dirs[1]))


def test_identity(dirs):
    _, account = dirs
    assert ad.identity(str(account)) is None
    _w(account / ".claude.json", {"oauthAccount": {"emailAddress": "a@example.com", "organizationUuid": "o1"}})
    assert ad.identity(str(account)) == ("a@example.com", "o1")


# ---------------------------------------------------------------- 同期


def _setup(shared_file: Path, account: Path, s: dict, a: dict | None, b: dict | None):
    _w(shared_file, s)
    if a is not None:
        _w(account / ".claude.json", a)
    if b is not None:
        _w(account / ad.BASE_FILE, {"version": 1, **b})


def test_first_sync_copies_shared_part_and_onboarding_only(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    s = {"projects": {"/p": {"hasTrustDialogAccepted": True}}, "mcpServers": {"m": {"command": "x"}}, "oauthAccount": {"emailAddress": "s@x"},
         "hasCompletedOnboarding": True, "lastOnboardingVersion": "2.1.0", "numStartups": 9}
    a = {"oauthAccount": {"emailAddress": "a@x"}, "userID": "u1", "numStartups": 1}
    _setup(sf, account, s, a, None)
    assert ad.sync_config(str(sf), str(account)) is None
    got = _r(account / ".claude.json")
    assert got["projects"] == s["projects"] and got["mcpServers"] == s["mcpServers"]
    assert got["oauthAccount"] == a["oauthAccount"] and got["numStartups"] == 1  # oauthAccount を写さない（I10）
    assert got["hasCompletedOnboarding"] is True and got["lastOnboardingVersion"] == "2.1.0"
    assert _r(sf) == s
    base = _r(account / ad.BASE_FILE)
    assert base["userID"] == "u1" and base["projects"] == s["projects"]
    assert stat.S_IMODE((account / ".claude.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("s_val", "a_val", "want"),
    [
        (2, 1, 2),  # 共有側だけ変わった
        (2, 3, 2),  # 両側で変わった → 共有側
        (1, 3, 3),  # アカウント側だけ変わった
        (1, 1, 1),  # どちらも変わっていない
        (None, 1, None),  # 共有側で消した
        (1, None, None),  # アカウント側で消した
    ],
)
def test_sync_table(dirs, s_val, a_val, want):
    shared, account = dirs
    sf = shared / ".claude.json"

    def proj(v):
        return {"/p": {"k": v, "other": 0}} if v is not None else {"/p": {"other": 0}}

    _setup(sf, account, {"projects": proj(s_val), "keep": "S"}, {"projects": proj(a_val), "userID": "u"}, {"projects": proj(1), "mcpServers": {}, "userID": "u"})
    assert ad.sync_config(str(sf), str(account)) is None
    for got in (_r(sf), _r(account / ".claude.json")):
        assert got["projects"]["/p"].get("k") == want and got["projects"]["/p"]["other"] == 0
    assert _r(sf)["keep"] == "S"


def test_session_added_project_is_written_back(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _setup(sf, account, {"projects": {"/a": {"t": 1}}}, {"projects": {"/a": {"t": 1}}, "userID": "u"}, {"projects": {"/a": {"t": 1}}, "userID": "u"})
    a = _r(account / ".claude.json")
    a["projects"]["/new"] = {"hasTrustDialogAccepted": True}
    _w(account / ".claude.json", a)
    assert ad.sync_config(str(sf), str(account)) is None
    assert _r(sf)["projects"]["/new"] == {"hasTrustDialogAccepted": True}


def test_shared_symlink_stays_symlink(dirs, tmp_path):
    shared, account = dirs
    real = tmp_path / "group.json"
    _w(real, {"projects": {"/a": {"t": 1}}, "x": 1})
    os.chmod(real, 0o640)
    sf = shared / ".claude.json"
    os.symlink(real, sf)
    _w(account / ".claude.json", {"projects": {"/a": {"t": 1}, "/b": {"t": 2}}, "userID": "u"})
    _w(account / ad.BASE_FILE, {"version": 1, "projects": {"/a": {"t": 1}}, "mcpServers": {}, "userID": "u"})
    assert ad.sync_config(str(sf), str(account)) is None
    assert sf.is_symlink() and _r(real) == {"projects": {"/a": {"t": 1}, "/b": {"t": 2}}, "x": 1}
    assert stat.S_IMODE(real.stat().st_mode) == 0o640


SHARED = {"projects": {"/a": {"t": 1}, "/b": {"t": 2}, "/c": {"t": 3}}, "mcpServers": {"m": {"command": "x"}}}


def _base(uid="u"):
    b = {"projects": SHARED["projects"], "mcpServers": SHARED["mcpServers"]}
    if uid is not None:
        b["userID"] = uid
    return b


@pytest.mark.parametrize(
    "account_value",
    [
        None,  # ファイルが無い
        {"projects": {}, "mcpServers": SHARED["mcpServers"], "userID": "u"},  # projects だけ空
        {"projects": {"/cwd": {"hasTrustDialogAccepted": False}}, "userID": "other"},  # 本体が初期化した後の形
        {"projects": {"/a": {"t": 1}}, "mcpServers": {}},  # userID が消えた
    ],
)
def test_lost_account_side_never_shrinks_shared(dirs, account_value):
    shared, account = dirs
    sf = shared / ".claude.json"
    _setup(sf, account, SHARED, account_value, _base())
    assert ad.sync_config(str(sf), str(account)) is None
    got = _r(sf)
    assert all(p in got["projects"] for p in SHARED["projects"]) and got["mcpServers"] == SHARED["mcpServers"]
    assert {k: v for k, v in _r(account / ".claude.json")["projects"].items() if k in SHARED["projects"]} == SHARED["projects"]


def test_base_without_user_id_is_not_used(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _setup(sf, account, SHARED, {"projects": {"/a": {"t": 1}}, "userID": "u"}, _base(None))
    assert ad.sync_config(str(sf), str(account)) is None
    assert _r(sf)["projects"] == SHARED["projects"]
    assert _r(account / ad.BASE_FILE)["userID"] == "u"


def test_new_user_id_is_recorded(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _setup(sf, account, SHARED, {"projects": {"/cwd": {}}, "userID": "new"}, _base())
    ad.sync_config(str(sf), str(account))
    assert _r(account / ad.BASE_FILE)["userID"] == "new"


def test_same_user_id_added_leaf_goes_to_shared(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    a = {"projects": {**SHARED["projects"], "/d": {"t": 4}}, "mcpServers": SHARED["mcpServers"], "userID": "u"}
    _setup(sf, account, SHARED, a, _base())
    ad.sync_config(str(sf), str(account))
    assert _r(sf)["projects"]["/d"] == {"t": 4}


def test_unreadable_then_restored_backup_never_shrinks_shared(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _setup(sf, account, SHARED, None, _base())
    _w(account / ".claude.json", '{"projects": ')
    s_before = (shared / ".claude.json").read_bytes()
    assert ad.sync_config(str(sf), str(account)) == "account_unreadable"
    assert (shared / ".claude.json").read_bytes() == s_before and (account / ".claude.json").read_text() == '{"projects": '
    assert not (account / ad.BASE_FILE).exists()
    _w(account / ".claude.json", {"projects": {"/a": {"t": 1}}, "userID": "u"})  # 退避から戻した形
    assert ad.sync_config(str(sf), str(account)) is None
    assert _r(sf)["projects"] == SHARED["projects"] and _r(account / ".claude.json")["projects"] == SHARED["projects"]


def test_unreadable_shared_writes_nothing(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _w(sf, "{")
    _w(account / ".claude.json", {"userID": "u"})
    assert ad.sync_config(str(sf), str(account)) == "shared_unreadable"
    assert sf.read_text() == "{" and _r(account / ".claude.json") == {"userID": "u"}


def test_busy_lock_writes_nothing(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _w(sf, SHARED)
    os.mkdir(str(sf) + ".lock")
    assert ad.sync_config(str(sf), str(account)) == "lock_busy"
    assert not (account / ".claude.json").exists() and not (account / ad.BASE_FILE).exists()
    assert not os.path.exists(str(account / ".claude.json") + ".lock")


def test_stale_lock_is_taken(dirs):
    shared, account = dirs
    sf = shared / ".claude.json"
    _w(sf, SHARED)
    lock = str(sf) + ".lock"
    os.mkdir(lock)
    os.utime(lock, (0, 0))
    assert ad.sync_config(str(sf), str(account)) is None and not os.path.exists(lock)
    assert _r(account / ".claude.json")["projects"] == SHARED["projects"]
