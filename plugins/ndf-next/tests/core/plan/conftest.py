"""ndf.core.plan のテストが共有する fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from . import _plan_helpers as H


@pytest.fixture
def repo(tmp_path) -> Path:
    return H.make_repo(tmp_path / "repo")
