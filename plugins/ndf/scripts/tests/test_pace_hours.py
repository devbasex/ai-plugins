"""業務時間の宣言（`.ndf/pace.json` の `business_hours`。#2913 の受け入れ条件 4・8 と I3・I4）。"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import pace  # noqa: E402

HOURS = {"timezone": "Asia/Tokyo", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "09:00", "end": "18:00"}
UTC = dt.timezone.utc


def write(tmp_path, **decl) -> Path:
    (tmp_path / ".ndf").mkdir(exist_ok=True)
    (tmp_path / ".ndf" / pace.DECL_NAME).write_text(json.dumps({"version": 1, **decl}), encoding="utf-8")
    return tmp_path


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.mark.parametrize(
    "now, inside, nxt",
    [
        ("2026-11-10T00:00:00Z", True, None),  # 火 09:00 JST（始まりを含む）
        ("2026-11-10T08:59:00Z", True, None),  # 火 17:59 JST
        ("2026-11-10T09:00:00Z", False, "2026-11-11T00:00:00Z"),  # 火 18:00 JST（終わりを含まない）
        ("2026-11-09T23:59:00Z", False, "2026-11-10T00:00:00Z"),  # 火 08:59 JST
        ("2026-11-13T12:00:00Z", False, "2026-11-16T00:00:00Z"),  # 金 21:00 JST → 月の朝
        ("2026-11-14T03:00:00Z", False, "2026-11-16T00:00:00Z"),  # 土 12:00 JST
    ],
)
def test_the_state_follows_the_declared_timezone_and_days(tmp_path, now, inside, nxt):
    decl, warnings = pace.read_hours(write(tmp_path, business_hours=HOURS))
    assert warnings == []
    assert pace.hours_state(decl, at(now)) == {"declared": True, "inside": inside, "timezone": "Asia/Tokyo", "next_start": nxt}


@pytest.mark.parametrize(
    "hours",
    [
        {k: v for k, v in HOURS.items() if k != "timezone"},
        {**HOURS, "timezone": "Nope/Nowhere"},
        {**HOURS, "timezone": "+09:00"},
        {**HOURS, "start": "25:00"},
        {**HOURS, "end": "9:00"},
        {**HOURS, "start": "18:00", "end": "09:00"},
        {**HOURS, "start": "09:00", "end": "09:00"},
        {**HOURS, "days": ["mon", "funday"]},
        {**HOURS, "days": ["mon", "mon"]},
        {**HOURS, "days": []},
        "09:00-18:00",
    ],
)
def test_a_broken_declaration_warns_and_is_read_as_no_declaration(tmp_path, hours):
    root = write(tmp_path, business_hours=hours)
    decl, warnings = pace.read_hours(root)
    assert decl is None and len(warnings) == 1
    assert pace.hours_items(root) == [{"level": "warning", "rule": "business-hours", "at": "business_hours", "message": warnings[0]}]
    # 一部の項目だけを既定へ戻さない: 時間外の時刻でも業務時間内（宣言なし）と判定する（I3）
    assert pace.hours_now(root, at("2026-11-14T03:00:00Z")) == {"declared": False, "inside": True, "timezone": None, "next_start": None}


def test_no_declaration_is_inside_and_has_no_warning(tmp_path):
    assert pace.read_hours(tmp_path) == (None, [])
    assert pace.read_hours(write(tmp_path)) == (None, [])
    assert pace.hours_items(tmp_path) == []
    assert pace.hours_now(tmp_path, at("2026-11-14T03:00:00Z"))["inside"] is True


def test_the_declaration_does_not_break_the_other_sections(tmp_path):
    root = write(tmp_path, business_hours={"timezone": 1}, auto={"enabled": True, "verify": "true"})
    assert pace.read_pace(root)["auto"]["enabled"] is True and pace.read_parallel(root)["warnings"] == []


def test_the_state_does_not_depend_on_the_machine_timezone(tmp_path):
    """受け入れ条件 8・I4: 同じ UTC の時刻に対し、TZ を変えても判定は変わらない。"""
    root = write(tmp_path, business_hours=HOURS)
    code = (
        "import datetime as dt, json, sys; sys.path.insert(0, sys.argv[1]); import pace;"
        "now = dt.datetime(2026, 11, 10, 8, 30, tzinfo=dt.timezone.utc);"
        "print(json.dumps([pace.hours_now(sys.argv[2], now), pace.hours_now(sys.argv[2], now.astimezone())]))"
    )
    outs = {
        tz: subprocess.run(
            [sys.executable, "-c", code, str(LIB), str(root)], capture_output=True, text=True, env={**os.environ, "TZ": tz}, check=True
        ).stdout
        for tz in ("UTC", "America/Los_Angeles", "Asia/Kolkata")
    }
    got = {json.dumps(json.loads(v)[0]) for v in outs.values()} | {json.dumps(json.loads(v)[1]) for v in outs.values()}
    assert len(got) == 1 and json.loads(got.pop())["inside"] is True


def test_a_start_inside_a_spring_forward_gap_is_read_with_the_offset_before_the_switch():
    """夏時間の切り替わりで存在しない始まり（02:30）は、切り替え前のオフセットで読む（設計の未確認の項目を固定する）。"""
    decl, _ = pace.parse_hours({"timezone": "America/New_York", "days": ["sun"], "start": "02:30", "end": "05:00"})
    got = pace.hours_state(decl, dt.datetime(2026, 3, 8, 5, 0, tzinfo=UTC))  # 2026-03-08 00:00 EST（日曜）
    assert got["inside"] is False and got["next_start"] == "2026-03-08T07:30:00Z"
