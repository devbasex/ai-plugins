"""止まった回の打ち直し: 続きから流す・持ち主の名前・差し込みの値の変更・外部への操作の冪等・残った子の停止（#2640・#2827・#3053・B9・B10）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ndf import process_group
from ndf.core.plan import steps
from ndf.testing import faults

from . import _plan_helpers as H

# 初めの 1 回は止まり（終了コード 1）、2 回目からは通るステップの本体。目印のファイルを第 1 引数で受ける
_FAIL_ONCE = "import pathlib, sys; m = pathlib.Path(sys.argv[1]); first = not m.exists(); m.touch(); sys.exit(1 if first else 0)"


def _fail_once(sid: str, mark: Path) -> dict:
    return {"id": sid, "run": [sys.executable, "-c", _FAIL_ONCE, str(mark)], "exit_cause": {"1": "check_failed"}}


def _rerun(repo: Path, out: dict) -> tuple[int, dict]:
    """結果の `next`（`ndf plan next ...`）をそのまま打つ。"""
    args = out["next"]["argv"]
    assert args[:3] == ["ndf", "plan", "next"]
    return H.ndf(repo, *args[2:])


def test_a_stopped_round_continues_from_the_stopped_step(repo, tmp_path):
    """止まった回の結果の next は止まったステップから続け、前の段階を頭から流し直さない（#2827）。"""
    plan = H.write_plan(
        repo, "c", [H.count_step("a", tmp_path / "a"), _fail_once("b", tmp_path / "b.mark"), H.count_step("c", tmp_path / "c")]
    )
    code, out = H.ndf(repo, "next", plan)
    assert out["cause"]["kind"] == "check_failed"
    argv = out["next"]["argv"]
    assert argv[argv.index("--from") + 1] == "b"
    code, out = _rerun(repo, out)
    assert (code, out["status"]) == (H.OK, "ok")
    assert (H.count(tmp_path / "a"), H.count(tmp_path / "c")) == (1, 1)


def test_the_owner_stays_the_same_across_rounds(repo, tmp_path):
    """回が変わっても `{owner}` は同じで、前の回が記録した持ち主の続きとして打てる（#2640）。"""
    code_text = (
        "import pathlib, sys; o = pathlib.Path(sys.argv[1]); v = sys.argv[2]\n"
        "if not o.exists(): o.write_text(v); sys.exit(1)\n"
        "sys.exit(0 if o.read_text() == v else 2)"
    )
    step = {
        "id": "push",
        "run": [sys.executable, "-c", code_text, str(tmp_path / "owner"), "{owner}"],
        "exit_cause": {"1": "check_failed", "2": "refused"},
    }
    plan = H.write_plan(repo, "o", [H.count_step("a", tmp_path / "a"), step])
    first = H.ndf(repo, "next", plan)[1]
    code, out = _rerun(repo, first)
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert out["items"][0]["run"] != first["items"][0]["run"]


def test_a_changed_set_value_runs_instead_of_returning_the_old_report(repo, tmp_path):
    """差し込みの値を大きくして打ち直すと、前の回の報告を返さずに止まったステップから続く（#3053）。"""
    check = "import sys; sys.exit(0 if int(sys.argv[1]) >= 150 else 1)"
    step = {"id": "work", "run": [sys.executable, "-c", check, "{minutes}"], "exit_cause": {"1": "fix_limit"}}
    plan = H.write_plan(repo, "l", [H.count_step("a", tmp_path / "a"), step])
    out = H.ndf(repo, "next", plan, "--set", "minutes=120")[1]
    assert out["cause"]["kind"] == "fix_limit"
    assert H.ndf(repo, "next", plan, "--set", "minutes=120")[1] == out
    code, out = _rerun(repo, {"next": {"argv": [a.replace("minutes=120", "minutes=180") for a in out["next"]["argv"]]}})
    assert (code, out["status"]) == (H.OK, "ok")
    assert H.count(tmp_path / "a") == 1


# 外部への操作（数を増やす）をしてから、初めの回だけ長く待つステップの本体。数のファイルを第 1 引数で受ける。
# 操作の後に親（`ndf plan next`）を落とせる時点を作り、落とした後の回では待たずに終わる
_OPERATE_THEN_HOLD = (
    "import pathlib, sys, time; p = pathlib.Path(sys.argv[1]); n = int(p.read_text()) + 1 if p.exists() else 1; "
    "p.write_text(str(n)); time.sleep(600 if n == 1 else 0)"
)


def _probe(counter: Path) -> list[str]:
    return [sys.executable, "-c", f"import pathlib, sys; sys.exit(0 if pathlib.Path({str(counter)!r}).exists() else 1)"]


def _start_next(repo: Path, plan: str, state: Path) -> subprocess.Popen:
    """`ndf plan next` を自分の組で起こす（障害の注入で親だけを落とす。ステップの子は別の組に残る）。"""
    env = {**os.environ, "PYTHONPATH": str(H.SRC)}
    env.pop("NDF_PROGRESS_DB", None)
    cmd = [sys.executable, "-m", "ndf", "plan", "next", plan, "--state-dir", str(state)]
    return subprocess.Popen(cmd, cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _kill_parent_once_recorded(repo: Path, plan: str, state: Path, step_id: str, counter: Path) -> None:
    """操作の後（数が書かれ、子の pid が記録された時点）に親を SIGKILL で落とす。"""
    pid_file = state / steps.STEPS_DIR / f"{step_id}{steps.PID_SUFFIX}"
    proc = _start_next(repo, plan, state)
    code = faults.kill_group_when(proc, lambda: counter.exists() and pid_file.exists(), what=f"{step_id} の操作の後")
    assert code == -signal.SIGKILL


def _gone(pid: int) -> bool:
    """pid のプロセスが動いていないか（無いか、終わって回収を待つだけか）。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] in ("Z", "X")


@pytest.mark.parametrize(("with_probe", "times"), [(True, 1), (False, 2)])
def test_an_external_operation_runs_once_after_a_crash(repo, tmp_path, with_probe, times):
    """操作の直後に落としてやり直しても、probe を持つステップの操作は 1 回だけ（B9）。probe が無ければ 2 回打つ。"""
    counter = tmp_path / "pushed"
    step = {"id": "push", "run": [sys.executable, "-c", _OPERATE_THEN_HOLD, str(counter)]}
    if with_probe:
        step["probe"] = _probe(counter)
    plan = H.write_plan(repo, "x", [step, H.count_step("after", tmp_path / "after")])
    state = tmp_path / "state"
    _kill_parent_once_recorded(repo, plan, state, "push", counter)
    code, out = H.ndf(repo, "next", plan, "--state-dir", str(state))
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert (H.count(counter), H.count(tmp_path / "after")) == (times, 1)


def test_a_child_left_by_a_killed_parent_does_not_run_twice(repo, tmp_path):
    """子を残したまま親を kill -9 して打ち直すと、前の子を止めてから打つ。2 つの子が同時に動かない（B10）。"""
    pids = tmp_path / "pids"
    body = (
        "import os, pathlib, sys, time; p = pathlib.Path(sys.argv[1]); before = p.read_text().split() if p.exists() else []; "
        "p.write_text(' '.join(before + [str(os.getpid())])); "
        "alive = [q for q in before if pathlib.Path(f'/proc/{q}').exists() and "
        "open(f'/proc/{q}/stat').read().rsplit(')', 1)[1].split()[0] not in 'ZX']; "
        "sys.exit(3) if alive else time.sleep(600 if not before else 0)"
    )
    step = {"id": "work", "run": [sys.executable, "-c", body, str(pids)], "exit_cause": {"3": "refused"}}
    plan = H.write_plan(repo, "k", [step])
    state = tmp_path / "state"
    _kill_parent_once_recorded(repo, plan, state, "work", pids)
    first = int(pids.read_text().split()[0])
    assert not _gone(first)
    try:
        code, out = H.ndf(repo, "next", plan, "--state-dir", str(state))
        assert (code, out["status"]) == (H.OK, "ok"), out
        assert _gone(first)
        assert len(pids.read_text().split()) == 2
    finally:
        process_group.stop_detached_group(first)


def test_from_retro_still_runs_deep_due_and_base_sync(repo, tmp_path):
    """#3047・#3114 の再現: 開始点より前の base-sync と deep-due は `--from` でも毎回通し、間のステップは飛ばす。"""
    names = ["base-sync", "release-verify", "deep-due", "retro", "refine"]
    plan = H.write_plan(repo, "close", [H.count_step(n, tmp_path / n) for n in names])
    code, out = H.ndf(repo, "next", plan, "--from", "retro")
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert [H.count(tmp_path / n) for n in names] == [1, 0, 1, 1, 1]


def test_work_base_is_on_the_repository_volume(repo, tmp_path):
    """#2933 の再現: 差し込み `{work_base}`（作業ディレクトリの置き場）は /tmp でなく、リポジトリの git の共通ディレクトリの中。"""
    seen = tmp_path / "seen"
    write = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
    plan = H.write_plan(repo, "w", [{"id": "s", "run": [sys.executable, "-c", write, str(seen), "{work_base}"]}])
    assert H.ndf(repo, "next", plan)[0] == H.OK
    base = Path(seen.read_text())
    assert base.is_relative_to((repo / ".git").resolve())
