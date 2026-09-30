"""`supervise.py queue` の重なりの組・資源の枠・落ちた後の打ち直し（#1142 の C-2・C-4・C-5・C-6・I20・I21）。

プランは run のステップだけで組む。ステップは始まりと終わりの時刻か、自分の id を作業場所のファイルへ書き、
同時に流れたか・2 度流れたかをそこから数える。`queue` は `supervise.py` を子プロセスで打つ（kill -9 するため）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbos  # noqa: E402,F401  耐久の記録の外部パッケージ。無ければ集めるところで落とす
import durable  # noqa: E402
import procs  # noqa: E402
from supervise_lib import admission, decl, engine  # noqa: E402
from test_supervise_resume import note, once_slow, wait_for  # noqa: E402

PY = sys.executable
SUPERVISE = SCRIPTS / "supervise.py"
RES = decl.QUEUE_RESOURCES


# ---- 重なりの組と資源のタグ（副作用の無い関数） ----


def test_containment_is_judged_by_path_components():
    assert admission.contains("a/b", "a/b/c.py") and admission.contains("a/b/", "a/b") and admission.contains("./a", "a/x")
    assert not admission.contains("a/b", "a/bc") and not admission.contains("a/b/c.py", "a/b")
    assert admission.overlaps({"p": ["a/b"], "q": ["a/bc", "a/b.py"]}) == []
    assert admission.overlaps({"p": ["x.py", "a/b/c.py"], "q": ["a/b"]}) == [{"a": "p", "b": "q", "paths": ["a/b/c.py", "a/b"]}]


def test_plans_touching_the_same_shared_list_overlap():
    shared = [{"path": "lib/README.md", "touched_by": ["lib/"]}, {"path": "index.md", "touched_by": ["index.md"]}]
    plans = {"p": ["lib/a.py"], "q": ["lib/sub/b.py"], "r": ["docs/x.md"], "s": ["index.md"]}
    assert admission.overlaps(plans, shared) == [{"a": "p", "b": "q", "paths": ["lib/README.md", "lib/README.md"]}]
    assert admission.shared_hits(["lib"], shared) == ["lib/README.md"] and admission.shared_hits(["libx/a.py"], shared) == []


def test_groups_join_chained_overlaps_and_keep_the_order():
    plans = {"a": ["x/"], "b": ["y.py"], "c": ["x/1.py", "z/"], "d": ["z/2.py"], "e": ["w.py"]}
    grouped = admission.groups(plans)
    assert grouped == [["a", "c", "d"], ["b"], ["e"]]  # a と d は直に重ならないが、c を介して同じ組
    assert admission.others(plans, grouped) == {
        "a": ["y.py", "w.py"],
        "c": ["y.py", "w.py"],
        "d": ["y.py", "w.py"],
        "b": ["x/", "x/1.py", "z/", "z/2.py", "w.py"],
        "e": ["x/", "x/1.py", "z/", "z/2.py", "y.py"],
    }


def test_tags_come_from_the_step_type_and_the_command():
    assert admission.tags({"type": "pr"}, RES) == ["graphql"] and admission.tags({"type": "drive"}, RES) == ["graphql"]
    assert admission.tags({"type": "run", "cmd": "python3 /x/merged-steps.py merge-when-green 1"}, RES) == ["graphql"]
    assert admission.tags({"type": "run", "cmd": "pytest"}, RES) == [] and admission.tags({"type": "work", "prompt": "merged-steps.py"}, RES) == []
    two = {**RES, "api": {"limit": 1, "types": [], "commands": ["merged-steps.py", "curl"]}}
    assert admission.tags({"type": "run", "cmd": "merged-steps.py"}, two) == ["api", "graphql"]


# ---- 宣言（.ndf/supervise.json の queue） ----


def test_queue_decl_defaults_and_overrides():
    assert decl.queue_decl({}) == {"resources": {"graphql": {"limit": 2, "types": ["pr", "drive"], "commands": ["merged-steps.py"]}}, "shared": []}
    got = decl.queue_decl({"queue": {"resources": {"graphql": {"limit": 1}, "api": {"limit": 3, "commands": ["curl"]}}, "shared": [{"path": "i.md"}]}})
    assert got["resources"]["graphql"] == {"limit": 1, "types": ["pr", "drive"], "commands": ["merged-steps.py"]}
    assert got["resources"]["api"] == {"limit": 3, "types": [], "commands": ["curl"]}
    assert got["shared"] == [{"path": "i.md", "touched_by": ["i.md"]}]


@pytest.mark.parametrize(
    "queue",
    [
        {"resources": {"graphql": {"limit": 0}}},
        {"resources": {"api": {"commands": ["curl"]}}},
        {"resources": ["graphql"]},
        {"shared": [{"touched_by": ["lib/"]}]},
        {"limit": 2},
        ["graphql"],
    ],
)
def test_a_malformed_queue_decl_is_an_error(queue):
    with pytest.raises(decl.DeclError):
        decl.queue_decl({"queue": queue})


# ---- queue を流す ----


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    return os.environ.copy()


class Queue:
    """作業場所 `tmp` のプランと、`supervise.py queue` の打ち方。"""

    def __init__(self, tmp: Path, env: dict, queue_decl: dict | None = None) -> None:
        self.tmp, self.env = tmp, env
        (tmp / ".ndf").mkdir()
        (tmp / ".ndf" / "supervise.json").write_text(json.dumps({"version": 1, **({"queue": queue_decl} if queue_decl else {})}))

    def plan(self, name: str, steps: list[dict], files: list[str] | None = None) -> str:
        steps[-1].setdefault("next", "end")
        plan = {"フェーズ": "試験", "課題": [], "作業場所": str(self.tmp), "steps": steps, **({"触るファイル": files} if files else {})}
        (self.tmp / f"{name}.json").write_text(json.dumps(plan, ensure_ascii=False))
        return str(self.tmp / f"{name}.json")

    def argv(self, *args: str) -> list[str]:
        return [PY, str(SUPERVISE), "queue", *args, "--poll", "0.1", "--done", str(self.tmp / "done.json")]

    def run(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        p = subprocess.run(self.argv(*args), capture_output=True, text=True, timeout=180, env=self.env, cwd=self.tmp)
        assert p.stdout.strip(), p.stderr
        return p, json.loads(p.stdout.splitlines()[-1])

    def spawn(self, *args: str) -> subprocess.Popen:
        return subprocess.Popen(self.argv(*args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.tmp)

    def sleeping(self, plan: str, sid: str) -> bool:
        """`plan` のステップ `sid` が眠り始め、`step.pid` に子が載ったか。"""
        pidfile, step_pid = self.tmp / f"{sid}.pid", self.state(plan) / "step.pid"
        if not (pidfile.exists() and pidfile.read_text().strip() and step_pid.exists()):
            return False
        return '"children": [{' in step_pid.read_text()

    def kill_when(self, cond, *args: str) -> None:
        """queue を起動し、`cond` が成り立ったら kill -9 する。"""
        proc = self.spawn(*args)
        wait_for(cond)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)

    def kill_when_sleeping(self, plan: str, sid: str, *args: str) -> None:
        self.kill_when(lambda: self.sleeping(plan, sid), *args)

    def state(self, plan: str) -> Path:
        return Path(plan).with_name(Path(plan).stem + "-state")

    def span(self, name: str, seconds: float) -> str:
        """始まりと終わりの時刻を書く run のステップの cmd。"""
        return f"date +%s.%N > {self.tmp}/{name}.s; sleep {seconds}; date +%s.%N > {self.tmp}/{name}.e"

    def spans(self, *names: str) -> list[tuple[float, float]]:
        return [(float((self.tmp / f"{n}.s").read_text()), float((self.tmp / f"{n}.e").read_text())) for n in names]

    def visits(self) -> list[str]:
        log = self.tmp / "steps.log"
        return log.read_text().split() if log.exists() else []

    def step_lines(self, plan: str) -> list[str]:
        rows = [json.loads(ln) for ln in (self.state(plan) / "progress.jsonl").read_text().splitlines() if ln.strip()]
        return [r["step"] for r in rows if r.get("kind") == "step"]


def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def most_at_once(spans: list[tuple[float, float]]) -> int:
    edges = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans], key=lambda x: (x[0], x[1]))
    now = top = 0
    for _, d in edges:
        now += d
        top = max(top, now)
    return top


def run_step(cmd: str, sid: str = "t") -> dict:
    return {"id": sid, "type": "run", "cmd": cmd}


# ---- C-4・I20: 重なりの組 ----


def test_overlapping_plans_of_a_stage_run_one_by_one_and_are_reported(tmp_path, env):
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(q.span("a", 0.8))], ["pkg/"])
    b = q.plan("b", [run_step(q.span("b", 0.3))], ["pkg/x.py"])
    c = q.plan("c", [run_step(q.span("c", 0.8))], ["pkgs/y.py"])  # pkg/ は pkgs/ を含まない
    p, res = q.run(a, b, c, "--max", "3")
    assert p.returncode == 0 and [i["result"] for i in res["items"]] == ["完了"] * 3, p.stderr
    sa, sb, sc = q.spans("a", "b", "c")
    assert not overlap(sa, sb) and sa[1] <= sb[0]  # 組の中は入れた順に 1 本ずつ
    assert overlap(sa, sc)  # 組の外のプランは同時に流れる
    items = {Path(i["plan"]).stem: i for i in res["items"]}
    assert items["a"]["overlap"] == [{"with": b, "paths": ["pkg/", "pkg/x.py"]}]
    assert items["b"]["overlap"] == [{"with": a, "paths": ["pkg/x.py", "pkg/"]}] and "overlap" not in items["c"]
    assert res["metrics"]["overlaps"] == 1 and res["metrics"]["resource_limits"] == {"graphql": 2}
    assert p.stderr.count(f"supervise-queue: 重なる組 {a} と {b}（pkg/ ⊃ pkg/x.py）を同時には流さない") == 1
    assert "- 結果: 完了" in Path(a).with_suffix(".log").read_text()  # <プラン>.log はプランの報告


def test_plans_hitting_the_same_shared_list_run_one_by_one(tmp_path, env):
    q = Queue(tmp_path, env, {"shared": [{"path": "lib/README.md", "touched_by": ["lib/"]}]})
    a = q.plan("a", [run_step(q.span("a", 0.6))], ["lib/one.py"])
    b = q.plan("b", [run_step(q.span("b", 0.3))], ["lib/two.py"])
    p, res = q.run(a, b, "--max", "3")
    assert p.returncode == 0, p.stderr
    sa, sb = q.spans("a", "b")
    assert not overlap(sa, sb)
    assert res["items"][0]["overlap"] == [{"with": b, "paths": ["lib/README.md", "lib/README.md"]}]
    assert f"重なる組 {a} と {b}（lib/README.md）" in p.stderr


def test_plans_of_another_stage_and_finished_plans_are_not_counted(tmp_path, env):
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(note(tmp_path, "a"))], ["pkg/"])
    later = q.plan("later", [run_step(note(tmp_path, "later"))], ["pkg/x.py"])
    p, res = q.run(a, "--then", later)
    assert p.returncode == 0 and res["metrics"]["overlaps"] == 0 and "重なる組" not in p.stderr, p.stderr
    # 終わったプラン（完了の記録がある a）は流し直さず、並行中にも数えない
    b = q.plan("b", [run_step(note(tmp_path, "b"))], ["pkg/y.py"])
    p, res = q.run(a, b)
    assert p.returncode == 0 and [i["result"] for i in res["items"]] == ["完了", "完了"], p.stderr
    assert res["metrics"]["overlaps"] == 0 and all("overlap" not in i for i in res["items"])
    assert q.visits() == ["a", "later", "b"]


def test_a_malformed_declaration_stops_the_queue_before_any_plan_runs(tmp_path, env):
    q = Queue(tmp_path, env, {"resources": {"graphql": {"limit": 0}}})
    a = q.plan("a", [run_step(note(tmp_path, "a"))])
    p, res = q.run(a)
    assert p.returncode == 1 and res["status"] == "stopped" and "limit" in res["summary"]
    assert q.visits() == [] and not list(durable.records_dir().glob("queue-*.sqlite"))


# ---- C-5・I21: 資源の枠 ----


def slot_plans(q: Queue, n: int) -> list[str]:
    """枠の外のステップ（free）の後に、資源のタグのあるステップ（slot。cmd に slot-cmd を含む）を流すプラン。"""
    return [
        q.plan(f"p{i}", [run_step(q.span(f"free{i}", 0.6), "free"), run_step(": slot-cmd; " + q.span(f"slot{i}", 0.7), "slot")])
        for i in range(n)
    ]


def test_tagged_steps_do_not_exceed_the_resource_limit(tmp_path, env):
    q = Queue(tmp_path, env, {"resources": {"graphql": {"limit": 1, "commands": ["slot-cmd"]}}})
    plans = slot_plans(q, 3)
    p, res = q.run(*plans, "--max", "3")
    assert p.returncode == 0 and res["metrics"]["resource_limits"] == {"graphql": 1}, p.stderr
    assert most_at_once(q.spans("slot0", "slot1", "slot2")) == 1
    assert most_at_once(q.spans("free0", "free1", "free2")) == 3  # 枠は --max と別に数える
    for plan in plans:
        assert q.step_lines(plan) == ["free", "slot"]


def test_the_resource_limit_comes_from_the_declaration(tmp_path, env):
    q = Queue(tmp_path, env, {"resources": {"graphql": {"limit": 2, "commands": ["slot-cmd"]}}})
    p, _ = q.run(*slot_plans(q, 3), "--max", "3")
    assert p.returncode == 0, p.stderr
    assert most_at_once(q.spans("slot0", "slot1", "slot2")) == 2


def test_a_tagged_step_of_a_single_engine_run_goes_through_the_slot(tmp_path):
    plan = {
        "フェーズ": "試験",
        "課題": [],
        "作業場所": str(tmp_path),
        "steps": [run_step("true", "a"), {**run_step(": merged-steps.py; echo tagged", "m"), "next": "end"}],
    }
    s = engine.Engine(plan, tmp_path / "state")
    assert "- 結果: 完了" in s.run() and "tagged" in s.state.results["m"]["text"]
    durable.launch("run", str((tmp_path / "state").resolve()))
    try:
        ids = durable.workflow_ids("plan-")
    finally:
        durable.close()
    assert [w.rsplit("-", 1)[1] for w in ids] == ["1", "t2"]  # 2 番目のステップだけが資源の枠を通る


# ---- C-6: 落ちた後の打ち直し ----


def test_killed_queue_continues_from_the_running_step_and_then_runs_the_next_stage(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env)
    a = q.plan("a", [run_step(note(t, "a1"), "a1"), run_step(once_slow(t, "a2"), "a2"), run_step(note(t, "a3"), "a3")])
    b = q.plan("b", [run_step(note(t, "b1"), "b1")])
    c = q.plan("c", [run_step(note(t, "c1"), "c1")])
    args = (a, b, "--max", "2", "--then", c)
    q.kill_when(lambda: (q.state(b) / "report.md").exists() and q.sleeping(a, "a2"), *args)
    assert sorted(q.visits()) == ["a1", "a2", "b1"] and not (t / "done.json").exists()
    p, res = q.run(*args)
    assert p.returncode == 0 and [(Path(i["plan"]).stem, i["result"]) for i in res["items"]] == [("a", "完了"), ("b", "完了"), ("c", "完了")], p.stderr
    assert sorted(q.visits()[:3]) == ["a1", "a2", "b1"] and q.visits()[3:] == ["a2", "a3", "c1"]  # 済んだステップとプランは流し直さない
    assert "を続ける" in p.stderr
    assert q.step_lines(a) == ["a1", "a2", "a3"] and q.step_lines(b) == ["b1"]  # C-3c: step の行は 2 度書かれない
    assert json.loads((t / "done.json").read_text()) == res


def test_killed_queue_continues_an_overlapping_group_in_order(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env)
    a = q.plan("a", [run_step(note(t, "a1"), "a1"), run_step(once_slow(t, "a2"), "a2")], ["pkg/"])
    b = q.plan("b", [run_step(note(t, "b1"), "b1")], ["pkg/x.py"])
    q.kill_when_sleeping(a, "a2", a, b)
    assert q.visits() == ["a1", "a2"]
    p, res = q.run(a, b)
    assert p.returncode == 0 and [i["result"] for i in res["items"]] == ["完了", "完了"], p.stderr
    assert q.visits() == ["a1", "a2", "a2", "b1"]
    assert p.stderr.count("重なる組") == 0 and res["metrics"]["overlaps"] == 1  # 知らせは打ち直しで 2 度出ない


def test_killed_queue_reruns_the_tagged_step_once(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env, {"resources": {"graphql": {"limit": 1, "commands": ["slot-cmd"]}}})
    a = q.plan("a", [run_step(note(t, "a1"), "a1"), run_step(": slot-cmd; " + once_slow(t, "a2"), "a2"), run_step(note(t, "a3"), "a3")])
    q.kill_when_sleeping(a, "a2", a)
    p, res = q.run(a)
    assert p.returncode == 0 and res["items"][0]["result"] == "完了", p.stderr
    assert q.visits() == ["a1", "a2", "a2", "a3"] and q.step_lines(a) == ["a1", "a2", "a3"]


def test_a_gate_stays_a_gate_and_a_stopped_plan_runs_again_from_the_top(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env)
    gate = q.plan("gate", [{**run_step(note(t, "g1", "exit 10"), "g1"), "gate_next": "end"}])
    stop = q.plan("stop", [run_step(note(t, "s1"), "s1"), run_step(note(t, "s2", "exit 1"), "s2")])
    for _ in range(2):
        p, res = q.run(gate, stop)
        assert p.returncode == 1 and {Path(i["plan"]).stem: i["result"] for i in res["items"]} == {"gate": "関門", "stop": "止まった"}
    assert sorted(q.visits()) == ["g1", "s1", "s1", "s2", "s2"]  # 関門は同じ所で止まったまま、止まったプランは頭から


def test_relaunching_with_other_plans_cancels_the_running_queue_before_launch(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env, {"resources": {"graphql": {"limit": 1, "commands": ["slot-cmd"]}}})
    a = q.plan("a", [run_step(note(t, "a1"), "a1"), run_step(": slot-cmd; " + once_slow(t, "a2"), "a2")])
    b = q.plan("b", [run_step(note(t, "b1"), "b1")])
    q.kill_when_sleeping(a, "a2", a)
    p, res = q.run(a, b)
    assert p.returncode == 0 and len(res["items"]) == 2, p.stderr
    assert "続けない途中の耐久ワークフローを止めた" in p.stderr
    assert sorted(q.visits()) == ["a1", "a1", "a2", "a2", "b1"]  # 並びを変えた打ち直しは、途中のプランを頭から流す
    durable.launch("queue", str(t / "done.json"))
    try:
        states = sorted(durable.status(w) for w in durable.workflow_ids("plan-"))
    finally:
        durable.close()
    # 古い回のプランと、資源の枠へ入れた子は止まる。新しい回の a・a の子・b が終わる
    assert states == ["CANCELLED", "CANCELLED", "SUCCESS", "SUCCESS", "SUCCESS"]


# ---- C-2: ポートと耐久の記録の置き場 ----


def test_no_port_is_listened_and_the_record_is_under_the_state_home(tmp_path, env):
    t, q = tmp_path, Queue(tmp_path, env)
    a = q.plan("a", [run_step(once_slow(t, "a1"), "a1")])
    proc = q.spawn(a)
    try:
        wait_for(lambda: (t / "a1.pid").exists())
        listening = [c for c in procs.psutil.Process(proc.pid).net_connections(kind="inet") if c.status == procs.psutil.CONN_LISTEN]
        assert listening == []
        assert [r.name for r in durable.records_dir().glob("*.sqlite")] == [f"{durable.launch_key('queue', str(t / 'done.json'))}.sqlite"]
    finally:
        proc.kill()
        proc.wait()
