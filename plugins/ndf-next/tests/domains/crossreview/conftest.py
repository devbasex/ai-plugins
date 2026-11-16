"""cross-review のテストに共通する fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._repos import Repos, make_repos


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    return make_repos(tmp_path, monkeypatch)
