"""ndf.progress のテストが共有する fixture。

進行表の DB の置き場は 2 つある。**ローカル（conductor の手元・CI の Runner）は SQLite、Factory は PostgreSQL の URL** を
`NDF_PROGRESS_DB` で渡す。同じテストを両方の置き場で流すため、`progress_url` は SQLite と PostgreSQL の 2 つに
パラメータで分かれる。PostgreSQL は使い捨ての DB を `initdb` と `pg_ctl` で起こす（GitHub Actions の ubuntu の Runner は
PostgreSQL を入れてある）。手元に無ければ PostgreSQL の側だけ飛ばし、CI（環境変数 `CI`）では落とす。
SQLite に固有の検査・控え・戻し（P1-1・P1-2・P1-5）は `test_store_sqlite.py`、PostgreSQL で行わないことの確かめ（P1-4）は
`test_store_pg.py` にある。
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import pytest

from ndf.core import durable
from ndf.progress import store


def _pg_bin() -> Path | None:
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/pg_ctl"))
    return Path(candidates[-1]).parent if candidates else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory):
    """使い捨ての PostgreSQL の URL（テストごとに DB を分けるため、末尾の DB 名は付けない）。"""
    bindir = _pg_bin()
    missing = "PostgreSQL（pg_ctl）が無い" if bindir is None else "root では initdb を打てない" if os.geteuid() == 0 else ""
    if missing:
        if os.environ.get("CI"):
            pytest.fail(f"CI で PostgreSQL を起こせない: {missing}")
        pytest.skip(missing)
    data = tmp_path_factory.mktemp("pgdata")
    port = _free_port()
    subprocess.run([bindir / "initdb", "-D", data, "-U", "ndf", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True)
    options = f"-c listen_addresses=127.0.0.1 -p {port} -k {data}"
    subprocess.run([bindir / "pg_ctl", "-D", data, "-o", options, "-l", data / "log", "-w", "start"], check=True, capture_output=True)
    try:
        yield bindir, f"postgresql://ndf@127.0.0.1:{port}"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], check=False, capture_output=True)


@pytest.fixture
def pg_url(pg_server):
    """テストごとに新しい DB を作った PostgreSQL の URL。"""
    bindir, base = pg_server
    name = "t" + uuid.uuid4().hex[:16]
    subprocess.run(
        [bindir / "createdb", "-h", "127.0.0.1", "-p", base.rsplit(":", 1)[1], "-U", "ndf", name], check=True, capture_output=True
    )
    return f"{base}/{name}"


@pytest.fixture(autouse=True)
def _no_progress_db(monkeypatch):
    """実行した人の `NDF_PROGRESS_DB` を読まない。"""
    monkeypatch.delenv("NDF_PROGRESS_DB", raising=False)


@pytest.fixture
def root(tmp_path):
    """チェックアウトの置き場。終わったら `prepare_store` が持った共有のロックを放す。"""
    path = tmp_path / "repo" / ".git" / "ndf" / "state"
    path.mkdir(parents=True)
    yield path
    durable.release_store(path)


@pytest.fixture(params=["sqlite", "postgresql"])
def progress_url(request, root):
    """同じテストを SQLite と PostgreSQL の両方で流す接続先。"""
    if request.param == "sqlite":
        return store.default_url(root)
    return request.getfixturevalue("pg_url")


@pytest.fixture
def opened(progress_url, root):
    with store.open_store(progress_url, root) as st:
        yield st
