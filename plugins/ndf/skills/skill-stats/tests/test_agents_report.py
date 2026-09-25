"""3 層の context window の集計（#550 の AC21・AC27〜AC29・AC32・AC35〜AC37）。

`skill-stats --agents` が出す 4 つの表を、`docs/specifications/ndf-context-window-metrics.md` の
「`skill-stats --agents`」の形で固定する。記録は `scripts/tests/fixtures/transcript_agents/`
の最小の記録で、そこには 2 つの supervisor が同じフェーズ（実装）を通した例が入っている。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "skill-stats.py"
PLUGIN_ROOT = SKILL_DIR.parents[1]
FIXTURES = PLUGIN_ROOT / "scripts" / "tests" / "fixtures" / "transcript_agents"

LEGACY_HEADER = "| skill | triggers源 | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--plugin-root", str(PLUGIN_ROOT), *args],
        env={"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": str(FIXTURES)},
        capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def report() -> dict:
    p = run("--agents", "--session", "sess-a", "--format", "json")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def both_sessions() -> dict:
    p = run("--agents", "--session", "sess-a", "--session", "sess-b", "--format", "json")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def group(rows: list[dict], layer: str, role: str, model: str = "claude-opus-5") -> dict:
    found = [
        r for r in rows
        if r["layer"] == layer and r["role"] == role and r["model"] == model
    ]
    assert len(found) == 1, (layer, role, model, rows)
    return found[0]


# ---------- AC21: conductor の行も同じ 11 項目で出る ----------

def test_the_conductor_row_has_the_same_columns(report) -> None:
    conductor = [r for r in report["agents"] if r["layer"] == "conductor"]
    assert len(conductor) == 1
    assert conductor[0]["depth"] == 0 and conductor[0]["role"] == "-"
    assert set(report["agents"][0]) == {
        "layer", "role", "depth", "model", "fixed", "peak", "work",
        "responses", "duration_seconds", "ending", "interruptions",
    }


def test_the_record_rows_never_carry_the_agent_id(report) -> None:
    # 識別子は `transcript_agents.py list` にだけ出る（投稿する表に載せないため）
    for row in report["agents"]:
        assert "agent_id" not in row


# ---------- AC27 / AC35: 複数のセッションを渡せる。判定は記録ごとの比 ----------

def test_two_sessions_are_read_into_one_report(both_sessions) -> None:
    assert both_sessions["totals"]["records"] == 12 + 3


def test_the_verdict_uses_the_ratio_of_each_record(both_sessions) -> None:
    # 設計の 2 件は固定費の水準が違う。中央値どうしを比べず、記録ごとに数える
    design = group(both_sessions["agent_summary"], "supervisor", "設計")
    assert design["records"] == 2 and design["work_below_fixed"] == 1


# ---------- AC28: 応答が 3 に満たない記録は束ねの表からだけ外れる ----------

def test_a_short_record_is_excluded_from_the_summary_only(report) -> None:
    assert report["excluded"] == 1
    assert not [r for r in report["agent_summary"] if r["role"] == "集計"], \
        "応答が 2 件の記録は束ねの表に出ない"
    worker = next(r for r in report["layer_totals"] if r["layer"] == "worker")
    assert worker["records"] == 6, "層ごとの合計には短命な記録も含める"


# ---------- AC29: 束ねの表と 2 つの印 ----------

def test_the_summary_holds_the_contract_columns(report) -> None:
    assert set(report["agent_summary"][0]) == {
        "layer", "role", "model", "records", "fixed_median", "work_median",
        "work_below_fixed", "peak_max", "mark",
    }


def test_the_merge_mark_is_put_only_on_supervisor_rows(report) -> None:
    # 検査の supervisor は実作業が固定費を下回る 1 件だけである
    assert group(report["agent_summary"], "supervisor", "検査")["mark"] == "束ねる候補"
    # worker も実作業が固定費を下回るが、印は付かない（決定 16）
    fix = group(report["agent_summary"], "worker", "修正")
    assert fix["work_below_fixed"] == 1 and fix["mark"] == ""


def test_the_design_post_never_gets_the_merge_mark(report) -> None:
    design = group(report["agent_summary"], "supervisor", "設計")
    assert design["work_below_fixed"] == 1 and design["mark"] == ""


def test_the_split_mark_follows_the_window_limit() -> None:
    p = run("--agents", "--session", "sess-a", "--window-limit", "50000",
            "--format", "json")
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)["agent_summary"]
    assert "割る候補" in group(rows, "supervisor", "実装")["mark"]
    assert "割る候補" not in group(rows, "worker", "修正")["mark"]


def test_the_default_window_limit_matches_the_context_window_guideline(report) -> None:
    assert report["meta"]["window_limit"] == 200000


# ---------- AC36: 層ごとの合計と総消費 ----------

def test_the_layer_totals_add_up_to_the_grand_total(report) -> None:
    rows = {r["layer"]: r for r in report["layer_totals"]}
    assert rows["conductor"] == {
        "layer": "conductor", "records": 1, "fixed_sum": 30000,
        "work_sum": 30000, "total_spend": 60000,
    }
    assert rows["supervisor"]["fixed_sum"] == 250000
    assert rows["worker"]["fixed_sum"] == 62000
    assert report["totals"] == {
        "records": 12, "fixed_sum": 342000, "work_sum": 280000,
        "total_spend": 622000,
    }


def test_layer_narrows_every_agents_table_to_supervisors() -> None:
    p = run(
        "--agents", "--session", "sess-a", "--layer", "supervisor",
        "--format", "json",
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)

    assert {r["layer"] for r in out["agents"]} == {"supervisor"}
    assert {r["layer"] for r in out["agent_summary"]} == {"supervisor"}
    assert out["layer_totals"] == [{
        "layer": "supervisor", "records": 5, "fixed_sum": 250000,
        "work_sum": 195000, "total_spend": 445000,
    }]
    assert out["totals"] == {
        "records": 5, "fixed_sum": 250000, "work_sum": 195000,
        "total_spend": 445000,
    }
    assert out["role_usage"] == []


# ---------- AC37: フェーズごとの worker の使い方 ----------

def test_the_role_usage_splits_by_the_supervisor_that_launched(report) -> None:
    rows = report["role_usage"]
    assert set(rows[0]) == {
        "role", "supervisor", "supervisor_work", "workers", "fixed_sum", "mark",
    }
    impl = [r for r in rows if r["role"] == "実装"]
    assert [r["supervisor"] for r in impl] == [1, 2], "同じフェーズは起動の早い順に連番"
    assert [r["workers"] for r in impl] == [1, 1]


def test_the_overuse_mark_compares_the_fixed_sum_with_the_work(report) -> None:
    rows = {(r["role"], r["supervisor"]): r for r in report["role_usage"]}
    design = rows[("設計", 1)]
    assert design["workers"] == 2 and design["fixed_sum"] == 80000
    assert design["supervisor_work"] == 30000 and design["mark"] == "worker を使いすぎ"
    # 実作業が固定費の合計を上回る supervisor には印が付かない
    assert rows[("実装", 2)]["mark"] == ""


def test_the_role_usage_carries_no_identifier(report) -> None:
    assert "agent_id" not in json.dumps(report["role_usage"], ensure_ascii=False)


# ---------- AC30: 出力に本文・パス・description の後ろ半分を含めない ----------

def test_the_agents_output_carries_no_text_and_no_path() -> None:
    p = run("--agents", "--session", "sess-a")
    assert p.returncode == 0, p.stderr
    for forbidden in ("#550 #657", "レビュー指摘の反映", "/work/sample", "agent_id"):
        assert forbidden not in p.stdout, forbidden


def test_the_markdown_holds_the_four_tables() -> None:
    p = run("--agents", "--session", "sess-a")
    assert p.returncode == 0, p.stderr
    for heading in (
        "| 層 | フェーズ | 深さ | モデル |",
        "| 層 | フェーズ | モデル | 件数 |",
        "| 層 | 件数 | 固定費の合計 | 実作業の合計 | 総消費 |",
        "| フェーズ | supervisor | supervisor の実作業 | worker の件数 |",
    ):
        assert heading in p.stdout, heading
    assert "束ねの表から外した記録: 1 件" in p.stdout
    assert "フェーズが読めなかった supervisor: 1 件" in p.stdout


def test_the_json_counts_the_supervisors_without_a_phase(report) -> None:
    assert report["unphased_supervisors"] == 1


def test_the_record_table_appears_only_with_a_session() -> None:
    p = run("--agents")
    assert p.returncode == 0, p.stderr
    assert "| 層 | フェーズ | 深さ | モデル |" not in p.stdout
    assert "| フェーズ | supervisor |" not in p.stdout


# ---------- AC32: 既定の引数での振る舞いが変わらない ----------

def test_without_agents_the_output_is_the_skill_table_only() -> None:
    p = run()
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines()[0] == LEGACY_HEADER
    assert "固定費" not in p.stdout


def test_without_agents_the_json_keys_are_unchanged() -> None:
    p = run("--format", "json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert set(out) == {"meta", "total", "grand_skills", "projects"}


def test_a_session_narrows_the_skill_statistics_to_the_conductor() -> None:
    # `--agents` を付けない `--session` は、conductor の記録だけで Skill を数える（AC6）
    p = run("--session", "sess-a", "--format", "json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["meta"]["transcripts"] == 1
