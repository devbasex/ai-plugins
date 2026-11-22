"""`.claude.json` の共有する部分の同期（`account_dir.sync_config` の U3 の範囲。#3604 #3864）。

設計書の「テスト設計」の 3604:6・3604:7（関数）の行と、詳細設計書の「起動の前の用意」の「処理の手順」「書く手順と
途中で止まった後」「単体テストの観点」の `sync_config`・`check_local` の行を縛る。値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path

import pytest

from ndf.domains.relay import account_dir as ad
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay import account_sync


def _json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _read(path: Path):
    return json.loads(path.read_text())


def _edit(path: Path, **kv) -> None:
    """通常のファイルのまま中身だけを書き換える（inode を保つ。利用者や claude の保存に当たる）。"""
    d = _read(path)
    for k, v in kv.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    path.write_text(json.dumps(d))


def _world(tmp_path: Path, shared_cfg=None, account_cfg=None) -> dict:
    shared = tmp_path / "shared"
    shared_file = _json(shared / ".claude.json", shared_cfg if shared_cfg is not None else {"projects": {}})
    store = shared / "ndf" / "accounts"
    acc = store / "work1"
    acc.mkdir(parents=True)
    _json(acc / reg.ACCOUNT_FILE, {"name": "work1", "email": "work1@example.com", "generation": "g1"})
    _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": "fake-access", "refreshToken": "fake-refresh"}})
    base = {"oauthAccount": {"emailAddress": "work1@example.com", "organizationUuid": "o1"}, "userID": "u1"}
    _json(acc / reg.CONFIG_FILE, {**base, **(account_cfg or {})})
    return {"shared": shared, "shared_file": shared_file, "store": store, "acc": acc, "config": acc / reg.CONFIG_FILE}


def _local(w: dict) -> ad.Local:
    got = ad.check_local(w["store"], w["acc"], w["shared_file"])
    assert not isinstance(got, str), got
    return got


def _sync(w: dict):
    """`prepare`・`settle` と同じく、同期のたびに実体を確かめ直してから打つ。"""
    return ad.sync_config(_local(w))


def _mcp(path: Path):
    return _read(path).get("mcpServers")


def _fail_replace(monkeypatch, target: Path) -> None:
    """`target` への置き換え（`os.replace`）だけを失敗させる（その直前で止まったのと同じ）。"""
    real = os.replace

    def fake(src, dst, *a, **kw):
        fd = kw.get("dst_dir_fd")  # 共有側は固定した親の記述子の下の名前で置き換える
        where = Path(os.readlink(f"/proc/self/fd/{fd}")) / dst if fd is not None else Path(dst)
        if where == target:
            raise OSError("止めた")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", fake)


def _tmp_files(w: dict) -> list[str]:
    return sorted(p.name for d in (w["acc"], w["shared"]) for p in d.iterdir() if ".ndf-sync." in p.name)


# ---------------------------------------------------------------- 3604:6（用意の後の同期）


def test_3604_6_first_sync_copies_the_shared_part_and_keeps_oauth_account(tmp_path):
    shared_cfg = {
        "projects": {"/p": {"allowedTools": ["Read"], "hasTrustDialogAccepted": True}},
        "mcpServers": {"m": {"command": "c"}},
        "hasCompletedOnboarding": True,
        "theme": "dark",
    }
    w = _world(tmp_path, shared_cfg, {"projects": {"/mine": {"allowedTools": ["Bash"]}}, "hasCompletedOnboarding": False})
    before = _read(w["config"])["oauthAccount"]
    assert _sync(w) is None
    got = _read(w["config"])
    assert got["projects"] == shared_cfg["projects"] and got["mcpServers"] == shared_cfg["mcpServers"]
    # 最初の同期はアカウント側にだけある葉も消す。ほかのキーは変わらない（丸ごと置き換えない）
    assert "/mine" not in got["projects"] and got["oauthAccount"] == before and got["userID"] == "u1"
    assert got["hasCompletedOnboarding"] is False and "theme" not in got
    assert _read(w["shared_file"]) == shared_cfg
    backup = w["acc"] / reg.BASE_FILE
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600 and _read(backup)["userID"] == "u1"
    assert not (w["acc"] / reg.BASE_NEXT_FILE).exists() and _tmp_files(w) == []


def test_3604_6_onboarding_keys_come_from_the_shared_side_only_when_missing(tmp_path):
    w = _world(tmp_path, {"hasCompletedOnboarding": True, "lastOnboardingVersion": "2.0.0"}, {"lastOnboardingVersion": "1.0.0"})
    assert _sync(w) is None
    got = _read(w["config"])
    assert got["hasCompletedOnboarding"] is True and got["lastOnboardingVersion"] == "1.0.0"


# ---------------------------------------------------------------- 3604:7（3 者の突き合わせ）


def _synced(tmp_path: Path, mcp: dict) -> dict:
    """バックアップ A（`mcpServers` が `mcp`）まで同期の済んだ世界。"""
    w = _world(tmp_path, {"projects": {"/p": {"allowedTools": ["Read"]}}, "mcpServers": mcp})
    assert _sync(w) is None
    return w


def test_3604_7_a_leaf_changed_only_on_the_account_side_moves_to_the_shared_side(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "A", "m2": "new"}, projects={"/p": {"allowedTools": ["Read", "Bash"]}})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"m": "A", "m2": "new"}
    assert _read(w["shared_file"])["projects"] == {"/p": {"allowedTools": ["Read", "Bash"]}}


def test_3604_7_a_leaf_changed_on_both_sides_keeps_the_shared_value(tmp_path):
    w = _synced(tmp_path, {"m": "A", "n": "A"})
    _edit(w["config"], mcpServers={"m": "from-account", "n": "A"})
    _edit(w["shared_file"], mcpServers={"m": "from-shared", "n": "shared-only"})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"m": "from-shared", "n": "shared-only"}


def test_3604_7_a_leaf_changed_to_the_same_value_on_both_sides_keeps_it(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "same"})
    _edit(w["shared_file"], mcpServers={"m": "same"})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"m": "same"}


@pytest.mark.parametrize("gone", [{}, None], ids=["empty-object", "key-removed"])
def test_3604_7_deleting_the_last_leaf_on_the_account_side_deletes_it_on_both(tmp_path, gone):
    w = _synced(tmp_path, {"a": "A"})
    _edit(w["config"], mcpServers=gone)
    assert _sync(w) is None
    assert not _mcp(w["shared_file"]) and not _mcp(w["config"])


def test_an_emptied_project_and_a_shared_change_of_its_item_reach_both_sides(tmp_path):
    """アカウント側が最後の項目を消して `{}`、共有側が同じ項目を変えたとき、両側が共有側の値になり次の同期でも消えない。"""
    w = _world(tmp_path, {"projects": {"/p": {"allowedTools": ["Read"]}}})
    assert _sync(w) is None
    _edit(w["config"], projects={"/p": {}})
    _edit(w["shared_file"], projects={"/p": {"allowedTools": ["Bash"]}})
    assert _sync(w) is None
    want = {"/p": {"allowedTools": ["Bash"]}}
    assert _read(w["config"])["projects"] == _read(w["shared_file"])["projects"] == want
    assert _sync(w) is None
    assert _read(w["config"])["projects"] == _read(w["shared_file"])["projects"] == want


def test_a_shared_file_replaced_by_another_lane_is_synced_again(tmp_path, monkeypatch):
    """錠を待つ間に別のレーンが共有側を正しく置き換えても、その実体で同期し直す（not_account_local にしない）。"""
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "A", "n": "mine"})
    local = _local(w)
    real = account_sync._claude_lock
    done: list[bool] = []

    @contextlib.contextmanager
    def lock_then_replace(path, *a):
        with real(path, *a):
            if not done:
                repl = _json(w["shared"] / "lane2.json", {**_read(w["shared_file"]), "mcpServers": {"m": "A", "o": "lane2"}})
                os.replace(repl, w["shared_file"])
                done.append(True)
            yield

    monkeypatch.setattr(account_sync, "_claude_lock", lock_then_replace)
    assert ad.sync_config(local) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"m": "A", "n": "mine", "o": "lane2"}


@pytest.mark.parametrize("gone", [{}, None], ids=["empty-object", "key-removed"])
def test_3604_7_a_shared_change_wins_over_an_account_deletion(tmp_path, gone):
    w = _synced(tmp_path, {"a": "A"})
    _edit(w["config"], mcpServers=gone)
    _edit(w["shared_file"], mcpServers={"a": "changed"})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"a": "changed"}


def test_3604_7_a_shared_deletion_wins_over_an_account_change(tmp_path):
    w = _synced(tmp_path, {"a": "A", "b": "B"})
    _edit(w["config"], mcpServers={"a": "changed", "b": "B"})
    _edit(w["shared_file"], mcpServers={"b": "B"})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == _mcp(w["config"]) == {"b": "B"}


@pytest.mark.parametrize("breaker", ["no-backup", "other-user", "broken-backup"])
def test_without_a_usable_backup_the_sync_is_the_first_one(tmp_path, breaker):
    w = _synced(tmp_path, {"m": "A"})
    backup = w["acc"] / reg.BASE_FILE
    if breaker == "no-backup":
        backup.unlink()
    elif breaker == "other-user":
        _edit(backup, userID="someone-else")
    else:
        backup.write_text("{")
    _edit(w["config"], mcpServers={"m": "A", "mine": "x"})
    assert _sync(w) is None
    # 最初の同期は共有側の値にそろえる（アカウント側にだけある葉は消える）
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "A"}


def test_no_change_writes_nothing(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    before = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (w["config"], w["shared_file"], w["acc"] / reg.BASE_FILE)]
    assert _sync(w) is None
    assert [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (w["config"], w["shared_file"], w["acc"] / reg.BASE_FILE)] == before


def test_the_shared_file_is_written_readable_only_by_its_owner(tmp_path):
    """認証のヘッダーを持ちうる値を、他のユーザーが読める権限のファイルへ移さない。"""
    w = _world(tmp_path, {"mcpServers": {}}, {"mcpServers": {}})
    assert _sync(w) is None
    w["shared_file"].chmod(0o644)
    _edit(w["config"], mcpServers={"m": {"headers": {"Authorization": "fake-header"}}})
    assert _sync(w) is None
    assert stat.S_IMODE(w["shared_file"].stat().st_mode) == 0o600 and _mcp(w["shared_file"])["m"]


def test_an_unchanged_shared_file_is_still_narrowed_to_its_owner(tmp_path):
    """内容に差分が無く書き直さない同期でも、認証のヘッダーを持つ共有側を他のユーザーが読める権限のまま残さない。"""
    w = _synced(tmp_path, {"m": {"headers": {"Authorization": "fake-header"}}})
    w["shared_file"].chmod(0o644)
    w["config"].chmod(0o644)
    before = [(p.stat().st_ino, p.read_bytes()) for p in (w["config"], w["shared_file"])]
    assert _sync(w) is None
    assert [(p.stat().st_ino, p.read_bytes()) for p in (w["config"], w["shared_file"])] == before
    assert stat.S_IMODE(w["shared_file"].stat().st_mode) == stat.S_IMODE(w["config"].stat().st_mode) == 0o600


@pytest.mark.parametrize("when", ["before-pin", "under-the-lock"])
def test_a_shared_parent_swapped_for_a_symlink_is_not_followed(tmp_path, monkeypatch, when):
    """共有側の親が確かめの後に symlink へ差し替わっても、その先（保存先の内側の別のファイル）へ書かない。"""
    w = _world(tmp_path, {"mcpServers": {}}, {"mcpServers": {}})
    home = tmp_path / "home"
    shared_file = _json(home / ".claude.json", {"mcpServers": {}})
    first = ad.check_local(w["store"], w["acc"], shared_file)
    assert not isinstance(first, str) and ad.sync_config(first) is None
    _edit(w["config"], mcpServers={"m": "B"})  # 次の同期で共有側へ移るアカウント側の変更
    local = ad.check_local(w["store"], w["acc"], shared_file)
    assert not isinstance(local, str)
    target = _json(w["store"] / "work2" / reg.CONFIG_FILE, {"mcpServers": {"x": "other"}})
    before = target.read_bytes()

    def swap() -> None:
        os.rename(home, tmp_path / "home.old")
        home.symlink_to(target.parent)  # 名前を解き直すと保存先の内側の work2 の .claude.json を指す

    if when == "before-pin":
        swap()
        assert ad.sync_config(local) == "not_account_local"
    else:
        real = account_sync._claude_lock
        done: list[bool] = []

        @contextlib.contextmanager
        def lock_then_swap(path, *a):
            with real(path, *a):
                if a and not done:
                    swap()
                    done.append(True)
                yield

        monkeypatch.setattr(account_sync, "_claude_lock", lock_then_swap)
        assert ad.sync_config(local) is None and done
        old = tmp_path / "home.old"
        assert _mcp(old / ".claude.json") == {"m": "B"}  # 固定した親の下だけへ書く
        assert sorted(p.name for p in old.iterdir()) == [".claude.json"]  # 錠・一時ファイルも固定した親の下で片付いた
    assert target.read_bytes() == before
    assert sorted(p.name for p in target.parent.iterdir()) == [reg.CONFIG_FILE]


def test_a_dotfiles_symlink_on_the_shared_side_is_written_through_and_kept(tmp_path):
    w = _world(tmp_path, {"mcpServers": {"m": "A"}})
    dot = _json(tmp_path / "dotfiles" / "claude.json", {"mcpServers": {"m": "A"}})
    w["shared_file"].unlink()
    w["shared_file"].symlink_to(dot)
    assert _sync(w) is None
    _edit(w["config"], mcpServers={"m": "A", "n": "new"})
    assert _sync(w) is None
    assert w["shared_file"].is_symlink() and Path(os.readlink(w["shared_file"])) == dot
    assert _read(dot)["mcpServers"] == {"m": "A", "n": "new"}


# ---------------------------------------------------------------- 本体の錠と実体の確かめ直し


def test_same_file_is_not_written(tmp_path):
    w = _world(tmp_path)
    local = _local(w)
    same = ad.Local(account=local.account, config=local.config, shared=local.config, ids=local.ids)
    before = w["config"].stat().st_mtime_ns
    assert ad.sync_config(same) == "same_file"
    assert w["config"].stat().st_mtime_ns == before and not Path(f"{w['config']}.lock").exists()


def test_a_busy_claude_lock_gives_lock_busy_and_writes_nothing(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "B"})
    Path(f"{w['shared_file']}.lock").mkdir()  # 動いている claude が持つ錠（新しい）
    shared_before = w["shared_file"].read_bytes()
    assert _sync(w) == "lock_busy"
    assert w["shared_file"].read_bytes() == shared_before and _mcp(w["config"]) == {"m": "B"}
    assert not Path(f"{w['config']}.lock").exists()  # 取った錠は返す


def test_a_stale_claude_lock_is_taken_over(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    lock = Path(f"{w['shared_file']}.lock")
    lock.mkdir()
    os.utime(lock, (1.0, 1.0))  # 本体と同じく 10 秒より古い錠は残骸
    _edit(w["config"], mcpServers={"m": "B"})
    assert _sync(w) is None
    assert _mcp(w["shared_file"]) == {"m": "B"} and not lock.exists()


def _swap(kind: str, w: dict, tmp_path: Path) -> Path:
    """錠を待つ間の置き換え。置き換えた後の実体（中身と更新時刻が変わらないことを見る先）を返す。"""
    other = _json(tmp_path / "elsewhere" / "other.json", {"mcpServers": {"x": "other"}})
    if kind == "account-replaced":
        repl = _json(w["acc"] / "repl.json", {"oauthAccount": {}, "mcpServers": {"x": "y"}})
        os.rename(repl, w["config"])
        return w["config"]
    if kind == "shared-symlinked":
        w["shared_file"].unlink()
        w["shared_file"].symlink_to(other)
        return other
    os.link(w["shared_file"], tmp_path / "elsewhere" / "hard.json")  # 解決先をハードリンクにする
    return w["shared_file"]


@pytest.mark.parametrize("kind", ["account-replaced", "shared-symlinked", "shared-hardlinked"])
def test_a_file_replaced_while_waiting_for_the_lock_is_not_touched(tmp_path, monkeypatch, kind):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "B"})
    local = _local(w)
    real = account_sync._claude_lock
    seen: list[tuple[Path, tuple]] = []

    @contextlib.contextmanager
    def lock_then_swap(path, *a):
        with real(path, *a):
            if not seen:
                target = _swap(kind, w, tmp_path)
                seen.append((target, (target.read_bytes(), target.stat().st_mtime_ns)))  # 置き換えた直後の中身と更新時刻
            yield

    monkeypatch.setattr(account_sync, "_claude_lock", lock_then_swap)
    assert ad.sync_config(local) == "not_account_local"
    target, before = seen[0]
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert _tmp_files(w) == [] and not (w["acc"] / reg.BASE_NEXT_FILE).exists()


def test_shared_file_under_the_store_is_not_account_local(tmp_path):
    w = _world(tmp_path)
    other = tmp_path / "shared" / "ndf" / "accounts" / "work2"
    _json(other / reg.CONFIG_FILE, {"mcpServers": {}})
    link = tmp_path / "chain"
    link.symlink_to(other / reg.CONFIG_FILE)
    assert ad.check_local(w["store"], w["acc"], link) == "not_account_local"


def test_unreadable_shared_file_is_reported_and_nothing_is_written(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "B"})
    w["shared_file"].write_text("[1]")
    assert _sync(w) == "shared_unreadable"
    assert _mcp(w["config"]) == {"m": "B"} and w["shared_file"].read_text() == "[1]"


# ---------------------------------------------------------------- 書く手順と途中で止まった後


def _both_changed(tmp_path: Path) -> dict:
    """バックアップ A の後、共有側が `m` を、アカウント側が `n` を変えた世界（B は両方を持つ）。"""
    w = _synced(tmp_path, {"m": "A", "n": "A"})
    _edit(w["shared_file"], mcpServers={"m": "B", "n": "A"})
    _edit(w["config"], mcpServers={"m": "A", "n": "B"})
    return w


B_VALUE = {"m": "B", "n": "B"}


def test_the_first_replace_failing_leaves_both_sides_unchanged(tmp_path, monkeypatch):
    w = _both_changed(tmp_path)
    before = (w["config"].read_bytes(), w["shared_file"].read_bytes())
    _fail_replace(monkeypatch, w["acc"] / reg.BASE_NEXT_FILE)
    assert _sync(w) == "write_failed"
    assert (w["config"].read_bytes(), w["shared_file"].read_bytes()) == before and _tmp_files(w) == []


@pytest.mark.parametrize("where", ["account", "shared", "backup"])
def test_a_sync_stopped_before_a_replace_settles_on_the_next_sync(tmp_path, monkeypatch, where):
    w = _both_changed(tmp_path)
    targets = {"account": w["config"], "shared": w["shared_file"], "backup": w["acc"] / reg.BASE_FILE}
    with monkeypatch.context() as m:
        _fail_replace(m, targets[where])
        assert _sync(w) == "write_failed"
    assert _sync(w) is None
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == B_VALUE
    assert not (w["acc"] / reg.BASE_NEXT_FILE).exists() and _tmp_files(w) == []


def test_a_shared_change_after_the_account_side_was_replaced_wins(tmp_path, monkeypatch):
    w = _both_changed(tmp_path)
    with monkeypatch.context() as m:
        _fail_replace(m, w["shared_file"])
        assert _sync(w) == "write_failed"
    assert _mcp(w["config"]) == B_VALUE  # アカウント側は B へ置き換え済み、共有側は A のまま
    _edit(w["shared_file"], mcpServers={"m": "C", "n": "A"})
    assert _sync(w) is None
    # 共有側の m は止まった後の変更なので C、n は共有側が A のまま（基準 A）なので B
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "C", "n": "B"}


@pytest.mark.parametrize("edit, expected", [("C", "C"), ("A", "A"), (None, None)], ids=["changed", "back-to-A", "deleted"])
def test_after_both_sides_were_replaced_an_account_edit_moves_to_both(tmp_path, monkeypatch, edit, expected):
    w = _synced(tmp_path, {"m": "A"})
    _edit(w["config"], mcpServers={"m": "B"})
    with monkeypatch.context() as m:
        _fail_replace(m, w["acc"] / reg.BASE_FILE)
        assert _sync(w) == "write_failed"
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "B"}
    _edit(w["config"], mcpServers={} if edit is None else {"m": edit})
    assert _sync(w) is None
    want = {} if expected is None else {"m": expected}
    assert (_mcp(w["config"]) or {}) == (_mcp(w["shared_file"]) or {}) == want


def test_a_first_sync_stopped_after_the_account_side_takes_a_later_account_edit(tmp_path, monkeypatch):
    w = _world(tmp_path, {"mcpServers": {"m": "S"}}, {"mcpServers": {"mine": "x"}})
    with monkeypatch.context() as m:
        _fail_replace(m, w["acc"] / reg.BASE_FILE)
        assert _sync(w) == "write_failed"
    assert _mcp(w["config"]) == {"m": "S"}
    _edit(w["config"], mcpServers={"m": "edited"})
    assert _sync(w) is None
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "edited"}


def test_a_first_sync_stopped_before_the_account_side_redoes_the_first_sync(tmp_path, monkeypatch):
    w = _world(tmp_path, {"mcpServers": {"m": "S"}}, {"mcpServers": {"mine": "x"}})
    with monkeypatch.context() as m:
        _fail_replace(m, w["config"])
        assert _sync(w) == "write_failed"
    assert (w["acc"] / reg.BASE_NEXT_FILE).exists()
    assert _sync(w) is None
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "S"}


def test_temp_files_without_a_record_are_removed_and_the_previous_sync_stands(tmp_path):
    w = _synced(tmp_path, {"m": "A"})
    mine = account_sync._owner(_local(w))
    stray = [Path(f"{w['config']}.ndf-sync.dead.tmp"), Path(f"{w['shared_file']}.ndf-sync.{mine}-dead.tmp")]
    for p in stray:
        _json(p, {"mcpServers": {"m": "stray"}})
    assert _sync(w) is None
    assert not any(p.exists() for p in stray)
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == {"m": "A"}


def test_the_shared_temp_file_of_another_account_is_kept_for_its_recovery(tmp_path, monkeypatch):
    """アカウント A の同期が共有側の置き換えの前で止まった後、B の同期が A の一時ファイルを消さず、A の復旧が A の変更を残す。"""
    w = _both_changed(tmp_path)
    with monkeypatch.context() as m:
        _fail_replace(m, w["shared_file"])
        assert _sync(w) == "write_failed"
    left = [p for p in w["shared"].iterdir() if ".ndf-sync." in p.name]
    assert left
    other = w["store"] / "work2"
    other.mkdir()
    _json(other / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "work2@example.com"}, "userID": "u2"})
    got = ad.check_local(w["store"], other, w["shared_file"])
    assert not isinstance(got, str)
    assert ad.sync_config(got) is None
    assert all(p.exists() for p in left)
    assert _sync(w) is None
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == B_VALUE


@pytest.mark.parametrize("again", ["record", "shared", "backup"])
@pytest.mark.parametrize("later", [None, "C"], ids=["untouched", "shared-edited"])
def test_a_recovery_stopped_again_still_settles_on_b(tmp_path, monkeypatch, again, later):
    """復旧の再中断: 共有側の前で止めた後、復旧の各位置でもう一度止めても、共有側が B になりアカウント側が A に戻らない。"""
    w = _both_changed(tmp_path)
    with monkeypatch.context() as m:
        _fail_replace(m, w["shared_file"])
        assert _sync(w) == "write_failed"
    targets = {"record": w["acc"] / reg.BASE_NEXT_FILE, "shared": w["shared_file"], "backup": w["acc"] / reg.BASE_FILE}
    with monkeypatch.context() as m:
        _fail_replace(m, targets[again])
        assert _sync(w) == "write_failed"
    if later:
        _edit(w["shared_file"], mcpServers={**_mcp(w["shared_file"]), "m": later})
    assert _sync(w) is None
    want = {"m": later or "B", "n": "B"}
    assert _mcp(w["config"]) == _mcp(w["shared_file"]) == want
    assert not (w["acc"] / reg.BASE_NEXT_FILE).exists() and _tmp_files(w) == []


def test_reregistering_drops_the_backup_the_record_and_the_account_temp_files(tmp_path, monkeypatch):
    w = _both_changed(tmp_path)
    with monkeypatch.context() as m:
        _fail_replace(m, w["shared_file"])
        assert _sync(w) == "write_failed"
    stray = Path(f"{w['config']}.ndf-sync.dead.tmp")
    _json(stray, {})
    staging = tmp_path / "staging"
    _json(staging / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": "fake-new", "refreshToken": "fake-new-r"}})
    _json(staging / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "work1@example.com"}, "userID": "u1"})
    reg.register(w["store"], "work1", staging, reg.Identity("work1@example.com", "", ""), 1.0)
    left = sorted(p.name for p in w["acc"].iterdir())
    assert reg.BASE_FILE not in left and reg.BASE_NEXT_FILE not in left
    assert not any(n.startswith(f"{reg.CONFIG_FILE}.ndf-sync.") for n in left)
