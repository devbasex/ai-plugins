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
