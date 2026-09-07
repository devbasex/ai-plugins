"""テストの変更の種類を見る（#443）。

**「テストを足したか」だけでは、期待値の変更を止められない。** 同じ入力に対する期待出力が
変わっていれば、それは振る舞いの変更である。

判定は 3 段で行う。ここで確かめるのは段 1（機械）と、段 2 へ渡す対象の切り出しである。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))


def test_the_same_assertions_are_seen_as_unchanged(verify) -> None:
    """接頭辞だけが変わった `assert` は、変わっていないものとして扱う。"""
    before = ["    assert refactor.build(x) == 3\n"]
    after = ["    assert gitfacts.build(x) == 3\n"]
    assert verify.assertion_change(before, after) == "unchanged"


def test_a_changed_expectation_is_detected(verify) -> None:
    """期待値が変わった `assert` を、変わったものとして扱う。"""
    before = ["    assert build(x) == 3\n"]
    after = ["    assert build(x) == 4\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_an_expectation_outside_the_assert_line_is_undecidable(verify) -> None:
    """期待値が `assert` の行の外にあるときは、判定できないものとして扱う。

    **通ったものとして扱わない。** 集合の一致は振る舞い不変の証明にならない。
    """
    before = ["    assert price(order) == (\n", "        270,\n", "    )\n"]
    after = ["    assert price(order) == (\n", "        300,\n", "    )\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_parametrised_value_is_undecidable(verify) -> None:
    """パラメータ化の値が変わったときも、判定できないものとして扱う。"""
    before = ['@pytest.mark.parametrize("n", [1, 2])\n', "def test_x(n):\n",
              "    assert f(n) > 0\n"]
    after = ['@pytest.mark.parametrize("n", [1, 3])\n', "def test_x(n):\n",
             "    assert f(n) > 0\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_adding_a_test_is_not_a_change_of_expectation(verify) -> None:
    """テストを足しただけのときは、変わっていないものとして扱う。"""
    before = ["    assert build(x) == 3\n"]
    after = ["    assert build(x) == 3\n", "    assert build(y) == 4\n"]
    assert verify.assertion_change(before, after) == "unchanged"


# ---------- 検証の経路へ組み込む ----------

def test_a_changed_expectation_fails_the_round(verify) -> None:
    """期待値が変わった差分は、適用ラウンドの検証で落ちること。"""
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
        {"tests/test_a.py": (["    assert f(1) == (\n", "        3,\n", "    )\n"],
                             ["    assert f(1) == (\n", "        4,\n", "    )\n"]),
         "tests/test_b.py": (["    assert g(1) == 3\n"], ["    assert g(1) == 3\n"])}
    )
    assert pending == ["tests/test_a.py"]


# ---------- 段 2 の起動 ----------

def test_the_launcher_accepts_the_judging_phase(tmp_path) -> None:
    """判定のフェーズが受け口にあること。"""
    import os
    import subprocess
    from crossref_helpers import make_state

    launch = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "launch-cli.sh"
    state_path = make_state(tmp_path)
    for name in ("work", "codex"):
        (state_path.parent.parent / name).mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    # 判定の対象は進行側が書き出す。**無ければ起動しない。**
    (state_path.parent / "test-diff-r1.diff").write_text("--- a\n+++ b\n", encoding="utf-8")
    proc = subprocess.run(
        [str(launch), "codex", "judge-test-changes", "130", "1"],
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    assert "未知のフェーズです" not in proc.stderr


def test_the_judging_phase_needs_the_diff(tmp_path) -> None:
    """判定の対象が無ければ起動しないこと。**渡すものが無いまま起動しない。**"""
    import os
    import subprocess
    from crossref_helpers import make_state

    launch = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "launch-cli.sh"
    state_path = make_state(tmp_path)
    proc = subprocess.run(
        [str(launch), "codex", "judge-test-changes", "130", "1"],
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent)},
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "判定する差分がありません" in proc.stderr


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
    assert "リポジトリを編集しない" in prompt


# ---------- 判定の穴（レビューの指摘） ----------

def test_a_duplicated_assertion_that_changes_is_detected(verify) -> None:
    """同じ `assert` が複数あるとき、その 1 つが変わったことを見落とさない。"""
    before = ["    assert f(1) == 3\n", "    assert f(1) == 3\n"]
    after = ["    assert f(1) == 3\n", "    assert f(1) == 4\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_a_removed_duplicate_is_detected(verify) -> None:
    """同じ `assert` の一部が消えたことを見落とさない。"""
    before = ["    assert f(1) == 3\n", "    assert f(1) == 3\n"]
    after = ["    assert f(1) == 3\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_a_diff_without_assertions_is_undecidable(verify) -> None:
    """`assert` を含まない差分は、判定できないものとして扱う。

    フィクスチャや定数の変更は期待値を動かしうるが、`assert` の行には現れない。
    """
    before = ["EXPECTED = 3\n"]
    after = ["EXPECTED = 4\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_dot_inside_a_string_is_kept(verify) -> None:
    """文字列の中のドットを、取り込み元の接頭辞として伏せない。"""
    before = ['    assert path == "file.txt"\n']
    after = ['    assert path == "other.txt"\n']
    assert verify.assertion_change(before, after) == "changed"


# ---------- 検証への配線（レビューの指摘） ----------

def test_the_facts_carry_the_test_diff(gitfacts) -> None:
    """git から取る事実に、テストの差分が含まれること。

    **含まれないと、検証はテストの期待値を見られない。**
    """
    assert hasattr(gitfacts, "commit_test_changes")


def test_the_round_verification_rejects_a_changed_expectation(verify) -> None:
    """適用ラウンドの検証が、期待値の変更を落とすこと。

    **新設した関数を呼ばなければ、手順書だけが「機械が見る」と書いた状態になる。**
    """
    items = [{"item_id": "R1-001", "technique": "extract_method",
              "estimated_diff_lines": 100, "path": "src/a.py"}]
    facts = [{
        "sha": "a" * 40, "exists": True, "diff_lines": 10, "files": ["src/a.py"],
        "trailers": {"Item-Id": "R1-001", "Round": "1",
                     "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
        "test_status": "pass", "touches_tests": True,
        "test_changes": {"tests/test_a.py": (["    assert f(1) == 3\n"],
                                             ["    assert f(1) == 4\n"])},
    }]
    problem = verify.verify_apply_round(items, facts)
    assert problem is not None
    assert "期待" in problem


def test_the_round_verification_reports_undecidable_diffs(verify) -> None:
    """判定できない差分を、検証の結果として持ち出せること。

    **落とさないが、通ったものとしても扱わない。** 進行側がこれを段 2 へ渡す。
    """
    facts = [{
        "test_changes": {"tests/test_a.py": (["    assert f(1) == (\n", "        3,\n"],
                                             ["    assert f(1) == (\n", "        4,\n"])},
    }]
    assert verify.pending_test_judgements(facts) == ["tests/test_a.py"]


# ---------- 判定を安全側へ倒す（レビューの指摘） ----------

def test_a_direct_import_is_undecidable(verify) -> None:
    """取り込み方を変えて呼び出しの形が変わった差分を、落とさないこと。

    **`assert` 行の不一致だけでは、期待出力が変わったとは決まらない。**
    `oldmod.f(1)` を `f(1)` へ変えただけでも行は一致しなくなる。
    """
    before = ["    assert oldmod.f(1) == 3\n"]
    after = ["    assert f(1) == 3\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_renamed_local_is_undecidable(verify) -> None:
    """局所の名前を変えた差分も、落とさずに段 2 へ回すこと。"""
    before = ["    assert build(order) == 3\n"]
    after = ["    assert build(o) == 3\n"]
    assert verify.assertion_change(before, after) == "undecidable"


def test_a_changed_literal_is_still_detected(verify) -> None:
    """**値だけが変わった差分は、機械で落とす。** 安全側へ倒しすぎない。"""
    before = ["    assert build(order) == 3\n"]
    after = ["    assert build(order) == 4\n"]
    assert verify.assertion_change(before, after) == "changed"


def test_a_changed_string_is_still_detected(verify) -> None:
    """文字列の期待値が変わった差分も、機械で落とすこと。"""
    before = ['    assert path == "file.txt"\n']
    after = ['    assert path == "other.txt"\n']
    assert verify.assertion_change(before, after) == "changed"


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


def test_the_merge_command_clears_or_fails(
    paths, patch_lib, refactor, tmp_path, env_tmp_dir, monkeypatch
) -> None:
    """取り込みのサブコマンドが、保留を解くか落とすこと。"""
    import json
    from crossref_helpers import make_state, read_state

    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None}, "reviewer_models": {},
        "proposed": {}, "items": [], "apply": {"applied": [], "failed": []},
        "fix_rounds": 0, "durations": {}, "reviews": [],
        "pending_test_judgements": ["tests/test_a.py"],
    }])
    env_tmp_dir(state_path)
    (state_path.parent / "codex-judge-test-changes-r1-result.json").write_text(
        json.dumps({"verdicts": [{"path": "tests/test_a.py", "verdict": "unchanged",
                                  "reason": "経路だけ"}]}), encoding="utf-8")

    refactor.cmd_merge_test_judgements(
        type("A", (), {"id": 130, "round": 1})())

    entry = read_state(state_path)["rounds"][0]
    assert entry.get("pending_test_judgements", []) == []
