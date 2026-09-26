"""gh_* のテストが使う `gh` の偽物（`gh_call.RUNNER` の差し替え先）。argv の先頭一致で応答を返す。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import gh_call  # noqa: E402

RATE = "GraphQL: API rate limit exceeded for user ID 1."
REST_RATE = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"


def rest_out(body, status="200 OK", etag=None, remaining=4999):
    head = f"HTTP/2.0 {status}\nX-Ratelimit-Remaining: {remaining}\r\nX-Ratelimit-Reset: 1700000000\r\n"
    if etag:
        head += f"Etag: {etag}\r\n"
    return head + "\r\n" + (json.dumps(body) if body is not None else "")


class FakeGh:
    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, *prefix, rc=0, out="", err=""):
        self.routes.append((prefix, gh_call.GhResult(rc, out, err)))
        return self

    def on_fn(self, *prefix, fn):
        self.routes.append((prefix, fn))
        return self

    def __call__(self, args, stdin=None, cwd=None):
        self.calls.append((list(args), stdin))
        for prefix, res in self.routes:
            if tuple(args[:len(prefix)]) == prefix:
                return res(args, stdin) if callable(res) else res
        pytest.fail(f"想定外の gh の呼び出し: {args}")

    def argvs(self):
        return [" ".join(a) for a, _ in self.calls]


@pytest.fixture()
def fake(monkeypatch):
    f = FakeGh()
    monkeypatch.setattr(gh_call, "RUNNER", f)
    monkeypatch.setattr(gh_call, "_ETAGS", {})
    return f
