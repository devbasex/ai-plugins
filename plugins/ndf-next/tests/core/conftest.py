"""ndf.core のテストが共有する fixture。"""

from __future__ import annotations

import pytest

from ndf.core import durable


@pytest.fixture
def root(tmp_path):
    """チェックアウトの置き場。終わったら `prepare_store` が持った共有のロックを放す。"""
    path = tmp_path / "ndf" / "state"
    yield path
    durable.release_store(path)


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory, start_postgres):
    """使い捨ての PostgreSQL の URL（末尾の DB 名は postgres）。"""
    with start_postgres(tmp_path_factory) as (_, base):
        yield base + "/postgres"
