"""副命令 `plan queue` を子プロセスで流す: 段と `--then`・承認ゲートを届ける段・作業場所の後片付け・`--resume`（#2876・#1886）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from . import _plan_helpers as H


@pytest.fixture
def repo(tmp_path) -> Path:
    return H.make_repo(tmp_path / "repo")


_WRITE = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"


def _gate_with_pr(sid: str, number: int) -> dict:
    """承認の答えの行き先を持たずに承認ゲートで終わり、PR の番号を items に載せるステップ（開発版の提示）。"""
    line = json.dumps({"tool": "t", "status": "gate", "summary": "本番の提示", "items": [{"kind": "pr", "number": number}]})
    return H.py_step(sid, f"print({line!r})")


def test_a_gate_handing_stage_runs_and_fills_queue_prs(repo, tmp_path):
    """#2876 の再現: 開発版のプランが本番の提示の承認ゲートで終わっても、承認ゲートを届ける段（リリースの依頼）が流れ、
    `{queue_prs}` が前の段の PR で埋まる。キュー全体は承認ゲートで待つ。"""
    seen = tmp_path / "seen"
    dev = H.write_plan(repo, "dev", [_gate_with_pr("present", 11)], touches=["a.py"])
    ask = H.write_plan(
        repo,
        "ask",
        [{"id": "ask", "run": [sys.executable, "-c", _WRITE, str(seen), "{queue_prs}"]}],
        touches=["b.py"],
        accepts_gate=True,
    )
    code, out = H.ndf(repo, "queue", dev, "--then", ask)
    assert out["status"] == "gate", out
    assert seen.read_text() == "11"
    assert [i["status"] for i in out["items"]] == ["gate", "ok"]


def test_a_stage_without_the_gate_role_waits_for_the_gate(repo, tmp_path):
    """承認ゲートを届ける役でない段は、前の段の承認ゲートの待ちを越えて流さない。"""
    dev = H.write_plan(repo, "dev", [_gate_with_pr("present", 11)], touches=["a.py"])
    after = H.write_plan(repo, "after", [H.count_step("s", tmp_path / "after")], touches=["b.py"])
    out = H.ndf(repo, "queue", dev, "--then", after)[1]
    assert out["status"] == "gate"
    assert H.count(tmp_path / "after") == 0


def test_a_queue_started_in_a_removed_worktree_runs_the_next_stage(repo, tmp_path):
    """#1886 の再現: 作業場所から起動したキューで、前の段のプランが作業場所を後片付けで消しても、--then の段が流れる。"""
    wt = tmp_path / "wt"
    H.git(repo, "worktree", "add", "-q", str(wt), "-b", "feat")
    remove = {"id": "cleanup", "run": ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], "in": "repo"}
    first = H.write_plan(repo, "merge", [remove], touches=["a.py"])
    second = H.write_plan(repo, "close", [H.count_step("s", tmp_path / "close")], touches=["b.py"])
    code, out = H.ndf(wt, "queue", str(repo / first), "--then", str(repo / second))
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert not wt.exists()
    assert H.count(tmp_path / "close") == 1


def test_resume_skips_the_finished_plans(repo, tmp_path):
    """結果の next（`--resume`）は前の回で完了したプランを流さずに、止まったプランの止まったステップから続ける。"""
    mark = tmp_path / "mark"
    fail_once = "import pathlib, sys; m = pathlib.Path(sys.argv[1]); first = not m.exists(); m.touch(); sys.exit(1 if first else 0)"
    a = H.write_plan(repo, "a", [H.count_step("s", tmp_path / "a")], touches=["a.py"])
    b = H.write_plan(
        repo, "b", [{"id": "s", "run": [sys.executable, "-c", fail_once, str(mark)], "exit_cause": {"1": "check_failed"}}], touches=["b.py"]
    )
    out = H.ndf(repo, "queue", a, "--then", b)[1]
    assert out["cause"]["kind"] == "check_failed"
    args = out["next"]["argv"]
    assert "--resume" in args and "b=s" in args
    code, out = H.ndf(repo, *args[2:])
    assert (code, out["status"]) == (H.OK, "ok"), out
    assert H.count(tmp_path / "a") == 1
