"""読む側の共通の規則（`lib/step_read.py`、#2227）。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
_spec = importlib.util.spec_from_file_location("ndf_lib_step_read", LIB / "step_read.py")
rd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)


def write_attempt(d: Path, n: int, step: str, status: str, value: str, tool: str = "t"):
    obj = {"tool": tool, "status": status, "summary": value, "items": [], "metrics": {}}
    if status == "gate":
        obj["next"] = "n"
    (d / f"{n:02d}-{step}.out").write_text("ログの行\n" + json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def test_picks_earlier_ok_when_latest_is_stopped(tmp_path):
    write_attempt(tmp_path, 1, "verify", "ok", "前の値")
    write_attempt(tmp_path, 2, "verify", "stopped", "落ちた値")
    p = rd.pick_ok_attempt(tmp_path, "verify", "t")
    assert p.outcome == "ok" and p.number == 1 and p.result["summary"] == "前の値"
    assert p.attempts == ((1, "ok"), (2, "stopped"))


def test_picks_latest_ok(tmp_path):
    write_attempt(tmp_path, 1, "verify", "ok", "古い")
    write_attempt(tmp_path, 3, "verify", "ok", "新しい")
    assert rd.pick_ok_attempt(tmp_path, "verify", "t").result["summary"] == "新しい"


def test_no_ok_attempt_returns_no_value(tmp_path):
    write_attempt(tmp_path, 1, "verify", "stopped", "落ちた 1")
    write_attempt(tmp_path, 2, "verify", "gate", "待ち")
    p = rd.pick_ok_attempt(tmp_path, "verify", "t")
    assert p.outcome == "no_ok_attempt" and p.result is None and p.number is None


def test_other_tool_and_step_and_broken_lines_are_ignored(tmp_path):
    write_attempt(tmp_path, 1, "verify", "ok", "別の tool", tool="other")
    write_attempt(tmp_path, 2, "other-step", "ok", "別のステップ")
    (tmp_path / "03-verify.out").write_text("{壊れた\n", encoding="utf-8")
    p = rd.pick_ok_attempt(tmp_path, "verify", "t")
    assert p.outcome == "no_ok_attempt" and p.attempts == ((1, "none"), (3, "none"))


def test_no_attempt_and_no_place(tmp_path):
    assert rd.pick_ok_attempt(tmp_path, "verify", "t").outcome == "no_attempt"
    assert rd.pick_ok_attempt(tmp_path / "無い", "verify", "t").outcome == "no_place"


def test_open_place_file_states(tmp_path):
    missing = rd.open_place(tmp_path / "無い.jsonl")
    assert missing.state == "missing"
    stop, code = rd.place_stop("t", missing, "差し戻しの記録")
    assert code == 3 and stop["status"] == "stopped" and "置き場が無い" in stop["summary"]
    empty = tmp_path / "空.jsonl"
    empty.write_text(" \n", encoding="utf-8")
    assert rd.open_place(empty).state == "empty" and rd.place_stop("t", rd.open_place(empty), "x") is None
    full = tmp_path / "ある.jsonl"
    full.write_text("{}\n", encoding="utf-8")
    place = rd.open_place(full)
    assert place.state == "present" and place.text == "{}\n" and rd.place_stop("t", place, "x") is None


def test_open_place_dir_states(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert rd.open_place(d).state == "empty"
    (d / "a").write_text("x", encoding="utf-8")
    place = rd.open_place(d)
    assert place.state == "present" and [p.name for p in place.entries] == ["a"]


def test_open_place_unreadable(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\xfa")
    place = rd.open_place(bad)
    assert place.state == "unreadable"
    stop, code = rd.place_stop("t", place, "記録")
    assert code == 2 and "読めない" in stop["summary"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root は権限で読めなくならない")
def test_open_place_unreadable_by_permission(tmp_path):
    p = tmp_path / "locked.txt"
    p.write_text("x", encoding="utf-8")
    p.chmod(0)
    try:
        assert rd.open_place(p).state == "unreadable"
    finally:
        p.chmod(0o600)
