"""`ndf gws` のテストの fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from . import Fakes


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    return Fakes.on_path(tmp_path, monkeypatch)
