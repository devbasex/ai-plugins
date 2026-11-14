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
