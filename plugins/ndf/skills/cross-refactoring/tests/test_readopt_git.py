"""採り直し（`readopt`、#1743 決定 10）と、持ち越し・巡ごとの取り込みを**実際の git**で確かめる。

| 条件 | 確かめること |
| --- | --- |
| I13 | 項目は期限を持たず、コミットの無い項目は持ち越す（`not_done` にしない） |
| I14 | 残った時間に入る候補だけを順位の順に採り、入らない候補は飛ばす。LLM を呼ばない |
| I15 | 採るのは `budget` の見送りと持ち越しだけ。入らなければ持ち越しを `not_done` にして最終ゲートへ |
| 巡 | 2 巡目の取り込みは新しい起点から働き、前の巡の検証済みに触れない。取り消し済みの判定は巡ごと |
"""

from __future__ import annotations

import argparse
import datetime as dt

import pytest

from crossref_helpers import TEST_TOTAL, build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_state

ID = 130
RESERVE = {"danger_whole_test": 0.0, "final_whole_test": 0.1, "fix": 0.1, "final_fix": 0.1}
SCOPE = [{"suite": "pytest", "kind": "test", "command": "pytest -q tests/test_calc.py"}]


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _entry(item_id, rank, symbol, *, minutes=1.5, tier="high", tests=(), status="planned"):
    """改善項目か改善候補（計画の時点の見積り・範囲テスト・`risk` を持つ）。"""
    targets = list(tests) or ["tests/test_calc.py"]
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": symbol,
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": tier,
        "risk": False,
        "tests": list(tests),
        "test_targets": targets,
        "scope_commands": [{**SCOPE[0], "command": f"pytest -q {' '.join(targets)}"}],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": round(minutes - 0.2, 2), "verify": 0.2},
        "round": 1,
        "status": status,
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
    }


def _plan(flow, items, candidates=(), deferred=(), left_minutes=None, phase="implement"):
    state = read_state(flow["path"])
    state["items"] = list(items)
    state["candidates"] = list(candidates)
    state["deferred_items"] = list(deferred)
    state["plan"] = {"base_sha": git("rev-parse", "HEAD", cwd=flow["work"]).stdout.strip(), "reserve": RESERVE, "table_source": "defaults"}
    state["readopt"] = {"round": 1, "events": []}
    state["pause"] = {"seconds": 0, "shifted_seconds": 0, "legacy": False, "events": []}
    state["phase"] = phase
    write_state(flow["path"], state)
    timeline = __import__("refactor_lib.timeline", fromlist=["of_state"])
    state["limits"] = timeline.of_state(state)
    if left_minutes is not None:
        # 残った時間 = 打ち切り − 今 − バッファ（0.3 分）
        now = dt.datetime.now().astimezone()
        state["limits"]["final_end_at"] = (now + dt.timedelta(minutes=left_minutes + 0.3)).isoformat(timespec="seconds")
    write_state(flow["path"], state)


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=ID, **kwargs))


def _exit_code(module, name):
    with pytest.raises(SystemExit) as e:
        _call(module, name)
    return e.value.code


def _set(flow, **values):
    state = read_state(flow["path"])
    state.update(values)
    write_state(flow["path"], state)


def _deferred(flow):
    return {d.get("item_id"): d["defer_reason"] for d in read_state(flow["path"])["deferred_items"]}


def _budget(candidate):
    return {"item_id": candidate["id"], "defer_reason": "budget", "path": candidate["path"], "symbol": candidate["symbol"]}


@pytest.fixture
def readopt(refactor):
    return __import__("refactor_lib.commands.readopt", fromlist=["cmd_readopt"])


def test_all_items_carried_reach_the_readopt(flow, cmd_setup, cmd_implement, readopt):
    """I15: 全項目にコミットが無くても見送らずに持ち越し、検証を飛ばして採り直しへ届く。入れば次の巡で採り直す。"""
    _plan(flow, [_entry("I-001", 1, "total"), _entry("I-002", 2, "add")])
    _call(cmd_setup, "cmd_start_phase", phase="implement")

    assert _exit_code(cmd_implement, "cmd_merge_implement") == 2
    state = read_state(flow["path"])
    assert state["phase"] == "readopt"
    assert {i["status"] for i in state["items"]} == {"carried"}
    assert _deferred(flow) == {}

    _call(readopt, "cmd_readopt")
    state = read_state(flow["path"])
    assert [(i["status"], i["round"]) for i in state["items"]] == [("planned", 2), ("planned", 2)]
    assert state["readopt"]["round"] == 2 and state["phase"] == "implement"
    assert [e["name"] for e in state["phases_history"]] == ["implement"]


def test_the_second_round_takes_in_from_a_new_base(flow, cmd_setup, cmd_implement, readopt):
    """巡: 1 巡目の取り込みの後に採り直した項目は、2 巡目の新しい起点からのコミットで取り込まれる。"""
    work = flow["work"]
    _plan(flow, [_entry("I-001", 1, "total"), _entry("I-002", 2, "add")])
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "src" / "calc.py").write_text((work / "src" / "calc.py").read_text().replace("result = 0", "result = 0  # total"))
    first = commit_with_trailers(work, "Refactor total", item_trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")
    state = read_state(flow["path"])
    state["items"][0]["status"] = "verified"  # 1 巡目の検証を終えた
    state["phase"] = "readopt"
    write_state(flow["path"], state)

    _call(readopt, "cmd_readopt")
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "src" / "calc.py").write_text((work / "src" / "calc.py").read_text().replace("return a + b", "return b + a"))
    second = commit_with_trailers(work, "Refactor add", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")

    items = {i["id"]: i for i in read_state(flow["path"])["items"]}
    assert items["I-002"]["status"] == "implemented" and items["I-002"]["commits"]["implement"] == second
    assert items["I-001"]["status"] == "verified" and items["I-001"]["commits"]["implement"] == first
    assert git("merge-base", "--is-ancestor", first, "HEAD", cwd=work).returncode == 0


def test_no_fit_defers_the_carried_items_and_goes_to_the_final_gate(flow, cmd_setup, cmd_implement, readopt):
    """I15: 残った時間に入らなければ持ち越しを `not_done` で見送り、`phase` を `final` にして終了コード 2。"""
    _plan(flow, [_entry("I-001", 1, "total")], left_minutes=0.5)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    assert _exit_code(cmd_implement, "cmd_merge_implement") == 2

    assert _exit_code(readopt, "cmd_readopt") == 2
    state = read_state(flow["path"])
    assert state["phase"] == "final"
    assert state["items"][0]["status"] == "deferred"
    assert _deferred(flow) == {"I-001": "not_done"}
    assert state["readopt"]["events"][-1]["reason"] == "no_fit"
    # 叩き直しても記録を返す
    assert _exit_code(readopt, "cmd_readopt") == 2
    assert len(read_state(flow["path"])["readopt"]["events"]) == 1


def test_the_readopt_takes_budget_candidates_in_rank_order_without_an_llm(flow, readopt, cmd_plan, monkeypatch):
    """I14: 残った時間 3.8 分に 2.1・2.0・1.1 分の候補（順位の順）なら 2.1 と 1.1 を採り、2.0 を飛ばす。ほかの理由の見送りは採らない。"""
    done = _entry("I-001", 1, "total", status="verified")
    big, mid, small = (_entry(f"C-{n}", 0, s, minutes=m, tier=t) for n, s, m, t in ((1, "a", 2.1, "high"), (2, "b", 2.0, "medium"), (3, "c", 1.1, "low")))
    ranked_out = _entry("C-4", 0, "d", minutes=0.5, tier="high")
    deferred = [_budget(c) for c in (big, mid, small)] + [{"item_id": "C-4", "defer_reason": "rank"}]
    _plan(flow, [done], [big, mid, small, ranked_out], deferred, left_minutes=3.8, phase="readopt")
    monkeypatch.setattr(cmd_plan, "_jev_boolean", lambda *a, **k: pytest.fail("採り直しは LLM を呼ばない"))

    _call(readopt, "cmd_readopt")

    state = read_state(flow["path"])
    added = [i for i in state["items"] if i["round"] == 2]
    assert [(i["id"], i["candidate_id"]) for i in added] == [("I-002", "C-1"), ("I-003", "C-3")]
    assert all(i["public_io_source"] == "runtime" and i["status"] == "planned" for i in added)
    assert _deferred(flow) == {"C-2": "budget", "C-4": "rank"}
    assert state["limits"]["implement_end_at"] < state["limits"]["final_end_at"]

    # 叩き直しは採り直しを重ねない
    _call(readopt, "cmd_readopt")
    assert len(read_state(flow["path"])["items"]) == 3

    # その巡の検証が終われば次を採る
    _set(flow, phase="readopt", items=[{**i, "status": "verified"} for i in read_state(flow["path"])["items"]])
    state = read_state(flow["path"])
    state["limits"]["final_end_at"] = (dt.datetime.now().astimezone() + dt.timedelta(minutes=2.5)).isoformat(timespec="seconds")
    write_state(flow["path"], state)
    _call(readopt, "cmd_readopt")
    state = read_state(flow["path"])
    assert state["readopt"]["round"] == 3
    assert [i["candidate_id"] for i in state["items"] if i["round"] == 3] == ["C-2"]


def test_drops_are_judged_per_round(flow, cmd_setup, cmd_implement, readopt):
    """巡: 1 巡目に取り消しがあっても、2 巡目の手順違反は取り消され、`drops[]` に巡 2 の記録が増える。"""
    work = flow["work"]
    _plan(flow, [_entry("I-001", 1, "total"), _entry("I-002", 2, "add")])
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "outside").mkdir()
    (work / "outside" / "x.py").write_text("X = 1\n")
    commit_with_trailers(work, "Out of scope", item_trailers("I-001"))
    assert _exit_code(cmd_implement, "cmd_merge_implement") == 2  # 残る項目 0 件（I-002 は持ち越し）
    assert [d.get("round") for d in read_state(flow["path"])["drops"]] == [1]
    _set(flow, phase="readopt")

    _call(readopt, "cmd_readopt")
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "outside").mkdir(exist_ok=True)  # 1 巡目の取り消しで消えている
    (work / "outside" / "y.py").write_text("Y = 1\n")
    commit_with_trailers(work, "Out of scope again", item_trailers("I-002"))
    assert _exit_code(cmd_implement, "cmd_merge_implement") == 2

    state = read_state(flow["path"])
    assert [d.get("round") for d in state["drops"]] == [1, 2]
    assert not (work / "outside" / "y.py").exists()
    assert {i["id"]: i["status"] for i in state["items"]}["I-002"] == "reverted"


def test_round_two_tests_leave_verified_items_alone(flow, cmd_setup, cmd_implement, readopt):
    """巡: 2 巡目のテストの取り込みは今の巡の `planned` だけを見て、前の巡の検証済みとそのコミットに触れない。"""
    work = flow["work"]
    _plan(flow, [_entry("I-001", 1, "total", tests=["tests/test_calc.py"])], [], [], phase="implement")
    state = read_state(flow["path"])
    state["items"][0]["status"] = "tested"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "src" / "calc.py").write_text((work / "src" / "calc.py").read_text().replace("result = 0", "result = 0  # total"))
    first = commit_with_trailers(work, "Refactor total", item_trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")
    candidate = _entry("C-9", 0, "add", tests=["tests/test_total.py"])
    state = read_state(flow["path"])
    state["items"][0]["status"] = "verified"
    state.update(phase="readopt", candidates=[candidate], deferred_items=[_budget(candidate)])
    write_state(flow["path"], state)

    _call(readopt, "cmd_readopt")
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    (work / "tests" / "test_total.py").write_text(TEST_TOTAL)
    commit_with_trailers(work, "Test", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_tests")

    items = {i["id"]: i for i in read_state(flow["path"])["items"]}
    assert items["I-002"]["status"] == "tested"
    assert items["I-001"]["status"] == "verified"
    assert git("merge-base", "--is-ancestor", first, "HEAD", cwd=work).returncode == 0
