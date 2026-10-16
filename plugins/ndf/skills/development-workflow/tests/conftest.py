"""進行の記録のテストのフィクスチャ。

判定は `plugins/ndf/scripts/lib/projects-common.sh` に集約されている。テストはこの層に
対して書き、GitHub への通信は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from projects_helpers import init_repo


@pytest.fixture(autouse=True)
def _hook_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wf_split` の語の分割（hook.py words）を、テストを流す python で起動する（#1142 の決定 20）。"""
    monkeypatch.setenv("NDF_HOOK_PYTHON", sys.executable)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """宣言を置く前のリポジトリ。

    進捗記録のスクリプトは通過記録を自分で積む（#725）。置き場所を試験用へ向け、実行した人の
    状態ディレクトリへ書かない。
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return init_repo(tmp_path / "main")
