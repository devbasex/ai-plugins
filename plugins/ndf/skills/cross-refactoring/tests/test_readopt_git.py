"""採り直し（`readopt`、#1743 決定 10）と、持ち越し・巡ごとの取り込みを**実際の git**で確かめる。

| 条件 | 確かめること |
| --- | --- |
| I13 | 項目は期限を持たず、コミットの無い項目は持ち越す（`not_done` にしない） |
| I14 | 実装の予算の残り（実効の予算 − 採っている項目の見積りの和）に入る候補だけを順位の順に採り、入らない候補は飛ばす。LLM を呼ばない |
| I15 | 採るのは `budget` の見送りと持ち越しだけ。入らなければ持ち越しを `not_done` にして最終ゲートへ |
| 巡 | 2 巡目の取り込みは新しい起点から働き、前の巡の検証済みに触れない。取り消し済みの判定は巡ごと |
"""

from __future__ import annotations

import argparse

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
    state["phase"] = phase
    write_state(flow["path"], state)
    timeline = __import__("refactor_lib.timeline", fromlist=["of_state"])
    state["limits"] = timeline.of_state(state)
    if left_minutes is not None:
        _left(state, left_minutes)
    write_state(flow["path"], state)


def _left(state, minutes):
    """実装の予算を、着手済みの項目の見積りの和 + `minutes` にする（採り直しの残り = `minutes`。#2457 の I1）。"""
    budget = __import__("refactor_lib.budget", fromlist=["estimate_total"])
    used = sum(
        budget.estimate_total(i["estimate"])
        for i in state["items"]
        if i.get("status") not in ("planned", "carried", "reverted", "deferred")
    )
    state["budget"] = {"minutes": round(used + minutes, 6), "extension": None}


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


def test_a_carried_item_keeps_its_test_commit_when_readopted(flow, readopt):
    """持ち越しは項目のコミットを取り消さない（#2457）。採り直しでテストのコミットを外すと、公開の直前に未所属で止まる。"""
    work = flow["work"]
    (work / "tests" / "test_total.py").write_text("def test_total():\n    assert True\n", encoding="utf-8")
    test_sha = commit_with_trailers(work, "Add tests for total", item_trailers("I-001"))
    item = _entry("I-001", 1, "total", status="carried", tests=("tests/test_total.py::test_total",))
    item["commits"]["test"] = test_sha
    item["seconds"] = {"test": 12.0}
    _plan(flow, [item], phase="readopt")

    _call(readopt, "cmd_readopt")

    state = read_state(flow["path"])
    got = state["items"][0]
    assert got["status"] == "tested" and got["round"] == 2
    assert state["phase"] == "implement", "テストを持つ項目だけを採り直したら、テストの追加を起動しない"
    assert got["commits"] == {"test": test_sha, "implement": [], "fix": []} and got["seconds"] == {"test": 12.0}


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
    assert items["I-002"]["status"] == "implemented" and items["I-002"]["commits"]["implement"] == [second]
    assert items["I-001"]["status"] == "verified" and items["I-001"]["commits"]["implement"] == [first]
    assert git("merge-base", "--is-ancestor", first, "HEAD", cwd=work).returncode == 0


def test_no_fit_defers_the_carried_items_and_goes_to_the_final_gate(flow, cmd_setup, cmd_implement, readopt):
    """I15: 実装の予算の残りに入らなければ持ち越しを `not_done` で見送り、`phase` を `final` にして終了コード 2。"""
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
    """I14: 残り 3.5 分に 1.9・1.8・0.9 分の候補（順位の順）なら 1.9 と 0.9 を採り、1.8 を飛ばす。ほかの理由の見送りは採らない。"""
    done = _entry("I-001", 1, "total", status="verified")
    big, mid, small = (
        _entry(f"C-{n}", 0, s, minutes=m, tier=t) for n, s, m, t in ((1, "a", 2.1, "high"), (2, "b", 2.0, "medium"), (3, "c", 1.1, "low"))
    )
    ranked_out = _entry("C-4", 0, "d", minutes=0.5, tier="high")
    deferred = [_budget(c) for c in (big, mid, small)] + [{"item_id": "C-4", "defer_reason": "rank"}]
    _plan(flow, [done], [big, mid, small, ranked_out], deferred, left_minutes=3.5, phase="readopt")
    monkeypatch.setattr(cmd_plan, "_jev_boolean", lambda *a, **k: pytest.fail("採り直しは LLM を呼ばない"))

    _call(readopt, "cmd_readopt")

    state = read_state(flow["path"])
    added = [i for i in state["items"] if i["round"] == 2]
    assert [(i["id"], i["candidate_id"]) for i in added] == [("I-002", "C-1"), ("I-003", "C-3")]
    assert all(i["public_io_source"] == "runtime" and i["status"] == "planned" for i in added)
    assert _deferred(flow) == {"C-2": "budget", "C-4": "rank"}

    # 叩き直しは採り直しを重ねない
    _call(readopt, "cmd_readopt")
    assert len(read_state(flow["path"])["items"]) == 3

    # その巡の検証が終われば次を採る
    _set(flow, phase="readopt", items=[{**i, "status": "verified"} for i in read_state(flow["path"])["items"]])
    state = read_state(flow["path"])
    _left(state, 2.5)
    write_state(flow["path"], state)
    _call(readopt, "cmd_readopt")
    state = read_state(flow["path"])
    assert state["readopt"]["round"] == 3
    assert [i["candidate_id"] for i in state["items"] if i["round"] == 3] == ["C-2"]


def test_outside_changes_are_kept_per_round(flow, cmd_setup, cmd_implement, readopt):
    """巡・#2457 受け入れ条件 8: どの巡でも範囲の外の変更で取り消さず、その巡の項目の `outside_scope` に残す。"""
    work = flow["work"]
    _plan(flow, [_entry("I-001", 1, "total"), _entry("I-002", 2, "add")])
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    # 範囲の外の既存のファイルを項目と関係なく変える（新しいファイルは範囲の中として扱う。#1814）
    ignore = work / ".gitignore"
    ignore.write_text(ignore.read_text() + "build/\n")
    commit_with_trailers(work, "Out of scope", item_trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")
    _set(flow, phase="readopt")

    _call(readopt, "cmd_readopt")
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    ignore.write_text(ignore.read_text() + "dist/\n")
    commit_with_trailers(work, "Out of scope again", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")

    state = read_state(flow["path"])
    assert not state.get("drops")
    assert "build/" in ignore.read_text() and "dist/" in ignore.read_text()
    items = {i["id"]: i for i in state["items"]}
    assert items["I-001"]["status"] == items["I-002"]["status"] == "implemented"
    assert [r["path"] for r in items["I-002"]["outside_scope"]] == [".gitignore"]


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
