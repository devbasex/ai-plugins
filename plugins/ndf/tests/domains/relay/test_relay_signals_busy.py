"""起動ごとのバックグラウンドの作業の記録の読み書き（#4049 の Task 5）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ndf.domains.relay import signals

A, B = "a" * 32, "b" * 32
GOOD = {"launch": A, "session_id": "s1", "count": 0, "source": "stop", "at": "2026-11-22T03:19:34+00:00"}


def busy(directory: Path, launch: str) -> Path:
    return directory / f"busy-{launch}.json"


def fail(*_args, **_kwargs):
    raise OSError("書けない")


@pytest.mark.parametrize("count, source, monitors", [(0, "session", 0), (1, "start", 0), (2, "stop", 3)])
def test_written_counts_are_read_back_by_the_same_launch_and_session(tmp_path, count, source, monitors):
    assert signals.write_busy(tmp_path, A, "s1", count, source, monitors)
    got = signals.read_busy(tmp_path, A, "s1")
    assert got is not None and (got[0], got[1], got[3]) == (count, source, monitors)
    assert signals.read_busy(tmp_path, B, "s1") is None and signals.read_busy(tmp_path, A, "s2") is None


def test_late_write_of_a_stopped_launch_does_not_reach_the_next_one(tmp_path):
    # 受け入れ条件 5: 止めた子の遅れた 0 件は、今の起動の 1 件を置き換えない
    assert signals.write_busy(tmp_path, B, "s2", 1, "start")
    assert signals.write_busy(tmp_path, A, "s1", 0, "stop")
    got = signals.read_busy(tmp_path, B, "s2")
    assert got is not None and got[0] == 1


def test_failed_write_keeps_the_previous_record(tmp_path, monkeypatch):
    assert signals.write_busy(tmp_path, A, "s1", 2, "stop")
    monkeypatch.setattr(signals, "replace_json", fail)
    assert not signals.write_busy(tmp_path, A, "s1", 0, "stop")
    got = signals.read_busy(tmp_path, A, "s1")
    assert got is not None and got[0] == 2


@pytest.mark.parametrize("launch", ["../x", "", "A" * 32, "a" * 31, "a" * 32 + "\n"])
def test_malformed_launch_id_is_not_used_as_a_file_name(tmp_path, launch):
    directory = tmp_path / "relay"
    directory.mkdir()
    assert not signals.write_busy(directory, launch, "s1", 0, "stop")
    assert signals.read_busy(directory, launch, "s1") is None
    assert list(tmp_path.rglob("*")) == [directory]


def test_record_without_the_monitor_count_reads_as_zero_monitors(tmp_path):
    busy(tmp_path, A).write_text(json.dumps(GOOD), encoding="utf-8")
    assert signals.read_busy(tmp_path, A, "s1") == (0, "stop", GOOD["at"], 0)


@pytest.mark.parametrize(
    "change",
    [
        {"launch": B},
        {"launch": None},
        {"session_id": "s2"},
        {"count": "0"},
        {"count": -1},
        {"count": True},
        {"count": None},
        {"monitors": -1},
        {"monitors": "1"},
        {"source": "other"},
        {"source": []},
        {"at": None},
        {"at": 1763781574},
        {"at": "yesterday"},
        {"at": "2026-11-22T03:19:34"},
    ],
    ids=str,
)
def test_record_of_another_shape_is_unknown(tmp_path, change):
    # None の項目はファイルから外す
    data = {key: value for key, value in {**GOOD, **change}.items() if value is not None}
    busy(tmp_path, A).write_text(json.dumps(data), encoding="utf-8")
    assert signals.read_busy(tmp_path, A, "s1") is None


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff\xfe"], ids=["broken", "list", "not-utf8"])
def test_broken_record_is_unknown(tmp_path, raw):
    busy(tmp_path, A).write_bytes(raw)
    assert signals.read_busy(tmp_path, A, "s1") is None


def test_unreadable_record_is_unknown(tmp_path, monkeypatch):
    assert signals.write_busy(tmp_path, A, "s1", 0, "stop")
    monkeypatch.setattr(Path, "read_text", fail)
    assert signals.read_busy(tmp_path, A, "s1") is None


def test_start_of_the_current_launch_is_recorded(tmp_path):
    signals.set_launch(tmp_path, A)
    assert signals.record_start(tmp_path, A, "s1", 0)
    got = signals.read_busy(tmp_path, A, "s1")
    assert got is not None and (got[0], got[1]) == (1, "start")


@pytest.mark.parametrize("current", [None, B], ids=["stopped", "replaced"])
def test_start_of_a_launch_that_is_not_current_is_not_recorded(tmp_path, current):
    signals.set_launch(tmp_path, current)
    assert signals.write_busy(tmp_path, A, "s1", 0, "stop")
    assert not signals.record_start(tmp_path, A, "s1", 0)
    got = signals.read_busy(tmp_path, A, "s1")
    assert got is not None and got[0] == 0


def test_start_is_not_recorded_while_the_stop_lock_is_held(tmp_path):
    # ラッパーが子を止める排他を持つ間は書かず、放した後は書く
    signals.set_launch(tmp_path, A)
    with signals.held(tmp_path / signals.STOP_LOCK, 0) as got:
        assert got
        assert not signals.record_start(tmp_path, A, "s1", 0)
    assert signals.read_busy(tmp_path, A, "s1") is None
    assert signals.record_start(tmp_path, A, "s1", 0)


def test_start_that_cannot_be_written_is_reported(tmp_path, monkeypatch):
    signals.set_launch(tmp_path, A)
    with monkeypatch.context() as m:
        m.setattr(signals, "replace_json", fail)
        assert not signals.record_start(tmp_path, A, "s1", 0)
    assert not signals.record_start(tmp_path / "missing", A, "s1", 0)


def test_records_of_other_launches_are_dropped(tmp_path):
    signals.drop_other_busy(tmp_path, B)
    for launch in (A, B):
        assert signals.write_busy(tmp_path, launch, "s1", 0, "stop")
    signals.drop_other_busy(tmp_path, B)
    assert [p.name for p in tmp_path.iterdir()] == [busy(tmp_path, B).name]


def test_record_that_cannot_be_dropped_does_not_keep_the_rest(tmp_path):
    (tmp_path / "busy-0.json").mkdir()  # ディレクトリは unlink できない
    for launch in (A, "c" * 32):
        assert signals.write_busy(tmp_path, launch, "s1", 0, "stop")
    signals.drop_other_busy(tmp_path, B)
    assert [p.name for p in tmp_path.iterdir()] == ["busy-0.json"]
