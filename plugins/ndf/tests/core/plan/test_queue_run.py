"""サブコマンド `plan queue` の本体（`queue_run.run`）を同じプロセスで流す: 波の分け方・並列の上限・承認ゲート・`--resume`。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ndf.cli import ndf_command
from ndf.core.plan import model, queue_run


# --- 現状固定テスト: `queue_run.run` を同じプロセスで流す（子の `ndf plan next` を偽の Popen に置き換える） ---


class _FakeChildren:
    """子のプロセスの代わり。起動と読み取りの順を `log` に残し、プラン名ごとに決めた結果を返す。"""

    def __init__(self, outputs: dict[str, object]):
        self.outputs = outputs
        self.log: list[tuple[str, str]] = []
        self.args: dict[str, list[str]] = {}
        self.cwds: dict[str, object] = {}
        self.envs: dict[str, dict] = {}

    def __call__(self, args, cwd=None, env=None, **_kw):
        name = Path(args[args.index("next") + 1]).stem
        self.log.append(("start", name))
        self.args[name] = list(args)
        self.cwds[name] = cwd
        self.envs[name] = env
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
    outs = queue_run.run([[a, b], [c]], qdir, tmp_path, queue_run.QueueOptions())
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
    queue_run.run([[a, b]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert fake.log == [("start", "a"), ("read", "a"), ("start", "b"), ("read", "b")]


def test_run_launches_at_most_max_parallel_plans_at_once(tmp_path, children):
    """スロットの 3 本がそろって起動するまで子を終わらせない（子の即時完了と起動の順の競合で落ちない）。"""
    full = threading.Event()
    fake = children({n: _ok() for n in "abcd"})
    real = _FakeProc.communicate

    def held(self):
        if sum(event == "start" for event, _ in self.owner.log) >= 3:
            full.set()
        assert full.wait(5)
        return real(self)

    plans = [_entry(tmp_path, n, touches=[f"{n}.py"]) for n in "abcd"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_FakeProc, "communicate", held)
        outs = queue_run.run([plans], tmp_path / "q", tmp_path, queue_run.QueueOptions(max_parallel=3))
    assert [o.name for o in outs] == list("abcd")
    assert fake.log[:3] == [("start", n) for n in "abc"]
    running, peak = 0, 0
    for event, _name in fake.log:
        running += 1 if event == "start" else -1
        peak = max(peak, running)
    assert peak == 3
    assert fake.log.index(("start", "d")) > min(fake.log.index(("read", n)) for n in "abc")


def test_run_starts_the_next_plan_when_one_finishes_without_waiting_for_the_rest(tmp_path, children):
    """空いたスロットへ次のプランを起動する（長引く 1 本が終わるのを待たない）。"""
    release = threading.Event()
    fake = children({n: _ok() for n in "abc"})
    real = _FakeProc.communicate

    def slow_a(self):
        if self.name == "a":
            assert release.wait(5)
        if self.name == "c":
            release.set()
        return real(self)

    plans = [_entry(tmp_path, n, touches=[f"{n}.py"]) for n in "abc"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_FakeProc, "communicate", slow_a)
        outs = queue_run.run([plans], tmp_path / "q", tmp_path, queue_run.QueueOptions(max_parallel=2))
    assert [o.status for o in outs] == ["ok", "ok", "ok"]
    assert ("start", "c") in fake.log


def test_resume_line_keeps_the_max_parallel(tmp_path):
    a = _entry(tmp_path, "a", touches=["a.py"])
    args = queue_run.again_args([[a]], tmp_path / "q", max_parallel=3)
    assert args[args.index("--max") + 1] == "3"


def test_run_puts_plans_needing_a_prior_stage_into_one_wave(tmp_path, children):
    fake = children({"a": _ok(), "b": _ok(), "c": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], needs=["a"])
    c = _entry(tmp_path, "c", touches=["c.py"], needs=["a"])
    queue_run.run([[a], [b, c]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert fake.log == [("start", "a"), ("read", "a"), ("start", "b"), ("start", "c"), ("read", "b"), ("read", "c")]


def test_run_does_not_launch_a_stage_after_a_stopped_stage(tmp_path, children):
    fake = children({"a": _stopped("check_failed"), "b": _ok()})
    a, b = _entry(tmp_path, "a", touches=["a.py"]), _entry(tmp_path, "b", touches=["b.py"])
    qdir = tmp_path / "q"
    outs = queue_run.run([[a], [b]], qdir, tmp_path, queue_run.QueueOptions())
    assert [(o.name, o.status, o.kind, o.step) for o in outs] == [("a", "stopped", "check_failed", "s"), ("b", None, None, None)]
    assert outs[1].detail == "前のステップのプランが完了でない: a（stopped）"
    assert ("start", "b") not in fake.log
    assert set(_queue_rows(qdir)) == {"a"}


def test_run_lets_a_gate_accepting_stage_run_after_a_gate(tmp_path, children):
    gate = {"status": "gate", "summary": "待つ", "items": [{"kind": "pr", "number": 7}], "routes": {"approve": None}}
    fake = children({"a": gate, "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], accepts_gate=True)
    outs = queue_run.run([[a], [b]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert [(o.name, o.status) for o in outs] == [("a", "gate"), ("b", "ok")]
    assert outs[0].routes == {"approve": None}
    assert ("start", "b") in fake.log


def test_run_stops_a_plan_whose_needs_did_not_finish(tmp_path, children):
    fake = children({"a": _stopped("check_failed"), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], needs=["a"])
    qdir = tmp_path / "q"
    outs = queue_run.run([[a, b]], qdir, tmp_path, queue_run.QueueOptions())
    assert [(o.name, o.status, o.kind) for o in outs] == [("a", "stopped", "check_failed"), ("b", "stopped", "precondition")]
    assert outs[1].detail == "前提のプランが完了でない: a（stopped）"
    assert ("start", "b") not in fake.log
    assert _queue_rows(qdir)["b"] == {"status": "stopped", "kind": "precondition", "prs": [], "release": False}


def test_run_fills_queue_prs_from_the_prior_stage(tmp_path, children):
    fake = children({"a": _ok(items=[{"kind": "pr", "number": 12}, {"kind": "pr", "number": 11}]), "r": _ok(), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    r = _entry(tmp_path, "r", touches=["r.py"], release=True)
    b = _entry(tmp_path, "b", touches=["b.py"], steps=[{"id": "s", "run": ["echo", "{queue_prs}"]}])
    outs = queue_run.run([[a, r], [b]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert [o.status for o in outs] == ["ok", "ok", "ok"]
    assert fake.args["b"][-2:] == ["--set", "queue_prs=11 12"]
    assert fake.args["a"] == ndf_command("plan", "next", str(tmp_path / "a.json"))
    assert fake.envs["a"] is None  # 環境は呼び出し元のまま（src を PYTHONPATH へ入れない。#4029）


def test_run_stops_a_queue_prs_plan_without_prs(tmp_path, children):
    fake = children({"a": _ok(), "b": _ok()})
    a = _entry(tmp_path, "a", touches=["a.py"])
    b = _entry(tmp_path, "b", touches=["b.py"], steps=[{"id": "s", "run": ["echo", "{queue_prs}"]}])
    outs = queue_run.run([[a], [b]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert (outs[1].status, outs[1].kind) == ("stopped", "input_missing")
    assert ("start", "b") not in fake.log


def test_run_reports_a_child_without_a_result_json(tmp_path, children):
    children({"a": ("not json\n", "boom\nlast line\n", 3), "b": ("", "", 5)})
    a, b = _entry(tmp_path, "a", touches=["a.py"]), _entry(tmp_path, "b", touches=["b.py"])
    outs = queue_run.run([[a, b]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
    assert [(o.status, o.kind) for o in outs] == [("stopped", "bad_report"), ("stopped", "bad_report")]
    assert outs[0].detail == "a が結果 JSON を返さなかった: last line"
    assert outs[1].detail == "b が結果 JSON を返さなかった: 終了コード 5"


def test_run_masks_secrets_in_the_stderr_of_a_child_without_a_result_json(tmp_path, children, monkeypatch):
    monkeypatch.setenv("NDF_TEST_TOKEN", "s3cr3t-value-123")
    children({"a": ("", "connect failed: s3cr3t-value-123\n", 3)})
    outs = queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], tmp_path / "q", tmp_path, queue_run.QueueOptions())
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
    outs = queue_run.run([[a, b, c]], qdir, tmp_path, queue_run.QueueOptions(resume=True))
    assert [(o.name, o.status) for o in outs] == [("a", "ok"), ("b", "ok"), ("c", "ok")]
    assert (outs[0].detail, outs[0].prs) == ("前の回で完了した", (4,))
    assert [n for kind, n in fake.log if kind == "start"] == ["b", "c"]
    assert fake.args["c"][-2:] == ["--from", "s"]


def test_run_without_resume_ignores_the_record(tmp_path, children):
    qdir = tmp_path / "q"
    _write_rows(qdir, {"a": {"status": "ok", "kind": None, "prs": [], "release": False}})
    fake = children({"a": _ok()})
    queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], qdir, tmp_path, queue_run.QueueOptions())
    assert fake.log == [("start", "a"), ("read", "a")]


def test_run_with_fresh_passes_fresh_to_each_child(tmp_path, children):
    """`queue --fresh` は子の `ndf plan next` へ `--fresh` を渡す。無ければ渡さない（#1592）。"""
    fake = children({"a": _ok()})
    queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], tmp_path / "q", tmp_path, queue_run.QueueOptions(fresh=True))
    assert fake.args["a"][-1] == "--fresh"
    queue_run.run([[_entry(tmp_path, "a", touches=["a.py"])]], tmp_path / "q2", tmp_path, queue_run.QueueOptions())
    assert "--fresh" not in fake.args["a"]
