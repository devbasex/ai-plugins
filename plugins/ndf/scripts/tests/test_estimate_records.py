"""lib/estimate_records.py: 記録の置き場・行の形・課題ごとの実績の合計・取り込み（#1856）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import estimate_records as er  # noqa: E402
import schema  # noqa: E402

DEAD = lambda run: False  # noqa: E731  実行中のプロセスは無い


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# --- 置き場 ---


def test_records_dir_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_ESTIMATE_DIR", str(tmp_path / "x"))
    assert er.records_dir(tmp_path) == tmp_path / "x"


def test_records_dir_git_common_dir_from_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_ESTIMATE_DIR", raising=False)
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
    _git(repo, "worktree", "add", "-q", str(tmp_path / "wt"))
    want = (repo / ".git" / "ndf" / "estimate").resolve()
    assert er.records_dir(repo).resolve() == want
    assert er.records_dir(tmp_path / "wt" ).resolve() == want


def test_records_dir_decl_then_non_git(tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_ESTIMATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    plain = tmp_path / "plain"
    plain.mkdir()
    got = er.records_dir(plain)
    assert got.parent == tmp_path / "state" / "ndf" / "estimate"
    assert len(got.name) == 12
    (plain / ".ndf").mkdir()
    (plain / ".ndf" / "estimate.json").write_text(json.dumps({"records_dir": "rec"}))
    assert er.records_dir(plain) == plain / "rec"


def test_decl_rejects_unknown_key(tmp_path):
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "estimate.json").write_text(json.dumps({"nope": 1}))
    with pytest.raises(er.DeclError):
        er.load_decl(tmp_path)


# --- 行の形 ---


def test_append_validates_shape(tmp_path):
    with pytest.raises(schema.ShapeError):
        er.append_actual(tmp_path, {"kind": "step", "run": "r"})  # step と type が無い
    assert not (tmp_path / er.ACTUALS).exists()
    assert er.append_actual(tmp_path, er.human_row([5], 300, "見た")) is None
    rows = er.read_actuals(tmp_path)
    assert rows[0]["kind"] == "human" and rows[0]["schema"] == 1


def test_estimate_row_has_no_sum_field(tmp_path):
    row = {
        "id": "x",
        "issue": 1,
        "done": "実装",
        "items": {},
        "config": {},
        "values": {k: {"mark": "未校正"} for k in "AHLC"},
        "total": 1,
    }
    with pytest.raises(schema.ShapeError):
        er.append_estimate(tmp_path, row)


def test_read_skips_rows_without_schema(tmp_path):
    (tmp_path / er.ACTUALS).write_text('{"kind": "human"}\nnot json\n')
    assert er.read_actuals(tmp_path) == []


def test_append_is_append_only(tmp_path):
    base = {"done": "実装", "items": {}, "config": {}, "values": {k: {"mark": "未校正"} for k in "AHLC"}}
    er.append_estimate(tmp_path, {"id": "a", "issue": 1, **base})
    er.append_estimate(tmp_path, {"id": "b", "issue": 1, **base})
    assert [r["id"] for r in er.read_estimates(tmp_path)] == ["a", "b"]


# --- 課題ごとの実績の合計 ---


def _run(rows, run, issues, phase, at, steps, outcome=None, end_at=None, frm=None, imported=False, config=None):
    rows.append({"schema": 1, "kind": "run_start", "at": at, "run": run, "plan": f"/p/{run}.json", "issues": issues, "phase": phase, "from": frm, "attempt": 1, "imported": imported})
    for sid, sec, cost in steps:
        rows.append({"schema": 1, "kind": "step", "at": at, "run": run, "step": sid, "type": "work", "exit": 0, "seconds": sec, "cost_usd": cost, "retry": 0})
    if outcome:
        rows.append({"schema": 1, "kind": "run_end", "at": end_at, "run": run, "outcome": outcome, "wall_seconds": 1, "config": config or {}})


def test_totals_include_failed_cancelled_interrupted_and_retry():
    rows: list[dict] = []
    _run(rows, "r1", [7], "実装", "2026-10-01T00:00:00+00:00", [("impl", 100, 1.0)], "失敗", "2026-10-01T00:10:00+00:00")
    _run(rows, "r2", [7], "実装", "2026-10-01T01:00:00+00:00", [("impl", 200, 2.0)], "取り消し", "2026-10-01T01:10:00+00:00")
    _run(rows, "r3", [7], "実装", "2026-10-01T02:00:00+00:00", [("impl", 300, None)])  # 中断
    _run(rows, "r4", [7], "実装", "2026-10-01T03:00:00+00:00", [("impl", 400, 4.0)], "成功", "2026-10-01T04:00:00+00:00", frm="impl")
    got = er.issue_actuals(rows, "実装", alive=DEAD)[7]
    assert got.a == 1000
    assert got.c == 7.0 and got.cost_unknown
    assert got.l == 4 * 3600
    assert got.completed
    assert {r.run: r.outcome for r in got.runs} == {"r1": "失敗", "r2": "取り消し", "r3": "中断", "r4": "成功"}
    assert any(r.retry for r in got.runs)


def test_totals_share_runs_between_issues_and_skip_incomplete():
    rows: list[dict] = []
    _run(rows, "d", [1, 2], "設計", "2026-10-01T00:00:00+00:00", [("w", 600, 1.0)], "成功", "2026-10-01T00:20:00+00:00")
    _run(rows, "i", [1], "実装", "2026-10-01T01:00:00+00:00", [("impl", 60, 0.5)], "成功", "2026-10-01T02:00:00+00:00")
    got = er.issue_actuals(rows, "実装", alive=DEAD)
    assert got[1].a == 360 and got[1].shared
    assert got[1].completed and not got[2].completed
    # 完了条件のフェーズより後の実行は足さない
    _run(rows, "c", [1], "検査", "2026-10-01T03:00:00+00:00", [("x", 999, 9.0)], "成功", "2026-10-01T04:00:00+00:00")
    assert er.issue_actuals(rows, "実装", alive=DEAD)[1].a == 360


def test_imported_and_running_runs_are_not_interrupted():
    rows: list[dict] = []
    _run(rows, "a", [1], "実装", "2026-10-01T00:00:00+00:00", [("impl", 5, None)], imported=True)
    _run(rows, "b", [1], "実装", "2026-10-01T00:00:00+00:00", [("impl", 5, None)])
    table = er.run_table(rows, alive=lambda run: run.run == "b")
    assert table["a"].outcome is None and table["b"].outcome == "実行中"


def test_human_rows_give_h():
    rows = [{"schema": 1, "kind": "human", "at": "2026-10-01T00:00:00+00:00", **er.human_row([3], 120, None)}]
    _run(rows, "x", [3], "実装", "2026-10-01T00:00:00+00:00", [("impl", 5, 0.1)], "成功", "2026-10-01T00:01:00+00:00")
    assert er.issue_actuals(rows, "実装", alive=DEAD)[3].h == 120


def test_since_limits_runs():
    rows: list[dict] = []
    _run(rows, "o", [1], "実装", "2026-09-01T00:00:00+00:00", [("impl", 50, 0.1)], "失敗", "2026-09-01T00:01:00+00:00")
    _run(rows, "n", [1], "実装", "2026-10-01T00:00:00+00:00", [("impl", 70, 0.1)], "成功", "2026-10-01T00:01:00+00:00")
    assert er.issue_actuals(rows, "実装", since="2026-09-15T00:00:00+00:00", alive=DEAD)[1].a == 70


# --- 取り込み ---


def test_import_progress_is_idempotent(tmp_path):
    sv = tmp_path / "sv" / "m1"
    sv.mkdir(parents=True)
    plan = sv / "5-impl-9.json"
    plan.write_text(json.dumps({"フェーズ": "実装", "課題": [9], "steps": []}, ensure_ascii=False))
    st = sv / "5-impl-9-state"
    st.mkdir()
    lines = [
        {"kind": "run", "at": "2026-10-01T00:00:00+09:00", "from": None},
        {"kind": "step", "at": "2026-10-01T00:10:00+09:00", "step": "impl", "type": "work", "exit": 1, "seconds": 600, "cost": 1.5},
        {"kind": "run", "at": "2026-10-01T01:00:00+09:00", "from": "impl"},
        {"kind": "step", "at": "2026-10-01T01:10:00+09:00", "step": "impl", "type": "work", "exit": 0, "seconds": 500, "cost": 1.0},
        {"kind": "worker", "at": "x", "text": "y"},
    ]
    (st / "progress.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    out = tmp_path / "rec"
    assert er.import_progress(tmp_path / "sv", out) == {"added": 2, "skipped": 0}
    assert er.import_progress(tmp_path / "sv", out) == {"added": 0, "skipped": 2}
    rows = er.read_actuals(out)
    starts = [r for r in rows if r["kind"] == "run_start"]
    assert all(r["imported"] for r in starts) and [r["attempt"] for r in starts] == [1, 2]
    assert starts[1]["from"] == "impl" and starts[0]["issues"] == [9]
    table = er.run_table(rows, alive=DEAD)
    assert all(r.outcome is None for r in table.values())
    assert er.issue_actuals(rows, "実装", alive=DEAD)[9].a == 1100
