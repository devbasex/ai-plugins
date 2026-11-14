"""ndf.progress のテストが共有する fixture。

進行表の DB の置き場は 2 つある。**ローカル（conductor の手元・CI の Runner）は SQLite、Factory は PostgreSQL の URL** を
`NDF_PROGRESS_DB` で渡す。同じテストを両方の置き場で流すため、`progress_url` は SQLite と PostgreSQL の 2 つに
パラメータで分かれる。PostgreSQL は使い捨ての DB を `initdb` と `pg_ctl` で起こす（GitHub Actions の ubuntu の Runner は
PostgreSQL を入れてある）。手元に無ければ PostgreSQL の側だけ飛ばし、CI（環境変数 `CI`）では落とす。
SQLite に固有の検査・控え・戻し（P1-1・P1-2・P1-5）は `test_store_sqlite.py`、PostgreSQL で行わないことの確かめ（P1-4）は
`test_store_pg.py` にある。
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from ndf.core import durable
from ndf.progress import command, model, store


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory, start_postgres):
    """使い捨ての PostgreSQL の URL（テストごとに DB を分けるため、末尾の DB 名は付けない）。"""
    with start_postgres(tmp_path_factory) as server:
        yield server


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
def repo(tmp_path, monkeypatch):
    """git で初めた空のリポジトリ（作業ディレクトリをそこへ移す）。終わったら置き場の共有のロックを放す。"""
    path = tmp_path / "work"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    yield path
    durable.release_store(command.store_root_of(path))


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


@pytest.fixture
def refused():
    """断られる操作の理由を返す関数。トランザクションの内側で `fn(conn)` を打ち、`Refused` がトランザクションの外へ
    伝わって（書いた分を戻して）から捕まえる。"""

    def run(st, fn):
        with pytest.raises(model.Refused) as got, store.transaction(st) as conn:
            fn(conn)
        return got.value.reason

    return run
