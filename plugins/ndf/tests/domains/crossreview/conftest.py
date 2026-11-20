"""cross-review のテストに共通する fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._repos import Repos, make_repos


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    return make_repos(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _runtimes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """担当すべてを起動できる前提にする（学習の設定のその場の確かめ `training.ensure` は HTTP を打つ。#3907）。

    始める前の確かめそのものを見るテストは `training.ensure` を上書きする。
    """
    from ndf.adapters import training

    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: None)
