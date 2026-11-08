"""チェックアウトの記録の移行（#2766）: 移し先の鍵のロック・WAL の書き戻し。"""

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
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
    with sqlite3.connect(old / f"{key}.sqlite") as con:
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('old')")
    con.close()
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
    assert _values(old / f"{key}.sqlite") == ["old"] and not (to / f"{key}.sqlite").exists()
    r = sc.migrate_all(env, cwd=_repo(tmp_path))  # 起動が終われば次に打てば移る
    assert r["status"] == "migrated" and _values(to / f"{key}.sqlite") == ["old"]


def _values(db: Path) -> list:
    con = sqlite3.connect(db)
    try:
        return [v for (v,) in con.execute("SELECT v FROM t ORDER BY rowid")]
    finally:
        con.close()


def test_migrate_holds_the_destination_lock_while_moving(tmp_path, monkeypatch):
    """衝突の再確認から一式の移動の完了まで、移し先の同じ鍵のロックを持つ（その間に同じ鍵の起動は取れない）。"""
    env = {"HOME": str(tmp_path / "h")}
    _old, to, key = _old_record(tmp_path, env)
    seen: list[bool] = []
    real_copy = shutil.copy2

    def copy_and_try_lock(src, dst):
        fd = os.open(to / f"{key}.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(False)
        except OSError:
            seen.append(True)
        finally:
            os.close(fd)
        return real_copy(src, dst)

    monkeypatch.setattr(shutil, "copy2", copy_and_try_lock)
    assert sc.migrate_all(env, cwd=_repo(tmp_path))["status"] == "migrated"
    assert seen and all(seen)


def _wal_record(old: Path, key: str, rows: int) -> None:
    """確定済みの行が WAL にだけある記録 `old/<key>.sqlite`（と `-wal`）を作る（開いている間に写した形）。"""
    work = old / ".make"
    work.mkdir()
    con = sqlite3.connect(work / "t.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t (v TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [(str(i),) for i in range(rows)])
    con.commit()
    shutil.copy(work / "t.sqlite", old / f"{key}.sqlite")
    shutil.copy(work / "t.sqlite-wal", old / f"{key}.sqlite-wal")
    con.close()
    shutil.rmtree(work)


def test_migrate_folds_committed_wal_rows_into_the_moved_record(tmp_path, monkeypatch):
    """WAL にだけある確定済みの行は本体へ書き戻して移し、移し先の本体だけで全部読める。WAL は移さない
    （WAL の移動が失敗しても本体だけが公開されて行が欠けることが無い）。移し元に記録は残らない。"""
    env = {"HOME": str(tmp_path / "h")}
    old = sh.legacy_home(env) / "dbos"
    old.mkdir(parents=True)
    key = "exp-cccccccccccc"
    _wal_record(old, key, 5)
    (old / f"{key}.lock").write_text("")
    real_move = shutil.move

    def refuse_wal(src, dst, *a, **k):
        if str(src).endswith("-wal"):
            raise OSError("WAL の移動の失敗")
        return real_move(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "move", refuse_wal)
    r = sc.migrate_all(env, cwd=_repo(tmp_path))
    to = (tmp_path / "work/repo/.git/ndf/state/dbos").resolve()
    assert r["status"] == "migrated" and r["dbos"]["moved"] == [str(to / f"{key}.sqlite")]
    assert sorted(p.name for p in to.iterdir()) == [f"{key}.lock", f"{key}.sqlite"]
    assert _values(to / f"{key}.sqlite") == [str(i) for i in range(5)]
    assert not old.exists()


def test_migrate_keeps_a_record_whose_wal_cannot_be_folded(tmp_path):
    """本体を読めず WAL を書き戻せなければ、移さず理由つきで残す。"""
    env = {"HOME": str(tmp_path / "h")}
    old = sh.legacy_home(env) / "dbos"
    old.mkdir(parents=True)
    key = "exp-dddddddddddd"
    (old / f"{key}.sqlite").write_text("not a database")
    (old / f"{key}.lock").write_text("")
    r = sc.migrate_all(env, cwd=_repo(tmp_path))
    assert r["status"] == "kept"
    assert [k["path"] for k in r["dbos"]["kept"]] == [str(old / f"{key}.sqlite")]
    assert "書き戻せない" in r["dbos"]["kept"][0]["reason"]
    assert (old / f"{key}.sqlite").read_text() == "not a database"
    assert not (tmp_path / "work/repo/.git/ndf/state/dbos" / f"{key}.sqlite").exists()
