"""耐久の実行（runner と副命令 plan）。子プロセスで `ndf plan` を打ち、鍵ごとの記録と回復を確かめる（B1・B2・B4・B5・B7・B8）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.contracts import result
from ndf.core import durable

from . import _plan_helpers as H


@pytest.fixture
def repo(tmp_path) -> Path:
    return H.make_repo(tmp_path / "repo")


def test_each_plan_round_has_its_own_key_and_is_retired(repo, tmp_path):
    """実行の回ごとに 1 つの鍵で流し、終わった鍵のファイルは retired/ へ移る（B1・B7）。"""
    a = H.write_plan(repo, "a", [H.count_step("s", tmp_path / "a")])
    b = H.write_plan(repo, "b", [H.count_step("s", tmp_path / "b")])
    assert H.ndf(repo, "next", a)[0] == 0
    assert H.ndf(repo, "next", b)[0] == 0
    retired = sorted(p.name for p in (H.dbos_dir(repo) / durable.RETIRED_DIR).glob(f"*{durable.DB_SUFFIX}"))
    assert len(retired) == 2
    assert list(H.dbos_dir(repo).glob(f"*{durable.DB_SUFFIX}")) == []


def test_a_finished_key_returns_its_result_without_running(repo, tmp_path):
    """終わった鍵で打ち直すと、retired/ の回の結果を返して流さない（B4）。打ち続けても記録は増えない（B7）。"""
    plan = H.write_plan(repo, "p", [H.count_step("s", tmp_path / "n")])
    first = H.ndf(repo, "next", plan)
    second = H.ndf(repo, "next", plan)
    assert first == second
    assert H.count(tmp_path / "n") == 1
    assert len(list((H.dbos_dir(repo) / durable.RETIRED_DIR).glob(f"*{durable.DB_SUFFIX}"))) == 1


def test_a_fixed_head_runs_a_new_round(repo, tmp_path):
    """差し戻しの後に head を直して同じ引数で打ち直すと、前回の報告を返さずに流す（#3176）。"""
    plan = H.write_plan(repo, "p", [H.count_step("s", tmp_path / "n")])
    H.ndf(repo, "next", plan)
    H.git(repo, "commit", "-q", "--allow-empty", "-m", "fix")
    code, out = H.ndf(repo, "next", plan)
    assert code == 0
    assert out["metrics"]["round"] == 1
    assert H.count(tmp_path / "n") == 2


def test_a_gate_survives_the_exit_and_the_next_call_resumes(repo, tmp_path):
    """承認の答えを待つ間にプロセスが抜けても、次の next が同じ鍵で回復して続ける（B2）。待ちは pending に出る（B5）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "after", "rejected": "stop"}}
    plan = H.write_plan(
        repo,
        "g",
        [H.count_step("before", tmp_path / "before"), ask, H.count_step("skip", tmp_path / "skip"), H.count_step("after", tmp_path / "after")],
    )

    code, out = H.ndf(repo, "next", plan)
    assert code == result.code_for({"status": "gate"})
    assert out["status"] == "gate"
    assert "--answer approved" in out["routes"]["approved"]
    pending = H.ndf(repo, "pending")[1]
    assert pending["metrics"]["pending"] == 1

    code, out = H.ndf(repo, "next", plan, "--answer", "approved")
    assert (code, out["status"]) == (0, "ok")
    assert (H.count(tmp_path / "before"), H.count(tmp_path / "skip"), H.count(tmp_path / "after")) == (1, 0, 1)
    assert H.ndf(repo, "pending")[1]["metrics"]["pending"] == 0


def test_a_stopped_plan_returns_the_kind(repo):
    plan = H.write_plan(repo, "s", [{"id": "s", "run": [sys.executable, "-c", "raise SystemExit(1)"], "exit_cause": {"1": "check_failed"}}])
    code, out = H.ndf(repo, "next", plan)
    assert out["cause"]["kind"] == "check_failed"
    assert code == result.code_for(out)


def test_secrets_stay_out_of_the_records(repo):
    """秘密の環境変数の値は耐久の記録と結果の控えに入らない（B8）。"""
    code_text = "import json, os; print(json.dumps({'status': 'ok', 'summary': os.environ['NDF_TEST_TOKEN']}))"
    plan = H.write_plan(repo, "x", [{"id": "s", "run": [sys.executable, "-c", code_text]}])
    assert H.ndf(repo, "next", plan)[0] == 0
    stored = [p for p in (repo / ".git").rglob("*") if p.is_file()] + [p for p in (repo / "plans").rglob("*") if p.is_file()]
    assert stored
    assert not [p for p in stored if H.SECRET.encode() in p.read_bytes()]


def test_wait_reports_the_gate_with_its_routes(repo, tmp_path):
    """wait は DBOS を開かずに、承認ゲートで待つ回を routes と続きの待ちの next つきで返す（SF7）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "end"}}
    plan = H.write_plan(repo, "w", [ask])
    H.ndf(repo, "next", plan)
    code, out = H.ndf(repo, "wait", plan, "--timeout", "5")
    assert out["status"] == "gate"
    assert out["next"].startswith("ndf plan wait")
    assert "--answer approved" in out["routes"]["approved"]


def test_two_rounds_of_one_plan_at_once_do_not_stop_each_other(repo, tmp_path):
    """同じプランの別の回を同時に打っても、後の next は先の回を待ち、先の回のステップの子を止めない（plan.lock）。"""
    plan = H.write_plan(repo, "c", [H.py_step("s", "import time; time.sleep(1.5)")])
    env = {**os.environ, "PYTHONPATH": str(H.SRC)}
    env.pop("NDF_PROGRESS_DB", None)
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "ndf", "plan", "next", plan, "--set", f"round={n}"], cwd=repo, env=env, stdout=subprocess.PIPE, text=True
        )
        for n in (1, 2)
    ]
    outs = [json.loads(p.communicate(timeout=180)[0].splitlines()[-1]) for p in procs]
    assert [o["status"] for o in outs] == ["ok", "ok"], outs
