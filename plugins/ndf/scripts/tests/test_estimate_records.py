"""lib/estimate_records.py: 記録の置き場・行の形・課題ごとの実績の合計・取り込み（#1856）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import estimate_import as ei  # noqa: E402
import estimate_records as er  # noqa: E402
import schema  # noqa: E402

DEAD = lambda run: False  # noqa: E731  実行中のプロセスは無い


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# --- 置き場 ---


def test_records_dir_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_ESTIMATE_DIR", str(tmp_path / "x"))
    assert er.estimate_dir(tmp_path) == tmp_path / "x"


def test_records_dir_git_common_dir_from_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_ESTIMATE_DIR", raising=False)
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
    _git(repo, "worktree", "add", "-q", str(tmp_path / "wt"))
    want = (repo / ".git" / "ndf" / "estimate").resolve()
    assert er.estimate_dir(repo).resolve() == want
    assert er.estimate_dir(tmp_path / "wt").resolve() == want


def test_records_dir_decl_then_non_git(tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_ESTIMATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    plain = tmp_path / "plain"
    plain.mkdir()
    got = er.estimate_dir(plain)
    assert got.parent == tmp_path / "state" / "ndf" / "estimate"
    assert len(got.name) == 12
    (plain / ".ndf").mkdir()
    (plain / ".ndf" / "estimate.json").write_text(json.dumps({"estimate_dir": "rec"}))
    assert er.estimate_dir(plain) == plain / "rec"


def test_decl_rejects_unknown_key(tmp_path):
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "estimate.json").write_text(json.dumps({"nope": 1}))
    with pytest.raises(er.DeclError):
        er.load_estimate_decl(tmp_path)


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
    rows.append(
        {
            "schema": 1,
            "kind": "run_start",
            "at": at,
            "run": run,
            "plan": f"/p/{run}.json",
            "issues": issues,
            "phase": phase,
            "from": frm,
            "attempt": 1,
            "imported": imported,
        }
    )
    for sid, sec, cost in steps:
        rows.append(
            {
                "schema": 1,
                "kind": "step",
                "at": at,
                "run": run,
                "step": sid,
                "type": "work",
                "exit": 0,
                "seconds": sec,
                "cost_usd": cost,
                "retry": 0,
            }
        )
    if outcome:
        rows.append(
            {"schema": 1, "kind": "run_end", "at": end_at, "run": run, "outcome": outcome, "wall_seconds": 1, "config": config or {}}
        )


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
    assert ei.import_supervise_progress(tmp_path / "sv", out) == {"added": 2, "skipped": 0}
    assert ei.import_supervise_progress(tmp_path / "sv", out) == {"added": 0, "skipped": 2}
    rows = er.read_actuals(out)
    starts = [r for r in rows if r["kind"] == "run_start"]
    assert all(r["imported"] for r in starts) and [r["attempt"] for r in starts] == [1, 2]
    assert starts[1]["from"] == "impl" and starts[0]["issues"] == [9]
    table = er.run_table(rows, alive=DEAD)
    assert all(r.outcome is None for r in table.values())
    assert er.issue_actuals(rows, "実装", alive=DEAD)[9].a == 1100


def _write_progress(state, lines):
    state.mkdir(parents=True, exist_ok=True)
    (state / "progress.jsonl").write_text(
        "".join((l if isinstance(l, str) else json.dumps(l, ensure_ascii=False)) + "\n" for l in lines),
        encoding="utf-8",
    )


def test_import_progress_step_row_fields(tmp_path):
    sv = tmp_path / "sv"
    sv.mkdir()
    (sv / "p.json").write_text(json.dumps({"フェーズ": "", "課題": [3, "4", "x", None]}, ensure_ascii=False))
    _write_progress(
        sv / "p-state",
        [
            {"kind": "step", "at": "2026-10-01T00:00:01+09:00", "step": "before", "type": "work"},
            "not json",
            "[1, 2]",
            {"kind": "run", "from": "a"},
            {"kind": "run", "at": "2026-10-01T00:00:00+09:00", "from": "s1"},
            {"kind": "step", "at": "t1", "step": "s1", "type": "run", "exit": 0, "seconds": 5, "cost": 9.0},
            {"kind": "step", "at": "t2", "step": "s2", "type": "pr", "exit": "0", "seconds": "5"},
            {"kind": "step", "at": "t3", "step": 7, "exit": 2, "seconds": 1.5, "cost": "1"},
            {"kind": "step", "at": "t4", "step": "s4", "type": "work", "cost": 2},
            {"kind": "step", "at": "t5", "step": "", "type": "work"},
        ],
    )
    out = tmp_path / "rec"
    assert ei.import_supervise_progress(sv, out) == {"added": 1, "skipped": 0}
    rows = er.read_actuals(out)
    start = rows[0]
    rid = er.actual_run_id(str(sv / "p.json"), "2026-10-01T00:00:00+09:00")
    assert {k: start[k] for k in ("kind", "at", "run", "plan", "issues", "phase", "from", "attempt", "imported")} == {
        "kind": "run_start",
        "at": "2026-10-01T00:00:00+09:00",
        "run": rid,
        "plan": str(sv / "p.json"),
        "issues": [3, 4],
        "phase": None,
        "from": "s1",
        "attempt": 1,
        "imported": True,
    }
    steps = [{k: r[k] for k in ("at", "run", "step", "type", "exit", "seconds", "cost_usd", "retry")} for r in rows[1:]]
    assert steps == [
        {"at": "t1", "run": rid, "step": "s1", "type": "run", "exit": 0, "seconds": 5, "cost_usd": 0.0, "retry": 0},
        {"at": "t2", "run": rid, "step": "s2", "type": "pr", "exit": None, "seconds": None, "cost_usd": 0.0, "retry": 0},
        {"at": "t3", "run": rid, "step": "7", "type": "", "exit": 2, "seconds": 1.5, "cost_usd": None, "retry": 0},
        {"at": "t4", "run": rid, "step": "s4", "type": "work", "exit": None, "seconds": None, "cost_usd": 2, "retry": 0},
    ]


def test_import_progress_finds_plan_and_skips_other_dirs(tmp_path):
    sv = tmp_path / "sv"
    run = {"kind": "run", "at": "2026-10-01T00:00:00+09:00"}
    # 隣のプランが無ければ中の写し（plan.json）を読む
    inner = sv / "a" / "q-state"
    _write_progress(inner, [run])
    (inner / "plan.json").write_text(json.dumps({"課題": [1]}), encoding="utf-8")
    # 名前が -state で終わらないディレクトリは見ない
    _write_progress(sv / "b" / "other", [run])
    (sv / "b" / "other.json").write_text(json.dumps({"課題": [2]}), encoding="utf-8")
    # プランが無い・壊れている・dict でない状態ディレクトリは見ない
    _write_progress(sv / "c" / "none-state", [run])
    _write_progress(sv / "d" / "bad-state", [run])
    (sv / "d" / "bad.json").write_text("{", encoding="utf-8")
    _write_progress(sv / "e" / "list-state", [run])
    (sv / "e" / "list.json").write_text("[1]", encoding="utf-8")
    # run の行が無ければ何も足さない
    _write_progress(sv / "f" / "empty-state", [{"kind": "step", "step": "x"}])
    (sv / "f" / "empty.json").write_text(json.dumps({"課題": [6]}), encoding="utf-8")
    out = tmp_path / "rec"
    assert ei.import_supervise_progress(str(sv), str(out)) == {"added": 1, "skipped": 0}
    rows = er.read_actuals(out)
    assert [r["kind"] for r in rows] == ["run_start"]
    assert rows[0]["plan"] == str(inner / "plan.json") and rows[0]["issues"] == [1]
    assert rows[0]["from"] is None and rows[0]["phase"] is None


def test_import_progress_skips_runs_already_recorded(tmp_path):
    sv = tmp_path / "sv"
    sv.mkdir()
    (sv / "p.json").write_text(json.dumps({"課題": [1]}), encoding="utf-8")
    first = {"kind": "run", "at": "2026-10-01T00:00:00+09:00"}
    _write_progress(sv / "p-state", [first])
    out = tmp_path / "rec"
    assert ei.import_supervise_progress(sv, out) == {"added": 1, "skipped": 0}
    _write_progress(
        sv / "p-state",
        [first, {"kind": "step", "step": "late"}, {"kind": "run", "at": "2026-10-01T01:00:00+09:00"}, {"kind": "step", "step": "s"}],
    )
    assert ei.import_supervise_progress(sv, out) == {"added": 1, "skipped": 1}
    rows = er.read_actuals(out)
    assert [(r["kind"], r.get("attempt"), r.get("step")) for r in rows] == [
        ("run_start", 1, None),
        ("run_start", 2, None),
        ("step", None, "s"),
    ]


def test_import_progress_raises_when_append_fails(tmp_path, monkeypatch):
    sv = tmp_path / "sv"
    sv.mkdir()
    (sv / "p.json").write_text(json.dumps({"課題": [1]}), encoding="utf-8")
    _write_progress(sv / "p-state", [{"kind": "run", "at": "2026-10-01T00:00:00+09:00"}])
    monkeypatch.setattr(ei, "append_actual", lambda directory, row: "書けない")
    with pytest.raises(OSError, match="書けない"):
        ei.import_supervise_progress(sv, tmp_path / "rec")
