"""monitor.py の per-agent stall timeout 既定値テスト (PLAN21).

`_agent_stall_default(agent)` の解決優先度:
  1. env `MONITOR_STALL_<AGENT>` (per-agent 明示)
  2. env `MONITOR_STALL` (両 agent 共通)
  3. `DEFAULT_STALL_AGENT_BUILTIN[agent]` (codex=180, gemini=480)

gemini は err.log にほぼ進捗を出さないため、ビルトイン既定を 480s と大きめに
取って 1 度目の STALLED 誤検知を避ける。codex は従来通り 180s で変更なし。
"""
from __future__ import annotations

import pytest


def test_builtin_default_codex(monkeypatch, monitor_mod):
    """codex のビルトイン既定は 180s。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    monkeypatch.delenv("MONITOR_STALL_GEMINI", raising=False)
    assert monitor_mod._agent_stall_default("codex") == 180


def test_builtin_default_gemini(monkeypatch, monitor_mod):
    """gemini のビルトイン既定は 480s (codex より大きい)。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    monkeypatch.delenv("MONITOR_STALL_GEMINI", raising=False)
    assert monitor_mod._agent_stall_default("gemini") == 480


def test_per_agent_env_overrides_builtin(monkeypatch, monitor_mod):
    """env `MONITOR_STALL_GEMINI` 設定で gemini 既定が上書きされる。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.setenv("MONITOR_STALL_GEMINI", "600")
    assert monitor_mod._agent_stall_default("gemini") == 600
    # codex は影響を受けない
    assert monitor_mod._agent_stall_default("codex") == 180


def test_shared_env_applies_to_both(monkeypatch, monitor_mod):
    """env `MONITOR_STALL` 共通指定は両 agent に同じ値が適用される (旧挙動互換)。

    `DEFAULT_STALL` モジュール定数は import 時に env を読むため固定だが、
    `_agent_stall_default()` は呼び出し時に env を見るので monkeypatch が効く。
    """
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    monkeypatch.delenv("MONITOR_STALL_GEMINI", raising=False)
    monkeypatch.setenv("MONITOR_STALL", "240")
    # 共通 env が両 agent に効く (per-agent 上書きなしの場合)
    assert monitor_mod._agent_stall_default("codex") == monitor_mod.DEFAULT_STALL
    assert monitor_mod._agent_stall_default("gemini") == monitor_mod.DEFAULT_STALL


def test_per_agent_env_takes_precedence_over_shared(monkeypatch, monitor_mod):
    """per-agent env > 共通 env の優先順位を確認する。"""
    monkeypatch.setenv("MONITOR_STALL", "240")
    monkeypatch.setenv("MONITOR_STALL_GEMINI", "777")
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    assert monitor_mod._agent_stall_default("gemini") == 777
    # codex 側は per-agent env が無いので 共通 env (= DEFAULT_STALL) にフォールバック
    assert monitor_mod._agent_stall_default("codex") == monitor_mod.DEFAULT_STALL


def test_unknown_agent_falls_back_to_default_stall(monkeypatch, monitor_mod):
    """ビルトインに無い agent 名は `DEFAULT_STALL` にフォールバックする。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.delenv("MONITOR_STALL_UNKNOWN", raising=False)
    assert monitor_mod._agent_stall_default("unknown") == monitor_mod.DEFAULT_STALL
