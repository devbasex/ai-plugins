"""`supervise.py run` を `kill -9` の後に打ち直すと、流れていたステップから続く（#1142 の C-6・I17・I18・決定 35）。

試行（`runner-trial.py check dbos`）の 5 つのシナリオ（pass・branch・loop・gate・stop）と、遅れの長いステップの
`kill -9` を、run のステップだけのプランで流す。ステップは `steps.log` へ自分の id を書き、2 度流れたかを数える。
子は `supervise.py run` と同じ `commands.cmd_run` を呼ぶ小さな起動（形式の版だけを引数で替えられる）で動かす。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import dbos  # noqa: E402,F401  耐久の記録の外部パッケージ。無ければ集めるところで落とす
import durable  # noqa: E402
import procs  # noqa: E402

PY = sys.executable
SUPERVISE = SCRIPTS / "supervise.py"
DRIVER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {scripts!r})
    import supervise_lib  # lib/ を sys.path へ足す
    import durable
    from supervise_lib import commands

    plan, fmt, start = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else None)
    if fmt != "-":
        durable.FORMAT = fmt
    sys.exit(commands.cmd_run(plan, None, [], start))
    """
).format(scripts=str(SCRIPTS))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    return os.environ.copy()


class Plan:
    """run のステップだけのプランと、その打ち方。"""

    def __init__(self, tmp: Path, env: dict, steps: list[dict], name: str = "plan") -> None:
        self.tmp, self.env = tmp, env
        self.log = tmp / "steps.log"
        self.path = tmp / f"{name}.json"
        self.driver = tmp / "driver.py"
        self.driver.write_text(DRIVER)
        self.write(steps)

    def write(self, steps: list[dict], **extra) -> None:
        plan = {"フェーズ": "試験", "課題": [], "作業場所": str(self.tmp), "steps": steps, **extra}
        self.path.write_text(json.dumps(plan, ensure_ascii=False))

    def argv(self, fmt: str = "-", start: str | None = None) -> list[str]:
        return [PY, str(self.driver), str(self.path), fmt, *([start] if start else [])]

    def run(self, fmt: str = "-", start: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(fmt, start), capture_output=True, text=True, timeout=120, env=self.env, cwd=self.tmp)

    def spawn(self, fmt: str = "-") -> subprocess.Popen:
        return subprocess.Popen(self.argv(fmt), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.tmp)

    def visits(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []

    @property
    def state(self) -> Path:
        return self.tmp / f"{self.path.stem}-state"

    def step_lines(self) -> list[str]:
        rows = [json.loads(l) for l in (self.state / "progress.jsonl").read_text().splitlines() if l.strip()]
        return [r["step"] for r in rows if r.get("kind") == "step"]

    def logged(self) -> list[dict]:
        return json.loads((self.state / "state.json").read_text())["log"]


def note(tmp: Path, sid: str, then: str = "true") -> str:
    """自分の id を steps.log へ書いてから `then` を打つ run のステップの cmd。"""
    return f"echo {sid} >> {tmp / 'steps.log'}; {then}"


def once_slow(tmp: Path, sid: str, then: str = "true") -> str:
    """最初の 1 回だけ長く眠る（pid を書いてから）。2 回目は `then` だけを打つ。"""
    mark, pid = tmp / f"{sid}.slept", tmp / f"{sid}.pid"
    return note(tmp, sid, f"if [ -f {mark} ]; then {then}; else touch {mark}; echo $$ > {pid}; sleep 60; fi")


def wait_for(cond, timeout: float = 60.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("待ちの上限を過ぎた")
        time.sleep(0.05)


def kill_when_sleeping(plan: Plan, sid: str, fmt: str = "-") -> int:
    """`sid` が眠り始めたら起動を kill -9 する。ステップの子（シェル）の pid を返す。"""
    proc = plan.spawn(fmt)
    pidfile = plan.tmp / f"{sid}.pid"
    wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    return int(pidfile.read_text())


# ---- 5 つのシナリオ（C-6） ----


def test_pass_continues_from_the_running_step_and_does_not_rerun_finished_ones(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "a", "type": "run", "cmd": note(t, "a")},
        {"id": "b", "type": "run", "cmd": note(t, "b")},
        {"id": "c", "type": "run", "cmd": once_slow(t, "c")},
        {"id": "d", "type": "run", "cmd": note(t, "d"), "next": "end"},
    ])  # fmt: skip
    kill_when_sleeping(plan, "c")
    assert plan.visits() == ["a", "b", "c"]
    p = plan.run()
    assert p.returncode == 0 and "- 結果: 完了" in p.stdout, p.stderr
    assert plan.visits() == ["a", "b", "c", "c", "d"]
    assert "を続ける" in p.stderr
    # C-3c: 続けても、記録のあるステップの step の行は 2 度書かれない
    assert plan.step_lines() == ["a", "b", "c", "d"]
    assert [e["id"] for e in plan.logged()] == ["a", "b", "c", "d"]
    assert "- 通ったステップ: a(exit=0) → b(exit=0) → c(exit=0) → d(exit=0)" in p.stdout


def test_branch_keeps_the_path_taken_before_the_kill(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "t", "type": "run", "cmd": note(t, "t", "exit 1"), "on_fail": "fix"},
        {"id": "ok", "type": "run", "cmd": note(t, "ok"), "next": "end"},
        {"id": "fix", "type": "run", "cmd": once_slow(t, "fix"), "next": "after"},
        {"id": "after", "type": "run", "cmd": note(t, "after"), "next": "end"},
    ])  # fmt: skip
    kill_when_sleeping(plan, "fix")
    p = plan.run()
    assert p.returncode == 0, p.stderr
    assert plan.visits() == ["t", "fix", "fix", "after"]
    assert plan.step_lines() == ["t", "fix", "after"]


def test_loop_counts_the_rounds_across_the_kill(tmp_path, env):
    """on_fail で戻るループの途中で落ちても、済んだ回を流し直さずに同じ回数で終わる。"""
    t = tmp_path
    count = t / "count"
    bump = f"n=$(cat {count} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {count}; [ $n -ge 4 ]"
    plan = Plan(t, env, [
        {"id": "check", "type": "run", "cmd": note(t, "check", bump), "on_fail": "fix", "next": "end"},
        {"id": "fix", "type": "run", "cmd": note(t, "fix", f"[ $(cat {count}) -eq 2 ] && [ ! -f {t / 'fix.slept'} ] && "
                                                           f"touch {t / 'fix.slept'} && echo $$ > {t / 'fix.pid'} && sleep 60; true"),
         "next": "check"},
    ])  # fmt: skip
    kill_when_sleeping(plan, "fix")
    p = plan.run()
    assert p.returncode == 0, p.stderr
    assert plan.visits() == ["check", "fix", "check", "fix", "fix", "check", "fix", "check"]
    assert count.read_text().strip() == "4"
    assert plan.step_lines() == ["check", "fix", "check", "fix", "check", "fix", "check"]


def test_gate_returns_the_same_report_until_from_continues(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "ready", "type": "run", "cmd": note(t, "ready", "exit 10"), "gate_next": "end"},
        {"id": "release", "type": "run", "cmd": note(t, "release"), "next": "end"},
    ])  # fmt: skip
    first = plan.run()
    assert first.returncode == 0 and "- 結果: 関門" in first.stdout, first.stderr
    again = plan.run()  # 承認の無い打ち直しは同じ所で止まったまま
    assert again.returncode == 0 and "- 結果: 関門" in again.stdout
    assert plan.visits() == ["ready"]
    assert plan.step_lines() == ["ready"]
    attention = [json.loads(l) for l in (plan.state / "progress.jsonl").read_text().splitlines() if '"attention"' in l]
    assert [a["reason"] for a in attention] == ["関門", "関門"]  # 打ち直しでも conductor へ知らせる
    cont = plan.run(start="release")  # 承認の後は --from で続く
    assert cont.returncode == 0 and "- 結果: 完了" in cont.stdout, cont.stderr
    assert plan.visits() == ["ready", "release"]


def test_stop_runs_again_from_the_top(tmp_path, env):
    t = tmp_path
    flag = t / "fixed"
    plan = Plan(t, env, [
        {"id": "a", "type": "run", "cmd": note(t, "a")},
        {"id": "b", "type": "run", "cmd": note(t, "b", f"[ -f {flag} ]"), "next": "end"},
    ])  # fmt: skip
    p = plan.run()
    assert p.returncode == 3 and "- 結果: 止まった" in p.stdout
    flag.touch()
    p = plan.run()  # 止まったの記録は続けず、次の実行の回で頭から流す
    assert p.returncode == 0 and "- 結果: 完了" in p.stdout, p.stderr
    assert plan.visits() == ["a", "b", "a", "b"]


# ---- 遅れの長いステップと孤児の片付け（決定 35） ----


def test_the_orphan_group_of_the_killed_step_is_stopped_before_the_step_runs_again(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "a", "type": "run", "cmd": note(t, "a")},
        {"id": "long", "type": "run", "cmd": once_slow(t, "long"), "next": "end"},
    ])  # fmt: skip
    orphan = kill_when_sleeping(plan, "long")
    assert procs.pid_alive(orphan)  # kill -9 は子のプロセスグループを残す
    assert (plan.state / "step.pid").is_file()
    p = plan.run()
    assert p.returncode == 0, p.stderr
    assert "子のプロセスグループ" in p.stderr
    procs.wait_gone(orphan, 10)
    assert not procs.pid_alive(orphan)
    assert not (plan.state / "step.pid").exists()
    logged = plan.logged()
    assert [e["id"] for e in logged] == ["a", "long"]
    assert logged[1].get("resumed") is True and "resumed" not in logged[0]


def test_a_live_process_whose_start_time_differs_is_not_stopped(tmp_path):
    from supervise_lib import state

    other = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        (tmp_path / "step.pid").write_text(
            json.dumps({"step": "x", "children": [{"pgid": other.pid, "pid": other.pid, "create_time": 1.0}]})
        )
        assert state.reap_orphans(tmp_path) == "x"
        assert other.poll() is None and not (tmp_path / "step.pid").exists()
    finally:
        other.kill()
        other.wait()


# ---- 耐久の記録（C-2・I17・I18・古い回の停止） ----


def test_no_port_is_listened_and_the_record_is_under_the_state_home(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [{"id": "long", "type": "run", "cmd": once_slow(t, "long"), "next": "end"}])
    proc = plan.spawn()
    try:
        wait_for(lambda: (t / "long.pid").exists())
        listening = [c for c in procs.psutil.Process(proc.pid).net_connections(kind="inet") if c.status == procs.psutil.CONN_LISTEN]
        assert listening == []
        records = list(durable.records_dir().glob("run-*.sqlite"))
        assert [r.name for r in records] == [f"{durable.launch_key('run', str(plan.path))}.sqlite"]
    finally:
        proc.kill()
        proc.wait()


def test_a_second_run_of_the_same_plan_waits_for_the_first(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "a", "type": "run", "cmd": note(t, "a", "sleep 1.5")},
        {"id": "b", "type": "run", "cmd": note(t, "b"), "next": "end"},
    ])  # fmt: skip
    first = plan.spawn()
    wait_for(lambda: plan.visits() == ["a"])
    second = plan.run()
    first.communicate(timeout=60)
    assert first.returncode == 0 and second.returncode == 0, second.stderr
    assert plan.visits() == ["a", "b"]  # 2 つ目は 1 つ目の完了の記録を返す


def test_a_record_of_another_format_is_not_continued(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [
        {"id": "a", "type": "run", "cmd": note(t, "a")},
        {"id": "b", "type": "run", "cmd": once_slow(t, "b"), "next": "end"},
    ])  # fmt: skip
    kill_when_sleeping(plan, "b", fmt="ndf-durable-old")
    p = plan.run()
    assert p.returncode == 0, p.stderr
    assert "形式の版" in p.stderr
    assert plan.visits() == ["a", "b", "a", "b"]  # 実行の回 2 を頭から流す


def test_editing_the_plan_cancels_the_old_run_before_launch(tmp_path, env):
    t = tmp_path
    steps = [
        {"id": "a", "type": "run", "cmd": note(t, "a")},
        {"id": "b", "type": "run", "cmd": once_slow(t, "b")},
        {"id": "c", "type": "run", "cmd": note(t, "c"), "next": "end"},
    ]
    plan = Plan(t, env, steps)
    kill_when_sleeping(plan, "b")
    plan.write(steps, 上限=10)  # プランを直すと中身鍵が変わる
    p = plan.run()
    assert p.returncode == 0, p.stderr
    assert "続けない途中の耐久ワークフローを止めた" in p.stderr
    assert plan.visits() == ["a", "b", "a", "b", "c"]  # 古い回の b・c は流れない
    durable.launch("run", str(plan.path))
    try:
        states = {w: durable.status(w) for w in durable.workflow_ids("plan-")}
    finally:
        durable.close()
    assert sorted(states.values()) == ["CANCELLED", "SUCCESS"]


def test_supervise_py_run_keeps_its_exit_codes(tmp_path, env):
    t = tmp_path
    plan = Plan(t, env, [{"id": "a", "type": "run", "cmd": note(t, "a", "exit 10"), "gate_next": "end"}])
    for _ in range(2):
        p = subprocess.run([PY, str(SUPERVISE), "run", str(plan.path)], capture_output=True, text=True, env=env, cwd=t, timeout=120)
        assert p.returncode == 0 and "- 結果: 関門" in p.stdout, p.stderr
    assert plan.visits() == ["a"]
