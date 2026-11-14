"""止まった回の打ち直し: 続きから流す・持ち主の名前・差し込みの値の変更・外部への操作の冪等（#2640・#2827・#3053・B9）。"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from . import _plan_helpers as H

# 初めの 1 回は止まり（終了コード 1）、2 回目からは通るステップの本体。目印のファイルを第 1 引数で受ける
_FAIL_ONCE = "import pathlib, sys; m = pathlib.Path(sys.argv[1]); first = not m.exists(); m.touch(); sys.exit(1 if first else 0)"


@pytest.fixture
def repo(tmp_path) -> Path:
    return H.make_repo(tmp_path / "repo")


def _fail_once(sid: str, mark: Path) -> dict:
    return {"id": sid, "run": [sys.executable, "-c", _FAIL_ONCE, str(mark)], "exit_cause": {"1": "check_failed"}}


def _rerun(repo: Path, out: dict) -> tuple[int, dict]:
    """結果の `next`（`ndf plan next ...`）をそのまま打つ。"""
    args = shlex.split(out["next"])
    assert args[:3] == ["ndf", "plan", "next"]
    return H.ndf(repo, *args[2:])


def test_a_stopped_round_continues_from_the_stopped_step(repo, tmp_path):
    """止まった回の結果の next は止まったステップから続け、前の段階を頭から流し直さない（#2827）。"""
    plan = H.write_plan(repo, "c", [H.count_step("a", tmp_path / "a"), _fail_once("b", tmp_path / "b.mark"), H.count_step("c", tmp_path / "c")])
    code, out = H.ndf(repo, "next", plan)
    assert out["cause"]["kind"] == "check_failed"
    assert "--from b" in out["next"]
    code, out = _rerun(repo, out)
    assert (code, out["status"]) == (0, "ok")
    assert (H.count(tmp_path / "a"), H.count(tmp_path / "c")) == (1, 1)


def test_the_owner_stays_the_same_across_rounds(repo, tmp_path):
    """回が変わっても `{owner}` は同じで、前の回が記録した持ち主の続きとして打てる（#2640）。"""
    code_text = (
        "import pathlib, sys; o = pathlib.Path(sys.argv[1]); v = sys.argv[2]\n"
        "if not o.exists(): o.write_text(v); sys.exit(1)\n"
        "sys.exit(0 if o.read_text() == v else 2)"
    )
    step = {"id": "push", "run": [sys.executable, "-c", code_text, str(tmp_path / "owner"), "{owner}"], "exit_cause": {"1": "check_failed", "2": "refused"}}
    plan = H.write_plan(repo, "o", [H.count_step("a", tmp_path / "a"), step])
    first = H.ndf(repo, "next", plan)[1]
    code, out = _rerun(repo, first)
    assert (code, out["status"]) == (0, "ok"), out
    assert out["items"][0]["run"] != first["items"][0]["run"]


def test_a_changed_set_value_runs_instead_of_returning_the_old_report(repo, tmp_path):
    """差し込みの値を大きくして打ち直すと、前の回の報告を返さずに止まったステップから続く（#3053）。"""
    check = "import sys; sys.exit(0 if int(sys.argv[1]) >= 150 else 1)"
    step = {"id": "work", "run": [sys.executable, "-c", check, "{minutes}"], "exit_cause": {"1": "fix_limit"}}
    plan = H.write_plan(repo, "l", [H.count_step("a", tmp_path / "a"), step])
    out = H.ndf(repo, "next", plan, "--set", "minutes=120")[1]
    assert out["cause"]["kind"] == "fix_limit"
    assert H.ndf(repo, "next", plan, "--set", "minutes=120")[1] == out
    code, out = _rerun(repo, {"next": out["next"].replace("minutes=120", "minutes=180")})
    assert (code, out["status"]) == (0, "ok")
    assert H.count(tmp_path / "a") == 1


def _kill_parent_after(counter: Path) -> str:
    """外部への操作（数を増やす）の直後に、ステップを打った親（`ndf plan next`）を落とす。"""
    return (
        f"import os, pathlib, signal; p = pathlib.Path({str(counter)!r}); "
        "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1'); os.kill(os.getppid(), signal.SIGKILL)"
    )


def _probe(counter: Path) -> list[str]:
    return [sys.executable, "-c", f"import pathlib, sys; sys.exit(0 if pathlib.Path({str(counter)!r}).exists() else 1)"]


def _next_raw(repo: Path, plan: str) -> int:
    env = {**os.environ, "PYTHONPATH": str(H.SRC)}
    env.pop("NDF_PROGRESS_DB", None)
    cmd = [sys.executable, "-m", "ndf", "plan", "next", plan]
    return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=180, check=False).returncode


@pytest.mark.parametrize(("with_probe", "times"), [(True, 1), (False, 2)])
def test_an_external_operation_runs_once_after_a_crash(repo, tmp_path, with_probe, times):
    """操作の直後に落としてやり直しても、probe を持つステップの操作は 1 回だけ（B9）。probe が無ければ 2 回打つ。"""
    counter = tmp_path / "pushed"
    step = H.py_step("push", _kill_parent_after(counter))
    if with_probe:
        step["probe"] = _probe(counter)
    plan = H.write_plan(repo, "x", [step, H.count_step("after", tmp_path / "after")])
    assert _next_raw(repo, plan) == -9
    if not with_probe:
        # probe の無いステップは打ち直すと、また親を落とす。数だけ確かめる
        _next_raw(repo, plan)
        assert H.count(counter) == times
        return
    code, out = H.ndf(repo, "next", plan)
    assert (code, out["status"]) == (0, "ok"), out
    assert (H.count(counter), H.count(tmp_path / "after")) == (times, 1)


def test_from_retro_still_runs_deep_due_and_base_sync(repo, tmp_path):
    """#3047・#3114 の再現: 開始点より前の base-sync と deep-due は `--from` でも毎回通し、間のステップは飛ばす。"""
    names = ["base-sync", "release-verify", "deep-due", "retro", "refine"]
    plan = H.write_plan(repo, "close", [H.count_step(n, tmp_path / n) for n in names])
    code, out = H.ndf(repo, "next", plan, "--from", "retro")
    assert (code, out["status"]) == (0, "ok"), out
    assert [H.count(tmp_path / n) for n in names] == [1, 0, 1, 1, 1]


def test_work_base_is_on_the_repository_volume(repo, tmp_path):
    """#2933 の再現: 差し込み `{work_base}`（作業ディレクトリの置き場）は /tmp でなく、リポジトリの git の共通ディレクトリの中。"""
    seen = tmp_path / "seen"
    write = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
    plan = H.write_plan(repo, "w", [{"id": "s", "run": [sys.executable, "-c", write, str(seen), "{work_base}"]}])
    assert H.ndf(repo, "next", plan)[0] == 0
    base = Path(seen.read_text())
    assert base.is_relative_to((repo / ".git").resolve())
