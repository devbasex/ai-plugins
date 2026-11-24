"""手順 0 の 3 つの確かめを 1 回の結果 JSON にまとめる（SF10 #3208）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import settings_legacy as legacy

from ndf.contracts import result
from ndf.domains.records import preflight
from ndf.domains.records import preflight_decide as pd
from ndf.domains.settings import renames

OK = {"status": "ok", "summary": "s", "metrics": {"present": True}}


def _runner(got: dict[str, Any]) -> preflight.Runner:
    def run(args: list[str], cwd: Path) -> dict[str, Any]:
        assert args == ["worktree", "check"]
        return got

    return run


def _mvv(root: Path, approved_body: str, body: str) -> None:
    (root / ".ndf").mkdir(exist_ok=True)
    sha = hashlib.sha256(approved_body.encode("utf-8")).hexdigest()
    (root / ".ndf" / "mvv.json").write_text(json.dumps({"body": "body.md", "versions": [{"version": 1, "sha256": sha}]}), encoding="utf-8")
    (root / ".ndf" / "body.md").write_text(body, encoding="utf-8")


def test_all_three_pass_into_one_ok_result(tmp_path: Path) -> None:
    _mvv(tmp_path, "本文", "本文")
    got = preflight.check(tmp_path, _runner(OK))
    assert got["status"] == "ok" and [i["name"] for i in got["items"]] == list(pd.ORDER)
    assert result.errors(got) == []


def test_missing_worktree_ndf_settings_returns_init_as_next_and_still_runs_the_rest(tmp_path: Path) -> None:
    _mvv(tmp_path, "承認した本文", "書き換えた本文")
    got = preflight.check(tmp_path, _runner({**OK, "metrics": {"present": False}}))
    assert got["status"] == "stopped" and got["next"]["argv"][1:] == ["worktree", "init"]
    assert [i["status"] for i in got["items"]] == ["stopped", "ok", "stopped"]
    assert result.errors(got) == []


def test_ndf_settings_with_unknown_keys_warns_and_unreadable_stops(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {}, "extra": 1}), encoding="utf-8")
    checked = preflight.ndf_settings_checked(tmp_path)
    assert checked.passed and any("extra" in w for w in checked.result["warnings"])
    (tmp_path / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert not preflight.ndf_settings_checked(tmp_path).passed


def test_mvv_states(tmp_path: Path) -> None:
    assert preflight.mvv_state(tmp_path) == "absent"
    _mvv(tmp_path, "a", "a")
    assert preflight.mvv_state(tmp_path) == "approved"
    (tmp_path / ".ndf" / "body.md").unlink()
    assert preflight.mvv_state(tmp_path) == "unreadable"


def test_combine_rejects_a_different_order() -> None:
    a = pd.Checked(pd.MVV, OK)
    with pytest.raises(ValueError):
        pd.combine([a, a, a])


# 現状固定（I-018 の前）: mvv_state が設定と本文を読むときの分岐
_SHA_X = hashlib.sha256(b"x").hexdigest()


def _approved(body: Any = "mvv.md", sha: Any = _SHA_X) -> bytes:
    decl: dict[str, Any] = {"versions": [{"version": 1, "sha256": sha}]}
    if body is not None:
        decl["body"] = body
    return json.dumps(decl).encode("utf-8")


@pytest.mark.parametrize(
    ("decl", "files", "state"),
    [
        (None, {"mvv.json": None}, "absent"),
        (b"{", {}, "unreadable"),
        (b"[]", {}, "unreadable"),
        (_approved(body=None), {"mvv.md": b"x"}, "approved"),
        (_approved(body=1), {"mvv.md": b"x"}, "approved"),
        (_approved(body="b.md"), {"mvv.md": b"y", "b.md": b"x"}, "approved"),
        (json.dumps({"body": "none.md", "versions": []}).encode("utf-8"), {}, "unapproved"),
        (_approved(sha=1), {"mvv.md": b"x"}, "unapproved"),
        (_approved(), {"mvv.md": b"z"}, "mismatch"),
        (_approved(), {"mvv.md": None}, "unreadable"),
        (_approved(body="../.ndf/mvv.md"), {"mvv.md": b"x"}, "unreadable"),
        (json.dumps({"body": "../x.md", "versions": []}).encode("utf-8"), {}, "unapproved"),
    ],
)
def test_mvv_state_reads_the_ndf_settings_and_the_body(tmp_path: Path, decl: bytes | None, files: dict, state: str) -> None:
    (tmp_path / ".ndf").mkdir()
    if decl is not None:
        (tmp_path / ".ndf" / "mvv.json").write_bytes(decl)
    for name, data in files.items():
        if data is None:
            (tmp_path / ".ndf" / name).mkdir()
        else:
            (tmp_path / ".ndf" / name).write_bytes(data)
    assert preflight.mvv_state(tmp_path) == state


def test_a_body_path_outside_the_settings_directory_stops_with_the_value(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "mvv.json").write_bytes(_approved(body="../x.md"))
    got = preflight.mvv_checked(tmp_path).result
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert ".ndf/mvv.json" in got["summary"] and "../x.md" in got["summary"]


# NOTE: 現状固定。UTF-8 でない設定は unreadable にならず、UnicodeDecodeError がそのまま出る
def test_mvv_state_raises_on_a_ndf_settings_that_is_not_utf8(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "mvv.json").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        preflight.mvv_state(tmp_path)


# 現状固定（I-017 の前）: mvv_checked が状態と停止理由を決める分岐（name・result・next の全体）
_HINT = "プロジェクト MVV の状態が {}（本文を承認し直すか、承認した本文へ戻す）"
_BAD_BODY = ".ndf/mvv.json: MVV の設定の body は設定のディレクトリの中の相対パスにする: {!r}"


def _place(root: Path, files: dict[str, bytes | None]) -> None:
    """`.ndf` の下へ置く（値が None ならディレクトリ）。"""
    for name, data in files.items():
        path = root / ".ndf" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            path.mkdir()
        else:
            path.write_bytes(data)


def _ok(state: str) -> pd.Checked:
    return pd.Checked(pd.MVV, result.result(preflight.TOOL, "ok", f"プロジェクト MVV: {state}"))


def _stopped(why: str) -> pd.Checked:
    return pd.Checked(pd.MVV, result.stopped(preflight.TOOL, "precondition", why))


@pytest.mark.parametrize(
    ("files", "want"),
    [
        pytest.param({}, _ok("absent"), id="設定が無い"),
        pytest.param({"mvv.json": None}, _ok("absent"), id="設定がディレクトリ"),
        pytest.param({"mvv.json": b"{"}, _stopped(_HINT.format("unreadable")), id="設定が JSON でない"),
        pytest.param({"mvv.json": b"[]"}, _stopped(_HINT.format("unreadable")), id="設定が object でない"),
        pytest.param({"mvv.json": b"{}"}, _ok("unapproved"), id="設定が空の object"),
        pytest.param({"mvv.json": b'{"body": "../x.md", "versions": []}'}, _ok("unapproved"), id="未承認なら body のパスを見ない"),
        pytest.param({"mvv.json": _approved(), "mvv.md": b"x"}, _ok("approved"), id="承認の記録と同じ"),
        pytest.param({"mvv.json": _approved(body="d/b.md"), "d/b.md": b"x", "mvv.md": b"y"}, _ok("approved"), id="body の指す本文"),
        pytest.param({"mvv.json": _approved(), "mvv.md": b"z"}, _stopped(_HINT.format("mismatch")), id="承認の記録と違う"),
        pytest.param({"mvv.json": _approved()}, _stopped(_HINT.format("unreadable")), id="本文が無い"),
        pytest.param({"mvv.json": _approved(), "mvv.md": None}, _stopped(_HINT.format("unreadable")), id="本文がディレクトリ"),
        pytest.param(
            {"mvv.json": _approved(body="../.ndf/mvv.md"), "mvv.md": b"x"}, _stopped(_BAD_BODY.format("../.ndf/mvv.md")), id="親を指す"
        ),
        pytest.param({"mvv.json": _approved(body="/x.md")}, _stopped(_BAD_BODY.format("/x.md")), id="絶対パス"),
        pytest.param({"mvv.json": _approved(body="")}, _stopped(_BAD_BODY.format("")), id="空のパス"),
    ],
)
def test_mvv_checked_decides_the_state_and_the_reason(tmp_path: Path, files: dict[str, bytes | None], want: pd.Checked) -> None:
    _place(tmp_path, files)
    assert preflight.mvv_checked(tmp_path) == want


def test_mvv_checked_accepts_the_approved_body_with_other_newlines(tmp_path: Path) -> None:
    sha = hashlib.sha256(b"a\nb\n").hexdigest()
    _place(tmp_path, {"mvv.json": _approved(sha=sha), "mvv.md": b"a\r\nb\r\n"})
    assert preflight.mvv_checked(tmp_path) == _ok("approved")


@pytest.mark.parametrize("body", ["mvv.md", "../x.md"])
@pytest.mark.parametrize("files", [{}, {"mvv.md": b"z"}])
def test_mvv_checked_reports_a_legacy_key_before_the_body(tmp_path: Path, body: str, files: dict[str, bytes | None]) -> None:
    decl = {**legacy.mvv(1), "body": body}
    _place(tmp_path, {"mvv.json": json.dumps(decl).encode("utf-8"), **files})
    why = renames.legacy_detail("mvv", decl, tmp_path)
    assert why is not None
    assert preflight.mvv_checked(tmp_path) == _stopped(why)


# NOTE: 現状固定。UTF-8 でない設定は停止の結果にならず、UnicodeDecodeError がそのまま出る
def test_mvv_checked_raises_on_a_ndf_settings_that_is_not_utf8(tmp_path: Path) -> None:
    _place(tmp_path, {"mvv.json": b"\xff"})
    with pytest.raises(UnicodeDecodeError):
        preflight.mvv_checked(tmp_path)
