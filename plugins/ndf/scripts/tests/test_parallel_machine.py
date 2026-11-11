"""マシンの数え方（#2999）: 同じマシンの並列の計画のプランの本数の警告・起動の待ち・起動の排他・上限の出所。

2 つのリポジトリの計画は一時ディレクトリに作り、計画の所在の一覧は一時の状態の根（conftest の `XDG_STATE_HOME`）へ書く。
別の計画が動いているかは、計画の置き場の `run.done` に状態を書いて決める（`parallel_lane.settled_status` がそれを読む）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import locks  # noqa: E402
import parallel_homes  # noqa: E402
import parallel_machine as pm  # noqa: E402
import parallel_plan as pp  # noqa: E402
import relay_limit  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env_cap(monkeypatch):
    monkeypatch.delenv("NDF_PARALLEL_MAX_PLANS", raising=False)
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)


def _git(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".ndf").mkdir(exist_ok=True)
    return d.resolve()


def _draft(name: str, plans: list[int], prefix: str) -> dict:
    lanes = [
        {"name": f"{prefix}{i}", "input": {"kind": "sprint", "issues": [100 + i]}, "plans": n, "touches": [f"d{i}/"]}
        for i, n in enumerate(plans)
    ]
    return {"version": 1, "name": name, "rules": [], "lanes": lanes}


def _plan(home: Path, name: str, plans: list[int], prefix: str, state: str | None = "PENDING") -> Path:
    """`home` に計画を置き、所在の一覧へ書く。`state` を `run.done` に書く（None なら書かない＝記録の無い計画）。"""
    d = pp.plan_dir(home, name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(_draft(name, plans, prefix)))
    if state:
        (d / "run.done").write_text(state)
    parallel_homes.record_home(home)
    return d


def _cli(cwd: Path, *args: str, stdin: str | None = None):
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), *args], cwd=cwd, input=stdin, capture_output=True, text=True)
    return got.returncode, json.loads(got.stdout.strip().splitlines()[-1])


def _machine(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("rule") == pm.RULE]


@pytest.fixture
def repos(tmp_path):
    """x に動いている計画 c1（4 本）、y に下書きの計画 p1（4 本）を置く。"""
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    _plan(x, "c1", [2, 2], "cx")
    assert _cli(y, "init", "p1", stdin=json.dumps(_draft("p1", [2, 2], "py")))[0] == 0
    return x, y


# ---- 検査と開始（受け入れ条件 1〜3・9、I5・I6・I13） ----


def test_check_warns_about_the_machine_total_and_keeps_the_exit_code(repos):
    x, y = repos
    code, res = _cli(y, "check", "p1")
    assert code == 0, res
    got = _machine(res["items"])
    assert len(got) == 1 and got[0]["level"] == "warning"
    w = got[0]
    assert (w["machine_plans"], w["own_plans"], w["max_plans"]) == (8, 4, 6)
    assert w["plans"] == [{"plan": f"{x}:c1", "plans": 4}]
    assert "8" in w["message"] and "6" in w["message"] and f"{x}:c1" in w["message"]
    assert res["metrics"]["warnings"] == sum(1 for i in res["items"] if i["level"] == "warning")


def test_no_warning_when_the_total_fits_or_the_other_plan_has_finished(repos):
    x, y = repos
    (pp.plan_dir(x, "c1") / "plan.json").write_text(json.dumps(_draft("c1", [1, 1], "cx")))  # 2 + 4 = 6 は上限ちょうど
    assert _machine(_cli(y, "check", "p1")[1]["items"]) == []
    (pp.plan_dir(x, "c1") / "plan.json").write_text(json.dumps(_draft("c1", [2, 2], "cx")))
    (pp.plan_dir(x, "c1") / "run.done").write_text("SUCCESS")  # 終わった計画は数えない
    assert _machine(_cli(y, "check", "p1")[1]["items"]) == []


def test_a_single_plan_over_the_cap_is_still_refused_with_i2(tmp_path):
    y = _git(tmp_path / "y")
    code, res = _cli(y, "init", "p1", stdin=json.dumps(_draft("p1", [4, 3], "py")))
    assert code == 1 and any(i["rule"] == "I2" and i["level"] == "error" for i in res["items"])


def test_start_carries_the_same_warning_and_starts(repos, monkeypatch):
    import parallel as cli
    import parallel_cli as pcli
    import parallel_lane as pl
    import parallel_run as pr_run

    _, y = repos
    out, spawned = {}, []
    monkeypatch.setattr(pr_run, "mark_organizer", lambda m, n, clear=False: True)
    monkeypatch.setattr(pr_run, "environment_report", lambda m, p: ({}, []))
    monkeypatch.setattr(pl, "live_run_pid", lambda m, n: None)
    monkeypatch.setattr(pl, "run_status", lambda m, n: "PENDING")
    monkeypatch.setattr(pcli.subprocess, "Popen", lambda argv, **kw: spawned.append(argv) or type("P", (), {"pid": 4321})())

    class Done(Exception):
        pass

    def reply(status, summary, items=None, code=None, **metrics):
        out.update(status=status, items=items or [])
        raise Done

    with pytest.raises(Done):
        pcli.start_plan(type("A", (), {"name": "p1"})(), reply, y, cli.checked_plan, SCRIPTS / "parallel.py")
    assert out["status"] == "ok" and spawned
    got = _machine(out["items"])
    assert len(got) == 1 and got[0]["machine_plans"] == 8


def test_unreadable_plans_are_counted_or_listed(tmp_path):
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    _plan(x, "c1", [3], "cx")
    _plan(x, "c2", [1], "cz")
    (pp.plan_dir(x, "c2") / "plan.json").write_text("{broken")

    def status(h, n):
        if n == "c1":
            raise RuntimeError("記録を開けない")
        return "PENDING"

    got = pm.declared(y, "p1", status)
    assert got["plans"] == [{"plan": f"{x}:c1", "plans": 3, "state": "unreadable"}]  # 状態を読めない計画は動いているものとして数える
    assert got["unreadable"] == [f"{x}:c2"]  # 宣言を読めない計画は 0 本として置き場を載せる
    w = pm.plan_warnings(y, "p1", 4, status)[0]
    assert w["machine_plans"] == 7 and w["unreadable"] == [f"{x}:c2"] and "状態を読めない" in w["message"]


# ---- 上限の出所（受け入れ条件 8、I1〜I3） ----


def test_the_cap_reads_the_machine_file_then_env_then_default(repos, monkeypatch):
    x, y = repos
    assert relay_limit.plans_cap()[:2] == (6, "default")
    monkeypatch.setenv("NDF_PARALLEL_MAX_PLANS", "5")
    assert relay_limit.plans_cap()[:2] == (5, "env")
    relay_limit.write_limit("max_plans", 9)
    assert relay_limit.plans_cap()[:2] == (9, "file") and pp.max_plans()[0] == 9
    assert _cli(x, "check", "c1")[1]["metrics"]["max_plans"] == 9
    assert _cli(y, "check", "p1")[1]["metrics"]["max_plans"] == 9


def test_writing_one_cap_keeps_the_other(monkeypatch):
    relay_limit.write_limit("max_plans", 7)
    relay_limit.write_limit("max_named", 4)
    assert relay_limit.plans_cap()[:2] == (7, "file") and relay_limit.named_cap()[:2] == (4, "file")
    relay_limit.write_limit("max_plans", 8)
    assert relay_limit.named_cap()[:2] == (4, "file")
    assert relay_limit.plans_cap()[2] is None  # 片方のキーだけのファイルでも注記を出さない


def test_relay_cap_plans_cli_writes_validates_and_reports_both():
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "relay.py"), "cap", *a], capture_output=True, text=True)  # noqa: E731
    assert run("--named", "4").returncode == 0
    got = run("--plans", "7")
    assert got.returncode == 0, got.stderr
    m = json.loads(got.stdout)["metrics"]
    assert (m["max_plans"], m["plans_source"], m["max_named"], m["source"]) == (7, "file", 4, "file")
    before = relay_limit.limits_path().read_text()
    for bad in ("0", "x", "-1"):
        assert run("--plans", bad).returncode == 1
    assert relay_limit.limits_path().read_text() == before


# ---- 起動の判定（受け入れ条件 4〜7、I7〜I11・I16） ----


@pytest.fixture
def busy(tmp_path):
    """x に動いている計画 c1（cx0: 3 本・cx1: 2 本）、y に自分の計画 p1（py0: 2 本）を置く。"""
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    _plan(x, "c1", [3, 2], "cx")
    _plan(y, "p1", [2], "py", state=None)
    return x, y


def _spawner(started: list[str]):
    return lambda n: started.append(n) or {"started": "t"}


def test_a_lane_waits_while_the_machine_is_full_and_starts_after(busy):
    _, y = busy
    rows = {"cx0": {"state": "running"}, "cx1": {"state": "awaiting_approval"}}
    started: list[str] = []
    got = pm.admit(y, "p1", [("py0", 2)], _spawner(started), ls=lambda: rows)
    w = got["py0"]["waiting"]
    assert started == [] and (w["reason"], w["machine_plans"], w["plans"], w["max_plans"]) == ("machine-plans", 5, 2, 6)
    rows["cx1"] = {"state": "stopped"}  # 別のリポジトリのレーンが止まった（合計 3）
    assert pm.admit(y, "p1", [("py0", 2)], _spawner(started), ls=lambda: rows) == {"py0": {"started": "t"}}
    assert started == ["py0"]


def test_an_empty_machine_starts_a_lane_over_the_cap(busy):
    _, y = busy
    started: list[str] = []
    assert pm.admit(y, "p1", [("py0", 9)], _spawner(started), ls=lambda: {}) == {"py0": {"started": "t"}}


def test_lanes_started_in_the_same_judgement_are_counted(busy):
    _, y = busy
    started: list[str] = []
    got = pm.admit(y, "p1", [("a", 4), ("b", 3)], _spawner(started), ls=lambda: {})
    assert started == ["a"] and got["b"]["waiting"]["machine_plans"] == 4


def test_a_held_lane_stopped_for_an_answer_is_still_counted(busy, monkeypatch):
    import parallel_ledger as plg

    x, y = busy
    rows = {"cx0": {"state": "running"}, "cx1": {"state": "stopped"}}
    asking = {"v": [{"id": "q1", "lane": "cx1"}]}
    monkeypatch.setattr(plg, "pending_questions", lambda h, n: asking["v"] if n == "c1" else [])
    got = pm.admit(y, "p1", [("py0", 2)], _spawner([]), ls=lambda: rows)
    assert got["py0"]["waiting"]["machine_plans"] == 5  # 保留で止まったレーンは数える
    asking["v"] = []  # 問いに答えた
    assert pm.admit(y, "p1", [("py0", 2)], _spawner([]), ls=lambda: rows) == {"py0": {"started": "t"}}


def test_a_held_admit_lock_makes_the_lanes_wait(busy):
    _, y = busy
    started: list[str] = []
    with locks.exclusive(pm.lock_target(), timeout=1):
        got = pm.admit(y, "p1", [("py0", 1)], _spawner(started), ls=lambda: {}, timeout=0.1)
    assert started == [] and got["py0"]["waiting"]["reason"] == "lock"


def test_two_concurrent_judgements_do_not_exceed_the_cap(tmp_path):
    """2 つの計画が同時に判定しても、起動の後の合計が上限 6 を超えない（受け入れ条件 6・I10）。"""
    x, y = _git(tmp_path / "x"), _git(tmp_path / "y")
    _plan(x, "c1", [4], "cx")
    _plan(y, "p1", [4], "py")
    rows: dict[str, dict] = {}
    guard = threading.Lock()

    def spawn(n):
        time.sleep(0.3)  # 起動の確かめの待ち。排他が無ければ、この間にもう一方が数えて起動する
        with guard:
            rows[n] = {"state": "running"}
        return {"started": "t"}

    out: dict[str, dict] = {}
    ts = [
        threading.Thread(target=lambda h=h, p=p, ln=ln: out.update(pm.admit(h, p, [(ln, 4)], spawn, ls=lambda: dict(rows), timeout=5)))
        for h, p, ln in ((x, "c1", "cx0"), (y, "p1", "py0"))
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted("started" in v for v in out.values()) == [False, True]
    assert sum(4 for v in out.values() if "started" in v) <= 6


def test_the_waiting_text_states_the_numbers():
    w = {"reason": "machine-plans", "machine_plans": 5, "plans": 2, "max_plans": 6}
    assert pm.waiting_text(w) == "起動待ち（マシン 5 + このレーン 2 > 上限 6）"
    assert pm.waiting_text({"reason": "lock"}) == "起動待ち（起動の排他を取れない）"
    assert pm.waiting_text(None) is None
