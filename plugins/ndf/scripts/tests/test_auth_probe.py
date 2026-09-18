from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


def _load_auth():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("ndf_lib_auth_probe", LIB / "auth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unknown_runtime_is_ignored():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    results = auth.check_auth(["unknown"], info=messages.append, die=failures.append, env={})

    assert "unknown" not in results
    assert failures == []


def test_probe_timeout_is_reported():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def time_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    results = auth.check_auth(
        ["codex"], info=messages.append, die=failures.append, env={}, runner=time_out
    )

    assert results["codex"]["ok"] is False
    assert str(auth.AUTH_PROBE_TIMEOUT) in results["codex"]["detail"]
    assert len(failures) == 1


def test_command_not_found_is_reported():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def not_found(*args, **kwargs):
        raise FileNotFoundError()

    results = auth.check_auth(
        ["agy"], info=messages.append, die=failures.append, env={}, runner=not_found
    )

    assert results["agy"]["ok"] is False
    assert results["agy"]["detail"] == "コマンドが見つかりません"
    assert "agy（コマンドが見つかりません）" in failures[0]


def test_nonzero_exit_is_reported():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def nonzero(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="error detail")

    results = auth.check_auth(
        ["claude"], info=messages.append, die=failures.append, env={}, runner=nonzero
    )

    assert results["claude"]["ok"] is False
    assert results["claude"]["detail"] == "error detail"
    assert "claude（error detail）" in failures[0]


def test_unauthenticated_marker_is_reported():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def unauth(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="not logged in", stderr="")

    results = auth.check_auth(
        ["kiro"], info=messages.append, die=failures.append, env={}, runner=unauth
    )

    assert results["kiro"]["ok"] is False
    assert results["kiro"]["detail"] == "not logged in"
    assert "kiro（not logged in）" in failures[0]


def test_multiple_failures_aggregation():
    auth = _load_auth()
    messages: list[str] = []
    failures: list[str] = []

    def fail_probe(cmd, **kwargs):
        if "codex" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="codex err")
        if "agy" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="agy err")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok", stderr="")

    results = auth.check_auth(
        ["codex", "agy"], info=messages.append, die=failures.append, env={}, runner=fail_probe
    )

    assert len(failures) == 1
    assert "認証されていない CLI があります: codex（codex err） / agy（agy err）。" in failures[0]
