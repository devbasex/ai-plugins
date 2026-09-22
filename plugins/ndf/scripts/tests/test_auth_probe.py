"""認証状態の確認（`lib/auth.py`）のテスト。

主題は止めない確認 `probe_auth`（#727）である。失敗しても例外を上げず、`ok` と理由を
返し、`NDF_SKIP_AUTH_CHECK` が立てば確認コマンドを 1 回も呼ばない（AC5 / AC6）。
従来の `check_auth` のテストは、その関数を消す Pull Request（P7）まで末尾に残す。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


def _load_auth():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("ndf_lib_auth_probe", LIB / "auth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def auth():
    return _load_auth()


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


# ---------- probe_auth: 失敗の 4 つの形（AC6） ----------

def test_probe_reports_a_missing_command(auth, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError(args[0][0])

    monkeypatch.setattr(auth.subprocess, "run", missing)
    messages: list[str] = []

    results, skipped = auth.probe_auth(["codex"], info=messages.append, env={})

    assert skipped is False
    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == "コマンドが見つかりません"
    assert results["codex"]["command"] == "codex login status"
    assert messages == ["❌ codex: codex login status"]


def test_probe_reports_a_timeout(auth, monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(auth.subprocess, "run", time_out)

    results, skipped = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert skipped is False
    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == f"{auth.AUTH_PROBE_TIMEOUT} 秒で応答しませんでした"


def test_probe_reports_a_nonzero_exit(auth, monkeypatch):
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: _completed(cmd, 1, stdout="", stderr="error: no session\n"),
    )

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is False
    assert results["codex"]["detail"] == "error: no session"


def test_probe_reports_an_unauthenticated_marker_despite_exit_zero(auth, monkeypatch):
    """kiro は成否を終了コードで表さない。終了コード 0 でも文言で未認証を拾う。"""
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: _completed(cmd, 0, stdout="Not logged in\n"),
    )

    results, _ = auth.probe_auth(["kiro"], info=lambda _m: None, env={})

    assert results["kiro"]["ok"] is False
    assert results["kiro"]["detail"] == "Not logged in"


# ---------- probe_auth: 成功と飛ばし ----------

def test_probe_reports_success(auth, monkeypatch):
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: _completed(cmd, 0, stdout="Logged in as x\n"),
    )
    messages: list[str] = []

    results, skipped = auth.probe_auth(["claude"], info=messages.append, env={})

    assert skipped is False
    assert results["claude"] == {
        "command": "claude auth status", "ok": True, "detail": "Logged in as x",
    }
    assert messages == ["✅ claude: claude auth status"]


def test_probe_truncates_detail_to_200_chars(auth, monkeypatch):
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: _completed(cmd, 1, stderr="x" * 300),
    )

    results, _ = auth.probe_auth(["codex"], info=lambda _m: None, env={})

    assert len(results["codex"]["detail"]) == 200


def test_probe_skips_without_running_any_command(auth, monkeypatch):
    """`NDF_SKIP_AUTH_CHECK` が立つと確認コマンドは 1 回も呼ばれない（AC5）。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd)) or _completed(cmd, 0),
    )
    messages: list[str] = []

    results, skipped = auth.probe_auth(
        ["codex", "agy"], info=messages.append, env={auth.SKIP_ENV: "1"},
    )

    assert (results, skipped) == ({}, True)
    assert calls == []
    assert messages == [f"⚠ {auth.SKIP_ENV} が設定されているため認証確認を飛ばしました"]


def test_probe_ignores_an_unknown_runtime(auth, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        auth.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd)) or _completed(cmd, 0),
    )

    results, skipped = auth.probe_auth(["unknown", "codex"], info=lambda _m: None, env={})

    assert skipped is False
    assert list(results) == ["codex"]
    assert calls == [["codex", "login", "status"]]


def test_probe_never_raises_and_returns_every_runtime(auth, monkeypatch):
    """1 者の失敗で残りの確認が止まらない。"""
    def run(cmd, **kw):
        if cmd[0] == "codex":
            raise FileNotFoundError(cmd[0])
        return _completed(cmd, 0, stdout="ok")

    monkeypatch.setattr(auth.subprocess, "run", run)

    results, _ = auth.probe_auth(["codex", "agy"], info=lambda _m: None, env={})

    assert results["codex"]["ok"] is False
    assert results["agy"]["ok"] is True


# ---------- check_auth（従来の確認。P7 で消す） ----------

def test_unknown_runtime_is_ignored():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    results = auth.check_auth(["unknown"], info=messages.append, die=failures.append, env={})

    assert "unknown" not in results
    assert failures == []


def test_probe_timeout_is_reported(monkeypatch):
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(auth.subprocess, "run", time_out)
    results = auth.check_auth(["codex"], info=messages.append, die=failures.append, env={})

    assert results["codex"]["ok"] is False
    assert str(auth.AUTH_PROBE_TIMEOUT) in results["codex"]["detail"]
    assert len(failures) == 1


def test_unauthenticated_marker_fails_even_when_probe_exits_zero(monkeypatch):
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    monkeypatch.setattr(
        auth.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Not logged in", stderr=""
        ),
    )

    results = auth.check_auth(["codex"], info=messages.append, die=failures.append, env={})

    assert results["codex"]["ok"] is False
    assert len(failures) == 1
