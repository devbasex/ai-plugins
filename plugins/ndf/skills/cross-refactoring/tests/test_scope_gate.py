"""`--scope` とテストの置き場所の関門（#436 決定 5 / 受け入れ条件 C3）。

**案内だけでは同じ失敗を繰り返す。** 実測では 4 ラウンド続けて同じ理由で項目が
落ちた。ここでは 2 つの条件（置き場所が範囲にあるか / その置き場所が
`--baseline-test` の実行集合に入るか）が**1 つの関門で**見られることを固定する。
"""
from __future__ import annotations

import pytest


# ---------- テストの置き場所の判定 ----------

@pytest.mark.parametrize("path", [
    "tests",
    "tests/services",
    "src/test",
    "spec/models",
    "app/__tests__",
    "src/services/test_bar.py",
    "src/services/bar_test.go",
    "web/app.spec.ts",
])
def test_a_test_location_is_recognized(refactor, path):
    assert refactor.is_test_location(path) is True


@pytest.mark.parametrize("path", [
    "src",
    "src/services",
    "plugins/ndf/scripts",
    "docs/latest.md",
])
def test_a_non_test_location_is_not_recognized(refactor, path):
    assert refactor.is_test_location(path) is False


def test_the_judgement_does_not_need_the_directory_to_exist(refactor):
    """`--scope` は範囲の宣言である。まだ無いディレクトリを指すことがある。"""
    assert refactor.is_test_location("tests/not-created-yet") is True


# ---------- `--baseline-test` の探索範囲 ----------

def test_a_command_without_paths_limits_nothing(refactor, tmp_path):
    assert refactor.baseline_search_roots("pytest -q", str(tmp_path)) == []


def test_only_directories_count_as_a_search_root(refactor, tmp_path):
    """ファイルを指す語は実行するスクリプトそのものであることが多い。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run-tests.sh").write_text("", encoding="utf-8")
    roots = refactor.baseline_search_roots(
        "bash scripts/run-tests.sh", str(tmp_path))
    assert roots == []


def test_directories_named_on_the_command_line_are_search_roots(refactor, tmp_path):
    (tmp_path / "scripts" / "tests").mkdir(parents=True)
    (tmp_path / "plugins").mkdir()
    roots = refactor.baseline_search_roots(
        "uv run --with pytest pytest scripts/tests plugins -q", str(tmp_path))
    assert roots == ["scripts/tests", "plugins"]


def test_an_unparsable_command_limits_nothing(refactor, tmp_path):
    """語として読めないコマンドは、範囲の宣言として読まない。"""
    assert refactor.baseline_search_roots('pytest "unclosed', str(tmp_path)) == []


def test_everything_is_covered_when_nothing_limits_the_search(refactor):
    assert refactor.covered_by_roots("tests/services", []) is True


def test_a_location_under_a_search_root_is_covered(refactor):
    assert refactor.covered_by_roots("tests/services", ["tests"]) is True


def test_a_location_outside_every_search_root_is_not_covered(refactor):
    assert refactor.covered_by_roots("tests", ["tests/unit"]) is False


def test_a_shared_prefix_is_not_enough(refactor):
    """`tests-legacy` は `tests` の下ではない。"""
    assert refactor.covered_by_roots("tests-legacy", ["tests"]) is False


# ---------- 関門 ----------

def test_a_scope_without_a_test_location_is_a_problem(refactor, tmp_path):
    problem = refactor.scope_problem(["src/services"], "pytest -q", str(tmp_path))
    assert problem is not None
    assert "--scope" in problem and "テストの置き場所" in problem


def test_a_scope_with_a_test_location_passes(refactor, tmp_path):
    assert refactor.scope_problem(
        ["src/services", "tests/services"], "pytest -q", str(tmp_path)) is None


def test_a_test_location_outside_the_baseline_search_is_a_problem(
    refactor, tmp_path
):
    """足したテストが一度も実行されないと、検証（Step 5）の判定に効かない。"""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    problem = refactor.scope_problem(
        ["src", "tests/services"], "pytest tests/unit", str(tmp_path))
    assert problem is not None
    assert "--baseline-test" in problem and "tests/services" in problem


def test_a_test_location_inside_the_baseline_search_passes(refactor, tmp_path):
    (tmp_path / "tests" / "services").mkdir(parents=True)
    assert refactor.scope_problem(
        ["src", "tests/services"], "pytest tests", str(tmp_path)) is None


def test_the_gate_stops_the_run(refactor, tmp_path):
    """**止める。** 案内だけでは同じ失敗を繰り返す（決定 5）。"""
    with pytest.raises(SystemExit) as e:
        refactor.require_scope_covers_tests(["src"], "pytest -q", str(tmp_path))
    assert e.value.code == refactor.ABORT


def test_the_gate_passes_a_valid_scope(refactor, tmp_path):
    refactor.require_scope_covers_tests(
        ["src", "tests"], "pytest -q", str(tmp_path))
