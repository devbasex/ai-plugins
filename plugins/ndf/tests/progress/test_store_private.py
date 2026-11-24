"""ステートマシンの保存先の閲覧範囲（`store.restrict`・`Store.private`。#3959 の「手元のファイルの閲覧範囲」）。

判定の自由記述を保存する前に、保存先を開いた OS の利用者だけが読めるようにする。限れなければ `private` を偽にし、
`append_events` が判定の自由記述を伏せて書く（伏せ方は test_run_mvv_answers.py）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.progress import store


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.mark.parametrize("where", ["default", "outside-missing", "outside-with-sides", "side-refused", "postgresql"])
def test_opening_the_store_restricts_it_to_the_owner_or_marks_it_not_private(root, tmp_path, monkeypatch, request, where):
    db = tmp_path / "p.sqlite"
    sides = [Path(f"{db}{x}") for x in ("-wal", "-shm")]
    urls = {"default": lambda: store.default_url(root), "postgresql": lambda: request.getfixturevalue("pg_url")}
    url = urls[where]() if where in urls else f"sqlite:///{db}"
    if where == "default":
        os.chmod(root, 0o755)
    if where in ("outside-with-sides", "side-refused"):
        for f in (db, *sides):
            f.write_bytes(b"")
            os.chmod(f, 0o644)
    if where == "outside-with-sides":
        # 開いた後は SQLite が副ファイルを作り直すので、接続の前の制限を直接確かめる
        assert store.restrict(root, db) is True and [_mode(f) for f in (db, *sides)] == [0o600] * 3
    if where == "side-refused":
        real = os.chmod
        monkeypatch.setattr(os, "chmod", lambda p, m, *a, **k: _refuse() if str(p) == str(sides[0]) else real(p, m, *a, **k))
    old = os.umask(0o022)
    try:
        with store.open_store(url, root) as st:
            private = st.private
    finally:
        os.umask(old)
    assert private is (where not in ("side-refused", "postgresql"))
    if where == "default":
        assert _mode(root) == 0o700
    if where.startswith("outside"):
        assert _mode(db) == 0o600


def _refuse():
    raise PermissionError("所有者でない")
