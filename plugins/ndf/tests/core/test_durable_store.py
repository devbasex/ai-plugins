"""チェックアウトの保存先の形の番号と、保存先のロック（決定 85・I10・I11）。"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lock_probe import taken_by_another

from ndf.core import durable

SRC = Path(__file__).resolve().parents[2] / "src"
OPEN_SCRIPT = Path(__file__).with_name("_durable_store_open.py")
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _format(root: Path) -> int:
    return json.loads((root / "format.json").read_text())["format"]


def _snapshot(base: Path) -> dict[str, bytes]:
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


@pytest.fixture
def old_store(root):
    """format.json の無い、入れ替えの前の保存先（今の NDF の .git/ndf/state にあたる）。"""
    (root / "dbos").mkdir(parents=True)
    (root / "dbos" / "run-old.sqlite").write_bytes(b"old")
    (root / "progress.sqlite").write_bytes(b"progress")
    return root


def test_missing_store_is_created_with_format(root):
    ready = durable.prepare_store(root, NOW)
    assert ready == durable.StoreReady(root)
    assert _format(root) == durable.STORE_FORMAT


def test_store_without_format_is_retired_as_the_legacy_form(old_store):
    # 規則 P1: format.json の無い保存先は旧い NDF の形のバージョン 10 で名付ける（identity と同じ規則）
    ready = durable.prepare_store(old_store, NOW)
    assert ready.retired_to == old_store.with_name("state.v10-20261114")
    assert (ready.retired_to / "dbos" / "run-old.sqlite").read_bytes() == b"old"
    assert (ready.retired_to / "progress.sqlite").read_bytes() == b"progress"
    assert sorted(p.name for p in old_store.iterdir()) == ["format.json"]
    assert _format(old_store) == durable.STORE_FORMAT


@pytest.mark.parametrize("value", ["2", 2.5, True, None])
def test_store_with_a_non_integer_format_is_retired_as_the_legacy_form(root, value):
    # 決定 85: format が JSON の整数でなければ番号を取れない置き場として版 10 で退避し、今の置き場として使わない
    root.mkdir(parents=True)
    (root / "format.json").write_text(json.dumps({"format": value}))
    (root / "progress.sqlite").write_bytes(b"p")
    ready = durable.prepare_store(root, NOW)
    assert ready.retired_to == root.with_name("state.v10-20261114")
    assert _format(root) == durable.STORE_FORMAT


def test_store_with_older_format_is_retired_with_its_number(root):
    root.mkdir(parents=True)
    (root / "format.json").write_text(json.dumps({"format": durable.STORE_FORMAT - 1}))
    (root / "progress.sqlite").write_bytes(b"p")
    ready = durable.prepare_store(root, NOW)
    assert ready.retired_to == root.with_name(f"state.v{durable.STORE_FORMAT - 1}-20261114")
    assert _format(root) == durable.STORE_FORMAT


def test_retire_target_that_exists_stops_without_changes(old_store):
    # 規則 P2: 退避先が既にあれば何も変えずに止まる
    taken = old_store.with_name("state.v10-20261114")
    taken.mkdir()
    before = _snapshot(old_store)
    with pytest.raises(durable.StoreRetireTargetExists) as err:
        durable.prepare_store(old_store, NOW)
    assert (err.value.kind, err.value.paths) == ("precondition", (taken,))
    assert err.value.path_items() == [{"path": str(taken), "reason": "retire_target_exists"}]
    assert _snapshot(old_store) == before
    assert list(taken.iterdir()) == []


def test_retire_names_both_places_when_the_format_file_cannot_be_written(old_store, monkeypatch):
    # 退避の後に format.json を書けなければ、退避した元と先を停止の結果に載せる（復旧の場所が結果から分かる）
    def fail(root):
        raise OSError("No space left on device")

    monkeypatch.setattr(durable, "_write_format", fail)
    retired = old_store.with_name("state.v10-20261114")
    with pytest.raises(durable.StoreConfigBroken) as err:
        durable.prepare_store(old_store, NOW)
    assert err.value.paths == (old_store, retired)
    assert (retired / "progress.sqlite").read_bytes() == b"progress"


@pytest.mark.parametrize("var", ["NDF_ACCOUNTS_DIR", "NDF_LOCK_DIR"])
def test_retire_stops_before_the_rename_when_a_kept_location_is_inside(old_store, monkeypatch, var):
    # 旧い NDF の退避しない保存先（環境変数が指す先も）を中に持つ保存先は、名前を変える前に止まる
    kept = old_store / "kept"
    kept.mkdir()
    (kept / "x").write_text("keep")
    monkeypatch.setenv(var, str(kept))
    before = _snapshot(old_store)
    with pytest.raises(durable.StoreConfigBroken, match="退避しない保存先") as err:
        durable.prepare_store(old_store, NOW)
    assert err.value.paths == (old_store, kept)
    assert _snapshot(old_store) == before
    assert not old_store.with_name("state.v10-20261114").exists()


def test_a_store_holding_only_an_interrupted_format_file_is_not_retired(root):
    # format.json を書く途中で止まった一時ファイルだけの保存先は、旧い利用者のデータとして退避しない
    root.mkdir(parents=True)
    (root / ".format.json.99999.tmp").write_text("{")
    ready = durable.prepare_store(root, NOW)
    assert ready.retired_to is None
    assert _format(root) == durable.STORE_FORMAT
    assert not root.with_name("state.v10-20261114").exists()


def test_store_with_newer_format_is_left_untouched(root):
    root.mkdir(parents=True)
    (root / "format.json").write_text(json.dumps({"format": durable.STORE_FORMAT + 1}))
    (root / "progress.sqlite").write_bytes(b"p")
    before = _snapshot(root.parent)

    with pytest.raises(durable.StoreFormatNewer) as err:
        durable.prepare_store(root, NOW)
    assert err.value.kind == "format_newer"
    assert _snapshot(root.parent) == {**before, "state.lock": b""}


def test_same_format_is_used_as_is_and_held_shared(root):
    root.mkdir(parents=True)
    (root / "format.json").write_text(json.dumps({"format": durable.STORE_FORMAT}))
    (root / "progress.sqlite").write_bytes(b"p")

    assert durable.prepare_store(root, NOW) == durable.StoreReady(root)
    with pytest.raises(durable.StoreInUse), durable.store_lock(root, exclusive=True):
        pass
    durable.release_store(root)
    with durable.store_lock(root, exclusive=True) as path:
        assert path == root.parent / durable.STORE_LOCK


def _hold(path: Path, mode: int):
    handle = open(path, "a")  # noqa: SIM115 - テストの間ロックを持つ
    fcntl.flock(handle, mode)
    return handle


@pytest.mark.parametrize(
    ("lock", "mode", "error"),
    [
        ("../state.lock", fcntl.LOCK_SH, durable.StoreInUse),  # 保存先を使うほかのプロセス
        ("dbos/run-old.lock", fcntl.LOCK_EX, durable.StoreLegacyRunning),  # キーのロックを持つ今の NDF（10.x）の耐久ワークフロー
        ("progress.sqlite.lock", fcntl.LOCK_SH, durable.StoreLegacyRunning),  # 別のチェックアウトからステートマシンの DB を開いたプロセス
    ],
)
def test_retire_stops_with_store_in_use_while_a_lock_is_held(old_store, lock, mode, error):
    held_path = (old_store / lock).resolve()
    held = _hold(held_path, mode)
    try:
        before = _snapshot(old_store)
        with pytest.raises(error) as err:
            durable.prepare_store(old_store, NOW)
        assert err.value.kind == "store_in_use"
        assert held_path in err.value.paths
        assert _snapshot(old_store) == before
        assert not list(old_store.parent.glob("state.v*"))
    finally:
        held.close()

    ready = durable.prepare_store(old_store, NOW)
    assert ready.retired_to is not None
    # 保存先のロックと検査と走査のロックは state/ の外にあり、退避で動かない
    assert (old_store.parent / durable.STORE_LOCK).exists()
    with durable.maint_lock(old_store, wait=False) as maint:
        assert maint == old_store.parent / durable.MAINT_LOCK


def test_an_old_run_holding_its_lock_is_not_waited_for(old_store, monkeypatch):
    # 10 系の耐久の実行の錠が取れなければ、prepare_store は待ち直さずに legacy_running で止まる
    held = _hold(old_store / "dbos" / "run-old.lock", fcntl.LOCK_EX)
    calls = []
    align = durable._align_store
    monkeypatch.setattr(durable, "_align_store", lambda root, now: calls.append(root) or align(root, now))
    try:
        with pytest.raises(durable.StoreLegacyRunning) as err:
            durable.prepare_store(old_store, NOW)
    finally:
        held.close()
    assert calls == [old_store]
    assert err.value.path_items() == [{"path": str(old_store / "dbos" / "run-old.lock"), "reason": "legacy_running"}]


def test_retire_holds_the_old_locks_through_the_rename_and_the_format_file(old_store, monkeypatch):
    # 不変条件 I3b: 旧い錠の確かめから名前の変更と format.json の書き込みが終わるまで、旧い錠を持ち続ける
    key_lock = old_store / "dbos" / "run-old.lock"
    key_lock.write_text("")
    progress_lock = old_store / durable.PROGRESS_LOCK
    progress_lock.write_text("")
    retired = old_store.with_name("state.v10-20261114")
    seen: dict[str, bool] = {}
    rename = Path.rename
    write_format = durable._write_format

    def probe_rename(self, target):
        if self == old_store:
            seen["rename"] = taken_by_another(key_lock) and taken_by_another(progress_lock)
        return rename(self, target)

    def probe_format(root):
        # 退避の中の 1 回目の書き込みだけを見る（後の DBOS の記録のそろえは錠の外でよい）
        seen.setdefault("format", taken_by_another(retired / "dbos" / "run-old.lock") and taken_by_another(retired / durable.PROGRESS_LOCK))
        write_format(root)

    monkeypatch.setattr(Path, "rename", probe_rename)
    monkeypatch.setattr(durable, "_write_format", probe_format)

    assert durable.prepare_store(old_store, NOW).retired_to == retired
    assert seen == {"rename": True, "format": True}
    assert not taken_by_another(retired / "dbos" / "run-old.lock")


def test_two_processes_opening_an_old_store_retire_it_once(old_store, tmp_path):
    go = tmp_path / "go"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    procs = [
        subprocess.Popen([sys.executable, str(OPEN_SCRIPT), str(old_store), str(go)], stdout=subprocess.PIPE, text=True, env=env)
        for _ in range(2)
    ]
    go.touch()
    outs = [json.loads(p.communicate(timeout=60)[0].strip().splitlines()[-1]) for p in procs]

    assert {o["kind"] for o in outs} <= {"ok", "store_in_use"}
    assert any(o["kind"] == "ok" for o in outs)
    assert [o["retired_to"] for o in outs if o.get("retired_to")] == [str(old_store.with_name("state.v10-" + _today()))]
    assert [p.name for p in old_store.parent.glob("state.v*")] == ["state.v10-" + _today()]
    assert (old_store.with_name("state.v10-" + _today()) / "dbos" / "run-old.sqlite").read_bytes() == b"old"
    assert _format(old_store) == durable.STORE_FORMAT


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def test_maint_lock_without_wait_yields_none_while_held(root):
    with durable.maint_lock(root, wait=True) as first:
        assert first is not None
        with durable.maint_lock(root, wait=False) as second:
            assert second is None


def test_store_root_is_shared_by_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "wt")], check=True)
    (repo / "sub").mkdir()

    expected = (repo / ".git").resolve() / "ndf" / "state"
    assert durable.store_root("ndf", repo) == expected
    assert durable.store_root("ndf", repo / "sub") == expected
    assert durable.store_root("ndf", tmp_path / "wt") == expected
