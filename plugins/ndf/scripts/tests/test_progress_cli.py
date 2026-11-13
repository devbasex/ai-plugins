"""進行表のスクリプト（progress.py。#3092 の受け入れ条件 3・5・10・15・I1・I5・I17・決定 19）。副命令の入出力と終了コード。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
CLI = SCRIPTS / "progress.py"


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return {**os.environ, "NDF_PROGRESS_DB": f"sqlite:///{tmp_path}/p.sqlite"}, repo


def run(env_repo, *args):
    env, repo = env_repo
    p = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env, cwd=repo)
    return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode


def test_new_show_and_I1(env):
    rep, code = run(env, "new", "s1", "--mode", "standard", "--issues", "1", "2", "--disable", "確定仕様化=文書だけ")
    assert code == 0, rep
    tid = rep["metrics"]["table"]
    assert {"stage": "確定仕様化", "reason": "文書だけ", "source": "sprint"} in rep["items"]
    show, code = run(env, "show", tid)
    assert code == 0
    stages = [r["stage"] for r in show["items"]]
    assert stages[-3:] == ["振り返り", "組み換え", "棚卸し"]
    assert next(r for r in show["items"] if r["stage"] == "確定仕様化")["state"] == "disabled"
    rep, code = run(env, "new", "s1", "--mode", "light", "--issues", "1")
    assert code == 3 and rep["metrics"]["current"] == tid


def test_ci_declaration_disables_refactor_by_definition(env):
    rep, _ = run(env, "new", "s2", "--mode", "standard", "--issues", "1")
    assert any(i["stage"] == "構造改善" and i["reason"] == "ci_declared" for i in rep["items"])
    rep, _ = run(env, "new", "s3", "--mode", "standard", "--issues", "1", "--fact", "ci_declared=true")
    assert not any(i["stage"] == "構造改善" for i in rep["items"])


def test_change_operations(env, tmp_path):
    rep, _ = run(env, "new", "s4", "--mode", "light", "--issues", "1")
    tid = rep["metrics"]["table"]
    assert run(env, "disable", tid, "棚卸し", "--reason", "要らない")[1] == 0
    assert run(env, "enable", tid, "棚卸し", "--reason", "やはり要る")[1] == 0
    assert run(env, "retry", tid, "棚卸し", "--reason", "x")[1] == 3  # 失敗でない行
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"activities": {}}))
    rep, code = run(env, "finish", tid, "棚卸し", "--output", str(bad), "--reason", "手で")
    assert code == 3 and "I17" in rep["summary"]
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"activities": {}, "summary": "手で済ませた"}))
    assert run(env, "finish", tid, "棚卸し", "--output", str(good), "--reason", "手で")[1] == 0
    show, _ = run(env, "show", tid)
    assert next(r for r in show["items"] if r["stage"] == "棚卸し")["state"] == "finished"
    kinds = [h["kind"] for h in show["metrics"]["history"] if h["row"] == "棚卸し"]
    assert kinds == ["created", "disabled", "enabled", "manual_finished"]
    assert all(h["reason"] for h in show["metrics"]["history"] if h["kind"] in ("disabled", "enabled", "manual_finished"))


def test_scope_raises_rev_and_refuses_lowering(env):
    rep, _ = run(env, "new", "s5", "--mode", "operation", "--issues", "1", "2")
    tid = rep["metrics"]["table"]
    rep, code = run(env, "scope", tid, "--drop", "2", "--reason", "設計承認ゲートで外した")
    assert code == 0 and rep["metrics"]["rev"] == 2
    assert run(env, "scope", tid, "--mode", "light", "--reason", "下げる")[1] == 3  # I5
    rep, code = run(env, "scope", tid, "--mode", "standard", "--reason", "上げる")
    assert code == 0 and rep["metrics"]["rev"] == 3
    show, _ = run(env, "show", tid)
    assert any(h["rev"] == 1 for h in show["metrics"]["history"])  # 前の改訂の事象が残る


def test_cancel_and_answer(env):
    rep, _ = run(env, "new", "s6", "--mode", "light", "--issues", "1")
    tid = rep["metrics"]["table"]
    rep, code = run(env, "answer", tid, "デプロイ/release", "--approved")
    assert code == 0 and rep["metrics"]["sent"] is False  # 実行の前の答えは answered だけ
    assert run(env, "cancel", tid, "--reason", "やめる")[1] == 0
    assert run(env, "cancel", tid, "--reason", "もう一度")[1] == 3
    assert run(env, "new", "s6", "--mode", "light", "--issues", "1")[1] == 3  # 中止の後の同じ名前（I1）


def test_adopt_sprint_partial_stage_fails_and_unknown_stage_writes_nothing(env, tmp_path):
    base = tmp_path / "sprint-x"
    base.mkdir()
    plans = {n: str(base / f"{n}.json") for n in ("design", "impl", "dev", "prod")}
    stages = [
        {"name": "設計", "plans": [plans["design"]]},
        {"name": "実装", "plans": [plans["impl"]]},
        {"name": "開発版", "plans": [plans["dev"]]},
        {"name": "本番", "plans": [plans["prod"]]},
    ]
    (base / "sprint.json").write_text(json.dumps({"スプリント": "x", "進め方": "normal", "ステージ": stages}, ensure_ascii=False))
    done = [{"plan": plans[k], "result": "完了"} for k in ("design", "impl", "dev")]
    (base / "done-1.json").write_text(json.dumps({"items": done}, ensure_ascii=False))
    rep, code = run(env, "adopt", "--sprint", str(base / "sprint.json"))
    assert code == 0, rep
    assert [i["stage"] for i in rep["items"]] == ["デプロイ"]  # 開発版だけ終わり本番が終わっていない（決定 19）
    show, _ = run(env, "show", rep["metrics"]["table"])
    state = {r["stage"]: r["state"] for r in show["items"]}
    assert state["設計"] == "finished" and state["実装"] == "finished" and state["デプロイ"] == "failed"
    (base / "sprint.json").write_text(json.dumps({"スプリント": "y", "ステージ": [{"name": "謎", "plans": []}]}, ensure_ascii=False))
    rep, code = run(env, "adopt", "--sprint", str(base / "sprint.json"))
    assert code == 2
    assert run(env, "new", "y", "--mode", "light", "--issues", "1")[1] == 0  # 何も書いていない


def test_lane_rows_and_carry_dropped(env):
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import progress_db as pdb
    import progress_model as pm

    store = pdb.connect(env[0]["NDF_PROGRESS_DB"])
    with pdb.transaction(store) as c:
        plan = pdb.create_table(
            c,
            "plan",
            "r",
            "p",
            None,
            None,
            [
                pm.EventDraft("created", "organizer", payload={"max_lanes": 2}),
                pm.EventDraft("lane_added", "organizer", payload={"lane": "a", "touches": []}),
                pm.EventDraft("plan_started", "organizer"),
            ],
        )
        lane = pdb.create_table(
            c,
            "lane",
            "r",
            "p/a",
            plan,
            None,
            [pm.EventDraft("created", "organizer"), pm.EventDraft("created", "organizer", "1", payload={"row": 1, "issues": [1, 2]})],
        )
    rep, code = run(env, "new", "s7", "--mode", "standard", "--issues", "1", "2", "--lane", lane, "--row", "1")
    assert code == 0
    rep, code = run(env, "scope", rep["metrics"]["table"], "--drop", "2", "--reason", "外した")
    assert code == 0
    show, _ = run(env, "show", lane)
    assert [(r["row"], r["issues"], r["state"]) for r in show["items"]] == [(1, [1, 2], "running"), (2, [2], "pending")]  # 受け入れ条件 15
    rep, code = run(env, "lane", "cancel-row", lane, "--row", "1", "--reason", "x")
    assert code == 3 and "I12a" in rep["summary"]
    assert run(env, "lane", "add-row", lane, "--issues", "9", "--reason", "足す")[1] == 0


def test_new_refuses_fast_without_pace_declaration(env):
    """pace: fast / auto は、宣言（.ndf/pace.json）と承認の記録が無ければ進行表を作らない。"""
    rep, code = run(env, "new", "fast1", "--mode", "standard", "--issues", "1", "--pace", "fast")
    assert code == 3 and rep["status"] == "stopped" and "pace.json" in rep["summary"], rep
    rep, code = run(env, "new", "fast1", "--mode", "standard", "--issues", "1")
    assert code == 0, rep  # 断ったときは何も書いていない
