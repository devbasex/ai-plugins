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
    (plain / ".ndf" / "estimate.json").write_text(json.dumps({"records_dir": "rec"}))
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


# --- 課題ごとの実績の合計: 現状固定（期待値は今のコードの出力。リファクタリングの退行検出が目的） ---


def _at(hour: int) -> str:
    return f"2026-10-01T{hour:02d}:00:00+00:00"


def _human(at, issues, seconds) -> dict:
    return {"schema": 1, "kind": "human", "at": at, "issues": issues, "seconds": seconds}


def _ids(acc) -> list[tuple[str, str | None]]:
    return [(r.run, r.outcome) for r in acc.runs]


def test_empty_rows_give_no_issues():
    assert er.issue_actuals([], "実装", alive=DEAD) == {}


def test_run_without_issues_is_not_counted():
    rows: list[dict] = []
    _run(rows, "r", [], "実装", _at(0), [("x", 100, 1.0)], "成功", _at(1))
    assert er.issue_actuals(rows, "実装", alive=DEAD) == {}


def test_not_completed_keeps_defaults():
    rows: list[dict] = []
    _run(rows, "r", [1], "実装", _at(0), [("x", 1, 0.1)], "承認ゲート", _at(1), config={"harness": "h"})
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert (got.a, got.c, got.h, got.l) == (1.0, 0.1, None, None)
    assert (got.completed, got.cost_unknown, got.shared) == (False, False, False)
    assert got.config == {} and got.finished is None
    assert _ids(got) == [("r", "承認ゲート")]


def test_success_of_earlier_phase_does_not_complete():
    rows: list[dict] = []
    _run(rows, "d", [1], "設計", _at(0), [("x", 10, 1.0)], "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert not got.completed and got.l is None and got.a == 10


def test_first_success_decides_the_end_and_cuts_later_runs():
    rows: list[dict] = []
    # 行の順と開始の順を変えてある。完了は開始の順で最初の成功で決まる
    _run(rows, "second", [1], "実装", _at(2), [("x", 10, 1.0)], "成功", _at(3), config={"harness": "h1"})
    _run(rows, "first", [1], "実装", _at(0), [("x", 20, 2.0)], "成功", _at(1), config={"harness": "h0"})
    _run(rows, "later", [1], "実装", _at(4), [("x", 40, 4.0)], "失敗", _at(5))
    _run(rows, "boundary", [1], "設計", _at(1), [("x", 80, 8.0)], "失敗", _at(2))  # 終わりと同時に始まる実行は残る
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert _ids(got) == [("first", "成功"), ("boundary", "失敗")]
    assert (got.a, got.c, got.l) == (100.0, 10.0, 3600.0)
    assert got.config == {"harness": "h0"} and got.finished == _at(1)


def test_later_phases_are_counted_when_done_is_later():
    rows: list[dict] = []
    _run(rows, "a", [1], "設計", _at(0), [("x", 1, 0.1)], "成功", _at(1))
    _run(rows, "b", [1], "設計", _at(2), [("x", 2, 0.2)], "失敗", _at(3))
    _run(rows, "c", [1], "配布（本番）", _at(4), [("x", 4, 0.4)], "成功", _at(5))
    design = er.issue_actuals(rows, "設計", alive=DEAD)[1]
    assert _ids(design) == [("a", "成功")] and (design.a, design.l) == (1.0, 3600.0)
    prod = er.issue_actuals(rows, "配布（本番）", alive=DEAD)[1]
    assert [r.run for r in prod.runs] == ["a", "b", "c"]
    assert (prod.a, prod.c, prod.l) == (7.0, 0.7, 18000.0) and prod.finished == _at(5)


def _phase_mix() -> list[dict]:
    rows: list[dict] = []
    _run(rows, "a", [1], "実装", _at(0), [("x", 1, 0.1)], "失敗", _at(1))
    _run(rows, "none", [1], None, _at(1), [("x", 2, 0.2)])
    _run(rows, "odd", [1], "不明", _at(2), [("x", 4, 0.4)], "成功", _at(3))
    _run(rows, "c", [1], "検査", _at(4), [("x", 8, 0.8)], "成功", _at(5))
    return rows


def test_run_with_unknown_phase_is_not_limited():
    got = er.issue_actuals(_phase_mix(), "実装", alive=DEAD)[1]
    assert _ids(got) == [("a", "失敗"), ("none", "中断"), ("odd", "成功")]
    assert (got.a, got.c, got.completed) == (7.0, 0.7, False)


def test_done_outside_phases_limits_nothing_and_matches_by_name():
    rows = _phase_mix()
    unmatched = er.issue_actuals(rows, "無い", alive=DEAD)[1]
    assert [r.run for r in unmatched.runs] == ["a", "none", "odd", "c"]
    assert (unmatched.a, unmatched.completed, unmatched.l) == (15.0, False, None)
    matched = er.issue_actuals(rows, "不明", alive=DEAD)[1]
    assert [r.run for r in matched.runs] == ["a", "none", "odd"]  # 終わりの後に始まった c は切られる
    assert (matched.a, matched.l, matched.finished) == (7.0, 10800.0, _at(3))


def test_step_values_that_are_not_numbers():
    rows: list[dict] = []
    steps = [("none", None, None), ("str", "9", "1"), ("bool", True, True), ("f1", 0.04, 0.00004), ("f2", 0.04, 0.00004)]
    _run(rows, "r", [1], "実装", _at(0), steps, "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    # 数でない秒は足さず、数でない費用は不明の印を立てる。bool は 1 として足す。丸めは合計の後
    assert (got.a, got.c) == (1.1, 1.0001) and got.cost_unknown


def test_steps_absent_gives_zero_totals():
    rows: list[dict] = []
    _run(rows, "r", [1], "実装", _at(0), [], "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert (got.a, got.c, got.l, got.cost_unknown, got.completed) == (0.0, 0.0, 3600.0, False, True)


def test_shared_run_is_split_and_rounded_per_issue():
    rows: list[dict] = []
    _run(rows, "r", [1, 2, 3], "実装", _at(0), [("x", 100, 1.0)], "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)
    assert list(got) == [1, 2, 3]
    assert all((v.a, v.c, v.shared, v.completed, v.l) == (33.3, 0.3333, True, True, 3600.0) for v in got.values())


def test_duplicate_issue_in_run_is_counted_twice_and_split_by_two():
    rows: list[dict] = []
    _run(rows, "r", [1, 1], "実装", _at(0), [("x", 100, 1.0)], "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert [r.run for r in got.runs] == ["r", "r"]
    assert (got.a, got.c, got.shared) == (100.0, 1.0, True)


def test_unreadable_start_of_success_gives_no_lead_time():
    rows: list[dict] = []
    _run(rows, "r", [1], "実装", "bad", [("x", 3, 0.5)], "成功", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert got.completed and got.l is None and got.a == 3


def test_unreadable_start_sorts_first_and_clears_lead_time():
    rows: list[dict] = []
    _run(rows, "ok", [1], "実装", _at(0), [("x", 4, 0.25)], "成功", _at(2))
    _run(rows, "bad", [1], "実装", "bad", [("x", 3, 0.5)], "失敗", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert [r.run for r in got.runs] == ["bad", "ok"]
    assert (got.a, got.c, got.l) == (7.0, 0.75, None) and got.completed


def test_unreadable_end_time_drops_runs_with_readable_start():
    # NOTE: 現状固定。終わりの時刻が読めないと `cut or 0` で完了した実行自身が足されない。
    #       妥当かは未判断で、変えるなら別の変更で行う
    rows: list[dict] = []
    _run(rows, "ok", [1], "実装", _at(0), [("x", 3, 0.5)], "成功", "bad")
    _run(rows, "bad", [1], "実装", "bad", [("x", 4, 0.25)], "失敗", _at(1))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert _ids(got) == [("bad", "失敗")]
    assert (got.a, got.c, got.l) == (4.0, 0.25, None)
    assert got.completed and got.finished == "bad"


def test_outcome_of_run_without_end_follows_alive_and_imported():
    rows: list[dict] = []
    _run(rows, "r", [1], "実装", _at(0), [("x", 1, 0.1)])
    assert _ids(er.issue_actuals(rows, "実装", alive=lambda run: True)[1]) == [("r", "実行中")]
    assert _ids(er.issue_actuals(rows, "実装")[1]) == [("r", "中断")]  # 既定は plan_alive（状態ディレクトリが無い）
    imported: list[dict] = []
    _run(imported, "r", [1], "実装", _at(0), [("x", 1, 0.1)], imported=True)
    assert _ids(er.issue_actuals(imported, "実装", alive=DEAD)[1]) == [("r", None)]


def _since_rows() -> list[dict]:
    rows: list[dict] = []
    _run(rows, "same", [1], "実装", _at(3), [("x", 1, 0.1)], "失敗", _at(4))
    _run(rows, "after", [1], "実装", _at(5), [("x", 2, 0.2)], "成功", _at(6))
    _run(rows, "bad", [1], "実装", "bad", [("x", 4, 0.4)], "失敗", _at(6))
    return rows


def test_since_excludes_same_time_and_unreadable_start():
    got = er.issue_actuals(_since_rows(), "実装", since=_at(3), alive=DEAD)[1]
    assert _ids(got) == [("after", "成功")]
    assert (got.a, got.l) == (2.0, 3600.0)  # L は残った最初の実行から測る


@pytest.mark.parametrize("since", ["bad", "", None])
def test_since_unreadable_or_empty_limits_nothing(since):
    got = er.issue_actuals(_since_rows(), "実装", since=since, alive=DEAD)[1]
    assert [r.run for r in got.runs] == ["bad", "same", "after"]
    assert (got.a, got.c, got.l) == (7.0, 0.7, None)


def _human_rows() -> list[dict]:
    return [
        _human(_at(1), [1, 2], 60),
        _human(_at(2), [1], 0.5),
        _human(_at(3), [1], "x"),
        _human(_at(3), [1], None),
        _human(_at(3), None, 5),
        _human("bad", [3], 7),
        _human(_at(0), [4], 0),
        _human(_at(1), [5], True),
    ]


def test_human_rows_make_issues_and_are_not_split():
    got = er.issue_actuals(_human_rows(), "実装", alive=DEAD)
    assert {k: v.h for k, v in got.items()} == {1: 60.5, 2: 60.0, 3: 7.0, 4: 0.0, 5: 1.0}
    assert (got[1].a, got[1].c, got[1].l, got[1].runs, got[1].completed) == (0.0, 0.0, None, [], False)


def test_human_rows_follow_since_with_same_rule_as_runs():
    got = er.issue_actuals(_human_rows(), "実装", since=_at(1), alive=DEAD)
    assert {k: v.h for k, v in got.items()} == {1: 0.5}


def test_human_row_is_counted_regardless_of_phase_limit():
    rows: list[dict] = []
    _run(rows, "c", [1], "検査", _at(0), [("x", 1, 0.1)], "成功", _at(1))
    rows.append(_human(_at(1), [1], 60))
    got = er.issue_actuals(rows, "実装", alive=DEAD)[1]
    assert got.h == 60 and got.runs == [] and got.a == 0


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
    assert ei.import_supervise_progress(tmp_path / "sv", out) == {"added": 2, "skipped": 0, "foreign": 0}
    assert ei.import_supervise_progress(tmp_path / "sv", out) == {"added": 0, "skipped": 2, "foreign": 0}
    rows = er.read_actuals(out)
    starts = [r for r in rows if r["kind"] == "run_start"]
    assert all(r["imported"] for r in starts) and [r["attempt"] for r in starts] == [1, 2]
    assert starts[1]["from"] == "impl" and starts[0]["issues"] == [9]
    table = er.run_table(rows, alive=DEAD)
    assert all(r.outcome is None for r in table.values())
    assert er.issue_actuals(rows, "実装", alive=DEAD)[9].a == 1100
