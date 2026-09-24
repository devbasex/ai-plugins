"""テストの変更の種類を見る（#443）。

**「テストを足したか」だけでは、期待値の変更を止められない。** 同じ入力に対する期待出力が
変わっていれば、それは振る舞いの変更である。

判定は 3 段で行う。ここで確かめるのは段 1（機械）と、段 2 へ渡す対象の切り出しである。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))


def test_an_identical_file_is_unchanged(verify) -> None:
    """前後が同じなら、変わっていないものとして扱う。"""
    rows = ["    assert build(x) == 3\n"]
    assert verify.assertion_change(rows, list(rows)) == "unchanged"


def test_a_lost_value_is_changed(verify) -> None:
    """値が失われた差分は、期待出力が変わったものとして扱う。"""
    before = ["    assert build(x) == 3\n"]
    after = ["    assert build(x) == 4\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_a_lost_string_is_changed(verify) -> None:
    """文字列の期待値が失われた差分も、同じに扱う。"""
    before = ['    assert path == "file.txt"\n']
    after = ['    assert path == "other.txt"\n']
    assert verify.assertion_change(before, after) == "changed"


def test_a_path_only_change_is_undecidable(verify) -> None:
    """読み込みの経路だけが変わった差分も、機械では決めない。

    **`unchanged` は「同じ」のときだけ返す。** 経路の変更が期待出力へ影響しないことを、
    機械では確かめられない。段 2 の AI が読む。
    """
    before = ["    assert refactor.build(x) == 3\n"]
    after = ["    assert gitfacts.build(x) == 3\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_an_attribute_change_is_not_hidden(verify) -> None:
    """属性の参照が変わった差分を、接頭辞として伏せないこと。

    `Status.SUCCESS.value` → `Status.FAILURE.value` は期待出力が変わりうる。
    """
    before = ["    assert state == Status.SUCCESS.value\n"]
    after = ["    assert state == Status.FAILURE.value\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_an_added_constant_is_undecidable(verify) -> None:
    """既存を残したまま値を足した差分も、機械では決めない。

    `EXPECTED = 4` を足すと、どちらが使われるかは行の並びでは決まらない。
    """
    before = ["EXPECTED = 3\n", "    assert f(1) == EXPECTED\n"]
    after = ["EXPECTED = 3\n", "EXPECTED = 4\n", "    assert f(1) == EXPECTED\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_changed_constant_is_changed(verify) -> None:
    """`assert` の外の値が失われた差分は、機械で落とす。"""
    before = ["EXPECTED = 3\n", "    assert f(1) == EXPECTED\n"]
    after = ["EXPECTED = 4\n", "    assert f(1) == EXPECTED\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_extracting_a_literal_keeps_the_value(verify) -> None:
    """値を定数へ抽出しただけなら、値は失われない。"""
    before = ["    assert f(1) == 3\n"]
    after = ["EXPECTED = 3\n", "    assert f(1) == EXPECTED\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_adding_a_test_is_undecidable(verify) -> None:
    """テストを足した差分も、機械では決めない。"""
    before = ["    assert build(x) == 3\n"]
    after = ["    assert build(x) == 3\n", "    assert build(y) == 4\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_multiline_expectation_is_undecidable(verify) -> None:
    """期待値が `assert` の行の外にある差分も、決めない。"""
    before = ["    assert price(order) == (\n", "        270,\n", "    )\n"]
    after = ["    assert price(order) == (\n", "        300,\n", "    )\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_a_parametrised_value_is_changed(verify) -> None:
    """パラメータ化の値が失われた差分は、機械で落とす。"""
    before = ['@pytest.mark.parametrize("n", [1, 2])\n', "    assert f(n) > 0\n"]
    after = ['@pytest.mark.parametrize("n", [1, 3])\n', "    assert f(n) > 0\n"]
    assert verify.assertion_change(before, after) == "changed"


# ---------- 検証の経路へ組み込む ----------

def test_a_changed_expectation_fails_the_intake(verify) -> None:
    """期待値が変わった差分は、取り込みの検査で落ちること。"""
    problem = verify.verify_test_changes(
        {"tests/test_a.py": (["    assert f(1) == 3\n"], ["    assert f(1) == 4\n"])}
    )
    assert problem is not None
    assert "期待" in problem


def test_a_path_only_change_passes(verify) -> None:
    """読み込みの経路だけが変わった差分は通ること。"""
    assert verify.verify_test_changes(
        {"tests/test_a.py": (["    assert refactor.f(1) == 3\n"],
                             ["    assert gitfacts.f(1) == 3\n"])}
    ) is None


def test_undecidable_diffs_are_collected_for_the_next_stage(verify) -> None:
    """判定できない差分は、落とさずに次の段へ渡す対象として集めること。

    **通ったものとして扱わない。** 戻り値に残ることで、呼ぶ側が段 2 を起動できる。
    """
    pending = verify.undecidable_test_changes(
        {"tests/test_a.py": (["    assert refactor.f(1) == 3\n"],
                             ["    assert gitfacts.f(1) == 3\n"]),
         "tests/test_b.py": (["    assert g(1) == 3\n"], ["    assert g(1) == 3\n"])}
    )
    assert pending == ["tests/test_a.py"]


# ---------- 段 2 の起動 ----------

def _launch_judge(tmp_path, with_diff: bool):
    import os
    import subprocess
    from crossref_helpers import make_state_v2

    launch = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "launch-cli.sh"
    for name in ("work", "codex"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_path = make_state_v2(tmp_path, tmp_path / "work", implementer="codex")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    if with_diff:
        # 判定の対象は進行側（`merge-implement`）が書き出す。**無ければ起動しない。**
        (state_path.parent / "test-diff-rf130.diff").write_text("--- a\n+++ b\n", encoding="utf-8")
    proc = subprocess.run(
        [str(launch), "codex", "judge-test-changes", "130"],
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    return proc, state_path


def test_the_launcher_accepts_the_judging_phase(tmp_path) -> None:
    """判定のフェーズが受け口にあり、差分の置き場所をプロンプトへ渡すこと。"""
    proc, state_path = _launch_judge(tmp_path, with_diff=True)
    assert proc.returncode == 0, proc.stderr
    prompt = (state_path.parent / "codex-judge-test-changes-rf130-prompt.md").read_text(
        encoding="utf-8")
    assert str(state_path.parent / "test-diff-rf130.diff") in prompt


def test_the_judging_phase_needs_the_diff(tmp_path) -> None:
    """判定の対象が無ければ起動しないこと。**渡すものが無いまま起動しない。**"""
    proc, state_path = _launch_judge(tmp_path, with_diff=False)
    assert proc.returncode != 0
    assert "判定する差分がありません" in proc.stderr
    assert not (state_path.parent / "codex-judge-test-changes-rf130-prompt.md").exists()


def test_the_judging_prompt_points_at_the_diff_file() -> None:
    """プロンプトが、対象の差分の位置と結果の書き出し先を指すこと。"""
    prompt = (pathlib.Path(__file__).resolve().parents[1]
              / "prompts" / "judge-test-changes.md").read_text(encoding="utf-8")
    assert "$RF_TEST_DIFF_PATH" in prompt
    assert "$RF_STEM-result.json" in prompt


def test_the_judging_prompt_asks_for_three_verdicts() -> None:
    """判定の答えが 3 択であること。**2 択だと迷ったものが片方へ倒れる。**"""
    prompt = (pathlib.Path(__file__).resolve().parents[1]
              / "prompts" / "judge-test-changes.md").read_text(encoding="utf-8")
    for verdict in ("unchanged", "changed", "undecidable"):
        assert verdict in prompt


# ---------- 検証への配線（レビューの指摘） ----------

def test_the_facts_carry_the_test_diff(gitfacts) -> None:
    """git から取る事実に、テストの差分が含まれること。

    **含まれないと、検証はテストの期待値を見られない。**
    """
    assert hasattr(gitfacts, "commit_test_changes")


def test_the_implement_intake_rejects_a_changed_expectation(cmd_implement, tmp_path) -> None:
    """実装の取り込みの検査が、期待値の変更を落とすこと。

    **新設した関数を呼ばなければ、手順書だけが「機械が見る」と書いた状態になる。**
    """
    item = {"id": "I-001", "technique": "extract_method", "estimated_diff_lines": 100,
            "path": "src/a.py"}
    facts = [{
        "sha": "a" * 40, "exists": True, "diff_lines": 10, "files": ["src/a.py"],
        "trailers": {"Item-Id": "I-001", "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
        "test_status": "pass", "touches_tests": True,
        "test_changes": {"tests/test_a.py": (["    assert f(1) == 3\n"],
                                             ["    assert f(1) == 4\n"])},
    }]
    problem = cmd_implement._implement_problem(
        item, facts, [], [], {"worktrees": {"work": str(tmp_path)}})
    assert problem is not None
    assert "期待" in problem


def test_the_fix_intake_rejects_a_changed_expectation(cmd_converge) -> None:
    """修正の取り込みの検査も、同じ基準で期待値の変更を落とすこと。"""
    facts = [{
        "sha": "b" * 40, "exists": True, "diff_lines": 4, "files": ["tests/test_a.py"],
        "trailers": {"Item-Id": "I-001", "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
        "test_changes": {"tests/test_a.py": (["    assert f(1) == 3\n"],
                                             ["    assert f(1) == 4\n"])},
    }]
    problems = cmd_converge._fix_problems({"target_scope": ["tests"]}, facts, {"I-001"})
    assert len(problems) == 1 and "期待" in problems[0]


def test_the_round_verification_reports_undecidable_diffs(verify) -> None:
    """判定できない差分を、検証の結果として持ち出せること。

    **落とさないが、通ったものとしても扱わない。** 進行側がこれを段 2 へ渡す。
    """
    facts = [{
        "test_changes": {"tests/test_a.py": (["    assert refactor.f(1) == 3\n"],
                                             ["    assert gitfacts.f(1) == 3\n"])},
    }]
    assert verify.pending_test_judgements(facts) == ["tests/test_a.py"]


# ---------- 判定結果の取り込み（レビューの指摘） ----------

def test_all_unchanged_clears_the_pending_record(verify) -> None:
    """全件が `unchanged` なら、保留の記録が消えること。"""
    verdicts = [{"path": "tests/test_a.py", "verdict": "unchanged", "reason": "経路だけ"}]
    outcome = verify.merge_test_judgements(["tests/test_a.py"], verdicts)
    assert outcome["pending"] == []
    assert outcome["problem"] is None


def test_a_changed_verdict_fails_the_round(verify) -> None:
    """1 件でも `changed` があれば、適用ラウンドを落とすこと。"""
    verdicts = [{"path": "tests/test_a.py", "verdict": "changed", "reason": "270 が 300 に"}]
    outcome = verify.merge_test_judgements(["tests/test_a.py"], verdicts)
    assert outcome["problem"] is not None
    assert "期待" in outcome["problem"]


def test_an_undecidable_verdict_is_carried_to_the_review(verify) -> None:
    """`undecidable` は保留のまま残し、レビューへ引き継ぐこと。"""
    verdicts = [{"path": "tests/test_a.py", "verdict": "undecidable", "reason": "追えない"}]
    outcome = verify.merge_test_judgements(["tests/test_a.py"], verdicts)
    assert outcome["pending"] == ["tests/test_a.py"]
    assert outcome["problem"] is None


def test_a_missing_verdict_is_treated_as_undecidable(verify) -> None:
    """答えが欠けたものは、判定できないものとして扱うこと。

    **通ったものとして扱わない。** 抜けを `unchanged` に倒すと、判定を返さない
    ことが通過の手段になる。
    """
    outcome = verify.merge_test_judgements(["tests/test_a.py", "tests/test_b.py"],
                                           [{"path": "tests/test_a.py",
                                             "verdict": "unchanged", "reason": "経路だけ"}])
    assert outcome["pending"] == ["tests/test_b.py"]


def test_an_unknown_verdict_is_treated_as_undecidable(verify) -> None:
    """知らない答えも、判定できないものとして扱うこと。"""
    outcome = verify.merge_test_judgements(
        ["tests/test_a.py"], [{"path": "tests/test_a.py", "verdict": "maybe"}])
    assert outcome["pending"] == ["tests/test_a.py"]


# ---------- 保留の持ち方と取り込み（項目ごと。実装計画 I7） ----------

@pytest.fixture
def judged(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir, cmd_setup, cmd_implement):
    """実装で経路だけを変えた項目（段 2 待ち）と、テストに触れない項目の 2 件を取り込んだ状態。"""
    import argparse

    from crossref_helpers import (build_git_flow, commit_with_trailers, git, item_trailers,
                                  read_state, write_state)

    flow = build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)
    work = flow["work"]

    def _item(item_id, rank, symbol):
        return {"id": item_id, "rank": rank, "path": "src/calc.py", "symbol": symbol,
                "smell": "long_method", "technique": "extract_method", "severity": "major",
                "proposed_by": ["codex"], "tier": "high", "risk": False, "tests": [],
                "test_targets": ["tests/test_calc.py"],
                "command": ["pytest", "-q", "tests/test_calc.py"], "command_source": "targets",
                "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
                "start_deadline": "2099-01-01T00:00:00+00:00", "test_start_deadline": None,
                "status": "planned", "commits": {"test": None, "implement": None, "fix": []},
                "seconds": {}, "fix_count": 0, "danger": [], "estimated_diff_lines": 20}

    state = read_state(flow["path"])
    state["items"] = [_item("I-001", 1, "add"), _item("I-002", 2, "total")]
    state["plan"] = {"base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
                     "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
                     "end_at": "2099-01-01T00:00:00+00:00", "table_source": "defaults"}
    state["phase"] = "implement"
    write_state(flow["path"], state)
    cmd_setup.cmd_start_phase(argparse.Namespace(id=130, phase="implement"))
    test_file = work / "tests" / "test_calc.py"
    test_file.write_text(test_file.read_text().replace(
        "from src.calc import add", "from src import calc").replace("add(1, 2)", "calc.add(1, 2)"),
        encoding="utf-8")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    (work / "src" / "other.py").write_text("X = 1\n", encoding="utf-8")
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    cmd_implement.cmd_merge_implement(argparse.Namespace(id=130))
    return flow


def _verdicts(flow, verdicts):
    import json

    (flow["path"].parent / "claude-judge-test-changes-rf130-result.json").write_text(
        json.dumps({"verdicts": verdicts}), encoding="utf-8")


def _items(flow):
    from crossref_helpers import read_state

    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def test_the_implement_intake_records_pending_per_item_and_writes_the_diff(judged) -> None:
    """保留は項目ごとに持つ。判定の材料（項目ごとのテストの差分）を書き出す。"""
    items = _items(judged)
    assert items["I-001"]["pending_test_judgements"] == ["tests/test_calc.py"]
    assert "pending_test_judgements" not in items["I-002"]
    diff = (judged["path"].parent / "test-diff-rf130.diff").read_text(encoding="utf-8")
    assert "# I-001" in diff and "tests/test_calc.py" in diff
    assert "# I-002" not in diff


def test_the_merge_command_clears_an_unchanged_verdict(judged, cmd_converge) -> None:
    import argparse

    _verdicts(judged, [{"path": "tests/test_calc.py", "verdict": "unchanged", "reason": "経路だけ"}])
    cmd_converge.cmd_merge_test_judgements(argparse.Namespace(id=130))
    items = _items(judged)
    assert "pending_test_judgements" not in items["I-001"]
    assert "review_test_judgements" not in items["I-001"]
    assert items["I-001"]["status"] == "implemented"


def test_the_merge_command_carries_a_missing_verdict_to_the_review(judged, cmd_converge) -> None:
    """答えが欠けたものは `unchanged` に倒さず、レビューへ引き継ぐ。"""
    import argparse

    _verdicts(judged, [])
    cmd_converge.cmd_merge_test_judgements(argparse.Namespace(id=130))
    items = _items(judged)
    assert items["I-001"]["review_test_judgements"] == ["tests/test_calc.py"]
    assert items["I-001"]["status"] == "implemented"


def test_the_merge_command_drops_only_the_item_with_a_changed_verdict(judged, cmd_converge) -> None:
    """`changed` の項目だけを取り消し、他の項目のコミットは残す。"""
    import argparse

    _verdicts(judged, [{"path": "tests/test_calc.py", "verdict": "changed", "reason": "3 が 4 に"}])
    cmd_converge.cmd_merge_test_judgements(argparse.Namespace(id=130))
    items = _items(judged)
    assert items["I-001"]["status"] == "reverted"
    assert "期待" in items["I-001"]["failure_reason"]
    assert items["I-002"]["status"] == "implemented"
    work = judged["work"]
    assert "from src.calc import add" in (work / "tests" / "test_calc.py").read_text()
    assert (work / "src" / "other.py").exists()


def test_judgements_are_read_per_item(cmd_converge, tmp_path, env_tmp_dir):
    """同じファイルを保留にした 2 項目では、`changed` はその `item_id` の項目だけに効く。"""
    import argparse

    from crossref_helpers import make_state_v2, read_state, write_result

    items = [
        {"id": f"I-00{n}", "rank": n, "status": "implemented", "path": "src/a.py",
         "pending_test_judgements": ["tests/test_x.py"],
         "commits": {"test": None, "implement": None, "fix": []}}
        for n in (1, 2)
    ]
    path = make_state_v2(tmp_path, tmp_path / "work", items=items)
    env_tmp_dir(path)
    write_result(path, "claude-judge-test-changes-rf130", {"verdicts": [
        {"item_id": "I-001", "path": "tests/test_x.py", "verdict": "unchanged"},
        {"item_id": "I-002", "path": "tests/test_x.py", "verdict": "changed"},
    ]})
    cmd_converge.cmd_merge_test_judgements(argparse.Namespace(id=130))
    status = {i["id"]: i["status"] for i in read_state(path)["items"]}
    assert status == {"I-001": "implemented", "I-002": "reverted"}
