"""本物の状態の置き場からテストを隔離する pytest の前提（#2776）。部品は `ndf.testing.isolation` が持つ。

`forbid_real_state` は、テストの間、状態の置き場を指す環境変数をすべてテストの一時ディレクトリへ向け、
本物の置き場（実行した人のホームの下）を開いて書くと落とす。conftest で autouse の fixture として受ける:

    from ._real_state import forbid_real_state  # noqa: F401

    @pytest.fixture(autouse=True)
    def _isolated(forbid_real_state): ...

子の pytest へは `-p _real_state`（このディレクトリを PYTHONPATH に足す）で読ませる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ndf.testing import isolation


@pytest.fixture
def forbid_real_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """状態の置き場を一時ディレクトリへ向け、本物の置き場への書き込みを落とす。一時ディレクトリの根を返す。"""
    # 守る根は差し替えの前に読む
    roots = isolation.protected_roots()
    base = tmp_path_factory.mktemp("ndf-state")
    for name, sub in isolation.REDIRECTED_ENV.items():
        monkeypatch.setenv(name, str(base / sub))
    for name in isolation.REMOVED_ENV:
        monkeypatch.delenv(name, raising=False)
    with isolation.guarding(roots, (str(tmp_path_factory.getbasetemp()),)) as found:
        yield base
    if found:
        pytest.fail("テストが本物の状態の置き場へ書こうとした: " + ", ".join(sorted(set(found))), pytrace=False)
