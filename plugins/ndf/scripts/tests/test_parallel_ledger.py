"""計画の台帳（#2647）: 行の追記と読み出し・チェックポイント・作り直しの材料・計画の状態・wait へ足す項目。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import parallel_ledger as plg  # noqa: E402
import parallel_plan as pp  # noqa: E402

FORM = json.loads((ROOT / "contracts" / "forms" / "parallel-ledger.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FORM["cases"], ids=lambda c: str(c["valid"]))
def test_form_cases(case):
    """条件 20: 契約の形の例と、読み手の形の検査が一致する。"""
    assert plg.known(case["message"]) is case["valid"]


def _req(i: int, lane: str = "la") -> dict:
    return {"kind": "request", "id": f"{lane}-r{i:012d}", "lane": lane, "prs": [i], "issues": [], "sprint_state": "/s", "at": "t"}


def _add(main, msg, topic="inbox", by=None):
    kind, key = plg.message_key(msg, topic)
    return plg.append_ledger_row(main, "p1", kind, key, msg, by=by or msg.get("lane"))


def test_same_key_is_written_once(tmp_path):
    """I2: 同じキーの 2 度目の追記は行を足さない。"""
    assert _add(tmp_path, _req(1)) is True
    assert _add(tmp_path, _req(1)) is False
    assert len(plg.read_ledger_rows(tmp_path, "p1").rows) == 1


def test_a_cut_line_becomes_one_broken_row_and_does_not_mix_with_the_next(tmp_path):
    """条件 8・I3・I4: 途中で切れた行の後に足した行は混ざらず読め、切れた行は壊れた行として 1 つ数える。"""
    _add(tmp_path, _req(1))
    path = plg.plan_ledger_path(tmp_path, "p1")
    with open(path, "ab") as f:
        f.write(b'{"v": 1, "key": "request:cut", "kind": "requ')
    _add(tmp_path, _req(2))
    got = plg.read_ledger_rows(tmp_path, "p1")
    assert [r["id"] for r in got.rows] == [_req(1)["id"], _req(2)["id"]] and got.broken == 1
    assert plg.restore(tmp_path, "p1")["broken"] == 1
    assert _add(tmp_path, _req(3)) is True  # 閉じた壊れた行が中ほどに残っても、重複の確かめで落ちない
    assert _add(tmp_path, _req(3)) is False


def test_an_unterminated_last_line_is_left_for_the_next_read(tmp_path):
    """条件 11・I3: 改行で終わらない末尾の行は追記の途中として数えず、次の位置はその行の先頭で止まる。"""
    _add(tmp_path, _req(1))
    path = plg.plan_ledger_path(tmp_path, "p1")
    head = path.stat().st_size
    line = json.dumps({"v": 1, "key": "request:x", "kind": "request", "id": "x", "at": "t", "by": "la", "ver": "1", "msg": _req(3)})
    with open(path, "ab") as f:
        f.write(line[:20].encode())
    got = plg.read_ledger_rows(tmp_path, "p1")
    assert len(got.rows) == 1 and got.broken == 0 and got.next == head
    with open(path, "ab") as f:
        f.write(line[20:].encode() + b"\n")
    again = plg.read_ledger_rows(tmp_path, "p1", got.next)
    assert [r["id"] for r in again.rows] == ["x"] and again.broken == 0


def test_credentials_are_masked_before_writing(tmp_path):
    """I12: 文字列の値の認証情報は台帳へ書く前に伏せる。"""
    msg = {**_req(1), "sprint_state": "https://x-access-token:ghp_abcdefghijklmnop@github.com/o/r"}
    _add(tmp_path, msg)
    text = plg.plan_ledger_path(tmp_path, "p1").read_text()
    assert "ghp_abcdefghijklmnop" not in text and "***" in text


APPEND = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import parallel_ledger as plg
main, lane = Path(sys.argv[2]), sys.argv[3]
for i in range(50):
    msg = {"kind": "request", "id": f"{lane}-r{i:012d}", "lane": lane, "pad": "x" * 3000}
    plg.append_ledger_row(main, "p1", "request", f"request:{msg['id']}", msg, by=lane)
"""


def test_concurrent_appends_do_not_mix_lines(tmp_path):
    """非機能のシステム環境: 4 プロセスが同時に足しても壊れた行は 0 で、全行が読める。"""
    lib = str(ROOT / "scripts" / "lib")
    procs = [subprocess.Popen([sys.executable, "-c", APPEND, lib, str(tmp_path), f"l{k}"]) for k in range(4)]
    assert all(p.wait(60) == 0 for p in procs)
    got = plg.read_ledger_rows(tmp_path, "p1")
    assert got.broken == 0 and len(got.rows) == 200


def test_restore_of_a_thousand_requests_takes_less_than_a_second(tmp_path):
    """非機能の性能: 依頼 1,000 行の復元と、観測の 1 回の読み出しがそれぞれ 1 秒以内に収まる。"""
    path = plg.plan_ledger_path(tmp_path, "p1")
    path.parent.mkdir(parents=True)
    rows = [
        {"v": 1, "key": f"request:{i}", "kind": "request", "id": str(i), "at": "t", "by": "la", "ver": "1", "msg": _req(i)}
        for i in range(1000)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    t0 = time.monotonic()
    got = plg.restore(tmp_path, "p1")
    assert time.monotonic() - t0 < 1.0 and len(got["rows"]) == 1000
    t0 = time.monotonic()
    plg.read_run(tmp_path, "p1", got["offset"], None, "inbox")
    assert time.monotonic() - t0 < 1.0


def test_restore_reads_only_rows_after_the_checkpoint(tmp_path):
    """I6: 読み終えた位置より後の行だけを戻す。読めないチェックポイントは無いものとして全行を読み、警告を返す。"""
    _add(tmp_path, _req(1))
    offset = plg.read_ledger_rows(tmp_path, "p1").next
    _add(tmp_path, _req(2))
    plg.save_checkpoint(tmp_path, "p1", {"offset": offset, "unread": [], "published": {"answer-a": {"result": "ok"}}, "pending": [_req(1)]})
    got = plg.restore(tmp_path, "p1")
    assert [r["id"] for r in got["rows"]] == [_req(2)["id"]] and got["state"]["pending"][0]["id"] == _req(1)["id"]
    assert plg.published(tmp_path, "p1", "answer-a") == {"result": "ok"}
    plg.checkpoint_path(tmp_path, "p1").write_text("{")
    broken = plg.restore(tmp_path, "p1")
    assert len(broken["rows"]) == 2 and broken["state"] is None and broken["warning"]


def test_rebuilding_a_legacy_plan_skips_rows_before_the_first_opened_row(tmp_path):
    """台帳を読まない途中の記録が処理した行（最初の `opened` より前）は、チェックポイントが無くても戻さない。"""
    _add(tmp_path, _req(1))
    _add(tmp_path, {"kind": "answer", "id": "la-q1"})
    pp.plan_dir(tmp_path, "p1").mkdir(parents=True, exist_ok=True)
    plg.open_run(tmp_path, "p1", tmp_path / "x.retired-1")
    _add(tmp_path, _req(2))
    got = plg.restore(tmp_path, "p1")
    assert [r["id"] for r in got["rows"]] == [_req(2)["id"]] and got["rebuilds"] == 1
    assert plg.restored_counts(got)["requests"] == 1


def test_rows_of_an_unknown_form_are_kept_and_reported(tmp_path):
    """条件 12・I5: 知らない形の行は状態へ反映せずに残り、件数と依頼の id が出る。読める版では反映する。"""
    _add(tmp_path, _req(1))
    path = plg.plan_ledger_path(tmp_path, "p1")
    pos = path.stat().st_size
    with open(path, "a") as f:
        f.write(json.dumps({"v": 2, "key": "request:new", "kind": "request", "id": "la-new", "at": "t", "by": "la", "ver": "99"}) + "\n")
    got = plg.read_ledger_rows(tmp_path, "p1")
    assert got.skipped == [pos] and len(got.rows) == 1
    rep = plg.ledger_report(tmp_path, "p1", [])
    assert rep["unknown"] == 1 and rep["unknown_ids"] == ["la-new"]
    assert plg.restore(tmp_path, "p1")["unread"] == [pos]
    # 前の版が飛ばした行（チェックポイントの unread）を、読める版の作り直しで反映する
    plg.save_checkpoint(tmp_path, "p1", {"offset": path.stat().st_size, "unread": [0], "published": {}})
    assert [r["id"] for r in plg.restore(tmp_path, "p1")["rows"]] == [_req(1)["id"]]


def test_versions_appear_only_when_a_lane_differs(tmp_path, monkeypatch):
    """条件 13: レーンの版がオーガナイザーの版と違うときだけ、両方の版が出る。"""
    monkeypatch.setattr(plg, "ndf_version", lambda: "10.0.0")
    _add(tmp_path, _req(1))
    assert "versions" not in plg.ledger_report(tmp_path, "p1", [])
    monkeypatch.setattr(plg, "ndf_version", lambda: "10.1.0")
    assert plg.ledger_report(tmp_path, "p1", [])["versions"] == {"organizer": "10.1.0", "lanes": {"la": "10.0.0"}}


def test_a_waiting_request_missing_from_the_ledger_is_an_orphan(tmp_path):
    """条件 14: ループの記録が待つ依頼が台帳に無ければ、id を orphans に出す。"""
    _add(tmp_path, _req(1))
    rep = plg.ledger_report(tmp_path, "p1", [("la", _req(1)["id"]), ("lb", "lb-q9")])
    assert rep["orphans"] == ["lb-q9"]


def _ledger(main, *kinds):
    for k in kinds:
        plg.append_ledger_row(main, "p1", k, k)


@pytest.mark.parametrize(
    "status,rows,checkpoint,state",
    [
        ("PENDING", [], False, "running"),
        ("SUCCESS", [], False, "closed"),
        ("ERROR", ["opened"], False, "lost"),
        ("ERROR", [], False, "legacy"),
        ("ERROR", ["opened", "closed"], False, "closed"),
        (None, ["opened", "request"], False, "lost"),
        (None, ["opened"], True, "lost"),
        (None, ["opened"], False, "fresh"),
        (None, [], False, "fresh"),
        (None, ["opened", "closed"], False, "closed"),
    ],
)
def test_plan_state_follows_the_record_and_the_ledger(tmp_path, status, rows, checkpoint, state):
    """計画の状態の表（I11）。受け付けるのは動いている計画と失われた計画だけである。"""
    _ledger(tmp_path, *rows)
    if checkpoint:
        plg.save_checkpoint(tmp_path, "p1", {"offset": 0, "unread": [], "published": {}})
    assert plg.plan_state(tmp_path, "p1", status) == state
    assert plg.accepts(state) is (state in ("running", "lost"))


def test_pending_questions_are_empty_for_a_plan_without_an_opened_ledger(tmp_path):
    """台帳を開いていない（台帳を読まない途中の記録の）計画は、外した問いを区別できないため問いを足さない。"""
    _add(tmp_path, {"kind": "ask", "id": "la-q1", "lane": "la"})
    assert plg.pending_questions(tmp_path, "p1") == []
    _ledger(tmp_path, "opened")
    assert plg.pending_questions(tmp_path, "p1") == []  # 開く前の問いは途中の記録が処理した（戻らない）
    _add(tmp_path, {"kind": "ask", "id": "la-q2", "lane": "la"})
    assert [q["id"] for q in plg.pending_questions(tmp_path, "p1")] == ["la-q2"]
    _add(tmp_path, {"kind": "answer", "id": "la-q2"})
    assert plg.pending_questions(tmp_path, "p1") == []


def test_retired_record_keeps_its_files_and_old_ones_are_purged(tmp_path):
    """決定 4: 退避は記録のファイルを別の名前へ移すだけで、古い退避は記録と同じ日数で消える。"""
    import durable_keys

    db = tmp_path / "parallel-abc.sqlite"
    db.write_bytes(b"x")
    (tmp_path / "parallel-abc.sqlite-wal").write_bytes(b"w")
    dst = durable_keys.retire_record(db)
    assert not db.exists() and dst.read_bytes() == b"x" and dst.with_name(dst.name + "-wal").exists()
    assert durable_keys.purge_retired(tmp_path, 30) == []
    assert sorted(durable_keys.purge_retired(tmp_path, 30, now=time.time() + 31 * 86400)) == sorted([dst.name, dst.name + "-wal"])


def test_rebuild_and_open_rows(tmp_path):
    """E1・E7: 失われた計画を始める直前に作り直しの行を足し、回を数える。未開始の計画には足さない。"""
    pp.plan_dir(tmp_path, "p1").mkdir(parents=True)
    plg.open_run(tmp_path, "p1", None)
    assert plg.kinds(tmp_path, "p1") == {"opened"}
    _add(tmp_path, _req(1))
    plg.open_run(tmp_path, "p1", None)
    plg.open_run(tmp_path, "p1", tmp_path / "x.retired-1")
    assert [r["key"] for r in plg.read_ledger_rows(tmp_path, "p1").rows if r["kind"] == "rebuilt"] == ["rebuilt:1", "rebuilt:2"]
    assert plg.restore(tmp_path, "p1")["rebuilds"] == 2
