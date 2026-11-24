"""ok の出力の記録（`conftest.py` の `pytest_sessionfinish`、#4331 の決定 8）。

`NDF_OK_TOOLS_OUT` を渡された実行（CI の分けたジョブ）は記録を書くだけで自分では落とさず、渡さない全体の実行は
ok を確かめなかったサブコマンドで落とす。`session` と `config` は偽物を渡し、子プロセスの pytest は起動しない。
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

CONFTEST = Path(__file__).resolve().parent / "conftest.py"


@pytest.fixture
def conftest(request: pytest.FixtureRequest) -> ModuleType:
    """pytest が読み込んだこのディレクトリの `conftest.py`（モジュールの状態 `_ok_tools` を共有する同じもの）。"""
    for plugin in request.config.pluginmanager.get_plugins():
        if isinstance(plugin, ModuleType) and Path(getattr(plugin, "__file__", "") or "").resolve() == CONFTEST:
            return plugin
    raise AssertionError("conftest.py が読み込まれていない")


@pytest.fixture
def session(conftest: ModuleType, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """全体の実行で、サブコマンド `a`・`b` のうち `a` だけ ok を確かめた終わりの状態。"""
    monkeypatch.setattr(conftest.cli, "COMMANDS", {"a": None, "b": None})
    monkeypatch.setattr(conftest, "_ok_tools", {"a"})
    monkeypatch.setattr(conftest, "is_full_run", lambda config: True)
    return SimpleNamespace(config=SimpleNamespace(), exitstatus=0)


def test_without_out_full_run_fails_on_unverified(conftest, session, monkeypatch) -> None:
    monkeypatch.delenv(conftest.OK_TOOLS_OUT_ENV, raising=False)

    conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1


def test_with_out_only_writes_record(conftest, session, monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "ok-tools.txt"
    monkeypatch.setattr(conftest, "_ok_tools", {"b-not-a-command", "a"})
    monkeypatch.setenv(conftest.OK_TOOLS_OUT_ENV, str(out))

    conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    assert out.read_text(encoding="utf-8") == "a\nb-not-a-command\n"


def test_empty_out_is_same_as_unset(conftest, session, monkeypatch) -> None:
    monkeypatch.setenv(conftest.OK_TOOLS_OUT_ENV, "")

    conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
