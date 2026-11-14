"""耐久の実行（runner と副命令 plan）。子プロセスで `ndf plan` を打ち、鍵ごとの記録と回復を確かめる（B1・B2・B4・B5・B7・B8）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import result
from ndf.core import durable

SRC = Path(__file__).resolve().parents[3] / "src"
SECRET = "plan-s3cr3t-value-42"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def _ndf(repo: Path, *args: str) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "NDF_TEST_TOKEN": SECRET}
    env.pop("NDF_PROGRESS_DB", None)
    done = subprocess.run(
        [sys.executable, "-m", "ndf", "plan", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=180, check=False
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert lines, done.stderr
    return done.returncode, json.loads(lines[-1])


def _count_step(sid: str, counter: Path, **kw) -> dict:
    code = f"import pathlib; p = pathlib.Path({str(counter)!r}); p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')"
    return {"id": sid, "run": [sys.executable, "-c", code], **kw}


def _write_plan(repo: Path, name: str, steps: list[dict]) -> str:
    path = repo / "plans" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"name": name, "repo": str(repo), "steps": steps}), encoding="utf-8")
    return f"plans/{name}.json"


def _dbos(repo: Path) -> Path:
    return repo / ".git" / identity.load().name / "state" / durable.DBOS_DIR


def _count(path: Path) -> int:
    return int(path.read_text()) if path.exists() else 0


def test_each_plan_round_has_its_own_key_and_is_retired(repo, tmp_path):
    """実行の回ごとに 1 つの鍵で流し、終わった鍵のファイルは retired/ へ移る（B1・B7）。"""
    a = _write_plan(repo, "a", [_count_step("s", tmp_path / "a")])
    b = _write_plan(repo, "b", [_count_step("s", tmp_path / "b")])
    assert _ndf(repo, "next", a)[0] == 0
    assert _ndf(repo, "next", b)[0] == 0
    retired = sorted(p.name for p in (_dbos(repo) / durable.RETIRED_DIR).glob(f"*{durable.DB_SUFFIX}"))
    assert len(retired) == 2
    assert list(_dbos(repo).glob(f"*{durable.DB_SUFFIX}")) == []


def test_a_finished_key_returns_its_result_without_running(repo, tmp_path):
    """終わった鍵で打ち直すと、retired/ の回の結果を返して流さない（B4）。打ち続けても記録は増えない（B7）。"""
    plan = _write_plan(repo, "p", [_count_step("s", tmp_path / "n")])
    first = _ndf(repo, "next", plan)
    second = _ndf(repo, "next", plan)
    assert first == second
    assert _count(tmp_path / "n") == 1
    assert len(list((_dbos(repo) / durable.RETIRED_DIR).glob(f"*{durable.DB_SUFFIX}"))) == 1


def test_a_fixed_head_runs_a_new_round(repo, tmp_path):
    """差し戻しの後に head を直して同じ引数で打ち直すと、前回の報告を返さずに流す（#3176）。"""
    plan = _write_plan(repo, "p", [_count_step("s", tmp_path / "n")])
    _ndf(repo, "next", plan)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix")
    code, out = _ndf(repo, "next", plan)
    assert code == 0
    assert out["metrics"]["round"] == 1
    assert _count(tmp_path / "n") == 2


def test_a_gate_survives_the_exit_and_the_next_call_resumes(repo, tmp_path):
    """承認の答えを待つ間にプロセスが抜けても、次の next が同じ鍵で回復して続ける（B2）。待ちは pending に出る（B5）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "after", "rejected": "stop"}}
    plan = _write_plan(
        repo,
        "g",
        [_count_step("before", tmp_path / "before"), ask, _count_step("skip", tmp_path / "skip"), _count_step("after", tmp_path / "after")],
    )

    code, out = _ndf(repo, "next", plan)
    assert code == result.code_for({"status": "gate"})
    assert out["status"] == "gate"
    assert "--answer approved" in out["routes"]["approved"]
    pending = _ndf(repo, "pending")[1]
    assert pending["metrics"]["pending"] == 1

    code, out = _ndf(repo, "next", plan, "--answer", "approved")
    assert (code, out["status"]) == (0, "ok")
    assert (_count(tmp_path / "before"), _count(tmp_path / "skip"), _count(tmp_path / "after")) == (1, 0, 1)
    assert _ndf(repo, "pending")[1]["metrics"]["pending"] == 0


def test_a_stopped_plan_returns_the_kind(repo):
    plan = _write_plan(repo, "s", [{"id": "s", "run": [sys.executable, "-c", "raise SystemExit(1)"], "exit_cause": {"1": "check_failed"}}])
    code, out = _ndf(repo, "next", plan)
    assert out["cause"]["kind"] == "check_failed"
    assert code == result.code_for(out)


def test_secrets_stay_out_of_the_records(repo):
    """秘密の環境変数の値は耐久の記録と結果の控えに入らない（B8）。"""
    code_text = "import json, os; print(json.dumps({'status': 'ok', 'summary': os.environ['NDF_TEST_TOKEN']}))"
    plan = _write_plan(repo, "x", [{"id": "s", "run": [sys.executable, "-c", code_text]}])
    assert _ndf(repo, "next", plan)[0] == 0
    stored = [p for p in (repo / ".git").rglob("*") if p.is_file()] + [p for p in (repo / "plans").rglob("*") if p.is_file()]
    assert stored
    assert not [p for p in stored if SECRET.encode() in p.read_bytes()]


def test_wait_reports_the_gate_with_its_routes(repo, tmp_path):
    """wait は DBOS を開かずに、承認ゲートで待つ回を routes と続きの待ちの next つきで返す（SF7）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "end"}}
    plan = _write_plan(repo, "w", [ask])
    _ndf(repo, "next", plan)
    code, out = _ndf(repo, "wait", plan, "--timeout", "5")
    assert out["status"] == "gate"
    assert out["next"].startswith("ndf plan wait")
    assert "--answer approved" in out["routes"]["approved"]
