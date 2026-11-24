"""開発ループの記録の読み書き（`ndf.domains.loop.store`）: イベントの型・旧い形の行の再生・時刻の書き方。#2339 の受け入れ条件 1・3・5。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import internal
from ndf.domains.loop import record_decide as rec
from ndf.domains.loop import store

T0 = 1795400000.0
# 形のバージョンを持つ前の NDF が書いた行（種類ごとに 1 件。`at` は秒）
OLD: list[dict[str, Any]] = [
    {"at": T0, "kind": "lease-taken", "session": "s1", "until": T0 + 600.5},
    {"at": T0, "kind": "row-bound", "table": "t1"},
    {"at": T0 + 1, "kind": "lap-opened", "lap": 1, "issues": [2, 1]},
    {"at": T0 + 2, "kind": "mode-decided", "lap": 1, "mode": "standard", "basis": "設計が要る"},
    {"at": T0 + 3, "kind": "issue-excluded", "lap": 1, "issue": 2, "reason": "実装で止まった"},
    {"at": T0 + 4, "kind": "issues-created", "lap": 1, "issues": [9]},
    {"at": T0 + 5, "kind": "gate-shown", "lap": 1, "pr": 4301, "head": "abc"},
    {"at": T0 + 6.123456, "kind": "gate-answered", "lap": 1, "pr": 4301, "answer": "rejected", "head": "abc", "note": "直す"},
    {"at": T0 + 7, "kind": "version-shipped", "lap": 1, "version": "11.0.23"},
    {"at": T0 + 8, "kind": "stop-requested", "by": "human", "reason": "終わり"},
    {"at": T0 + 9, "kind": "lap-closed", "lap": 1, "outcome": "完了", "reason": "r"},
    {"at": T0 + 9, "kind": "loop-halted", "by": "human", "reason": "user_stop"},
    {"at": T0 + 9, "kind": "lease-returned", "session": "s1"},
]
EXPECTED = rec.LoopState(
    laps=(
        rec.Lap(
            1, (2, 1), excluded=((2, "実装で止まった"),), created=(9,), gates=((4301, "abc"),), released=("11.0.23",),
            answers=(rec.Answer(4301, "rejected", "abc", "直す", T0 + 6.123456),), outcome="完了", reason="r",
        ),
    ),
    requested=rec.Stop("human", "終わり", T0 + 8), stopped=rec.Stop("human", "user_stop", T0 + 9), row="t1",
)  # fmt: skip


def _loop(tmp_path: Path, rows: list[Any]) -> store.LoopStore:
    loop = store.LoopStore(tmp_path / "demo")
    loop.directory.mkdir()
    loop.events_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return loop


def _new(row: dict[str, Any]) -> dict[str, Any]:
    """旧い行と同じことを今の形で書いた行。"""
    keys = {("product_version" if k == "version" else k): v for k, v in row.items()}
    return {**keys, "at": internal.stamp(row["at"]), "version": internal.version()}


def test_the_samples_cover_every_event() -> None:
    assert {row["kind"] for row in OLD} == set(rec.REQUIRED)


def test_reads_old_rows_in_the_current_form(tmp_path: Path) -> None:
    got = _loop(tmp_path, OLD).read()
    assert got == [_new(r) for r in OLD]
    assert got[0]["until"] == T0 + 600.5  # 期限は秒のまま
    assert got[8]["product_version"] == "11.0.23" and got[7]["at"] == "2026-11-23T02:13:26.123456+00:00"


@pytest.mark.parametrize("pick", [lambda i: False, lambda i: True, lambda i: i % 2 == 0, lambda i: i % 2 == 1])
def test_replays_old_new_and_mixed_rows_to_the_same_state(tmp_path: Path, pick: Any) -> None:
    """旧い行だけ・今の行だけ・両方の混ざった記録（並行して走る旧い NDF が足した行）から同じ状態を再生する。"""
    rows = [_new(r) if pick(i) else r for i, r in enumerate(OLD)]
    assert rec.fold(_loop(tmp_path, rows).read()) == EXPECTED


def test_appended_events_are_the_current_form_and_replay_unchanged(tmp_path: Path) -> None:
    loop = store.LoopStore(tmp_path / "demo")
    events = [rec.event(r["kind"], r["at"], **{k: v for k, v in _new(r).items() if k not in ("kind", "at", "version")}) for r in OLD]
    loop.append(events)
    written = [json.loads(line) for line in loop.events_path.read_text(encoding="utf-8").splitlines()]
    assert written == [_new(r) for r in OLD] == loop.read()
    assert rec.fold(events) == EXPECTED


def test_the_order_within_a_second_is_kept() -> None:
    first, second = (rec.event(rec.STOP_REQUESTED, t, by="human", reason="r") for t in (T0 + 0.1, T0 + 0.100001))
    got = [rec.fold([ev]).requested for ev in (first, second)]
    assert [s.at for s in got if s is not None] == [T0 + 0.1, T0 + 0.100001]


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "lap-opened", "at": internal.stamp(T0), "lap": 1, "issues": [1], "extra": "x"},  # スキーマに無い欄
        {"kind": "lap-opened", "at": internal.stamp(T0), "lap": "1", "issues": [1]},  # 固有の欄の型の違い
        {"kind": "lap-opened", "at": T0, "lap": 1, "issues": [1]},  # 秒の時刻
        {"kind": "version-shipped", "at": internal.stamp(T0), "lap": 1, "version": "11.0.23"},  # 製品のバージョンの旧い名前
        {"kind": "lap-opened", "at": internal.stamp(T0), "lap": 1},  # 種類の要る欄の欠け
        {"kind": "nope", "at": internal.stamp(T0)},
    ],
)
def test_append_writes_nothing_when_an_event_does_not_fit(tmp_path: Path, bad: dict[str, Any]) -> None:
    loop = store.LoopStore(tmp_path / "demo")
    good = rec.event(rec.ROW_BOUND, T0, table="t1")
    with pytest.raises((internal.InternalError, rec.RecordError)):
        loop.append([good, bad])
    assert loop.read() == []


@pytest.mark.parametrize(
    "bad",
    [
        {**OLD[2], "extra": "x"},
        {**OLD[2], "issues": ["1"]},
        {**OLD[2], "at": "2026-11-23T11:13:20+09:00"},
        {**OLD[2], "at": float("nan")},
        {**OLD[2], "at": 1e300},
        {k: v for k, v in OLD[8].items() if k != "version"},
        {"at": T0, "kind": "nope"},
        [1],
        1,
    ],
)
def test_read_stops_with_the_line_of_a_row_that_does_not_fit(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(store.StoreError, match="2 行目"):
        _loop(tmp_path, [OLD[0], bad]).read()


@pytest.mark.parametrize(
    "bad",
    [
        {**OLD[2], "sk-test-secret": "x"},
        {"at": T0, "kind": "sk-test-secret"},
        {"at": T0, "kind": "lap-closed", "lap": 1, "outcome": "sk-test-secret", "reason": "r"},
    ],
)
def test_the_error_of_a_bad_row_does_not_show_the_row(tmp_path: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(store.StoreError) as exc:
        _loop(tmp_path, [bad]).read()
    assert "sk-test-secret" not in str(exc.value)


def test_read_stops_on_a_newer_version(tmp_path: Path) -> None:
    with pytest.raises(internal.InternalError) as exc:
        _loop(tmp_path, [{**_new(OLD[0]), "version": internal.version() + 1}]).read()
    assert exc.value.kind == "precondition" and "プラグインを更新する" in str(exc.value)


def test_a_note_with_a_unicode_line_separator_stays_in_one_row(tmp_path: Path) -> None:
    loop = store.LoopStore(tmp_path / "demo")
    loop.append([rec.event(rec.GATE_ANSWERED, T0, lap=1, pr=4301, answer="rejected", head="abc", note="一行\u2028二行\u0085三行")])
    assert [ev["note"] for ev in loop.read()] == ["一行\u2028二行\u0085三行"]
