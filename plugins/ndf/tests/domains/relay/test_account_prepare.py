"""起動の前の用意・書き戻し・使用の届け・関所・保留の更新（`account_dir.py` の U3 の範囲。#3604 #3864）。

設計書の「テスト設計」の 3604:6・3604:7（関数）・3604:12・I10 の行と、詳細設計書の「起動の前の用意」の
「単体テストの観点」の `prepare`・`settle`・`planned_links`・`ready`・登録のコマンドと使用の届けの行を縛る。
値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ndf.domains.relay import account_dir as ad
from ndf.domains.relay import account_registry as reg
from relay_children import _json

ACCESS = "fake-access-value-3604-u3"
REFRESH = "fake-refresh-value-3604-u3"
NEW_ACCESS = "fake-new-access-3604-u3"
NEW_REFRESH = "fake-new-refresh-3604-u3"
SECRETS = (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, "shared-access-u3", "shared-refresh-u3")


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    """管理の設定の保存先と HOME を一時ディレクトリへ替える（手元と CI の /etc・~ を読まない）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    etc = tmp_path / "etc" / "claude-code"
    etc.mkdir(parents=True)
    monkeypatch.setattr(ad, "MANAGED_FILES", (etc / "managed-settings.json",))
    monkeypatch.setattr(ad, "MANAGED_DIR", etc / "managed-settings.d")


def _read(path: Path):
    return json.loads(path.read_text())


def _world(tmp_path: Path, name: str = "work1") -> dict:
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    _json(shared / ".claude.json", {"projects": {"/p": {"allowedTools": ["Read"]}}, "mcpServers": {"m": "A"}})
    _json(shared / ".credentials.json", {"claudeAiOauth": {"accessToken": "shared-access-u3", "refreshToken": "shared-refresh-u3"}})
    store = shared / "ndf" / "accounts"
    acc = store / name
    acc.mkdir(parents=True)
    _json(acc / reg.ACCOUNT_FILE, {"name": name, "email": f"{name}@example.com", "needs_relogin": False, "generation": "g1"})
    _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": ACCESS, "refreshToken": REFRESH}, "other": 1})
    _json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": f"{name}@example.com"}, "userID": "u1"})
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    environ = {"CLAUDE_CONFIG_DIR": str(shared)}
    return {"shared": shared, "store": store, "acc": acc, "cwd": cwd, "environ": environ, "shared_file": shared / ".claude.json"}


def _launch(w: dict) -> ad.Launch:
    return ad.Launch({"CLAUDE_CONFIG_DIR": str(w["acc"])}, w["cwd"], ["claude"])


def _prepare(w: dict, name: str = "work1") -> ad.Prepared:
    return ad.prepare(w["store"], name, w["environ"], _launch(w))


def _leases(w: dict, name: str = "work1") -> dict[str, dict]:
    d = reg.lease_dir(w["store"], name)
    return {p.stem: _read(p) for p in d.glob("*.json")} if d.is_dir() else {}


def _stamp(path: Path) -> tuple:
    return path.read_bytes(), path.stat().st_mtime_ns


def _local(w: dict) -> ad.Local:
    got = ad.check_local(w["store"], w["acc"], w["shared_file"])
    assert not isinstance(got, str), got
    return got


def _no_secret(text: str) -> None:
    for s in SECRETS:
        assert s not in text


# ---------------------------------------------------------------- prepare（3604:6・3604:12）


def test_3604_6_prepare_links_syncs_and_writes_a_starting_lease(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w)
    assert got.ok and got.reason is None and got.config_dir == Path(os.path.abspath(w["store"] / "work1"))
    assert got.config_dir.is_absolute() and got.generation == "g1" and got.resolved is not None
    cfg = _read(w["acc"] / reg.CONFIG_FILE)
    assert cfg["projects"] == {"/p": {"allowedTools": ["Read"]}} and cfg["mcpServers"] == {"m": "A"}
    assert cfg["oauthAccount"] == {"emailAddress": "work1@example.com"}
    assert (w["acc"] / "projects").is_symlink() and got.links.projects_shared
    leases = _leases(w)
    assert list(leases) == [got.lease]
    assert leases[got.lease]["state"] == "starting" and leases[got.lease]["generation"] == "g1"
    assert leases[got.lease]["owner"]["pid"] == os.getpid() and leases[got.lease]["child"] is None
    assert ad.in_use(w["store"], "work1")
    # 2 回続けて打っても同じ形（届けは起動ごとに別）
    again = _prepare(w)
    assert again.ok and again.links.added == 0 and again.lease != got.lease and len(_leases(w)) == 2


@pytest.mark.parametrize(
    "reason, relogin",
    [
        ("no_refresh", True),
        ("identity_mismatch", True),
        ("legacy_config", True),
        ("no_credentials", False),
        ("account_unreadable", False),
    ],
)
def test_prepare_refuses_by_reason_and_marks_only_the_relogin_reasons(tmp_path, reason, relogin):
    w = _world(tmp_path)
    acc = w["acc"]
    if reason == "no_refresh":
        _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": ACCESS, "refreshToken": ""}})
    elif reason == "identity_mismatch":
        _json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "someone@example.com"}})
    elif reason == "legacy_config":
        _json(acc / ".config.json", {"oauthAccount": {"emailAddress": "work1@example.com"}})
    elif reason == "no_credentials":
        (acc / reg.CRED_FILE).unlink()
    else:
        (acc / reg.CONFIG_FILE).write_text("{")
    shared_cred = _stamp(w["shared"] / ".credentials.json")
    got = _prepare(w)
    assert not got.ok and got.reason == reason and got.lease is None and _leases(w) == {}
    assert reg.load(w["store"], "work1").needs_relogin is relogin
    assert _stamp(w["shared"] / ".credentials.json") == shared_cred
    _no_secret(ad.REASONS[reason] + repr(got))


def test_r2_1_prepare_refuses_a_fixed_target_left_by_an_interrupted_relogin(tmp_path, monkeypatch):
    """認証を置き換えた直後に登録し直しが止まると、旧い .claude.json が本人確認を通っても固定先の用意は起動を拒む。"""
    w = _world(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    _json(staging / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH}})
    _json(staging / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "other@example.com"}})
    real = os.replace

    def stop_after_credentials(src, dst):
        real(src, dst)
        if Path(dst).name == reg.CRED_FILE:
            raise OSError("中断")

    monkeypatch.setattr(reg.os, "replace", stop_after_credentials)
    with pytest.raises(OSError):
        reg.register(w["store"], "work1", staging, reg.Identity("other@example.com", "", ""), 1.0)
    monkeypatch.setattr(reg.os, "replace", real)
    assert _read(w["acc"] / reg.CRED_FILE)["claudeAiOauth"]["accessToken"] == NEW_ACCESS
    assert _read(w["acc"] / reg.CONFIG_FILE)["oauthAccount"]["emailAddress"] == "work1@example.com"
    got = _prepare(w)
    assert not got.ok and got.reason == "needs_relogin" and got.lease is None and _leases(w) == {}
    _no_secret(ad.REASONS["needs_relogin"] + repr(got))


def test_prepare_with_a_shared_dir_not_made_yet_makes_it_and_syncs(tmp_path):
    """共有の設定ディレクトリがまだ無い親の環境から起動しても、外さずに作ってから同期する。"""
    w = _world(tmp_path)
    fresh = tmp_path / "fresh-shared"
    w["environ"] = {"CLAUDE_CONFIG_DIR": str(fresh)}
    got = _prepare(w)
    assert got.ok and got.reason is None and got.sync is None, got
    assert (fresh / "projects").is_dir() and got.links.projects_shared
    _json(w["acc"] / reg.CONFIG_FILE, {**_read(w["acc"] / reg.CONFIG_FILE), "mcpServers": {"m": "mine"}})
    assert ad.settle(w["store"], "work1", w["environ"], got.generation, got.lease) is None
    assert _read(fresh / ".claude.json")["mcpServers"] == {"m": "mine"}


def test_prepare_refuses_a_real_projects_dir_and_keeps_it(tmp_path):
    w = _world(tmp_path)
    (w["acc"] / "projects").mkdir()
    (w["acc"] / "projects" / "keep.jsonl").write_text("x")
    got = _prepare(w)
    assert not got.ok and got.reason == "projects_not_shared" and _leases(w) == {}
    assert (w["acc"] / "projects" / "keep.jsonl").read_text() == "x"


def test_prepare_of_an_unregistered_name_is_refused(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w, "work9")
    assert not got.ok and got.reason == "not_registered"


def test_3604_12_prepare_and_settle_never_touch_the_shared_credentials_or_show_values(tmp_path):
    w = _world(tmp_path)
    shared_cred = _stamp(w["shared"] / ".credentials.json")
    got = _prepare(w)
    _json(w["acc"] / reg.CONFIG_FILE, {**_read(w["acc"] / reg.CONFIG_FILE), "mcpServers": {"m": "A", "n": "B"}})
    assert ad.settle(w["store"], "work1", w["environ"], got.generation, got.lease) is None
    assert _stamp(w["shared"] / ".credentials.json") == shared_cred
    assert not (w["acc"] / reg.CRED_FILE).is_symlink()
    _no_secret(repr(got))
    for path in (w["acc"] / reg.BASE_FILE, w["shared_file"], w["acc"] / reg.CONFIG_FILE):
        _no_secret(path.read_text())
    for text in ad.REASONS.values():
        _no_secret(text)


def test_prepare_waits_while_a_refresh_lock_exists_and_starts_with_the_new_credentials(tmp_path):
    w = _world(tmp_path)
    lock = w["acc"] / ad.REFRESH_LOCK
    lock.mkdir()

    def refresh() -> None:
        time.sleep(0.3)
        _json(w["acc"] / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH}})
        lock.rmdir()

    t = threading.Thread(target=refresh)
    start = time.monotonic()
    t.start()
    got = _prepare(w)
    t.join()
    assert got.ok and time.monotonic() - start >= 0.3
    assert _read(w["acc"] / reg.CRED_FILE)["claudeAiOauth"]["accessToken"] == NEW_ACCESS


# ---------------------------------------------------------------- 保留の更新・認証ファイルの書き込み


def test_prepare_applies_a_pending_refresh_before_checking(tmp_path):
    w = _world(tmp_path)
    ad.write_pending(_local(w), "g1", (ACCESS, REFRESH), {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH})
    assert (w["acc"] / reg.PENDING_FILE).is_file()
    got = _prepare(w)
    assert got.ok
    cred = _read(w["acc"] / reg.CRED_FILE)
    assert cred["claudeAiOauth"] == {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH} and cred["other"] == 1
    assert not (w["acc"] / reg.PENDING_FILE).exists()


@pytest.mark.parametrize("change", ["generation", "base"])
def test_a_pending_refresh_of_another_generation_or_pair_is_discarded(tmp_path, change):
    w = _world(tmp_path)
    gen, base = ("g0", (ACCESS, REFRESH)) if change == "generation" else ("g1", ("other-a", "other-r"))
    ad.write_pending(_local(w), gen, base, {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH})
    before = _stamp(w["acc"] / reg.CRED_FILE)
    assert ad.apply_pending("g1", _local(w)) == "none"
    assert _stamp(w["acc"] / reg.CRED_FILE) == before and not (w["acc"] / reg.PENDING_FILE).exists()


def test_a_pending_refresh_is_kept_while_the_refresh_lock_is_held(tmp_path):
    w = _world(tmp_path)
    ad.write_pending(_local(w), "g1", (ACCESS, REFRESH), {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH})
    local = _local(w)
    with ad.refresh_lock(w["acc"]) as got:
        assert got
        assert ad.apply_pending("g1", local) == "kept"
    assert (w["acc"] / reg.PENDING_FILE).is_file()
    assert _read(w["acc"] / reg.CRED_FILE)["claudeAiOauth"]["accessToken"] == ACCESS
    assert ad.apply_pending("g1", local) == "written"


def test_apply_pending_without_a_pending_file_is_none(tmp_path):
    w = _world(tmp_path)
    assert ad.apply_pending("g1", _local(w)) == "none"


def test_refresh_lock_is_taken_without_waiting_and_not_stolen(tmp_path):
    w = _world(tmp_path)
    with ad.refresh_lock(w["acc"]) as first:
        with ad.refresh_lock(w["acc"]) as second:
            assert first and not second
        assert (w["acc"] / ad.REFRESH_LOCK).is_dir()  # 取れなかった側は消さない
    assert not (w["acc"] / ad.REFRESH_LOCK).exists()


def test_replace_auth_keeps_a_file_written_after_the_read(tmp_path):
    w = _world(tmp_path)
    local = _local(w)
    body, seen = ad.read_auth(local)
    assert body["claudeAiOauth"]["accessToken"] == ACCESS
    assert ad.replace_auth(local, seen, {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH}, body)
    assert _read(w["acc"] / reg.CRED_FILE) == {"claudeAiOauth": {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH}, "other": 1}
    local = _local(w)
    body, seen = ad.read_auth(local)
    with open(w["acc"] / reg.CRED_FILE, "w") as f:  # 読んだ後に利用者が書いた
        f.write(json.dumps({"claudeAiOauth": {"accessToken": "fake-user", "refreshToken": "fake-user-r"}}))
    assert not ad.replace_auth(local, seen, {"accessToken": "x", "refreshToken": "y"}, body)
    assert _read(w["acc"] / reg.CRED_FILE)["claudeAiOauth"]["accessToken"] == "fake-user"
    assert sorted(p.name for p in w["acc"].iterdir() if p.name.endswith(".tmp")) == []


def test_a_replaced_account_dir_is_not_read_or_written(tmp_path):
    w = _world(tmp_path)
    local = _local(w)
    os.rename(w["acc"], tmp_path / "moved")
    other = tmp_path / "other"
    other.mkdir()
    w["acc"].symlink_to(other)
    assert ad.pin_dir(local) == "not_account_local" and ad.read_auth(local) == "not_account_local"
    ad.write_pending(local, "g1", (ACCESS, REFRESH), {"accessToken": NEW_ACCESS, "refreshToken": NEW_REFRESH})
    assert list(other.iterdir()) == [] and not (tmp_path / "moved" / reg.PENDING_FILE).exists()


# ---------------------------------------------------------------- ready


def test_ready_passes_a_first_account_without_projects_and_writes_nothing(tmp_path):
    w = _world(tmp_path)
    before = sorted((p.name, p.stat().st_mtime_ns) for p in w["acc"].iterdir())
    got = ad.ready(w["store"], "work1", w["environ"], _launch(w))
    assert got.name == "work1" and got.reason is None
    assert sorted((p.name, p.stat().st_mtime_ns) for p in w["acc"].iterdir()) == before
    for item in (reg.ACCOUNT_FILE, reg.CRED_FILE, reg.CONFIG_FILE, "projects", "settings.json"):
        assert any(k.startswith(str(w["acc"] / item)) for k in got.stamps), item


def test_ready_refuses_a_real_projects_dir_and_an_api_key_helper_in_the_account_settings(tmp_path):
    w = _world(tmp_path)
    (w["acc"] / "projects").mkdir()
    assert ad.ready(w["store"], "work1", w["environ"], _launch(w)).reason == "projects_not_shared"
    (w["acc"] / "projects").rmdir()
    _json(w["acc"] / "settings.json", {"apiKeyHelper": "echo fake-helper"})
    got = ad.ready(w["store"], "work1", w["environ"], _launch(w))
    assert got.reason == "auth_overridden" and str(w["acc"] / "settings.json") in got.stamps
    _no_secret(repr(got))
    assert "fake-helper" not in repr(got)


def test_ready_keeps_the_stamps_of_a_failed_check(tmp_path):
    w = _world(tmp_path)
    (w["acc"] / reg.CRED_FILE).unlink()
    got = ad.ready(w["store"], "work1", w["environ"], _launch(w))
    assert got.reason == "no_credentials" and got.stamps[str(w["acc"] / reg.CRED_FILE)] is None


# ---------------------------------------------------------------- settle（3604:7）


def test_3604_7_settle_moves_account_changes_to_the_shared_side_and_drops_the_lease(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w)
    cfg = w["acc"] / reg.CONFIG_FILE
    _json(cfg, {**_read(cfg), "mcpServers": {"m": "A", "added": "by-child"}})
    assert ad.settle(w["store"], "work1", w["environ"], got.generation, got.lease) is None
    assert _read(w["shared_file"])["mcpServers"] == {"m": "A", "added": "by-child"}
    assert _leases(w) == {} and not ad.in_use(w["store"], "work1")


def test_settle_without_changes_does_not_write(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w)
    before = _stamp(w["shared_file"])
    assert ad.settle(w["store"], "work1", w["environ"], got.generation, got.lease) is None
    assert _stamp(w["shared_file"]) == before


def test_settle_of_an_old_generation_does_not_write_back(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w)
    _json(w["acc"] / reg.ACCOUNT_FILE, {**_read(w["acc"] / reg.ACCOUNT_FILE), "generation": "g2"})
    cfg = w["acc"] / reg.CONFIG_FILE
    _json(cfg, {**_read(cfg), "mcpServers": {"m": "changed"}})
    before = _stamp(w["shared_file"])
    assert ad.settle(w["store"], "work1", w["environ"], got.generation, got.lease) == "reregistered"
    assert _stamp(w["shared_file"]) == before and _leases(w) == {}


def _swap_after_prepare(kind: str, w: dict, tmp_path: Path) -> None:
    cfg = w["acc"] / reg.CONFIG_FILE
    if kind == "account-to-shared":
        cfg.unlink()
        cfg.symlink_to(w["shared_file"])
    elif kind == "account-hardlink":
        other = _json(tmp_path / "work2-config.json", _read(cfg))
        cfg.unlink()
        os.link(other, cfg)
    elif kind == "shared-hardlink":
        w["shared_file"].unlink()
        os.link(cfg, w["shared_file"])
    elif kind == "legacy":
        _json(w["acc"] / ".config.json", _read(cfg))
    else:
        _json(w["acc"] / "x.json", {"oauthAccount": {"emailAddress": "someone@example.com"}, "mcpServers": {"m": "z"}})
        os.rename(w["acc"] / "x.json", cfg)


@pytest.mark.parametrize(
    "kind, reason",
    [
        ("account-to-shared", "not_account_local"),
        ("account-hardlink", "not_account_local"),
        ("shared-hardlink", "not_account_local"),
        ("legacy", "legacy_config"),
        ("identity", "identity_mismatch"),
    ],
)
def test_settle_after_a_swap_does_not_write_back_and_drops_the_lease(tmp_path, kind, reason):
    w = _world(tmp_path)
    got = _prepare(w)
    _swap_after_prepare(kind, w, tmp_path)
    watched = [w["shared_file"], w["acc"] / reg.CONFIG_FILE]
    before = [_stamp(p) for p in watched]
    environ = {"NDF_SHARED_CONFIG_DIR": str(w["shared"])}
    assert ad.settle(w["store"], "work1", environ, got.generation, got.lease) == reason
    assert [_stamp(p) for p in watched] == before and _leases(w) == {}


# ---------------------------------------------------------------- 使用の届け（I10）


def _put_lease(w: dict, owner: dict, child, state: str = "running", lease: str = "l1") -> Path:
    d = reg.lease_dir(w["store"], "work1")
    d.mkdir(parents=True, exist_ok=True)
    return _json(d / f"{lease}.json", {"generation": "g1", "state": state, "owner": owner, "child": child})


def _me() -> dict:
    return {"pid": os.getpid(), "start": reg.proc_start(os.getpid())}


def _dead() -> dict:
    return {"pid": os.getpid(), "start": 1.0}  # pid は生きているが開始時刻が違う（使い回された pid）


def test_i10_a_lease_is_alive_while_its_owner_or_child_runs(tmp_path):
    w = _world(tmp_path)
    p = _put_lease(w, _me(), None, state="starting")
    assert ad.in_use(w["store"], "work1")
    _put_lease(w, _dead(), _me())  # ラッパーだけが終わり、claude が残っている
    assert ad.in_use(w["store"], "work1")
    _put_lease(w, _dead(), _dead())
    assert not ad.in_use(w["store"], "work1") and not p.exists()  # 残骸は消して進む
    _put_lease(w, _dead(), None, state="starting")
    assert not ad.in_use(w["store"], "work1")


def test_i10_registering_or_removing_stops_while_a_lease_lives(tmp_path):
    w = _world(tmp_path)
    _put_lease(w, _me(), None, state="starting")
    staging = tmp_path / "staging"
    _json(staging / reg.CRED_FILE, {})
    before = sorted((p.name, p.read_bytes()) for p in w["acc"].iterdir() if p.is_file())
    with pytest.raises(reg.AccountInUse, match="動いているレーン"):
        reg.register(w["store"], "work1", staging, reg.Identity("work1@example.com", "", ""), 1.0)
    with pytest.raises(reg.AccountInUse):
        reg.remove_account(w["store"], "work1", lambda: True)
    assert sorted((p.name, p.read_bytes()) for p in w["acc"].iterdir() if p.is_file()) == before
    _put_lease(w, _dead(), _me())
    with pytest.raises(reg.AccountInUse, match=f"pid {os.getpid()}"):
        reg.remove_account(w["store"], "work1", lambda: True)


def test_i10_a_lease_without_a_start_time_is_not_alive(tmp_path):
    w = _world(tmp_path)
    _put_lease(w, {"pid": os.getpid(), "start": None}, {"pid": os.getpid(), "start": None})
    assert not ad.in_use(w["store"], "work1")


def test_attach_refuses_a_child_whose_start_time_is_unknown(tmp_path, monkeypatch):
    w = _world(tmp_path)
    got = _prepare(w)
    monkeypatch.setattr(reg, "proc_start", lambda pid: None)
    assert ad.attach(w["store"], "work1", got.lease, os.getpid()) == "child_unknown"
    assert _leases(w)[got.lease]["child"] is None


def _busy_lock(monkeypatch) -> None:
    """登録の排他を待ちきれないようにする。"""

    def busy(*a, **kw):
        raise reg.AccountLockTimeout("待ちきれない")

    monkeypatch.setattr(reg, "locked", busy)


def test_settle_and_release_drop_the_lease_even_when_the_lock_times_out(tmp_path, monkeypatch):
    w = _world(tmp_path)
    first, second = _prepare(w), _prepare(w)
    with monkeypatch.context() as m:
        _busy_lock(m)
        assert ad.settle(w["store"], "work1", w["environ"], first.generation, first.lease) == "lock_timeout"
        ad.release(w["store"], "work1", second.lease)
    assert _leases(w) == {}


def test_prepare_without_a_pending_file_checks_the_entity_once(tmp_path, monkeypatch):
    w = _world(tmp_path)
    calls: list[int] = []
    real = ad.check_local
    monkeypatch.setattr(ad, "check_local", lambda *a: calls.append(1) or real(*a))
    assert _prepare(w).ok and len(calls) == 1


def test_attach_marks_the_lease_running_and_release_drops_it(tmp_path):
    w = _world(tmp_path)
    got = _prepare(w)
    assert ad.attach(w["store"], "work1", got.lease, os.getpid()) is None
    row = _leases(w)[got.lease]
    assert row["state"] == "running" and row["child"]["pid"] == os.getpid()
    ad.release(w["store"], "work1", got.lease)
    assert _leases(w) == {}
    assert ad.attach(w["store"], "work1", got.lease, os.getpid()) == "lease_lost"


def test_acquire_checks_the_config_dir_and_the_generation(tmp_path):
    w = _world(tmp_path)
    lease = ad.acquire(w["store"], "work1", w["acc"], "g1")
    assert _leases(w)[lease]["state"] == "starting"
    with pytest.raises(reg.AccountError, match="config_mismatch"):
        ad.acquire(w["store"], "work1", w["shared"], "g1")
    with pytest.raises(reg.AccountError, match="reregistered"):
        ad.acquire(w["store"], "work1", w["acc"], "g0")
    with pytest.raises(reg.AccountError):
        ad.acquire(w["store"], "work9", w["store"] / "work9", "g1")
    assert list(_leases(w)) == [lease]


# ---------------------------------------------------------------- 起動の関所


def _gate(tmp_path: Path, argv: list[str], go: bool) -> tuple[int, bytes]:
    r, w = os.pipe()
    sr, sw = os.pipe()
    p = subprocess.Popen(ad.gated(argv, r, sw), pass_fds=(r, sw))
    os.close(r)
    os.close(sw)
    if go:
        os.write(w, b"1")
    os.close(w)
    with os.fdopen(sr, "rb") as f:
        said = f.read()
    return p.wait(timeout=30), said


def _fake_claude(tmp_path: Path) -> tuple[str, Path]:
    marker = tmp_path / "ran"
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\necho ran > {marker}\n")
    script.chmod(0o755)
    return str(script), marker


def test_gated_runs_the_command_only_after_the_go_byte(tmp_path):
    script, marker = _fake_claude(tmp_path)
    assert ad.gated([script], 7, 9)[:6] == [sys.executable, "-I", "-c", ad.GATE_CODE, "7", "9"]
    code, said = _gate(tmp_path, [script], go=True)
    assert code == 0 and said == b"" and marker.exists()


def test_gated_does_not_run_the_command_when_the_owner_goes_away(tmp_path):
    script, marker = _fake_claude(tmp_path)
    code, said = _gate(tmp_path, [script], go=False)
    assert code == 125 and said == b"" and not marker.exists()


def test_gated_reports_the_errno_when_the_command_is_missing(tmp_path):
    code, said = _gate(tmp_path, [str(tmp_path / "no-such-claude")], go=True)
    assert code == 127 and said == b"2"
