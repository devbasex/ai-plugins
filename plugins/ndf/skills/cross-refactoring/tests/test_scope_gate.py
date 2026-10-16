"""`--scope` とテストの置き場所の関門（#436 決定 5 / #1334 決定 12・AC12）。

**案内だけでは同じ失敗を繰り返す。** 関門が見るのは「置き場所が範囲にあるか」の 1 つで、
コマンドの語から実行集合を読む判定は持たない（雛形の `{paths}` に置き場所が直に入る）。
"""

from __future__ import annotations

import subprocess

import pytest


# ---------- テストの置き場所の判定 ----------


@pytest.mark.parametrize(
    "path",
    [
        "tests",
        "tests/services",
        "src/test",
        "spec/models",
        "app/__tests__",
        "src/services/test_bar.py",
        "src/services/bar_test.go",
        "web/app.spec.ts",
    ],
)
def test_a_test_location_is_recognized(scope, tmp_path, path):
    assert scope.is_test_location(path, str(tmp_path)) is True


@pytest.mark.parametrize(
    "path",
    [
        "src",
        "src/services",
        "plugins/ndf/scripts",
        "docs/latest.md",
    ],
)
def test_a_non_test_location_is_not_recognized(scope, tmp_path, path):
    assert scope.is_test_location(path, str(tmp_path)) is False


def test_the_judgement_does_not_need_the_directory_to_exist(scope, tmp_path):
    """`--scope` は範囲の宣言である。まだ無いディレクトリを指すことがある。"""
    assert scope.is_test_location("tests/not-created-yet", str(tmp_path)) is True


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
    problem = scope.scope_problem(["src/services"], str(tmp_path))
    assert problem is not None
    assert "--scope" in problem and "テストの置き場所" in problem


def test_a_scope_with_a_test_location_passes(scope, tmp_path):
    assert scope.scope_problem(["src/services", "tests/services"], str(tmp_path)) is None


def test_the_gate_stops_the_run(refactor_lib, refactor, scope, tmp_path):
    """**止める。** 案内だけでは同じ失敗を繰り返す（決定 5）。"""
    with pytest.raises(SystemExit) as e:
        scope.require_scope_covers_tests(["src"], str(tmp_path))
    assert e.value.code == refactor_lib.ABORT


def test_the_gate_passes_a_valid_scope(scope, tmp_path):
    scope.require_scope_covers_tests(["src", "tests"], str(tmp_path))


# ---------- 実体としてテストの置き場所を持つ親（#518-2） ----------


def test_a_parent_holding_tests_is_recognized(scope, tmp_path):
    """名前で当たらなくても、配下に実体があれば置き場所として扱う。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.is_test_location("skills/development-workflow", str(tmp_path)) is True


def test_a_parent_without_tests_is_still_not_recognized(scope, tmp_path):
    (tmp_path / "skills" / "development-workflow" / "docs").mkdir(parents=True)
    assert scope.is_test_location("skills/development-workflow", str(tmp_path)) is False


def test_the_scan_does_not_go_deeper_than_one_level(scope, tmp_path):
    """深く潜ると、無関係な階層のテストを根拠にして関門が素通りする。"""
    (tmp_path / "src" / "a" / "b" / "tests").mkdir(parents=True)
    assert scope.is_test_location("src", str(tmp_path)) is False


def test_the_returned_location_is_the_one_that_matched(scope, tmp_path):
    """後段が見るのは置き場所そのものである。親のままでは必ず落ちる。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.test_locations(
        ["skills/development-workflow"],
        str(tmp_path),
    ) == ["skills/development-workflow/tests"]


def test_a_location_named_directly_is_returned_as_written(scope, tmp_path):
    assert scope.test_locations(["tests/services"], str(tmp_path)) == ["tests/services"]


def test_a_parent_holding_tests_passes_the_gate(scope, tmp_path):
    """#518-2 の実測。`tests/` を実体として持つ親を渡して止まらないこと。"""
    (tmp_path / "skills" / "development-workflow" / "tests").mkdir(parents=True)
    assert scope.scope_problem(["skills/development-workflow"], str(tmp_path)) is None


# ---------- テストを本体と同じ場所に置く構成（#1334 AC12・決定 12） ----------


def _git_repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_a_directory_holding_spec_files_is_a_test_location(scope, tmp_path):
    """`src/**/*.spec.ts` だけでテストのディレクトリが無い構成で、`--scope src` の関門が通る（AC12）。"""
    _git_repo(tmp_path, ["src/app.ts", "src/app.spec.ts", "src/lib/util.ts"])
    assert scope.is_test_location("src", str(tmp_path)) is True
    assert scope.test_locations(["src"], str(tmp_path)) == ["src"]
    assert scope.scope_problem(["src"], str(tmp_path)) is None
    assert scope.test_files_under("src", str(tmp_path)) == ["src/app.spec.ts"]


def test_a_directory_without_test_named_files_stays_outside(scope, tmp_path):
    """追跡ファイルの名前がテストの形でなければ置き場所に数えない（数えると関門が素通りする）。"""
    _git_repo(tmp_path, ["src/app.ts", "src/lib/util.ts"])
    assert scope.is_test_location("src", str(tmp_path)) is False
    assert scope.scope_problem(["src"], str(tmp_path)) is not None
