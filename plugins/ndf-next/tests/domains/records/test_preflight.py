"""手順 0 の 3 つの確かめを 1 回の結果 JSON にまとめる（SF10 #3208）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import result
from ndf.domains.records import preflight
from ndf.domains.records import preflight_decide as pd

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


def test_missing_worktree_declaration_returns_init_as_next_and_still_runs_the_rest(tmp_path: Path) -> None:
    _mvv(tmp_path, "承認した本文", "書き換えた本文")
    got = preflight.check(tmp_path, _runner({**OK, "metrics": {"present": False}}))
    assert got["status"] == "stopped" and got["next"]["argv"][1:] == ["worktree", "init"]
    assert [i["status"] for i in got["items"]] == ["stopped", "ok", "stopped"]
    assert result.errors(got) == []


def test_declaration_with_unknown_keys_warns_and_unreadable_stops(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {}, "extra": 1}), encoding="utf-8")
    checked = preflight.declaration_checked(tmp_path)
    assert checked.passed and any("extra" in w for w in checked.result["warnings"])
    (tmp_path / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert not preflight.declaration_checked(tmp_path).passed


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


# 現状固定（I-018 の前）: mvv_state が宣言と本文を読むときの分岐
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
    ],
)
def test_mvv_state_reads_the_declaration_and_the_body(tmp_path: Path, decl: bytes | None, files: dict, state: str) -> None:
    (tmp_path / ".ndf").mkdir()
    if decl is not None:
        (tmp_path / ".ndf" / "mvv.json").write_bytes(decl)
    for name, data in files.items():
        if data is None:
            (tmp_path / ".ndf" / name).mkdir()
        else:
            (tmp_path / ".ndf" / name).write_bytes(data)
    assert preflight.mvv_state(tmp_path) == state


# NOTE: 現状固定。UTF-8 でない宣言は unreadable にならず、UnicodeDecodeError がそのまま出る
def test_mvv_state_raises_on_a_declaration_that_is_not_utf8(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "mvv.json").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        preflight.mvv_state(tmp_path)
