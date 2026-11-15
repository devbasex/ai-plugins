"""`ndf gws thumbnails`（Google Slides のサムネイルの保存。#3547・#2067）。

偽の gws・curl を PATH（偽のコマンドの置き場だけ）に置き、受付（`ndf.cli.main`）から通して確かめる。偽の curl は
`-o` の次のパスへ URL の文字を書く。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import limits
from ndf.domains.gws import report_decide as rd

from . import Fakes, assert_routes, run_gws

PID = "pres-1"
GET = ["slides", "presentations", "get"]
THUMB = ["slides", "presentations", "pages", "getThumbnail"]
AUTH = ["auth", "status"]


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    return Fakes.on_path(tmp_path, monkeypatch)


def _pages(*ids: str) -> dict[str, Any]:
    return {"prefix": GET, "out": json.dumps({"slides": [{"objectId": i} for i in ids]})}


def _thumb(url: str = "https://example.invalid/t.png") -> dict[str, Any]:
    return {"prefix": THUMB, "out": json.dumps({"contentUrl": url})}


def _status(source: str) -> dict[str, Any]:
    return {"prefix": AUTH, "out": json.dumps({"credential_source": source, "auth_method": "none", "client_config_exists": True})}


def _curl(code: int = 0) -> list[dict[str, Any]]:
    return [{"prefix": [], "code": code, "write_after": "-o", "err": "curl: failed\n" if code else ""}]


def _thumb_params(call: list[str]) -> dict[str, Any]:
    return json.loads(call[call.index("--params") + 1])


def test_saves_every_page_in_order(fakes: Fakes, tmp_path: Path, capsys, last_result) -> None:
    fakes.add("gws", [_pages("a", "b", "c"), _thumb()])
    fakes.add("curl", _curl())
    out = tmp_path / "out"
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(out))
    assert (code, got["status"]) == (0, "ok")
    assert got["items"] == [
        {"name": f"p{n}", "result": "saved", "objectId": oid, "detail": str(out / f"p{n}.png")} for n, oid in enumerate("abc", start=1)
    ]
    assert got["metrics"] == {"pages": 3, "size": "LARGE", "out_dir": str(out)}
    assert sorted(p.name for p in out.iterdir()) == ["p1.png", "p2.png", "p3.png"]
    thumbs = [c for c in fakes.calls("gws") if c[:4] == THUMB]
    assert [_thumb_params(c)["pageObjectId"] for c in thumbs] == ["a", "b", "c"]
    assert {_thumb_params(c)["thumbnailProperties.thumbnailSize"] for c in thumbs} == {"LARGE"}
    assert all(c[:2] != AUTH for c in fakes.calls("gws"))
    assert all("--max-time" in c for c in fakes.calls("curl"))


def test_size_is_passed_to_gws(fakes: Fakes, tmp_path: Path, capsys, last_result) -> None:
    fakes.add("gws", [_pages("a"), _thumb()])
    fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"), "--size", "SMALL")
    assert (code, got["metrics"]["size"]) == (0, "SMALL")
    thumb = next(c for c in fakes.calls("gws") if c[:4] == THUMB)
    assert _thumb_params(thumb)["thumbnailProperties.thumbnailSize"] == "SMALL"


@pytest.mark.parametrize("argv", [["thumbnails", PID], ["thumbnails", PID, "out", "--size", "HUGE"], ["thumbnails"]], ids=["no-out", "bad-size", "none"])
def test_bad_arguments_return_2(fakes: Fakes, capsys, last_result, argv: list[str]) -> None:
    code, got = run_gws(capsys, last_result, *argv)
    assert (code, got["cause"]["kind"]) == (2, "invalid")


@pytest.mark.parametrize("source", ["none", "client_secret.json"], ids=["no-token", "client-secret-only-2067"])
def test_unauthenticated_returns_11(fakes: Fakes, tmp_path: Path, capsys, last_result, source: str) -> None:
    fakes.add("gws", [_status(source), {"prefix": GET, "code": 1, "err": "401 Unauthorized\n"}])
    fakes.add("curl", _curl())
    out = str(tmp_path / "out")
    code, got = run_gws(capsys, last_result, "thumbnails", PID, out)
    assert (code, got["status"], got["metrics"]) == (11, "gate", {"state": "unauthenticated"})
    assert got["items"][0]["result"] == "unauthenticated" and "401" in got["items"][0]["detail"]
    assert_routes(got)
    assert got["routes"] == {"retry": {"argv": ["ndf", "gws", "thumbnails", PID, out, "--size", "LARGE"]}, "cancel": None}
    assert [c for c in fakes.calls("gws") if c[:2] == AUTH] == [AUTH]


def test_gws_failure_stops_with_1_and_shows_the_tail(fakes: Fakes, tmp_path: Path, capsys, last_result) -> None:
    err = "".join(f"e{n}\n" for n in range(30))
    fakes.add("gws", [_status("token_cache"), {"prefix": GET, "code": 1, "err": err}])
    fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"], got["items"][0]["result"]) == (1, "unknown", "gws-failed")
    detail = got["items"][0]["detail"].splitlines()
    assert len(detail) == rd.DETAIL_LINES and detail[-1] == "e29"


@pytest.mark.parametrize("status", [{"prefix": AUTH, "code": 1}, {"prefix": AUTH, "sleep": 10}], ids=["exit-1", "over-the-limit"])
def test_unreadable_status_after_a_failure_is_a_plain_failure(fakes: Fakes, tmp_path: Path, capsys, last_result, monkeypatch, status: dict) -> None:
    asked: list[str] = []

    def short(name: str) -> float:
        asked.append(name)
        return 2

    monkeypatch.setattr(limits, "seconds", short)
    fakes.add("gws", [status, {"prefix": GET, "code": 1, "err": "boom\n"}])
    fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"]) == (1, "unknown")
    assert [c for c in fakes.calls("gws") if c[:2] == AUTH] == [AUTH]
    assert "google-workspace.thumbnails" in asked and "gws.auth-status" not in asked


@pytest.mark.parametrize("missing", ["gws", "curl"])
def test_missing_gws_or_curl_returns_3(fakes: Fakes, tmp_path: Path, capsys, last_result, missing: str) -> None:
    if missing != "gws":
        fakes.add("gws", [_pages("a"), _thumb()])
    if missing != "curl":
        fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"]) == (3, "precondition")


@pytest.mark.parametrize(
    "rules",
    [
        [{"prefix": GET, "out": "not json"}],
        [{"prefix": GET, "out": json.dumps({"slides": []})}],
        [{"prefix": GET, "out": json.dumps({"slides": [{"title": "x"}]})}],
        [_pages("a"), {"prefix": THUMB, "out": json.dumps({})}],
    ],
    ids=["not-json", "no-pages", "no-object-id", "no-content-url"],
)
def test_unreadable_answers_return_2(fakes: Fakes, tmp_path: Path, capsys, last_result, rules: list) -> None:
    fakes.add("gws", rules)
    fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"]) == (2, "invalid")


@pytest.mark.parametrize(("curl_code", "kind"), [(22, "unknown"), (28, "interrupted")])
def test_download_failure_stops_with_1(fakes: Fakes, tmp_path: Path, capsys, last_result, curl_code: int, kind: str) -> None:
    fakes.add("gws", [_pages("a", "b"), _thumb()])
    fakes.add("curl", _curl(curl_code))
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"]) == (1, kind)
    assert len(fakes.calls("curl")) == 1
    if kind == "unknown":
        assert got["items"] == [{"name": "p1（a）のサムネイル", "result": "download-failed", "detail": "curl: failed"}]


def test_gws_over_the_limit_is_interrupted(fakes: Fakes, tmp_path: Path, capsys, last_result, monkeypatch) -> None:
    monkeypatch.setattr(limits, "seconds", lambda name: 0.5)
    fakes.add("gws", [{"prefix": GET, "sleep": 10}])
    fakes.add("curl", _curl())
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(tmp_path / "out"))
    assert (code, got["cause"]["kind"]) == (1, "interrupted")


def test_out_dir_that_cannot_be_made_stops_with_1(fakes: Fakes, tmp_path: Path, capsys, last_result) -> None:
    fakes.add("gws", [_pages("a"), _thumb()])
    fakes.add("curl", _curl())
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    code, got = run_gws(capsys, last_result, "thumbnails", PID, str(blocker / "out"))
    assert (code, got["cause"]["kind"]) == (1, "unknown")
