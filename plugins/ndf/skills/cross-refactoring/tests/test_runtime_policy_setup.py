"""cross-refactoring の参加者とランタイムの宣言（`.ndf/runtimes.json`。#1598）。"""

from __future__ import annotations

import json

import pytest


def _declare(root, body):
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    f = root / ".ndf" / "runtimes.json"
    f.write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")
    return f


@pytest.fixture()
def probe_calls(cmd_setup, monkeypatch):
    calls: list[str] = []

    def probe(names, *, info, env=None, models=None, level="model"):
        calls.extend(names)
        return {n: {"command": n, "ok": True, "detail": ""} for n in names}, False

    monkeypatch.setattr(cmd_setup.auth, "probe_auth", probe)
    return calls


def test_pool_is_narrowed_and_only_allowed_are_probed(cmd_setup, tmp_path, monkeypatch, probe_calls):
    """AC2・AC5: 宣言の中の者だけを確かめ、母集合に入れる。"""
    f = _declare(tmp_path, {"allowed": ["claude", "codex"]})
    monkeypatch.chdir(tmp_path)
    p = cmd_setup.resolve_participants("kiro", [], [], False)
    assert probe_calls == ["claude", "codex"]
    assert p["pool"] == ["claude", "codex"] and p["available"] == ["claude", "codex"]
    assert p["policy"]["path"] == str(f.resolve())


@pytest.mark.parametrize(("names", "source"), [(["codex"], "--include"), ([None, "kiro"], "--implementer")])
def test_outside_names_stop_before_participants(cmd_setup, tmp_path, monkeypatch, capsys, names, source):
    """AC3・AC16: 新しく渡した宣言の外の名前は、作業ディレクトリを用意する前に中断する。"""
    f = _declare(tmp_path, {"allowed": ["claude"]})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        cmd_setup.runtime_decl.require_in_policy(names, source)
    assert e.value.code != 0
    err = capsys.readouterr().err
    assert "宣言の外" in err and str(f.resolve()) in err and "allowed: claude" in err


def test_broken_declaration_stops(cmd_setup, tmp_path, monkeypatch, capsys, probe_calls):
    """AC10: 壊れた宣言では認証確認もせずに中断する。"""
    _declare(tmp_path, "{oops")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cmd_setup.resolve_participants("claude", [], [], False)
    assert "JSON として読めない" in capsys.readouterr().err
    assert probe_calls == []


def test_resume_rebuilds_when_the_declaration_changed(cmd_setup, tmp_path, monkeypatch, probe_calls):
    """前提 8: 記録の宣言の写しと今の宣言が違えば作り直し、記録から引き継いだ外の名前を落とす。"""
    monkeypatch.chdir(tmp_path)
    state = {
        "host": "claude",
        "worktree_root": str(tmp_path / "rf"),
        "participants": {"included": ["agy"], "excluded": [], "ignored_exclude": [], "require_all": False, "policy": None},
        "implementer_named": "agy",
    }
    assert cmd_setup.runtime_decl.policy_changed(state) is False
    _declare(tmp_path, {"allowed": ["claude", "codex"]})
    assert cmd_setup.runtime_decl.policy_changed(state) is True
    cmd_setup._rebuild_participants(state, None, None, None)
    assert state["runtimes"] == ["claude", "codex"]
    assert state["implementer_named"] is None
    assert "agy" not in probe_calls
    assert {c["field"] for c in state["resume_changes"]} >= {"policy:included", "policy:implementer_named", "participants"}
