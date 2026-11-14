"""作り直しの NDF のテストに共通する前提。

テストはプラグインの根の依存の環境（`pyproject.toml` と `uv.lock`）で流す（決定 78）。リポジトリの根から
`pytest .` で流したときは、根の環境にこのディレクトリの依存が無いため収集しない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if config.rootpath.resolve() != PLUGIN_ROOT:
        return True
    return None


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """実行した人の状態の置き場とほかのプラグインの根を読まない。"""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("NDF_CALLER_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _last_result(stdout: str) -> dict:
    """標準出力の最後の 1 行（前後の空白を除いた後）を結果 JSON として読む。"""
    return json.loads(stdout.strip().splitlines()[-1])


@pytest.fixture
def last_result():
    """標準出力の最後の 1 行を結果 JSON として読む関数（`last_result(stdout)`）。"""
    return _last_result
