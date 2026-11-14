"""副命令 `plan queue` を子プロセスで流す: 段と `--then`・承認ゲートを届ける段・作業場所の後片付け・`--resume`（#2876・#1886）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ndf.core.plan import model, queue_run

from . import _plan_helpers as H


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


# --- 現状固定テスト: `queue_run.run` を同じプロセスで流す（子の `ndf plan next` を偽の Popen に置き換える） ---


class _FakeChildren:
    """子のプロセスの代わり。起動と読み取りの順を `log` に残し、プラン名ごとに決めた結果を返す。"""

    def __init__(self, outputs: dict[str, object]):
        self.outputs = outputs
        self.log: list[tuple[str, str]] = []
        self.args: dict[str, list[str]] = {}
        self.cwds: dict[str, object] = {}

    def __call__(self, args, cwd=None, **_kw):
        name = Path(args[5]).stem
        self.log.append(("start", name))
        self.args[name] = list(args)
        self.cwds[name] = cwd
        return _FakeProc(self, name)


class _FakeProc:
    """結果が dict なら 1 行の JSON を、(stdout, stderr, 終了コード) の組ならそのまま返す。"""

    def __init__(self, owner: _FakeChildren, name: str):
        self.owner = owner
        self.name = name
        self.returncode = 0

    def communicate(self):
        self.owner.log.append(("read", self.name))
        out = self.owner.outputs[self.name]
        if isinstance(out, tuple):
            self.returncode = out[2]
            return out[0], out[1]
        return json.dumps(out) + "\n", ""


def _entry(tmp_path: Path, name: str, start=None, **raw) -> queue_run.Entry:
    plan = model.parse({"name": name, "steps": [{"id": "s", "run": ["true"]}], **raw})
    return queue_run.Entry(tmp_path / f"{name}.json", plan, start)


def _ok(**kw) -> dict:
    return {"status": "ok", "summary": "done", "items": [], **kw}


def _stopped(kind: str, step: str = "s") -> dict:
    return {"status": "stopped", "summary": "落ちた", "cause": {"kind": kind}, "items": [{"kind": "plan", "step": step}]}


@pytest.fixture
def children(monkeypatch):
    def install(outputs):
        fake = _FakeChildren(outputs)
        monkeypatch.setattr(queue_run.subprocess, "Popen", fake)
        return fake

    return install


def _queue_rows(qdir: Path) -> dict:
    return json.loads((qdir / queue_run.QUEUE_FILE).read_text(encoding="utf-8"))["plans"]


def _write_rows(qdir: Path, rows: dict) -> None:
    qdir.mkdir()
    (qdir / queue_run.QUEUE_FILE).write_text(json.dumps({"plans": rows}), encoding="utf-8")


def test_run_returns_outcomes_in_queue_order_and_records_each_plan(tmp_path, children):
    fake = children({"a": _ok(items=[{"kind": "pr", "number": 3}]), "b": _ok(), "c": _ok()})
    a, b, c = (_entry(tmp_path, n, touches=[f"{n}.py"]) for n in "abc")
    qdir = tmp_path / "q"
    outs = queue_run.run([[a, b], [c]], qdir, tmp_path, resume=False)
    assert [(o.name, o.status, o.prs) for o in outs] == [("a", "ok", (3,)), ("b", "ok", ()), ("c", "ok", ())]
    assert fake.log == [("start", "a"), ("start", "b"), ("read", "a"), ("read", "b"), ("start", "c"), ("read", "c")]
    assert fake.cwds == {"a": tmp_path, "b": tmp_path, "c": tmp_path}
    assert _queue_rows(qdir) == {
        "a": {"status": "ok", "kind": None, "prs": [3], "release": False},
        "b": {"status": "ok", "kind": None, "prs": [], "release": False},
        "c": {"status": "ok", "kind": None, "prs": [], "release": False},
    }


def test_run_puts_overlapping_plans_of_a_stage_into_separate_waves(tmp_path, children):
    fake = children({"a": _ok(), "b": _ok()})
    a, b = _entry(tmp_path, "a", touches=["x.py"]), _entry(tmp_path, "b", touches=["x.py"])
    queue_run.run([[a, b]], tmp_path / "q", tmp_path, resume=False)
    assert fake.log == [("start", "a"), ("read", "a"), ("start", "b"), ("read", "b")]


def test_run_puts_plans_needing_a_prior_stage_into_one_wave(tmp_path, children):
    fake = children({"a": _ok(), "b": _ok(), "c": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], needs=["a"])
    c = _entry(tmp_path, "c", touches=["c.py"], needs=["a"])
    queue_run.run([[a], [b, c]], tmp_path / "q", tmp_path, resume=False)
    assert fake.log == [("start", "a"), ("read", "a"), ("start", "b"), ("start", "c"), ("read", "b"), ("read", "c")]


def test_run_does_not_launch_a_stage_after_a_stopped_stage(tmp_path, children):
    fake = children({"a": _stopped("check_failed"), "b": _ok()})
    a, b = _entry(tmp_path, "a", touches=["a.py"]), _entry(tmp_path, "b", touches=["b.py"])
    qdir = tmp_path / "q"
    outs = queue_run.run([[a], [b]], qdir, tmp_path, resume=False)
    assert [(o.name, o.status, o.kind, o.step) for o in outs] == [("a", "stopped", "check_failed", "s"), ("b", None, None, None)]
    assert outs[1].detail == "前の段のプランが完了でない: a（stopped）"
    assert ("start", "b") not in fake.log
    assert set(_queue_rows(qdir)) == {"a"}


def test_run_lets_a_gate_accepting_stage_run_after_a_gate(tmp_path, children):
    gate = {"status": "gate", "summary": "待つ", "items": [{"kind": "pr", "number": 7}], "routes": {"approve": None}}
    fake = children({"a": gate, "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], accepts_gate=True)
    outs = queue_run.run([[a], [b]], tmp_path / "q", tmp_path, resume=False)
    assert [(o.name, o.status) for o in outs] == [("a", "gate"), ("b", "ok")]
    assert outs[0].routes == {"approve": None}
    assert ("start", "b") in fake.log


def test_run_stops_a_plan_whose_needs_did_not_finish(tmp_path, children):
    fake = children({"a": _stopped("check_failed"), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], needs=["a"])
    qdir = tmp_path / "q"
    outs = queue_run.run([[a, b]], qdir, tmp_path, resume=False)
    assert [(o.name, o.status, o.kind) for o in outs] == [("a", "stopped", "check_failed"), ("b", "stopped", "precondition")]
    assert outs[1].detail == "前提のプランが完了でない: a（stopped）"
    assert ("start", "b") not in fake.log
    assert _queue_rows(qdir)["b"] == {"status": "stopped", "kind": "precondition", "prs": [], "release": False}


def test_run_fills_queue_prs_from_the_prior_stage(tmp_path, children):
    fake = children({"a": _ok(items=[{"kind": "pr", "number": 12}, {"kind": "pr", "number": 11}]), "r": _ok(), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    r = _entry(tmp_path, "r", touches=["r.py"], release=True)
    b = _entry(tmp_path, "b", touches=["b.py"], steps=[{"id": "s", "run": ["echo", "{queue_prs}"]}])
    outs = queue_run.run([[a, r], [b]], tmp_path / "q", tmp_path, resume=False)
    assert [o.status for o in outs] == ["ok", "ok", "ok"]
    assert fake.args["b"][-2:] == ["--set", "queue_prs=11 12"]
    assert fake.args["a"] == [sys.executable, "-m", "ndf", "plan", "next", str(tmp_path / "a.json")]


def test_run_stops_a_queue_prs_plan_without_prs(tmp_path, children):
    fake = children({"a": _ok(), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], steps=[{"id": "s", "run": ["echo", "{queue_prs}"]}])
    outs = queue_run.run([[a], [b]], tmp_path / "q", tmp_path, resume=False)
    assert (outs[1].status, outs[1].kind) == ("stopped", "input_missing")
    assert ("start", "b") not in fake.log


def test_run_reports_a_child_without_a_result_json(tmp_path, children):
    children({"a": ("not json\n", "boom\nlast line\n", 3), "b": ("", "", 5)})
    a, b = _entry(tmp_path, "a", touches=["a.py"]), _entry(tmp_path, "b", touches=["b.py"])
    outs = queue_run.run([[a, b]], tmp_path / "q", tmp_path, resume=False)
    assert [(o.status, o.kind) for o in outs] == [("stopped", "bad_report"), ("stopped", "bad_report")]
    assert outs[0].detail == "a が結果 JSON を返さなかった: last line"
    assert outs[1].detail == "b が結果 JSON を返さなかった: 終了コード 5"


def test_run_masks_secrets_in_the_stderr_of_a_child_without_a_result_json(tmp_path, children, monkeypatch):
    monkeypatch.setenv("NDF_TEST_TOKEN", "s3cr3t-value-123")
    children({"a": ("", "connect failed: s3cr3t-value-123\n", 3)})
    outs = queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], tmp_path / "q", tmp_path, resume=False)
    assert outs[0].detail == "a が結果 JSON を返さなかった: connect failed: [REDACTED]"


def test_run_with_resume_keeps_finished_plans_and_reruns_the_rest(tmp_path, children):
    qdir = tmp_path / "q"
    _write_rows(
        qdir,
        {
            "a": {"status": "ok", "kind": None, "prs": [4], "release": False},
            "b": {"status": "stopped", "kind": "check_failed", "prs": [], "release": False},
            "c": {"status": "ok", "kind": None, "prs": [], "release": False},
        },
    )
    fake = children({"b": _ok(), "c": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"])
    c = _entry(tmp_path, "c", "s", touches=["c.py"])
    outs = queue_run.run([[a, b, c]], qdir, tmp_path, resume=True)
    assert [(o.name, o.status) for o in outs] == [("a", "ok"), ("b", "ok"), ("c", "ok")]
    assert (outs[0].detail, outs[0].prs) == ("前の回で完了した", (4,))
    assert [n for kind, n in fake.log if kind == "start"] == ["b", "c"]
    assert fake.args["c"][-2:] == ["--from", "s"]


def test_run_without_resume_ignores_the_record(tmp_path, children):
    qdir = tmp_path / "q"
    _write_rows(qdir, {"a": {"status": "ok", "kind": None, "prs": [], "release": False}})
    fake = children({"a": _ok()})
    queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], qdir, tmp_path, resume=False)
    assert fake.log == [("start", "a"), ("read", "a")]
