"""フレーキーなテストの直し（#2457 決定 9・受け入れ条件 11・I14）を**実際の git** で確かめる。

| 確かめること |
| --- |
| 見分けたテストの直しは `Flaky-Fix` のコミットで残り、オーケストレーターが残すコミットとして台帳に載る。項目には結ばない |
| テスト以外のファイルを変えた直しは 1 回だけ差し戻し、それでも直らなければ打ち消す。項目には触れない |
| 直しのコミットが無ければ、報告に残して終える |
| 同じテストの直しは 1 回の実行で 1 回だけ依頼する |
"""

from __future__ import annotations

import argparse

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state, write_state

TEST_ID = "tests/test_clock.py::tests.test_clock::test_now"


def _setup(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir()
    git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    (work / "src" / "calc.py").write_text("X = 1\n", encoding="utf-8")
    (work / "tests" / "test_clock.py").write_text("def test_now():\n    assert True\n", encoding="utf-8")
    commit_with_trailers(work, "init", {})
    (work / "src" / "calc.py").write_text("X = 2\n", encoding="utf-8")
    impl = commit_with_trailers(work, "Refactor I-001", item_trailers("I-001"))
    item = {
        "id": "I-001",
        "rank": 1,
        "path": "src/calc.py",
        "status": "verified",
        "commits": {"test": None, "implement": [impl], "fix": []},
    }
    path = make_state_v2(tmp_path, work, items=[item], phase="verify")
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(path.parent))
    state = read_state(path)
    state["flaky_request"] = {
        "tests": [TEST_ID],
        "log": "x.log",
        "commands": ["pytest tests/test_clock.py"],
        "sendback": None,
        "attempts": 0,
    }
    state["flaky_requested"] = [TEST_ID]
    state.setdefault("phases", {})["flaky-fix"] = {"base_sha": impl}
    write_state(path, state)
    return work, path, impl


def _merge(cmd_flaky, capsys, state_id=130):
    capsys.readouterr()
    cmd_flaky.cmd_merge_flaky_fix(argparse.Namespace(id=state_id))
    out = capsys.readouterr().out
    return [line.split("=", 1)[1] for line in out.splitlines() if line.startswith("FLAKY_FIX=")][-1]


def test_a_flaky_fix_is_kept_as_its_own_commit_and_not_tied_to_an_item(tmp_path, monkeypatch, cmd_flaky, capsys):
    work, path, impl = _setup(tmp_path, monkeypatch)
    (work / "tests" / "test_clock.py").write_text("def test_now():\n    assert 1 == 1\n", encoding="utf-8")
    fix = commit_with_trailers(work, "fix flaky", {"Flaky-Fix": TEST_ID})

    assert _merge(cmd_flaky, capsys) == "kept"

    state = read_state(path)
    assert state["flaky_fixes"] == [{"test_id": TEST_ID, "commit": fix, "by": "claude", "result": "kept"}]
    assert fix in state["ledger"]["orchestrator_commits"]
    assert state["items"][0]["commits"]["implement"] == [impl] and state["items"][0]["status"] == "verified"
    assert state["flaky_request"] is None


def test_a_fix_outside_the_tests_is_sent_back_once_then_reverted(tmp_path, monkeypatch, cmd_flaky, capsys):
    work, path, _ = _setup(tmp_path, monkeypatch)
    (work / "src" / "calc.py").write_text("X = 3\n", encoding="utf-8")
    commit_with_trailers(work, "fix flaky in src", {"Flaky-Fix": TEST_ID})

    assert _merge(cmd_flaky, capsys) == "sendback"
    assert read_state(path)["flaky_request"]["sendback"][0]["kind"] == "non_test"

    assert _merge(cmd_flaky, capsys) == "reverted"
    state = read_state(path)
    assert (work / "src" / "calc.py").read_text(encoding="utf-8") == "X = 2\n", "直しのコミットだけを打ち消す"
    assert state["flaky_fixes"][0]["result"] == "reverted"
    assert state["items"][0]["status"] == "verified", "項目には触れない"
    assert git("status", "--porcelain", cwd=work).stdout.strip() == ""


def test_no_fix_commit_is_recorded_and_closes_the_request(tmp_path, monkeypatch, cmd_flaky, capsys):
    _, path, _ = _setup(tmp_path, monkeypatch)

    assert _merge(cmd_flaky, capsys) == "none"

    state = read_state(path)
    assert state["flaky_fixes"] == [{"test_id": TEST_ID, "commit": None, "by": "claude", "result": "none"}]
    assert state["flaky_request"] is None


def test_the_same_test_is_requested_only_once_per_run(tmp_path, monkeypatch, cmd_flaky):
    _, path, _ = _setup(tmp_path, monkeypatch)
    state = read_state(path)
    state["flaky_request"] = None
    state["strategy"] = state.get("strategy")

    assert cmd_flaky.request(state, [TEST_ID], "x.log") == [], "依頼済みのテストは積まない"
    assert state["flaky_request"] is None
