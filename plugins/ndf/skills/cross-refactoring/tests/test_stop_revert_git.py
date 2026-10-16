"""工程の 1 つとして起動したときの、最終ゲート修正の打ち切りの後の取り消し（#1669）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 案 A | 原因の項目だけを取り消し、落ちたテストが通った時点で止める。push して確かめ直し、最終ゲートが通る（AC1〜3・AC8） |
| 案 B | 案 A が締め切りで止まったら、リファクタリング計画の起点の木へ戻すコミットを 1 本積む（AC4） |
| 案 B の後 | 最終ゲートでも落ちたら終了コード 4 で止まり、取り消しを重ねない（AC5・I7） |
| 履歴 | 送ったコミットは前に送った HEAD の子孫である（AC7・C4） |
| 上限 | 打ち切りの後の取り消しの締め切りが、計画の上限の表に載る（AC6） |
| 同じ秒の書き換え | 案 A の最後の走らせ直しが直前の書き換えの秒の内に終わったら、案 B は着手前の木へ戻す前に次の秒まで待つ（#1806 決定 6） |
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

import pytest

from crossref_helpers import CALC, TEST_TOTAL, build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_state

FAR = "2099-01-01T00:00:00+00:00"
CALC_RAISES = CALC.replace("    return result\n", "    raise RuntimeError('calc broke')\n")


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    """公開は差し替えるが、本物の `push_head` と同じく公開した地点を台帳に残す（取り消しが公開済みのコミットを revert する）。"""
    built = build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)
    ledger = sys.modules["refactor_lib.ledger"]

    def push(state):
        head = git("rev-parse", "HEAD", cwd=state["worktrees"]["work"]).stdout.strip()
        built["pushed"].append(head)
        ledger.note_published(state, head)

    patch_lib("push_head", push)
    return built


def _item(item_id, rank, path):
    return {
        "id": item_id,
        "rank": rank,
        "path": path,
        "symbol": "add",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "public_io": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR,
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
    }


def _call(module, name):
    return getattr(module, name)(argparse.Namespace(id=130))


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _write(work, rel, text):
    (work / rel).parent.mkdir(parents=True, exist_ok=True)
    (work / rel).write_text(text, encoding="utf-8")


def _verified_run(flow, cmd_setup, cmd_implement, cmd_converge):
    """#1663 の形: 危険フラグの無い 2 項目が検証を通り、片方（I-002）が範囲テストの外の全体テストを落とす。"""
    work = flow["work"]
    _write(work, "tests/test_total.py", TEST_TOTAL)
    commit_with_trailers(work, "既存のテスト", {})
    state = read_state(flow["path"])
    state["baseline_test"]["head"] = _head(work)
    state["items"] = [_item("I-001", 1, "src/helper.py"), _item("I-002", 2, "src/calc.py")]
    state["plan"] = {"base_sha": _head(work), "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5}, "end_at": FAR}
    state["phase"] = "implement"
    write_state(flow["path"], state)
    cmd_setup.cmd_start_phase(argparse.Namespace(id=130, phase="implement"))
    _write(work, "src/helper.py", "def helper():\n    return 1\n")
    commit_with_trailers(work, "Refactor I-001", item_trailers("I-001"))
    _write(work, "src/calc.py", CALC_RAISES)
    commit_with_trailers(work, "Refactor I-002", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")
    _call(cmd_converge, "cmd_verify")
    state = read_state(flow["path"])
    assert [i["status"] for i in state["items"]] == ["verified", "verified"]
    assert state["whole_test"].get("ran") is not True, "危険フラグが立たず、検証の中では全体テストを走らせない"
    return state


def _cut_off(flow, minutes_ago):
    """最終ゲート修正を 1 度試みた後で、開始から `minutes_ago` 分たった状態にする。想定最大時間は 60 分、確かめの見込み v は
    6 秒、最終ゲート修正 1 回の見込み F は 300 秒（58 分前に始めたなら、確かめは入り、最終ゲート修正は入らない）。"""
    state = read_state(flow["path"])
    state["started_at"] = (_dt.datetime.now() - _dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    state["plan"]["reserve"]["final_fix"] = 5.0
    state.pop("limits", None)
    state["final_gate"].update({"fix_rounds": 1, "status": "failing"})
    write_state(flow["path"], state)


def _gate(cmd_gate, capsys):
    capsys.readouterr()
    try:
        cmd_gate.cmd_final_gate(argparse.Namespace(id=130))
        code = 0
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    gate = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith("FINAL_GATE=")]
    return code, gate[-1] if gate else None


def _descends(work, pushed):
    for older, newer in zip(pushed, pushed[1:]):
        assert git("merge-base", "--is-ancestor", older, newer, cwd=work).returncode == 0


def test_plan_a_reverts_only_the_culprit_and_the_rechecked_gate_passes(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys):
    """AC1〜3・AC7・AC8 — 原因の項目だけを取り消して push し、次の最終ゲートが通る。採用は残った項目の数で、未確認は出ない。"""
    work = flow["work"]
    _verified_run(flow, cmd_setup, cmd_implement, cmd_converge)
    _cut_off(flow, minutes_ago=58)

    code, verdict = _gate(cmd_gate, capsys)

    assert (code, verdict) == (2, "recheck")
    state = read_state(flow["path"])
    record = state["final_gate"]["stop_revert"]
    assert record["plan"] == "A" and record["reverted"] == ["I-002"] and record["end_at"]
    assert state["final_gate"]["culprit"]["culprits"] == ["I-002"]
    items = {i["id"]: i for i in state["items"]}
    assert items["I-002"]["status"] == "reverted" and "打ち切った後" in items["I-002"]["failure_reason"]
    assert items["I-001"]["status"] == "verified"
    assert flow["pushed"][-1] == _head(work)

    code, verdict = _gate(cmd_gate, capsys)

    assert (code, verdict) == (0, "passed")
    ledger = sys.modules["refactor_lib.ledger"]
    state = read_state(flow["path"])
    assert ledger.adoption_confirmed(state) and ledger.remaining_count(state) == 1
    _descends(work, flow["pushed"])


def test_every_child_limit_from_the_cut_off_to_the_recheck_ends_by_the_end(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys, patch_lib
):
    """#1932 AC2・I1 — 最終ゲートが落ち、最終ゲート修正が入らずに打ち切り、案 A で取り消して打ち直すまでに子プロセス（テスト・
    走らせ直し）へ渡す上限は、どれも渡した時刻 + 上限 ≤ H である。案 A の走らせ直しは H − v 以前に止まる。"""
    clock, process, timeline = (sys.modules[f"refactor_lib.{m}"] for m in ("clock", "process", "timeline"))
    real = process.run_with_timeout
    given: list = []

    def spy(command, cwd, timeout, *a, **k):
        state = read_state(flow["path"])
        given.append((clock.now() + _dt.timedelta(seconds=timeout), bool(state["final_gate"].get("stop_revert")), state))
        return real(command, cwd, timeout, *a, **k)

    _verified_run(flow, cmd_setup, cmd_implement, cmd_converge)
    _cut_off(flow, minutes_ago=58)
    patch_lib("run_with_timeout", spy)

    assert _gate(cmd_gate, capsys) == (2, "recheck")
    assert _gate(cmd_gate, capsys) == (0, "passed")

    assert any(reverting and state["final_gate"]["status"] != "recheck" for _, reverting, state in given)
    for until, reverting, state in given:
        assert until <= timeline.final_end(state)
        if reverting and state["final_gate"]["status"] != "recheck":
            assert until <= timeline.gate_start_end(state)


def test_plan_b_restores_the_planned_tree_when_plan_a_runs_out_of_time(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys):
    """AC4・AC7・#1932 I3 — 確かめの開始の締め切り（H − v）を過ぎていたら、確かめを始めずに計画の起点の木へ戻すコミットを
    1 本積む。次の最終ゲートは、HEAD の木が起点の木と同じなので確かめを省いて通す。"""
    work = flow["work"]
    state = _verified_run(flow, cmd_setup, cmd_implement, cmd_converge)
    base = state["plan"]["base_sha"]
    _cut_off(flow, minutes_ago=80)

    code, verdict = _gate(cmd_gate, capsys)

    assert (code, verdict) == (2, "recheck")
    state = read_state(flow["path"])
    record = state["final_gate"]["stop_revert"]
    assert record["plan"] == "B" and record["base_sha"] == base and record["commit"] == _head(work)
    assert record["fallback_reason"] == sys.modules["refactor_lib.stop_revert"].NO_ROOM
    assert state["final_gate"]["check_skipped"]["base_identity"] is False
    assert all(i["status"] == "reverted" for i in state["items"])
    assert git("diff", "--quiet", base, "HEAD", cwd=work).returncode == 0
    _descends(work, flow["pushed"])

    code, verdict = _gate(cmd_gate, capsys)
    assert (code, verdict) == (0, "passed")
    state = read_state(flow["path"])
    assert state["final_gate"]["status"] == "passed" and state["final_gate"]["check_skipped"]["base_identity"] is True
    assert not [c for c in state["final_gate"]["checks"] if c.get("at", "") > record["at"]], "確かめは走らせない"


def test_after_plan_b_a_failing_gate_stops_without_reverting_again(refactor, tmp_path):
    """AC5・I7 — 案 B の後の最終ゲートでも落ちたら終了コード 4 で止まる。"""
    stop_revert = sys.modules["refactor_lib.stop_revert"]
    gate = {"stop_revert": {"plan": "B"}}
    with pytest.raises(SystemExit) as e:
        stop_revert.revert_after_cutoff(tmp_path / "state.json", {}, gate)
    assert e.value.code == 4


def test_the_check_start_deadline_is_written_with_the_plan(refactor):
    """#1932 AC8 — 計画の後の上限の表に、確かめの開始の締め切り（H − v）と確かめの見込み v が載り、H + 0.20·B は載らない。"""
    timeline, plan = sys.modules["refactor_lib.timeline"], sys.modules["refactor_lib.plan"]
    start = _dt.datetime(2026, 10, 3, 0, 0, 0, tzinfo=_dt.timezone.utc)
    limits = timeline.compute(start, 30, 60, items=[], reserve={"fix": 1.0, "final_whole_test": 400 / 60})
    deadline = timeline.gate_start_end({"limits": limits})
    assert deadline == start + _dt.timedelta(seconds=1400) and limits["gate_seconds"] == 400
    lines = plan.limits_section(limits)
    assert any(deadline.isoformat() in line for line in lines)
    assert not any(timeline.compute(start, 36, 60)["final_end_at"] in line for line in lines)
    assert timeline.compute(start, 30, 60)["gate_seconds"] is None


def test_plan_a_is_skipped_after_the_check_start_deadline(refactor, tmp_path, monkeypatch):
    """#1932 I2 — 確かめの開始の締め切りを過ぎて打ち切ると、案 A の取り消しを 1 件も行わずに案 B を積む。"""
    stop_revert = sys.modules["refactor_lib.stop_revert"]
    past = _dt.datetime.now().astimezone() - _dt.timedelta(seconds=1)
    monkeypatch.setattr(stop_revert.timeline, "gate_start_end", lambda state: past)
    monkeypatch.setattr(stop_revert.culprit, "judge", lambda *a, **k: pytest.fail("案 A を始めた"))
    monkeypatch.setattr(stop_revert.statefile, "save", lambda path, state: None)
    seen: list = []
    monkeypatch.setattr(stop_revert, "_plan_b", lambda path, state, gate, why: seen.append(why))
    gate = {"triage": {"caused": ["t"], "rerun_commands": ["a"]}}
    assert stop_revert.revert_after_cutoff(tmp_path / "state.json", {}, gate) is True
    assert seen == [stop_revert.NO_ROOM] and gate["stop_revert"]["reverted"] == []


def test_plan_b_stops_without_discarding_uncommitted_changes(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys):
    """案 B は read-tree -u --reset で木を上書きするため、未コミットの変更があれば捨てずに終了コード 4 で止まる。"""
    work = flow["work"]
    _verified_run(flow, cmd_setup, cmd_implement, cmd_converge)
    head = _head(work)
    _cut_off(flow, minutes_ago=80)
    target = next(p for p in git("ls-files", cwd=work).stdout.split() if p.endswith(".py"))
    with open(f"{work}/{target}", "a", encoding="utf-8") as f:
        f.write("# 待機中に加えた変更\n")

    code, _ = _gate(cmd_gate, capsys)

    assert code == 4 and _head(work) == head
    assert "# 待機中に加えた変更" in open(f"{work}/{target}", encoding="utf-8").read()


def test_plan_a_stops_without_discarding_uncommitted_changes(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys):
    """案 A の取り消し（drop の reset --hard）も、未コミットの変更があれば捨てずに終了コード 4 で止まる。"""
    work = flow["work"]
    _verified_run(flow, cmd_setup, cmd_implement, cmd_converge)
    head = _head(work)
    _cut_off(flow, minutes_ago=61)
    target = next(p for p in git("ls-files", cwd=work).stdout.split() if p.endswith(".py"))
    with open(f"{work}/{target}", "a", encoding="utf-8") as f:
        f.write("# 待機中に加えた変更\n")

    code, _ = _gate(cmd_gate, capsys)

    assert code == 4 and _head(work) == head
    assert "# 待機中に加えた変更" in open(f"{work}/{target}", encoding="utf-8").read()


def test_plan_b_waits_for_the_next_second_before_restoring_the_planned_tree(refactor, tmp_path, monkeypatch):
    """#1806 決定 6 — 案 A の最後の走らせ直しが直前の書き換えの秒の内に終わったら、read-tree の前に次の秒まで待つ。"""
    stop_revert, worktree = sys.modules["refactor_lib.stop_revert"], sys.modules["refactor_lib.worktree"]
    now, events = [100.45], []

    def sleep(seconds):
        events.append(f"sleep {round(seconds, 3)}")
        now[0] += seconds

    monkeypatch.setattr(worktree, "_last_rewrite", 100.2)
    monkeypatch.setattr(worktree, "_clock", lambda: now[0])
    monkeypatch.setattr(worktree, "_sleep", sleep)
    monkeypatch.setattr(worktree, "stop_if_dirty", lambda state, work, what: None)
    monkeypatch.setattr(stop_revert.culprit, "git_ok", lambda work, args: events.append(" ".join(args[:2])) or True)
    monkeypatch.setattr(stop_revert, "git_out", lambda work, args: "f" * 40)
    state = {"worktrees": {"work": str(tmp_path)}, "plan": {"base_sha": "b" * 40}, "items": []}

    stop_revert._plan_b(tmp_path / "state.json", state, {}, "締め切りを過ぎた")

    assert events == ["sleep 0.55", "read-tree -u", "commit -q"]
    assert worktree._last_rewrite == 101.0
