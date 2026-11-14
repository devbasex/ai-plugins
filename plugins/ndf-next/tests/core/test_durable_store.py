"""チェックアウトの置き場の形の番号と、置き場のロック（決定 85・I10・I11）。"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ndf.core import durable

SRC = Path(__file__).resolve().parents[2] / "src"
OPEN_SCRIPT = Path(__file__).with_name("_durable_store_open.py")
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "ndf" / "state"
    yield path
    durable.release_store(path)


def _format(root: Path) -> int:
    return json.loads((root / "format.json").read_text())["format"]


def _snapshot(base: Path) -> dict[str, bytes]:
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


@pytest.fixture
def old_store(root):
    """format.json の無い、入れ替えの前の置き場（今の NDF の .git/ndf/state にあたる）。"""
    (root / "dbos").mkdir(parents=True)
    (root / "dbos" / "run-old.sqlite").write_bytes(b"old")
    (root / "progress.sqlite").write_bytes(b"progress")
    return root


def test_missing_store_is_created_with_format(root):
    ready = durable.prepare_store(root, NOW)
    assert ready == durable.StoreReady(root)
    assert _format(root) == durable.STORE_FORMAT


def test_store_without_format_is_retired_as_v0(old_store):
    ready = durable.prepare_store(old_store, NOW)
    assert ready.retired_to == old_store.with_name("state.v0-20261114")
    assert (ready.retired_to / "dbos" / "run-old.sqlite").read_bytes() == b"old"
    assert (ready.retired_to / "progress.sqlite").read_bytes() == b"progress"
    assert sorted(p.name for p in old_store.iterdir()) == ["format.json"]
    assert _format(old_store) == durable.STORE_FORMAT


def test_store_with_older_format_is_retired_with_its_number(root):
    root.mkdir(parents=True)
    (root / "format.json").write_text(json.dumps({"format": durable.STORE_FORMAT - 1}))
    (root / "progress.sqlite").write_bytes(b"p")
    ready = durable.prepare_store(root, NOW)
    assert ready.retired_to == root.with_name(f"state.v{durable.STORE_FORMAT - 1}-20261114")
    assert _format(root) == durable.STORE_FORMAT


def test_retire_target_that_exists_gets_a_time(old_store):
    taken = old_store.with_name("state.v0-20261114")
    taken.mkdir()
    ready = durable.prepare_store(old_store, NOW)
    assert ready.retired_to == old_store.with_name("state.v0-20261114T120000")
    assert taken.is_dir()


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
    ("lock", "mode"),
    [
        ("../state.lock", fcntl.LOCK_SH),  # 置き場を使うほかのプロセス
        ("dbos/run-old.lock", fcntl.LOCK_EX),  # 鍵のロックを持つ今の NDF（10.x）の耐久ワークフロー
        ("progress.sqlite.lock", fcntl.LOCK_SH),  # 別のチェックアウトから進行表の DB を開いたプロセス
    ],
)
def test_retire_stops_with_store_in_use_while_a_lock_is_held(old_store, lock, mode):
    held_path = (old_store / lock).resolve()
    held = _hold(held_path, mode)
    try:
        before = _snapshot(old_store)
        with pytest.raises(durable.StoreInUse) as err:
            durable.prepare_store(old_store, NOW)
        assert err.value.kind == "store_in_use"
        assert held_path in err.value.paths
        assert _snapshot(old_store) == before
        assert not list(old_store.parent.glob("state.v*"))
    finally:
        held.close()

    ready = durable.prepare_store(old_store, NOW)
    assert ready.retired_to is not None
    # 置き場のロックと検査と走査のロックは state/ の外にあり、退避で動かない
    assert (old_store.parent / durable.STORE_LOCK).exists()
    with durable.maint_lock(old_store, wait=False) as maint:
        assert maint == old_store.parent / durable.MAINT_LOCK


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
    assert [o["retired_to"] for o in outs if o.get("retired_to")] == [str(old_store.with_name("state.v0-" + _today()))]
    assert [p.name for p in old_store.parent.glob("state.v*")] == ["state.v0-" + _today()]
    assert (old_store.with_name("state.v0-" + _today()) / "dbos" / "run-old.sqlite").read_bytes() == b"old"
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
