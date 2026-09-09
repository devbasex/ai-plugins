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
def test_a_test_location_is_recognized(scope, tmp_path, path):
    assert scope.is_test_location(path, str(tmp_path)) is True


@pytest.mark.parametrize("path", [
    "src",
    "src/services",
    "plugins/ndf/scripts",
    "docs/latest.md",
])
def test_a_non_test_location_is_not_recognized(scope, tmp_path, path):
    assert scope.is_test_location(path, str(tmp_path)) is False


def test_the_judgement_does_not_need_the_directory_to_exist(scope, tmp_path):
    """`--scope` は範囲の宣言である。まだ無いディレクトリを指すことがある。"""
    assert scope.is_test_location("tests/not-created-yet", str(tmp_path)) is True


# ---------- `--baseline-test` の探索範囲 ----------

def test_a_command_without_paths_limits_nothing(scope, tmp_path):
    assert scope.baseline_search_roots("pytest -q", str(tmp_path)) == []


def test_only_directories_count_as_a_search_root(scope, tmp_path):
    """ファイルを指す語は実行するスクリプトそのものであることが多い。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run-tests.sh").write_text("", encoding="utf-8")
    roots = scope.baseline_search_roots(
        "bash scripts/run-tests.sh", str(tmp_path))
    assert roots == []


def test_directories_named_on_the_command_line_are_search_roots(scope, tmp_path):
    (tmp_path / "scripts" / "tests").mkdir(parents=True)
    (tmp_path / "plugins").mkdir()
    roots = scope.baseline_search_roots(
        "uv run --with pytest pytest scripts/tests plugins -q", str(tmp_path))
    assert roots == ["scripts/tests", "plugins"]


def test_an_unparsable_command_limits_nothing(scope, tmp_path):
    """語として読めないコマンドは、範囲の宣言として読まない。"""
    assert scope.baseline_search_roots('pytest "unclosed', str(tmp_path)) == []


def test_everything_is_covered_when_nothing_limits_the_search(scope):
    assert scope.covered_by_roots("tests/services", []) is True


def test_a_location_under_a_search_root_is_covered(scope):
    assert scope.covered_by_roots("tests/services", ["tests"]) is True


def test_a_location_outside_every_search_root_is_not_covered(scope):
    assert scope.covered_by_roots("tests", ["tests/unit"]) is False


def test_a_shared_prefix_is_not_enough(scope):
    """`tests-legacy` は `tests` の下ではない。"""
    assert scope.covered_by_roots("tests-legacy", ["tests"]) is False


# ---------- 関門 ----------

def test_a_scope_without_a_test_location_is_a_problem(scope, tmp_path):
    problem = scope.scope_problem(["src/services"], "pytest -q", str(tmp_path))
    assert problem is not None
    assert "--scope" in problem and "テストの置き場所" in problem


def test_a_scope_with_a_test_location_passes(scope, tmp_path):
    assert scope.scope_problem(
        ["src/services", "tests/services"], "pytest -q", str(tmp_path)) is None


def test_a_test_location_outside_the_baseline_search_is_a_problem(
    refactor, scope, tmp_path
):
    """足したテストが一度も実行されないと、検証（Step 5）の判定に効かない。"""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    problem = scope.scope_problem(
        ["src", "tests/services"], "pytest tests/unit", str(tmp_path))
    assert problem is not None
    assert "--baseline-test" in problem and "tests/services" in problem


def test_a_test_location_inside_the_baseline_search_passes(scope, tmp_path):
    (tmp_path / "tests" / "services").mkdir(parents=True)
    assert scope.scope_problem(
        ["src", "tests/services"], "pytest tests", str(tmp_path)) is None


def test_the_gate_stops_the_run(refactor_lib, refactor, scope, tmp_path):
    """**止める。** 案内だけでは同じ失敗を繰り返す（決定 5）。"""
    with pytest.raises(SystemExit) as e:
        scope.require_scope_covers_tests(["src"], "pytest -q", str(tmp_path))
    assert e.value.code == refactor_lib.ABORT


def test_the_gate_passes_a_valid_scope(scope, tmp_path):
    scope.require_scope_covers_tests(
        ["src", "tests"], "pytest -q", str(tmp_path))


# ---------- 実体としてテストの置き場所を持つ親（#518-2） ----------

def test_a_parent_holding_tests_is_recognized(scope, tmp_path):
    """名前で当たらなくても、配下に実体があれば置き場所として扱う。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.is_test_location(
        "skills/development-workflow", str(tmp_path)) is True


def test_a_parent_without_tests_is_still_not_recognized(scope, tmp_path):
    (tmp_path / "skills" / "development-workflow" / "docs").mkdir(parents=True)
    assert scope.is_test_location(
        "skills/development-workflow", str(tmp_path)) is False


def test_the_scan_does_not_go_deeper_than_one_level(scope, tmp_path):
    """深く潜ると、無関係な階層のテストを根拠にして関門が素通りする。"""
    (tmp_path / "src" / "a" / "b" / "tests").mkdir(parents=True)
    assert scope.is_test_location("src", str(tmp_path)) is False


def test_the_returned_location_is_the_one_that_matched(scope, tmp_path):
    """後段が見るのは置き場所そのものである。親のままでは必ず落ちる。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.test_locations(
        ["skills/development-workflow"], str(tmp_path),
    ) == ["skills/development-workflow/tests"]


def test_a_location_named_directly_is_returned_as_written(scope, tmp_path):
    assert scope.test_locations(["tests/services"], str(tmp_path)) == [
        "tests/services"]


def test_a_parent_holding_tests_passes_the_gate(scope, tmp_path):
    """#518-2 の実測。`tests/` を実体として持つ親を渡して止まらないこと。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.scope_problem(
        ["skills/development-workflow"],
        "pytest skills/development-workflow/tests",
        str(tmp_path),
    ) is None


def test_the_matched_location_is_checked_against_the_search_roots(scope, tmp_path):
    """返す値が親のままだと、通した直後に同じ関門の別の判定が拒む。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    problem = scope.scope_problem(
        ["skills/development-workflow"], "pytest other", str(tmp_path))
    assert problem is not None
    assert "skills/development-workflow/tests" in problem
