"""monitor.py の import 時 env 評価 safety テスト (PLAN21 round 6 / 最終).

codex round 5 指摘: `DEFAULT_TIMEOUT` / `DEFAULT_STALL` / `DEFAULT_POLL` を
`int(os.environ.get(name, ...))` で **import 時** に評価していると、
`MONITOR_STALL=abc` のような非数値 env が設定されているだけで
`_agent_stall_default()` に到達する前に `int()` の `ValueError` で
monitor.py の import そのものが落ち、監視プロセスが起動できなくなる。

修正方針:
  - `DEFAULT_TIMEOUT` / `DEFAULT_STALL` / `DEFAULT_POLL` は import 時に
    **固定数値** (420 / 180 / 15) で保持する
  - env の解釈は `_agent_stall_default()` / `_safe_int_env()` 内で
    try/except 付きで行う (既に round 4 で実装済み)

本テストは fixture 経由ではなく **毎回 fresh に source loader で import** し、
非数値 env が設定された状態でも import が成功することを確認する。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest


_HERE = pathlib.Path(__file__).resolve().parent
_MONITOR = _HERE.parent / "scripts" / "monitor.py"


def _fresh_import_monitor(name: str = "cross_review_monitor_fresh") -> types.ModuleType:
    """毎回 fresh に monitor.py を import する (sys.modules キャッシュを使わない)。

    session-scoped fixture (`monitor_mod`) は最初の import 時の env を保持してしまい
    本テストでは「非数値 env がセットされた状態での import が安全か」を検証できない。
    """
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _MONITOR)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_import_succeeds_with_non_numeric_monitor_stall(monkeypatch):
    """MONITOR_STALL=abc でも monitor.py の import が成功すること (codex round 5)."""
    monkeypatch.setenv("MONITOR_STALL", "abc")
    mod = _fresh_import_monitor("cross_review_monitor_fresh_stall")
    # 固定 default が保たれる
    assert mod.DEFAULT_STALL == 180
    # 呼び出し時の解決も builtin にフォールバック (既存 round 4 挙動と同じ)
    assert mod._agent_stall_default("codex") == 180
    assert mod._agent_stall_default("agy") == 480


def test_import_succeeds_with_non_numeric_monitor_timeout(monkeypatch):
    """MONITOR_TIMEOUT=xxx でも monitor.py の import が成功すること."""
    monkeypatch.setenv("MONITOR_TIMEOUT", "xxx")
    mod = _fresh_import_monitor("cross_review_monitor_fresh_timeout")
    # 固定 default が保たれる
    assert mod.DEFAULT_TIMEOUT == 420
    # safe parse ヘルパも fallback を返す
    assert mod._safe_int_env("MONITOR_TIMEOUT", mod.DEFAULT_TIMEOUT) == 420


def test_import_succeeds_with_non_numeric_monitor_poll(monkeypatch):
    """MONITOR_POLL=foo でも monitor.py の import が成功すること."""
    monkeypatch.setenv("MONITOR_POLL", "foo")
    mod = _fresh_import_monitor("cross_review_monitor_fresh_poll")
    assert mod.DEFAULT_POLL == 15
    assert mod._safe_int_env("MONITOR_POLL", mod.DEFAULT_POLL) == 15


def test_import_succeeds_with_all_non_numeric_envs(monkeypatch):
    """3 つ全て非数値でも import に失敗しないこと (組み合わせ防御)."""
    monkeypatch.setenv("MONITOR_STALL", "abc")
    monkeypatch.setenv("MONITOR_TIMEOUT", "xxx")
    monkeypatch.setenv("MONITOR_POLL", "???")
    mod = _fresh_import_monitor("cross_review_monitor_fresh_all")
    assert mod.DEFAULT_STALL == 180
    assert mod.DEFAULT_TIMEOUT == 420
    assert mod.DEFAULT_POLL == 15


def test_safe_int_env_returns_value_when_numeric(monkeypatch, monitor_mod):
    """数値が設定されている場合は素直に int を返す."""
    monkeypatch.setenv("SOME_TEST_INT_ENV", "42")
    assert monitor_mod._safe_int_env("SOME_TEST_INT_ENV", 1) == 42


def test_safe_int_env_returns_fallback_when_missing(monkeypatch, monitor_mod):
    """env が未設定なら fallback を返す."""
    monkeypatch.delenv("SOME_TEST_INT_ENV", raising=False)
    assert monitor_mod._safe_int_env("SOME_TEST_INT_ENV", 99) == 99


def test_safe_int_env_warns_on_non_numeric(monkeypatch, monitor_mod, capsys):
    """非数値時は stderr に警告を出して fallback を返す."""
    monkeypatch.setenv("SOME_TEST_INT_ENV", "not-a-number")
    assert monitor_mod._safe_int_env("SOME_TEST_INT_ENV", 7) == 7
    captured = capsys.readouterr()
    assert "SOME_TEST_INT_ENV" in captured.err
    assert "int に変換できません" in captured.err
