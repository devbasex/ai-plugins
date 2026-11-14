"""依存の環境の置き場・根の目印・片付け（M3-H14・#3105）。"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
from pathlib import Path

from ndf.domains.hooks import env, tables

DAY = 86400.0


def _plugin(root: Path, lock: str = "lock-a") -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (root / "uv.lock").write_text(lock, encoding="utf-8")
    return root


def _ident(tmp_path: Path, name: str = "p", lock: str = "lock-a") -> env.Ident:
    return env.Ident.of(_plugin(tmp_path / name, lock), {"XDG_CACHE_HOME": str(tmp_path / "cache")})


def _venv(ident: env.Ident, name: str, used_at: float) -> Path:
    path = ident.venv_root / name
    (path / "bin").mkdir(parents=True)
    (path / env.LAST_USED).touch()
    os.utime(path / env.LAST_USED, (used_at, used_at))
    return path


def test_places_follow_the_lock_hash(tmp_path):
    a = _ident(tmp_path, "a", "one")
    b = _ident(tmp_path, "b", "two")
    assert env.venv_dir(a) != env.venv_dir(b) and env.venv_dir(a).parent == tmp_path / "cache" / "demo" / "venv"
    assert env.venv_python(a) == env.venv_dir(a) / "bin" / "python"


def test_mark_root_writes_once_per_content(tmp_path):
    ident = _ident(tmp_path)
    assert env.mark_root(ident) is True and env.mark_root(ident) is False
    marks = list(ident.roots_dir.glob("*.json"))
    assert len(marks) == 1 and json.loads(marks[0].read_text(encoding="utf-8"))["lock_hash"] == env.lock_hash(ident.plugin_root)


def test_prune_keeps_marked_recent_and_locked_places(tmp_path):
    now = 100 * DAY
    keep = tables.policy_value("hook.venv-keep", "seconds")
    ident = _ident(tmp_path)
    env.mark_root(ident)
    marked = _venv(ident, env.lock_hash(ident.plugin_root), 0)
    recent = _venv(ident, "recent", now - keep + DAY)
    old = _venv(ident, "old", now - keep - DAY)
    locked = _venv(ident, "locked", 0)
    fd = os.open(ident.venv_root / "locked.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        removed = env.prune(ident, now)
    finally:
        os.close(fd)
    assert removed == [old]
    assert marked.exists() and recent.exists() and locked.exists() and not old.exists()


def test_prune_drops_marks_of_removed_installations(tmp_path):
    gone = _ident(tmp_path, "gone", "old-lock")
    env.mark_root(gone)
    stale = _venv(gone, env.lock_hash(gone.plugin_root), 0)
    shutil.rmtree(gone.plugin_root)
    live = _ident(tmp_path, "live", "new-lock")
    assert env.prune(live, 100 * DAY) == [stale]
    assert list(live.roots_dir.glob("*.json")) == []


def test_prepare_does_not_rebuild_a_place_in_use(tmp_path):
    ident = _ident(tmp_path)
    python = env.venv_python(ident)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    assert env.prepare(ident, uv="/nonexistent/uv") == env.venv_dir(ident)


def test_failed_prepare_leaves_the_reason_and_no_place(tmp_path):
    ident = _ident(tmp_path)
    assert env.prepare(ident, uv="/nonexistent/uv") is None
    assert "uv" in env.failed_path(ident).read_text(encoding="utf-8")
    assert not env.venv_dir(ident).exists() and not list(ident.venv_root.glob(".*tmp*"))


def test_prepare_skips_while_another_prepare_holds_the_lock(tmp_path):
    ident = _ident(tmp_path)
    lock = ident.venv_root / f"{env.lock_hash(ident.plugin_root)}.lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert env.preparing(ident) is True
        assert env.prepare(ident, uv="/nonexistent/uv") is None
        assert env.kick_prepare(ident) is False
        assert not env.failed_path(ident).exists()
    finally:
        os.close(fd)
    assert env.preparing(ident) is False


def test_touch_used_only_marks_an_existing_place(tmp_path):
    ident = _ident(tmp_path)
    env.touch_used(ident)
    assert not env.venv_dir(ident).exists()
    env.venv_dir(ident).mkdir(parents=True)
    env.touch_used(ident)
    assert (env.venv_dir(ident) / env.LAST_USED).exists()
