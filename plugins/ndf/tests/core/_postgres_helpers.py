"""PostgreSQL を使うテストが共有する fixture（`pg_url`）。使う側が明示的に import して登録する。"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory, start_postgres):
    with start_postgres(tmp_path_factory) as (_, base):
        yield base + "/postgres"
