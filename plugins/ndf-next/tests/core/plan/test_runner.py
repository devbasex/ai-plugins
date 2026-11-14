"""耐久の実行（runner と副命令 plan）。子プロセスで `ndf plan` を打ち、鍵ごとの記録と回復を確かめる（B1・B2・B4・B5・B7・B8）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.contracts import chosen, result, routing
from ndf.core import durable
from ndf.core.plan import assemble_decide, model, records, runner

from . import _plan_helpers as H


@pytest.fixture
def repo(tmp_path) -> Path:
    return H.make_repo(tmp_path / "repo")


def test_each_plan_round_has_its_own_key_and_is_retired(repo, tmp_path):
    """実行の回ごとに 1 つの鍵で流し、終わった鍵のファイルは retired/ へ移る（B1・B7）。"""
    a = H.write_plan(repo, "a", [H.count_step("s", tmp_path / "a")])
    b = H.write_plan(repo, "b", [H.count_step("s", tmp_path / "b")])
    assert H.ndf(repo, "next", a)[0] == H.OK
    assert H.ndf(repo, "next", b)[0] == H.OK
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
    assert code == H.OK
    assert out["metrics"]["round"] == 1
    assert H.count(tmp_path / "n") == 2


def test_a_gate_survives_the_exit_and_the_next_call_resumes(repo, tmp_path):
    """承認の答えを待つ間にプロセスが抜けても、次の next が同じ鍵で回復して続ける（B2）。待ちは pending に出る（B5）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "after", "rejected": "stop"}}
    plan = H.write_plan(
        repo,
        "g",
        [
            H.count_step("before", tmp_path / "before"),
            ask,
            H.count_step("skip", tmp_path / "skip"),
            H.count_step("after", tmp_path / "after"),
        ],
    )

    code, out = H.ndf(repo, "next", plan)
    assert code == result.code_for({"status": "gate"})
    assert out["status"] == "gate"
    assert out["routes"]["approved"]["argv"][-2:] == ["--answer", "approved"]
    pending = H.ndf(repo, "pending")[1]
    assert pending["metrics"]["pending"] == 1

    code, out = H.ndf(repo, "next", plan, "--answer", "approved")
    assert (code, out["status"]) == (H.OK, "ok")
    assert (H.count(tmp_path / "before"), H.count(tmp_path / "skip"), H.count(tmp_path / "after")) == (1, 0, 1)
    assert H.ndf(repo, "pending")[1]["metrics"]["pending"] == 0


def test_the_gate_marker_holds_the_step_seq_and_routes(repo):
    """現状固定: 承認ゲートで抜けた後の待ちの目印は、ステップ・何度目の待ちか・答えごとのコマンドを持ち、一時ファイルを残さない。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "end", "rejected": "stop"}}
    plan = H.write_plan(repo, "m", [ask])

    code, out = H.ndf(repo, "next", plan)
    assert out["status"] == "gate"
    sdir = repo / "plans" / "m.state"
    markers = sorted(sdir.glob(f"{runner.WAITING_PREFIX}*.json"))
    assert len(markers) == 1
    marker = json.loads(markers[0].read_text(encoding="utf-8"))
    assert sorted(marker) == ["routes", "seq", "step"]
    assert (marker["step"], marker["seq"]) == ("ask", 1)
    assert marker["routes"] == out["routes"]
    assert sorted(marker["routes"]) == ["approved", "rejected"]
    assert list(sdir.rglob("*.tmp")) == []


def test_a_stopped_plan_returns_the_kind(repo):
    plan = H.write_plan(repo, "s", [{"id": "s", "run": [sys.executable, "-c", "raise SystemExit(1)"], "exit_cause": {"1": "check_failed"}}])
    code, out = H.ndf(repo, "next", plan)
    assert out["cause"]["kind"] == "check_failed"
    assert code == result.code_for(out)


def test_secrets_stay_out_of_the_records(repo):
    """秘密の環境変数の値は耐久の記録と結果の控えに入らない（B8）。"""
    code_text = "import json, os; print(json.dumps({'status': 'ok', 'summary': os.environ['NDF_TEST_TOKEN']}))"
    plan = H.write_plan(repo, "x", [{"id": "s", "run": [sys.executable, "-c", code_text]}])
    assert H.ndf(repo, "next", plan)[0] == H.OK
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
    assert out["next"]["argv"][:3] == ["ndf", "plan", "wait"]
    assert out["routes"]["approved"]["argv"][-2:] == ["--answer", "approved"]


def test_the_gate_next_waits_on_the_given_state_dir(repo, tmp_path):
    """--state-dir で始めた回の承認ゲートの next は、同じ置き場を wait へ渡す。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": "x"}})
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "end"}}
    plan = H.write_plan(repo, "g", [ask])
    sdir = str(tmp_path / "elsewhere")
    _, out = H.ndf(repo, "next", plan, "--state-dir", sdir)
    argv = out["next"]["argv"]
    assert argv[argv.index("--state-dir") + 1] == sdir
    _, waited = H.ndf(repo, *argv[2:], "--timeout", "5")
    assert waited["status"] == "gate"


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


def _choices(repo: Path) -> list[dict]:
    return [r for p in sorted(repo.rglob(records.PROGRESS_FILE)) for r in records.recorded_choices(p.parent)]


def test_each_chosen_route_is_recorded_once(repo, tmp_path):
    """行き先を選ぶたびに選択の記録を 1 件書き、終わった回を打ち直しても書き足さない（#3395 の決定 114）。"""
    gate_line = json.dumps({"tool": "t", "status": "gate", "summary": "ask", "routes": {"approved": None}})
    fail = {
        "id": "a",
        "run": [sys.executable, "-c", "raise SystemExit(1)"],
        "exit_cause": {"1": "test_failed"},
        "on_cause": {"test_failed": "ask"},
    }
    ask = {"id": "ask", "run": [sys.executable, "-c", f"print({gate_line!r})"], "on_answer": {"approved": "end"}}
    plan = H.write_plan(repo, "r", [fail, ask])
    assert H.ndf(repo, "next", plan)[1]["status"] == "gate"
    code, out = H.ndf(repo, "next", plan, "--answer", "approved")
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert H.ndf(repo, "next", plan)[1] == out
    rows = _choices(repo)
    assert [chosen.errors(r) for r in rows] == [[], []]
    assert [(r["rule"], r["chooser"], r["from"], r["to"], r.get("cause")) for r in rows] == [
        ("R03", "script", "a", "ask", "test_failed"),
        ("R13", "human", "ask", "end", None),
    ]
    assert {r["run_id"] for r in rows} == {out["run_id"]}


def test_step_outputs_reach_the_plan_result(repo):
    """ステップの結果 JSON の outputs を重ね（後のステップが勝つ）、プランの結果の outputs に載せる。"""

    def emit(outputs: dict) -> str:
        return f"print({json.dumps({'tool': 't', 'status': 'ok', 'summary': 's', 'outputs': outputs})!r})"

    plan = H.write_plan(repo, "o", [H.py_step("a", emit({"pr": 1, "mode": "standard"})), H.py_step("b", emit({"pr": 2}))])
    code, out = H.ndf(repo, "next", plan)
    assert (code, out["outputs"]) == (H.OK, {"pr": 2, "mode": "standard"})


def test_outputs_out_of_the_contract_become_a_warning(repo):
    line = json.dumps({"tool": "t", "status": "ok", "summary": "s", "outputs": {"unknown_key": 1}})
    plan = H.write_plan(repo, "w", [H.py_step("a", f"print({line!r})")])
    code, out = H.ndf(repo, "next", plan)
    assert code == H.OK and "outputs" not in out
    assert out["warnings"] and result.errors(out) == []


# --- 現状固定テスト: `runner.prepare` を同じプロセスで呼ぶ（DBOS を開かない） ---


@pytest.fixture
def in_repo(repo, monkeypatch) -> Path:
    """相対のプランのパスはプロセスの cwd で読まれる（`model.load`）ため、リポジトリへ移る。"""
    monkeypatch.chdir(repo)
    return repo


def _head_of(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _root(repo: Path) -> Path:
    return repo / ".git" / "ndf" / "state"


def test_prepare_a_fresh_plan(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    root = _root(in_repo)
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    plan = model.load(in_repo / rel)
    head = _head_of(in_repo)
    sdir = in_repo / "plans" / "p.state"
    assert inv.plan_path == in_repo / rel
    assert inv.plan == plan
    assert (inv.start, inv.prelude, inv.state_dir, inv.head) == (None, (), sdir, head)
    assert inv.digest == plan.digest()
    assert (inv.key, inv.round_no) == (assemble_decide.run_key("p", plan.digest(), None, head, 0), 0)
    assert inv.values == {
        "state_dir": str(sdir),
        "repo": str(in_repo),
        "worktree": "",
        "plan": str(in_repo / rel),
        "owner": assemble_decide.owner_of("p", str(sdir)),
        "work_base": str(root.parent / runner.WORK_DIR),
    }
    assert inv.again == (str(in_repo / rel),)


def test_prepare_with_start_state_dir_and_sets(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("a", "pass"), H.py_step("b", "pass")])
    inv = runner.prepare(_root(in_repo), Path(rel), "b", "st", in_repo, {"b": "2", "a": "1"})
    plan = model.load(in_repo / rel)
    assert inv.state_dir == in_repo / "st"
    assert inv.digest == assemble_decide.run_digest(plan.digest(), {"a": "1", "b": "2"})
    assert inv.digest != plan.digest()
    assert (inv.values["a"], inv.values["b"]) == ("1", "2")
    assert inv.again == (str(in_repo / rel), "--from", "b", "--state-dir", "st", "--set", "a=1", "--set", "b=2")
    assert inv.key == assemble_decide.run_key("p", inv.digest, "b", _head_of(in_repo), 0)


def test_prepare_uses_the_state_dir_of_the_plan(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")], state_dir="from-plan")
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    assert inv.state_dir == in_repo / "from-plan"
    assert inv.again == (str(in_repo / rel),)


def test_prepare_keeps_an_absolute_plan_path_and_a_plan_without_a_place(in_repo, tmp_path):
    path = tmp_path / "loose.json"
    path.write_text(json.dumps({"name": "loose", "steps": [H.py_step("s", "pass")]}), encoding="utf-8")
    inv = runner.prepare(_root(in_repo), path, None, None, in_repo)
    assert inv.plan_path == path
    assert inv.head is None
    assert (inv.values["repo"], inv.values["worktree"], inv.values["plan"]) == ("", "", str(path))
    assert inv.state_dir == tmp_path / "loose.state"


def test_prepare_keeps_the_resume_prelude_before_the_start(in_repo):
    ids = ["base-sync", "x", "deep-due", "y"]
    rel = H.write_plan(in_repo, "p", [H.py_step(i, "pass") for i in ids])
    assert runner.prepare(_root(in_repo), Path(rel), "y", None, in_repo).prelude == ("base-sync", "deep-due")
    assert runner.prepare(_root(in_repo), Path(rel), routing.END, None, in_repo).prelude == ()


def test_prepare_refuses_an_unknown_start(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    with pytest.raises(model.PlanError, match="^p に nope は無い$"):
        runner.prepare(_root(in_repo), Path(rel), "nope", None, in_repo)


def test_prepare_refuses_sets_that_override_fixed_values(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    with pytest.raises(model.PlanError, match="^差し込みの値 plan, repo は上書きできない$"):
        runner.prepare(_root(in_repo), Path(rel), None, None, in_repo, {"repo": "x", "plan": "y", "ok": "z"})


def _write_record(sdir: Path, **record) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / runner.RUN_FILE).write_text(json.dumps(record), encoding="utf-8")


def test_prepare_continues_the_recorded_round_that_has_not_finished(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=3, heads=["other"])
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == ("K", 3)


def test_prepare_starts_the_next_round_when_a_finished_round_had_another_head(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    root = _root(in_repo)
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=3, heads=["other"])
    retired = root / durable.DBOS_DIR / durable.RETIRED_DIR
    retired.mkdir(parents=True)
    (retired / f"{durable.run_name('K')}-1{durable.DB_SUFFIX}").touch()
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == (assemble_decide.run_key("p", digest, None, _head_of(in_repo), 4), 4)


def test_prepare_keeps_a_finished_round_with_the_same_head(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    root = _root(in_repo)
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=2, heads=[_head_of(in_repo)])
    retired = root / durable.DBOS_DIR / durable.RETIRED_DIR
    retired.mkdir(parents=True)
    (retired / f"{durable.run_name('K')}{durable.DB_SUFFIX}").touch()
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == ("K", 2)
