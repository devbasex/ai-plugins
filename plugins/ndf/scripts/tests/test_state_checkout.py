"""チェックアウトの記録の移行（#2766）: 移し先の鍵のロック。"""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import state_checkout as sc  # noqa: E402
import state_home as sh  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    main = tmp_path / "work" / "repo"
    (main / ".git").mkdir(parents=True, exist_ok=True)
    return main


def _old_record(tmp_path: Path, env: dict) -> tuple[Path, Path, str]:
    """旧の置き場の耐久の記録 1 本（鍵は `exp-…`）。(旧の dbos, 移し先の dbos, 鍵)。"""
    old = sh.legacy_home(env) / "dbos"
    old.mkdir(parents=True)
    key = "exp-bbbbbbbbbbbb"
    (old / f"{key}.sqlite").write_text("old")
    (old / f"{key}.lock").write_text("")
    to = (tmp_path / "work/repo/.git/ndf/state/dbos").resolve()
    to.mkdir(parents=True)
    return old, to, key


def test_migrate_keeps_a_record_whose_destination_lock_is_held(tmp_path):
    """移し先の同じ鍵のロックを動いている起動が持っていれば、移さず残し、そのロックの実体を置き換えない。"""
    env = {"HOME": str(tmp_path / "h")}
    old, to, key = _old_record(tmp_path, env)
    with open(to / f"{key}.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inode = os.stat(to / f"{key}.lock").st_ino
        r = sc.migrate_all(env, cwd=_repo(tmp_path))
        assert os.stat(to / f"{key}.lock").st_ino == inode
    assert r["status"] == "kept"
    assert [k["path"] for k in r["dbos"]["kept"]] == [str(old / f"{key}.sqlite")]
    assert (old / f"{key}.sqlite").read_text() == "old" and not (to / f"{key}.sqlite").exists()
    r = sc.migrate_all(env, cwd=_repo(tmp_path))  # 起動が終われば次に打てば移る
    assert r["status"] == "migrated" and (to / f"{key}.sqlite").read_text() == "old"


def test_migrate_holds_the_destination_lock_while_moving(tmp_path, monkeypatch):
    """衝突の再確認から一式の移動の完了まで、移し先の同じ鍵のロックを持つ（その間に同じ鍵の起動は取れない）。"""
    import shutil

    env = {"HOME": str(tmp_path / "h")}
    _old, to, key = _old_record(tmp_path, env)
    seen: list[bool] = []
    real_move = shutil.move

    def move_and_try_lock(src, dst):
        fd = os.open(to / f"{key}.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(False)
        except OSError:
            seen.append(True)
        finally:
            os.close(fd)
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", move_and_try_lock)
    assert sc.migrate_all(env, cwd=_repo(tmp_path))["status"] == "migrated"
    assert seen and all(seen)
