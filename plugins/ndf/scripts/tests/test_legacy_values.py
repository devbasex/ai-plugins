"""旧い値の読み替え（#2620）。旧い値を持つ記録が、新しい値を持つ記録と同じに読まれること。

この文書と `scripts/data/legacy-values.json` だけが、書かれなくなった値をそのまま持つ（`.ndf/glossary.json` の外す範囲）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import context_policy as cp  # noqa: E402
import dist_record  # noqa: E402
import estimate_records as er  # noqa: E402
import legacy_values  # noqa: E402
import slow_step  # noqa: E402
import sprint_loop_gate as sl_gate  # noqa: E402
import sprint_loop_select as sl_select  # noqa: E402
import stage_skills  # noqa: E402
import transcript_agents  # noqa: E402
from step_result import EXIT_UNREADABLE, StepError  # noqa: E402
from supervise_lib import queue  # noqa: E402
from test_progress_record_stages import RECORD, SYNC, Env  # noqa: E402
from test_sprint_loop import env as loop_env  # noqa: E402,F401  ループの 1 回の記録の土台
from test_sprint_loop import mvv, rec, request_lap  # noqa: E402,F401
from test_projects_sync_record import declare, repo, run  # noqa: E402,F401

OLD_WORD = "配布"


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("stage", "配布", "デプロイ"),
        ("record_heading", "## 配布の記録", "## リリース記録"),
        ("phase", "配布（開発版）", "開発版のデプロイ"),
        ("phase", "配布（本番）", "リリース"),
        ("phase", "配布の依頼", "リリースの依頼"),
        ("lap_outcome", "配布待ち", "リリース待ち"),
        ("failure_kind", "配布", "リリース"),
        ("failure_kind", "配布の失敗", "リリースの失敗"),
        ("pipeline_stage", "配布", "デプロイ"),
    ],
)
def test_old_value_reads_as_the_new_value_and_the_new_value_stays(name, old, new):
    assert legacy_values.to_current(name, old) == new
    assert legacy_values.to_current(name, new) == new


def test_record_stage_converts_the_prefix_and_keeps_the_reason():
    got = legacy_values.to_current("record_stage_prefix", "配布なし（本番の記録なし）")
    assert got == "デプロイなし（本番の記録なし）"
    assert legacy_values.to_current("record_stage_prefix", "本番") == "本番"


def test_a_value_outside_the_table_passes_through():
    assert legacy_values.to_current("stage", "実装") == "実装"
    assert legacy_values.to_current("stage", None) is None


def test_unknown_set_stops():
    with pytest.raises(StepError) as e:
        legacy_values.to_current("無い集合", "a")
    assert e.value.code == EXIT_UNREADABLE


@pytest.mark.parametrize(
    "text",
    [
        '{"version": 1, "sets": {"a": {"x": "y", "x": "z"}}}',  # 旧い値が 2 つの新しい値へ変換される
        '{"version": 1, "sets": {"a": {"x": "y", "y": "z"}}}',  # 新しい値が旧い値の欄にある
        '{"version": 2, "sets": {}}',
        "読めない",
    ],
)
def test_a_broken_table_stops(text):
    with pytest.raises(StepError) as e:
        legacy_values.parse_table(text)
    assert e.value.code == EXIT_UNREADABLE


def test_new_values_of_every_set_do_not_carry_the_old_word():
    for name, pairs in legacy_values.legacy_table().items():
        assert all(OLD_WORD not in new for new in pairs.values()), name


def test_the_shipped_table_is_json_with_every_set_of_the_design():
    raw = json.loads(legacy_values.TABLE.read_text(encoding="utf-8"))
    assert set(raw["sets"]) == {"stage", "record_heading", "record_stage_prefix", "phase", "lap_outcome", "failure_kind", "pipeline_stage"}


# ---------------- 読み手: 旧い値の記録を新しい値の記録と同じに読む（受け入れ条件 7）----------------


def script_module(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "old_block,new_block",
    [
        (
            "## 配布の記録\n\n段階: 配布なし（本番の記録なし）\nスプリント: #5\n",
            "## リリース記録\n\n段階: デプロイなし（本番の記録なし）\nスプリント: #5\n",
        ),
        (
            "## 配布の記録\n\n段階: 本番\n版: 1.0.0 → 1.1.0\nスプリント: #5\n",
            "## リリース記録\n\n段階: 本番\n版: 1.0.0 → 1.1.0\nスプリント: #5\n",
        ),
        ("## 配布の記録\n\n段階: 検証\nスプリント: #5\n", "## リリース記録\n\n段階: 検証\nスプリント: #5\n"),
    ],
)
def test_record_with_the_old_heading_reads_the_same(old_block, new_block):
    old, new = dist_record.parse_record(old_block), dist_record.parse_record(new_block)
    assert old == new and old["found"]
    assert dist_record.released(old["stage"]) == dist_record.released(new["stage"])


def test_last_record_is_chosen_across_old_and_new_headings():
    text = "## リリース記録\n\n段階: 検証\n\n## 配布の記録\n\n段階: 本番\n版: 1.0.0 → 1.1.0\n"
    assert dist_record.parse_record(text)["stage"] == "本番"


def test_old_pending_lap_stays_pending_and_is_not_selected_again(loop_env):  # noqa: F811
    """旧い値（配布待ち）のループの 1 回は、新しい値のループの 1 回と同じ保留したループの 1 回として読み、課題を選び直さない。"""
    request_lap(loop_env)
    path = loop_env.root / ".ndf/loop/l1/loop.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["laps"][0]["outcome"] = "配布待ち"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    out = sl_select.cmd_select(loop_env, "l1")
    assert out["metrics"]["selected"] == [13]
    assert {x["number"]: x["reason"] for x in out["items"]}[11] == "保留したループの 1 回の課題"
    assert rec(loop_env)["laps"][0]["outcome"] == "リリース待ち"


def test_old_failure_of_a_closed_lap_reads_as_the_new_kind():
    got = legacy_values.normalize_laps({"laps": [{"n": 1, "outcome": "失敗", "failure": {"kind": "配布", "step": "配布の失敗"}}]})
    assert got["laps"][0]["failure"] == {"kind": "リリース", "step": "リリースの失敗"}


def test_plan_of_the_previous_version_keeps_its_meaning(tmp_path):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({"フェーズ": "配布の依頼"}, ensure_ascii=False), encoding="utf-8")
    assert sl_gate.plan_info(str(plan))["フェーズ"] == sl_gate.RELEASE_REQUEST_PHASE


@pytest.mark.parametrize("old,kind", [("配布（開発版）", "開発版"), ("配布（本番）", "本番")])
def test_plan_kind_of_an_old_phase(tmp_path, old, kind):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({"フェーズ": old}, ensure_ascii=False), encoding="utf-8")
    assert script_module("sprint-state.py").plan_kind(str(plan)) == kind


def test_queue_skips_the_pull_request_of_an_old_release_plan(tmp_path):
    old = tmp_path / "old.md"
    old.write_text("- フェーズ: 配布（開発版）\n- Pull Request: #5\n", encoding="utf-8")
    new = tmp_path / "new.md"
    new.write_text("- フェーズ: 開発版のデプロイ\n- Pull Request: #6\n", encoding="utf-8")
    impl = tmp_path / "impl.md"
    impl.write_text("- フェーズ: 実装\n- Pull Request: #7\n", encoding="utf-8")
    items = [{"result": "完了", "report": str(p)} for p in (old, new, impl)]
    assert queue.queue_prs(items) == ["7"]


def test_old_stage_reads_as_the_new_stage_in_skills_and_transcripts():
    table = {"stages": {"デプロイ": ["a"]}}
    assert stage_skills.skills_for_step({"stage": "配布"}, table) == ["a"]
    assert transcript_agents.role_of("配布: #540", "supervisor") == transcript_agents.role_of("デプロイ: #540", "supervisor") == "取り込み"


def test_rework_counts_old_and_new_stage_as_one():
    rows = [{"verdict": cp.CONTINUE, "stage": "配布"}, {"verdict": cp.CONTINUE, "stage": "デプロイ"}]
    assert script_module("context-policy.py")._rework(rows) == 1


def test_history_of_an_old_phase_counts_for_the_new_phase(tmp_path):
    hist = tmp_path / "h.jsonl"
    rows = [{"phase": "配布（本番）", "step": "s", "seconds": 1}, {"phase": "リリース", "step": "s", "seconds": 2}]
    hist.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert slow_step.read_history(hist, "リリース", "s") == [1.0, 2.0]


def test_estimate_of_an_old_phase_passes_the_shape_with_the_new_value():
    row = {"done": "配布（本番）"}
    with pytest.raises(er.schema.ShapeError):  # 他の欄が無いので形では落ちるが、done は読み替えた後である
        er.check_estimate(row)
    assert row["done"] == "リリース"


def test_final_stage_accepts_the_old_stage_name():
    mod = script_module("sprint-close.py")
    args = mod.build_parser().parse_args(["--record-pr", "0", "--final-stage", "配布"])
    assert args.final_stage == "デプロイ"


def progress_env(tmp_path) -> Env:
    return Env(tmp_path)


def test_old_stage_argument_is_recorded_under_the_new_name(tmp_path):
    env = progress_env(tmp_path)
    out = env.run(str(RECORD), "7", "配布")
    assert out.returncode == 0, out.stderr
    assert "- [x] デプロイ" in env.body(7) and "配布" not in env.body(7)
    assert env.stages(7) == ["デプロイ"]


def test_old_stage_line_of_the_body_moves_to_the_new_name_and_keeps_its_time(tmp_path):
    env = progress_env(tmp_path)
    (env.bodies / "7.md").write_text("# 課題\n\n## 進行\n\nモード: —\n\n- [x] 配布 — 2026-01-01 00:00\n", encoding="utf-8")
    assert env.run(str(RECORD), "7", "実装").returncode == 0
    assert "- [x] デプロイ — 2026-01-01 00:00" in env.body(7) and "配布" not in env.body(7)


def test_old_stage_of_the_passed_record_reads_as_the_new_name(tmp_path):
    env = progress_env(tmp_path)
    assert env.run(str(SYNC), "7", "stage", "配布").returncode == 0
    state = env.state_file(7)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["stages"] = ["配布"]
    state.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    lib = SCRIPTS.parent / "skills/development-workflow/scripts/lib/workflow-common.sh"
    out = env.sh(f". {lib}; _wf_recorded '{json.dumps(raw, ensure_ascii=False)}'")
    assert out.stdout.split() == ["デプロイ"]


@pytest.fixture()
def board_with(repo):  # noqa: F811
    """工程のフィールドの選択肢を差し替えたボードの宣言を置き、書いた選択肢を確かめられるようにする。"""

    def make(options):
        gh = repo.bin / "gh"
        text = gh.read_text(encoding="utf-8")
        gh.write_text(text.replace('{"id": "O_design", "name": "\\u8a2d\\u8a08"}', json.dumps(options)[1:-1]), encoding="utf-8")
        declare(repo)
        return repo

    return make


def test_board_without_the_new_option_writes_the_old_one_and_tells_the_rename(board_with):
    board = board_with([{"id": "O_old", "name": "配布"}])
    out = run(board, SYNC, "42", "stage", "デプロイ")
    assert out.returncode == 0, out.stderr
    assert "--single-select-option-id O_old" in board.calls.read_text(encoding="utf-8")
    assert "デプロイ" in out.stderr and "NOTE:" in out.stderr


def test_board_with_the_new_option_writes_the_new_one_without_a_note(board_with):
    board = board_with([{"id": "O_old", "name": "配布"}, {"id": "O_new", "name": "デプロイ"}])
    out = run(board, SYNC, "42", "stage", "デプロイ")
    assert "--single-select-option-id O_new" in board.calls.read_text(encoding="utf-8")
    assert "選択肢" not in out.stderr


def test_board_without_either_option_still_reports_the_missing_option(board_with):
    board = board_with([{"id": "O_other", "name": "設計"}])
    out = run(board, SYNC, "42", "stage", "デプロイ")
    assert "選択肢「デプロイ」が無い" in out.stdout + out.stderr


@pytest.mark.parametrize("old", ["配布（承認ゲート 2）", "リリース（承認ゲート 2）"])
def test_old_phase_of_the_release_gate_reads_as_the_current_one(old):
    assert legacy_values.to_current("phase", old) == "リリース（リリース承認ゲート）"


# ---------------- 本番チャネルへのマージのフェーズ名（#2748）----------------

MERGE_PHASE = "リリース（本番チャネルへのマージ）"
OLD_MERGE_PHASES = ["リリース（昇格）", "配布（昇格）"]


@pytest.mark.parametrize("old", OLD_MERGE_PHASES)
def test_old_phase_of_the_merge_to_the_production_channel_reads_as_the_new_one(old):
    assert legacy_values.to_current("phase", old) == MERGE_PHASE
    assert legacy_values.to_current("phase", MERGE_PHASE) == MERGE_PHASE


def test_the_plan_of_the_merge_writes_the_new_phase():
    from supervise_lib import delivery_templates

    assert delivery_templates.PROMOTE_PHASE == MERGE_PHASE


def test_queue_skips_the_pull_request_of_an_old_merge_plan_like_the_new_one(tmp_path):
    items = []
    for i, phase in enumerate([*OLD_MERGE_PHASES, MERGE_PHASE, "実装"]):
        report = tmp_path / f"r{i}.md"
        report.write_text(f"- フェーズ: {phase}\n- Pull Request: #{i + 1}\n", encoding="utf-8")
        items.append({"result": "完了", "report": str(report)})
    assert queue.queue_prs(items) == ["4"]


def test_history_of_the_old_merge_phase_counts_for_the_new_phase(tmp_path):
    hist = tmp_path / "h.jsonl"
    rows = [{"phase": old, "step": "s", "seconds": i + 1} for i, old in enumerate(OLD_MERGE_PHASES)]
    rows.append({"phase": MERGE_PHASE, "step": "s", "seconds": 3})
    hist.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert slow_step.read_history(hist, MERGE_PHASE, "s") == [1.0, 2.0, 3.0]
