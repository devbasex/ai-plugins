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
    proc = subprocess.run(
        [str(launch), "codex", "judge-test-changes", "130", "1"],
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    assert "未知のフェーズです" not in proc.stderr


def test_the_judging_prompt_asks_for_three_verdicts() -> None:
    """判定の答えが 3 択であること。**2 択だと迷ったものが片方へ倒れる。**"""
    prompt = (pathlib.Path(__file__).resolve().parents[1]
              / "prompts" / "judge-test-changes.md").read_text(encoding="utf-8")
    for verdict in ("unchanged", "changed", "undecidable"):
        assert verdict in prompt
    assert "リポジトリを編集しない" in prompt
