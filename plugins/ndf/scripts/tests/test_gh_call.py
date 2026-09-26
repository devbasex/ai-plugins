"""GitHub の呼び出しの最下層（lib/gh_call.py・#1142 の L0・不足 g）。ETag 付きの読み直しと、githubkit と gh api の切り替え。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_fake import fake, rest_out  # noqa: E402,F401
import gh_call  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"


def test_rest_keeps_the_argv_of_a_plain_request(fake):
    fake.on("api", "-i", "repos/o/r/pulls/1", out=rest_out({"number": 1}))
    resp = gh_call.rest("repos/o/r/pulls/1")
    assert resp.body == {"number": 1} and resp.rate_remaining == 4999 and resp.status == 200
    assert fake.argvs() == ["api -i repos/o/r/pulls/1"]


def test_rest_failure_is_none_and_request_keeps_the_reason(fake):
    fake.on("api", rc=1, out="", err="HTTP 404: Not Found")
    assert gh_call.rest("repos/o/r/pulls/9") is None
    resp = gh_call.request("repos/o/r/pulls/9")
    assert not resp.ok or resp.error
    assert "404" in resp.error


def test_rest_cached_sends_the_etag_and_a_304_is_not_a_failure(fake):
    """読み直しは If-None-Match を付け、304（gh api は終了コード 1）なら前の本文を返す。"""
    responses = [(0, rest_out({"state": "open"}, etag='W/"e1"')),
                 (1, rest_out(None, status="304 Not Modified", etag='W/"e1"'))]
    fake.on_fn("api", fn=lambda args, stdin: gh_call.GhResult(*responses.pop(0), "gh: HTTP 304" if not responses else ""))
    first = gh_call.rest_cached("repos/o/r/pulls/1")
    second = gh_call.rest_cached("repos/o/r/pulls/1")
    assert first.body == {"state": "open"} and not first.not_modified
    assert second.body == {"state": "open"} and second.not_modified and second.status == 304 and second.ok
    assert fake.calls[0][0] == ["api", "-i", "repos/o/r/pulls/1"]
    assert fake.calls[1][0] == ["api", "-H", 'If-None-Match: W/"e1"', "-i", "repos/o/r/pulls/1"]


def test_rest_cached_forgets_nothing_when_the_read_fails(fake):
    responses = [(0, rest_out({"state": "open"}, etag='"e1"'), ""), (1, "", "HTTP 502")]
    fake.on_fn("api", fn=lambda args, stdin: gh_call.GhResult(*responses.pop(0)))
    gh_call.rest_cached("p")
    bad = gh_call.rest_cached("p")
    assert bad.error and gh_call._ETAGS["p"] == ('"e1"', {"state": "open"})


def test_client_is_not_used_when_the_runner_is_replaced(fake):
    """RUNNER を差し替えたテストは githubkit が入っていても gh api を使い、GitHub へ届かない。"""
    assert gh_call.client() is None


class FakeResponse:
    def __init__(self, status, body, headers=None, revalidated=False):
        import json
        self.status_code, self.headers = status, headers or {}
        self.content = json.dumps(body).encode() if body is not None else b""
        self._body = body
        self.raw_response = type("Raw", (), {"extensions": {"hishel_revalidated": revalidated}})()

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, json=None, headers=None):
        self.calls.append((method, url, json, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_request_goes_through_githubkit_when_available(monkeypatch):
    c = FakeClient(FakeResponse(200, {"number": 1}, {"X-RateLimit-Remaining": "10", "ETag": '"e"'}),
                   FakeResponse(200, {"number": 1}, {"ETag": '"e"'}, revalidated=True))
    monkeypatch.setattr(gh_call, "client", lambda: c)
    monkeypatch.setattr(gh_call, "_ETAGS", {})
    first = gh_call.rest_cached("repos/o/r/pulls/1")
    second = gh_call.rest_cached("repos/o/r/pulls/1")
    assert first.body == {"number": 1} and first.rate_remaining == 10 and not first.not_modified
    assert second.not_modified and second.body == {"number": 1}
    assert c.calls[0] == ("GET", "/repos/o/r/pulls/1", None, None)


def test_githubkit_failures_become_a_response_with_the_reason(monkeypatch):
    class Failed(Exception):
        def __init__(self):
            super().__init__("Request failed")
            self.response = type("R", (), {"raw_response": type("Raw", (), {
                "status_code": 403, "headers": {"X-RateLimit-Remaining": "0"},
                "text": "API rate limit exceeded"})()})()
    monkeypatch.setattr(gh_call, "client", lambda: FakeClient(Failed()))
    resp = gh_call.request("repos/o/r/pulls/1", "PATCH", {"body": "x"})
    assert resp.status == 403 and resp.rate_remaining == 0 and "rate limit" in resp.error
    monkeypatch.setattr(gh_call, "client", lambda: FakeClient(Failed()))
    assert gh_call.rest("repos/o/r/pulls/1") is None


def test_rest_response_keeps_the_four_positional_fields():
    r = gh_call.RestResponse({}, {"a": 1}, 5, "9")
    assert (r.headers, r.body, r.rate_remaining, r.rate_reset) == ({}, {"a": 1}, 5, "9")
    assert r.status == 200 and not r.not_modified and r.ok


def test_gh_modules_import_in_one_direction():
    """どの gh_* も gh_parts を import しない。gh_call・gh_fields・gh_sections は他の gh_* を import しない（循環しない）。"""
    import ast
    for f in sorted(LIB.glob("gh_*.py")):
        tree = ast.parse(f.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert "gh_parts" not in names, f.name
        if f.stem in ("gh_call", "gh_fields", "gh_sections"):
            assert not {n for n in names if n.startswith("gh_")}, f.name


def test_gh_parts_reexports_but_not_the_runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ndf_lib_gh_parts_reexport", LIB / "gh_parts.py")
    gp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gp)
    for name in ("GhResult", "RestResponse", "parse_rest_headers", "gh", "is_rate_limited", "view_json",
                 "unresolved_threads", "fetch_check_runs", "fold_check_runs", "check_result"):
        assert hasattr(gp, name), name
    assert not hasattr(gp, "RUNNER")


@pytest.mark.parametrize("text, expected", [("HTTP/2.0 304 Not Modified\n\n", 304), ("HTTP/1.1 200 OK\n\n{}", 200)])
def test_status_line_is_read(fake, text, expected):
    fake.on("api", rc=1 if expected == 304 else 0, out=text)
    assert gh_call.request("x").status == expected
