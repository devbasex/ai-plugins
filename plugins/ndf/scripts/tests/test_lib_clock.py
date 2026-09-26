"""時刻の読み書き（lib/clock.py・#1142 の L0）。8 つの `now` / `now_iso` と 5 つの `_parse_time` の形を 1 つで出せる。"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import clock  # noqa: E402

UTC = dt.timezone.utc
T = dt.datetime(2026, 9, 26, 7, 0, 0, 123456, tzinfo=UTC)


def test_now_is_timezone_aware():
    assert clock.now().tzinfo is not None
    assert clock.now(utc=True).utcoffset() == dt.timedelta(0)


@pytest.mark.parametrize("form, pattern", [
    ("utc", "2026-09-26T07:00:00+00:00"),
    ("z-ms", "2026-09-26T07:00:00.123Z"),
])
def test_iso_forms_in_utc(form, pattern):
    assert clock.iso(T, form) == pattern


def test_iso_local_and_naive_are_the_same_instant():
    local = clock.iso(T, "local")
    naive = clock.iso(T, "naive")
    assert dt.datetime.fromisoformat(local) == T.replace(microsecond=0)
    assert dt.datetime.fromisoformat(naive).tzinfo is None
    assert local.startswith(naive)


def test_iso_rejects_an_unknown_form():
    with pytest.raises(ValueError):
        clock.iso(T, "rfc")


def test_now_iso_round_trips_through_parse():
    for form in clock.FORMS:
        assert clock.parse(clock.now_iso(form)) is not None


@pytest.mark.parametrize("text", ["2026-09-26T07:00:00Z", "2026-09-26T07:00:00.123Z", "2026-09-26T07:00:00+00:00",
                                  " 2026-09-26T16:00:00+09:00 "])
def test_parse_reads_z_on_python_310(text):
    got = clock.parse(text)
    assert got is not None and got.astimezone(UTC).replace(microsecond=0) == T.replace(microsecond=0)


@pytest.mark.parametrize("value", [None, "", "   ", "not a time", 12, []])
def test_parse_returns_none_for_unreadable(value):
    assert clock.parse(value) is None


def test_parse_naive_choices():
    assert clock.parse("2026-09-26T07:00:00", naive="utc") == T.replace(microsecond=0)
    assert clock.parse("2026-09-26T07:00:00", naive="reject") is None
    assert clock.parse("2026-09-26T07:00:00").tzinfo is not None


def test_parse_accepts_datetimes():
    assert clock.parse(T) is T
    assert clock.parse(dt.datetime(2026, 9, 26), naive="utc").tzinfo == UTC


def test_seconds_between():
    assert clock.seconds_between("2026-09-26T07:00:00Z", "2026-09-26T16:01:00+09:00") == 60
    assert clock.seconds_between("x", "2026-09-26T07:00:00Z") is None
