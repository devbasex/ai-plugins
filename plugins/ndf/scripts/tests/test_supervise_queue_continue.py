"""キューの続き `supervise.py queue --resume <done>`（#1426 の決定 2・AC5〜AC7・I4〜I6）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_supervise_queue import PY, SUPERVISE, Queue, run_step  # noqa: E402
from test_supervise_resume import note  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    return os.environ.copy()


def resume(q: Queue, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
    argv = [PY, str(SUPERVISE), "queue", "--resume", str(q.tmp / "done.json"), "--poll", "0.1", *args]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=180, env=q.env, cwd=q.tmp)
    assert p.stdout.strip(), p.stderr
    return p, json.loads(p.stdout.splitlines()[-1])


def results(res: dict) -> dict:
    return {Path(i["plan"]).stem: i["result"] for i in res["items"]}


def test_resume_continues_from_the_stopped_step_and_runs_the_next_stage(tmp_path, env):
    """AC7: 3 ステップ目で止まったプランを続けると、止まったステップから流れ、後続のステージまで完了する。
    同じステージで完了したプランは流し直さない（I4）。直す前に続けて再び止まれば後続を流さない（I5）。"""
    t, q = tmp_path, Queue(tmp_path, env)
    fixed = t / "fixed"
    a = q.plan("a", [run_step(note(t, "a1"), "a1"), run_step(note(t, "a2"), "a2"), run_step(note(t, "a3", f"test -f {fixed}"), "a3")])
    b = q.plan("b", [run_step(note(t, "b1"), "b1")])
    c = q.plan("c", [run_step(note(t, "c1"), "c1")])
    p, res = q.run(a, b, "--then", c)
    assert results(res) == {"a": "止まった", "b": "完了", "c": "流さなかった"}
    p, res = resume(q)
    assert results(res) == {"a": "止まった", "b": "完了", "c": "流さなかった"}
    fixed.touch()
    p, res = resume(q)
    assert p.returncode == 0, p.stderr
    assert results(res) == {"a": "完了", "b": "完了", "c": "完了"}
    assert [i.get("resumed_from") for i in res["items"]] == ["a3", None, None]
    visits = q.visits()  # a と b は同じステージで並んで流れる
    assert sorted(visits) == ["a1", "a2", "a3", "a3", "a3", "b1", "c1"] and visits[-1] == "c1"  # 頭から流し直さない・b を流し直さない
    assert not list(t.glob("*-r2.json"))  # プランのファイルを写さない


def test_resume_with_from_and_without_a_listing(tmp_path, env):
    """--from で開始のステップを上書きする。一覧の無い done では何も流さない（I6）。"""
    t, q = tmp_path, Queue(tmp_path, env)
    p, res = resume(q)
    assert (p.returncode, res["status"], res["items"]) == (1, "stopped", [])
    g = q.plan(
        "g",
        [
            {**run_step(note(t, "g1", f"test -f {t / 'ok'} || exit 10"), "g1"), "gate_next": "end", "next": "g2"},
            run_step(note(t, "g2"), "g2"),
        ],
    )
    p, res = q.run(g)
    assert results(res) == {"g": "関門"}
    p, res = resume(q, "--from", "nothing=g1")
    assert (res["status"], q.visits()) == ("stopped", ["g1"])
    (t / "ok").touch()
    p, res = resume(q, "--from", "g=g2")
    assert (results(res), res["items"][0]["resumed_from"], q.visits()) == ({"g": "完了"}, "g2", ["g1", "g2"])
    listing = json.loads((t / "done.plans.json").read_text())
    assert (listing["stages"], listing["max"], listing["starts"]) == ([[g]], 3, {g: "g2"})


def test_resume_keeps_the_start_of_a_finished_plan(tmp_path, env):
    """完了したプランの前の開始のステップを次の --resume でも保つ。落とすと元の関門の結果を拾い、後続を止める。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from supervise_lib import queue_resume

    done = tmp_path / "done.json"
    (tmp_path / "done.plans.json").write_text(json.dumps({"stages": [["d"], ["v"]], "max": 2, "starts": {"d": "approve"}}))
    done.write_text(json.dumps({"items": [{"plan": "d", "result": "完了"}, {"plan": "v", "result": "止まった"}]}))
    stages, _, found = queue_resume.resume_targets(done)
    assert stages == [["d"], ["v"]]
    assert {t.plan: t.step for t in found}["d"] == "approve"
