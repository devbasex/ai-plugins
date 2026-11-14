"""作り直しの NDF のテストに共通する前提。

テストはプラグインの根の依存の環境（`pyproject.toml` と `uv.lock`）で流す（決定 78）。リポジトリの根から
`pytest .` で流したときは、根の環境にこのディレクトリの依存が無いため収集しない。

使い捨ての PostgreSQL は `initdb` と `pg_ctl` で起こす（fixture `start_postgres`。実体は `support/postgres_server.py`）。GitHub Actions の ubuntu の Runner は
PostgreSQL を入れてある（サービスは止まっている）。手元に無ければ飛ばし、CI（環境変数 `CI`）では落とす。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import postgres_server
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
    monkeypatch.delenv("NDF_STATE_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # エントリポイント（`ndf.cli`）が副命令の前に旧い置き場（`<CLAUDE_CONFIG_DIR>/<名前>/state` など）を退避しうるため
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


@pytest.fixture(scope="session")
def start_postgres() -> Callable[[pytest.TempPathFactory], AbstractContextManager[tuple[Path, str]]]:
    """PostgreSQL を起こす contextmanager。fixture の scope は使う側が決める。"""
    return postgres_server.start_postgres


def _last_result(stdout: str) -> dict:
    """標準出力の最後の 1 行（前後の空白を除いた後）を結果 JSON として読む。"""
    return json.loads(stdout.strip().splitlines()[-1])


@pytest.fixture
def last_result():
    """標準出力の最後の 1 行を結果 JSON として読む関数（`last_result(stdout)`）。"""
    return _last_result
