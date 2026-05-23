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

    codex round 3 指摘: `_agent_stall_default()` は呼び出し時に env を再評価する
    (import 時固定の `DEFAULT_STALL` ではなく `int(os.environ["MONITOR_STALL"])` を返す)。
    本テストは monkeypatch で `MONITOR_STALL=240` に書き換え、両 agent が 240 を
    返すことを確認する (= 共通 env が実際に反映されることの検証)。
    """
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    monkeypatch.delenv("MONITOR_STALL_GEMINI", raising=False)
    monkeypatch.setenv("MONITOR_STALL", "240")
    # 共通 env が両 agent に効く (per-agent 上書きなしの場合)
    assert monitor_mod._agent_stall_default("codex") == 240
    assert monitor_mod._agent_stall_default("gemini") == 240


def test_per_agent_env_takes_precedence_over_shared(monkeypatch, monitor_mod):
    """per-agent env > 共通 env の優先順位を確認する。"""
    monkeypatch.setenv("MONITOR_STALL", "240")
    monkeypatch.setenv("MONITOR_STALL_GEMINI", "777")
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    assert monitor_mod._agent_stall_default("gemini") == 777
    # codex 側は per-agent env が無いので 共通 env (= 240) にフォールバック
    assert monitor_mod._agent_stall_default("codex") == 240


def test_unknown_agent_falls_back_to_default_stall(monkeypatch, monitor_mod):
    """ビルトインに無い agent 名は `DEFAULT_STALL` にフォールバックする。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.delenv("MONITOR_STALL_UNKNOWN", raising=False)
    assert monitor_mod._agent_stall_default("unknown") == monitor_mod.DEFAULT_STALL


# ---------------- PLAN21 round 5: env が非数値だった場合のフォールバック ----------------


def test_shared_env_non_numeric_falls_back_to_builtin(monkeypatch, monitor_mod, capsys):
    """env `MONITOR_STALL` が非数値なら builtin にフォールバック (ValueError で落ちない)。

    gemini round 4 指摘: `int(os.environ[...])` は非数値で ValueError を出す。
    監視プロセスを env 設定ミスでクラッシュさせないため、try/except で builtin に戻す。
    """
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    monkeypatch.delenv("MONITOR_STALL_GEMINI", raising=False)
    monkeypatch.setenv("MONITOR_STALL", "abc")
    # codex / gemini とも builtin 既定 (180 / 480) に戻る
    assert monitor_mod._agent_stall_default("codex") == 180
    assert monitor_mod._agent_stall_default("gemini") == 480
    captured = capsys.readouterr()
    # 警告メッセージが stderr に出る
    assert "MONITOR_STALL" in captured.err
    assert "int に変換できません" in captured.err


def test_per_agent_env_non_numeric_falls_back_to_builtin(
    monkeypatch, monitor_mod, capsys
):
    """env `MONITOR_STALL_<AGENT>` が非数値なら builtin にフォールバック。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.setenv("MONITOR_STALL_GEMINI", "not-a-number")
    monkeypatch.delenv("MONITOR_STALL_CODEX", raising=False)
    # gemini は builtin (480) にフォールバック
    assert monitor_mod._agent_stall_default("gemini") == 480
    # codex は env 未設定なので builtin (180)
    assert monitor_mod._agent_stall_default("codex") == 180
    captured = capsys.readouterr()
    assert "MONITOR_STALL_GEMINI" in captured.err
    assert "int に変換できません" in captured.err


def test_per_agent_env_non_numeric_does_not_affect_other_agent(
    monkeypatch, monitor_mod
):
    """non-numeric な per-agent env は対象 agent だけに影響する。"""
    monkeypatch.delenv("MONITOR_STALL", raising=False)
    monkeypatch.setenv("MONITOR_STALL_GEMINI", "xxx")
    monkeypatch.setenv("MONITOR_STALL_CODEX", "200")  # codex 側は正常
    assert monitor_mod._agent_stall_default("codex") == 200
    assert monitor_mod._agent_stall_default("gemini") == 480
