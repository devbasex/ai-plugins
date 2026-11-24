"""ステートマシンの保存先の閲覧範囲（`store.restrict`・`Store.private`。#3959 の「手元のファイルの閲覧範囲」）。

判定の自由記述を保存する前に、保存先を開いた OS の利用者だけが読めるようにする。限れなければ `private` を偽にし、
`append_events` が判定の自由記述を伏せて書く（伏せ方は test_run_mvv_answers.py）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.progress import model, store


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_the_default_store_directory_becomes_owner_only(root):
    os.chmod(root, 0o755)
    with store.open_store(store.default_url(root), root) as st:
        assert st.private is True and _mode(root) == 0o700


def test_an_outside_sqlite_file_is_created_owner_only(root, tmp_path):
    db = tmp_path / "outside" / "p.sqlite"
    db.parent.mkdir()
    old = os.umask(0o022)
    try:
        with store.open_store(f"sqlite:///{db}", root) as st:
            assert st.private is True and _mode(db) == 0o600
    finally:
        os.umask(old)


def test_an_outside_sqlite_file_and_its_side_files_become_owner_only(root, tmp_path):
    db = tmp_path / "p.sqlite"
    sides = [Path(f"{db}{x}") for x in ("-wal", "-shm")]
    for f in (db, *sides):
        f.write_bytes(b"")
        os.chmod(f, 0o644)
    assert store.restrict(root, db) is True
    assert [_mode(f) for f in (db, *sides)] == [0o600] * 3


def test_a_side_file_that_cannot_be_restricted_opens_the_store_as_not_private(root, tmp_path, monkeypatch):
    db = tmp_path / "p.sqlite"
    side = Path(f"{db}-wal")
    side.write_bytes(b"")
    real = os.chmod

    def refuse(path, mode, *a, **k):
        if str(path) == str(side):
            raise PermissionError("所有者でない")
        return real(path, mode, *a, **k)

    monkeypatch.setattr(os, "chmod", refuse)
    with store.open_store(f"sqlite:///{db}", root) as st:
        assert st.private is False


def test_postgresql_is_never_private(pg_url, root):
    with store.open_store(pg_url, root) as st:
        assert st.private is False


@pytest.mark.parametrize("holds", [[], None])
def test_the_stored_view_keeps_counts_and_every_mark_of_the_verdict(holds):
    held = {
        "gate": "design",
        "skip": True,
        "verdict": {"verdict": "not_follow", "reasons": ["r1", "r2"], "boundary": ["b1"], "basis": ["Value 2"]},
        "why": ["w1", "w2", "w3"],
        "holds": ["h1", "h2"],
        "noted": True,
        "note_url": "https://github.com/o/r/pull/1#issuecomment-1",
        "pr": 1,
        "head": "abc",
        "stopped_by": {"kind": "pr_moved", "text": "定型文"},
    }
    got = model.mvv_stored_view(held)
    w = model.MVV_WITHHELD
    assert got["verdict"]["reasons"] == [w, w] and got["verdict"]["boundary"] == [w] and got["why"] == [w] * 3 and got["holds"] == [w, w]
    for key in ("skip", "pr", "head", "note_url", "noted", "stopped_by", "gate"):
        assert got[key] == held[key]
    assert (got["verdict"]["verdict"], got["verdict"]["basis"]) == ("not_follow", ["Value 2"])
    assert model.mvv_stored_view({**held, "holds": holds})["holds"] == holds
