"""作り直しの NDF のテストに共通する前提。

テストはプラグインの根の依存の環境（`pyproject.toml` と `uv.lock`）で流す（決定 78）。リポジトリの根から
`pytest .` で流したときは、根の環境にこのディレクトリの依存が無いため収集しない。

テストの間にサブコマンドが出す結果 JSON は、すべて結果のコントラクト（形と、状態・停止理由と終了コードの対応）とサブコマンドの結果の
スキーマ（`contracts/schemas/io/<サブコマンド>.result.schema.json`）で確かめる（fixture `_checked_emit`。#3518 の決定 9）。
形の誤った結果をわざと作るテストはマーカー `ndf_bad_result` を付ける。全体の実行の終わりに、ok の出力を 1 度も確かめなかった
サブコマンドがあれば、その名前を出して落とす（`pytest_sessionfinish`）。

使い捨ての PostgreSQL は `initdb` と `pg_ctl` で起動する（fixture `start_postgres`。実体は `support/postgres_server.py`）。GitHub Actions の ubuntu の Runner は
PostgreSQL を入れてある（サービスは止まっている）。手元に無ければ飛ばし、CI（環境変数 `CI`）では落とす。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import postgres_server
import pytest

from ndf import cli
from ndf.checks import spec_model
from ndf.contracts import result as result_contract

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# 形の誤った結果 JSON をわざと作るテストのマーカー
BAD_RESULT_MARK = "ndf_bad_result"
# 並列の実行で worker から controller へ ok を確かめたサブコマンドを渡すキー
OK_TOOLS_KEY = "ndf_ok_tools"
# 全体の実行を狭める引数（どれかがあれば ok の出力の確かめを行わない）
NARROWING_OPTIONS = ("keyword", "markexpr", "deselect", "lf", "last_failed", "failedfirst")
_ok_tools: set[str] = set()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", f"{BAD_RESULT_MARK}: 形の誤った結果 JSON をわざと作るテスト（結果のコントラクトの確かめで落とさない）"
    )


def record_output(obj: dict, code: int | None, allow_bad: bool) -> None:
    """サブコマンドが出した結果 JSON と終了コードの組を結果のコントラクトで、結果 JSON をサブコマンドの結果のスキーマで確かめる。

    確かめるのは `tool` がサブコマンドの表（とエントリポイント `cli`）にある結果だけで、ほかの名前の結果（結果のコントラクトそのものの
    テストが作る見本）は見ない。合えば ok のサブコマンドの名前を記録する。
    """
    tool = obj.get("tool") if isinstance(obj, dict) else None
    if tool not in cli.COMMANDS and tool != cli.TOOL:
        return
    errs = result_contract.errors(obj, code if code is not None else result_contract.code_for(obj))
    if errs and not allow_bad:
        pytest.fail(f"サブコマンド {tool} の結果 JSON が結果のコントラクトに合わない: {errs[:5]}")
    validator = spec_model.result_validator(tool) if tool in cli.COMMANDS else None
    shape = (
        sorted(f"/{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator.iter_errors(obj))
        if validator is not None
        else []
    )
    if shape and not allow_bad:
        pytest.fail(f"サブコマンド {tool} の結果 JSON が結果のスキーマに合わない: {shape[:5]}")
    if not errs and not shape and obj.get("status") == result_contract.OK:
        _ok_tools.add(tool)


@pytest.fixture(autouse=True)
def _checked_emit(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """テストの間だけ結果 JSON を出す 1 か所（`ndf.contracts.result.emit`）を、確かめてから元の `emit` を呼ぶ関数にする。"""
    original = result_contract.emit
    allow_bad = request.node.get_closest_marker(BAD_RESULT_MARK) is not None

    def checked(obj, code=None, stream=None):
        record_output(obj, code, allow_bad)
        return original(obj, code, stream)

    monkeypatch.setattr(result_contract, "emit", checked)


def is_full_run(config: pytest.Config) -> bool:
    """全体の実行（引数のパスがプラグインの根か `testpaths` のディレクトリだけで、絞り込みが無い）か。

    引数を書かずに流すと、pytest は `testpaths`（`pyproject.toml` の `["tests"]`）を引数にする。
    """
    whole = {PLUGIN_ROOT} | {(config.rootpath / t).resolve() for t in config.getini("testpaths")}
    base = config.invocation_params.dir
    paths = [(base / a.split("::")[0]).resolve() for a in config.args]
    if any(p not in whole for p in paths):
        return False
    return not any(getattr(config.option, name, None) for name in NARROWING_OPTIONS)


def pytest_testnodedown(node, error) -> None:
    """並列の実行で、終わった worker の記録を集める（pytest-xdist の controller で呼ばれる）。"""
    _ok_tools.update(getattr(node, "workeroutput", {}).get(OK_TOOLS_KEY, []))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """worker は記録を controller へ渡す。controller（か並列でない実行）は、全体の実行のときだけ ok の無いサブコマンドで落とす。"""
    config = session.config
    if hasattr(config, "workeroutput"):
        config.workeroutput[OK_TOOLS_KEY] = sorted(_ok_tools)
        return
    if not is_full_run(config) or exitstatus != 0:
        return
    missing = sorted(set(cli.COMMANDS) - _ok_tools)
    if missing:
        print(f"ok の出力が確かめられないサブコマンド: {', '.join(missing)}", file=sys.stderr)
        session.exitstatus = 1


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if config.rootpath.resolve() != PLUGIN_ROOT:
        return True
    return None


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """実行した人の状態の保存先とほかのプラグインの根を読まない。"""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("NDF_CALLER_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("NDF_STATE_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # エントリポイント（`ndf.cli`）がサブコマンドの前に旧い保存先（`<CLAUDE_CONFIG_DIR>/<名前>/state` など）を退避しうるため
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


@pytest.fixture(scope="session")
def start_postgres() -> Callable[[pytest.TempPathFactory], AbstractContextManager[tuple[Path, str]]]:
    """PostgreSQL を起動する contextmanager。fixture の scope は使う側が決める。"""
    return postgres_server.start_postgres


def _last_result(stdout: str) -> dict:
    """標準出力の最後の 1 行（前後の空白を除いた後）を結果 JSON として読む。"""
    return json.loads(stdout.strip().splitlines()[-1])


@pytest.fixture
def last_result():
    """標準出力の最後の 1 行を結果 JSON として読む関数（`last_result(stdout)`）。"""
    return _last_result
