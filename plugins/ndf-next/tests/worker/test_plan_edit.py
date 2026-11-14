"""conductor のプランの書き換え: 控え・差分・理由を残す（#3111）。"""

from __future__ import annotations

import json

import pytest

from ndf.core import worker


def _plan(path, *ids):
    path.write_text(json.dumps({"name": "p", "steps": [{"id": i, "run": ["true"]} for i in ids]}, indent=1), encoding="utf-8")
    return path


def test_edit_keeps_backup_diff_and_reason(tmp_path):
    plan, new, state = _plan(tmp_path / "plan.json", "a", "b"), _plan(tmp_path / "new.json", "a"), tmp_path / "state"
    old = plan.read_text(encoding="utf-8")
    row = worker.edit_plan(plan, new, "b を飛ばす", state, now="2026-11-14T01:00:00+00:00")
    assert plan.read_text(encoding="utf-8") == new.read_text(encoding="utf-8")
    assert (tmp_path / row["backup"]).read_text(encoding="utf-8") == old
    assert '-   "id": "b",' in row["diff"]
    [logged] = worker.list_edits([state / "progress.jsonl"])
    assert logged["reason"] == "b を飛ばす" and logged["event"] == "plan_edit"


def test_same_content_writes_nothing(tmp_path):
    plan, state = _plan(tmp_path / "plan.json", "a"), tmp_path / "state"
    assert worker.edit_plan(plan, _plan(tmp_path / "new.json", "a"), "同じ", state) is None
    assert not (state / "progress.jsonl").exists()


@pytest.mark.parametrize("reason,content", [("", None), ("理由", "{壊れた"), ("理由", '{"name": "p", "steps": []}')])
def test_wrong_calls_write_nothing(tmp_path, reason, content):
    plan, new, state = _plan(tmp_path / "plan.json", "a"), _plan(tmp_path / "new.json", "b"), tmp_path / "state"
    if content is not None:
        new.write_text(content, encoding="utf-8")
    before = plan.read_text(encoding="utf-8")
    with pytest.raises(worker.WorkerError) as err:
        worker.edit_plan(plan, new, reason, state)
    assert err.value.kind == "invalid"
    assert plan.read_text(encoding="utf-8") == before and not state.exists()


def test_list_edits_orders_by_time_across_plans(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text('{"event": "plan_edit", "at": "2"}\n{"event": "chosen", "at": "0"}\n', encoding="utf-8")
    b.write_text('{"event": "plan_edit", "at": "1"}\n{"event": "plan_edit", "at": "3"}\n', encoding="utf-8")
    assert [r["at"] for r in worker.list_edits([a, b, tmp_path / "none.jsonl"])] == ["1", "2", "3"]


def test_list_edits_stops_on_a_broken_line(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('{"event": "plan_edit"}\n{壊れた\n', encoding="utf-8")
    with pytest.raises(worker.WorkerError) as err:
        worker.list_edits([path])
    assert err.value.kind == "invalid"


def test_plan_edit_command_reports_the_edit(tmp_path, capsys):
    plan, new = _plan(tmp_path / "plan.json", "a", "b"), _plan(tmp_path / "new.json", "a")
    code = worker.main(["plan-edit", str(plan), "--new", str(new), "--reason", "飛ばす", "--state-dir", str(tmp_path / "s")])
    assert code == 0
    capsys.readouterr()
    assert worker.main(["plan-edits", str(plan), "--state-dir", str(tmp_path / "s")]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["metrics"] == {"edits": 1} and out["items"][0]["reason"] == "飛ばす"


def test_list_edits_stops_on_an_edit_without_time(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('{"event": "plan_edit"}\n', encoding="utf-8")
    with pytest.raises(worker.WorkerError):
        worker.list_edits([path])


# --- 現状固定テスト（edit_plan の分割の前に経路を固定する） ----------------------------------


def test_edit_row_shape_and_backup_name(tmp_path):
    plan, new, state = _plan(tmp_path / "plan.json", "a", "b"), _plan(tmp_path / "new.json", "a"), tmp_path / "s" / "deep"
    at = "2026-11-14T01:02:03+00:00"
    row = worker.edit_plan(plan, new, "理由", state, now=at)
    assert set(row) == {"event", "at", "plan", "reason", "diff", "backup"}
    assert row["event"] == "plan_edit" and row["at"] == at and row["plan"] == str(plan) and row["reason"] == "理由"
    assert row["backup"] == str(tmp_path / "plan.json.bak-20261114T0102030000")
    assert row["diff"].startswith(f"--- {plan}\n+++ {new}\n")
    assert not (tmp_path / "plan.json.tmp").exists()
    [line] = (state / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == row


def test_successive_edits_append_rows(tmp_path):
    plan, state = _plan(tmp_path / "plan.json", "a", "b", "c"), tmp_path / "state"
    worker.edit_plan(plan, _plan(tmp_path / "n1.json", "a", "b"), "一回目", state, now="2026-11-14T01:00:00+00:00")
    worker.edit_plan(plan, _plan(tmp_path / "n2.json", "a"), "二回目", state, now="2026-11-14T02:00:00+00:00")
    rows = worker.list_edits([state / "progress.jsonl"])
    assert [r["reason"] for r in rows] == ["一回目", "二回目"]
    assert len(list(tmp_path.glob("plan.json.bak-*"))) == 2


def test_blank_reason_is_checked_before_reading_new(tmp_path):
    plan, state = _plan(tmp_path / "plan.json", "a"), tmp_path / "state"
    with pytest.raises(worker.WorkerError) as err:
        worker.edit_plan(plan, tmp_path / "missing.json", "  \n", state)
    assert err.value.kind == "invalid" and not state.exists()


def test_missing_plan_is_invalid(tmp_path):
    plan, state = tmp_path / "plan.json", tmp_path / "state"
    with pytest.raises(worker.WorkerError) as err:
        worker.edit_plan(plan, _plan(tmp_path / "new.json", "a"), "理由", state)
    assert err.value.kind == "invalid" and str(plan) in err.value.detail
    assert not state.exists() and not list(tmp_path.glob("plan.json.bak-*"))
