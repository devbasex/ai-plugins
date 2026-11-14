from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ndf.testing import records


def _at(seconds: float) -> str:
    """記録の行の時刻（UTC の ISO 8601、秒まで）。実装の整形に頼らず作る。"""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def test_records_drop_old_and_excess_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "KEEP_ROWS", 5)
    now = time.time()
    old = {"at": _at(now - 31 * 86400), "result": "finished"}
    recent = {"at": _at(now - 60), "result": "finished"}
    (tmp_path / records.EVENTS_FILE).write_text("\n".join(json.dumps(r) for r in [old] + [recent] * 5) + "\n")
    records.append_event(tmp_path, {"at": _at(now), "result": "gave_up"}, now=now)
    rows = records.read_events(tmp_path)
    assert len(rows) == 5 and rows[-1]["result"] == "gave_up" and old not in rows


def test_report_counts_and_never_lets_the_oom_increase_go_negative(tmp_path):
    now = time.time()
    rows = [
        {"at": _at(now - 9 * 86400), "waited_seconds": 99, "result": "gave_up", "oom_kill": 0},  # 期間の外
        {"at": _at(now - 300), "waited_seconds": 4, "result": "finished", "oom_kill": 5},
        {"at": _at(now - 200), "waited_seconds": 0, "result": "memory_stopped", "oom_kill": 7},
        {"at": _at(now - 100), "waited_seconds": 6, "result": "gave_up", "oom_kill": 1},  # 再起動で減った
    ]
    (tmp_path / records.EVENTS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = records.report(tmp_path, 7 * 86400, now=now, current_oom=3)
    assert got == {"runs": 3, "waited_runs": 2, "waited_seconds": 10.0, "memory_stopped": 1, "gave_up": 1, "oom_kill_increase": 4}


# --- 現状固定テスト（report の経路: 記録が無い・読めない行・期間の境・並べ替え・OOM の数でない値） ---


def test_report_without_records_counts_zero(tmp_path):
    expected = {"runs": 0, "waited_runs": 0, "waited_seconds": 0, "memory_stopped": 0, "gave_up": 0, "oom_kill_increase": 0}
    assert records.report(tmp_path, 86400, now=1000.0) == expected
    # 記録が無ければ今の回数だけでは増分にならない
    assert records.report(tmp_path, 86400, now=1000.0, current_oom=5) == expected


def _write_rows(directory: Path, rows: list[dict], tail: str = "") -> None:
    (directory / records.EVENTS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)


def test_report_skips_unreadable_rows_sorts_by_time_and_ignores_non_integer_oom(tmp_path):
    now = 1_800_000_000.0
    iso = _at
    rows = [
        {"at": iso(now - 100), "waited_seconds": 0.04, "result": "finished", "oom_kill": 4},
        {"at": iso(now - 300), "waited_seconds": 0.03, "result": "finished", "oom_kill": 2},  # 書いた順でなく時刻の順に並べる
        {"at": "broken", "waited_seconds": 50, "result": "gave_up"},  # 読めない時刻は数えない
        {"waited_seconds": 50, "result": "gave_up"},  # 時刻の無い行は数えない
        {"at": iso(now - 50), "waited_seconds": None, "result": "memory_stopped", "oom_kill": True},  # 真偽値は回数でない
        {"at": iso(now - 40), "result": "interrupted", "oom_kill": "9"},  # 文字列は回数でない
        {"at": iso(now - 3600), "waited_seconds": 1, "result": "gave_up", "oom_kill": 1},  # 期間の境はちょうどで含む
        {"at": iso(now + 10), "waited_seconds": 2, "result": "gave_up", "oom_kill": 10},  # 先の時刻も含む
    ]
    _write_rows(tmp_path, rows, tail="not json\n[1]\n")
    whole = {"runs": 6, "waited_runs": 4, "waited_seconds": 3.1, "memory_stopped": 1, "gave_up": 2, "oom_kill_increase": 9}
    assert records.report(tmp_path, 3600, now=now) == whole
    assert records.report(tmp_path, 3600, now=now, current_oom=True) == whole  # 今の回数も真偽値なら数えない
    # 境を 1 秒縮めると境の行が外れ、今の回数（7）が並びの最後に付く（10 → 7 は負なので足さない）
    assert records.report(tmp_path, 3599, now=now, current_oom=7) == {
        "runs": 5,
        "waited_runs": 3,
        "waited_seconds": 2.1,
        "memory_stopped": 1,
        "gave_up": 1,
        "oom_kill_increase": 8,
    }
