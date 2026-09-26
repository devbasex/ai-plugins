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


def test_an_option_value_and_the_work_root_are_not_search_roots(scope, tmp_path):
    """`--project <ディレクトリ>` はオプションの値で、`.` は作業ディレクトリの根である。

    検査のプランが渡す形（`uv run --project <dir> ... pytest . -q -n 4`）で、全体を
    走らせるコマンドを範囲の限定と読むと、関門がすべての置き場所を範囲の外として止める。
    """
    (tmp_path / "tools" / "kit").mkdir(parents=True)
    command = ("uv run --project tools/kit --with pytest --with pytest-xdist"
               " pytest . -q -n 4")
    assert scope.baseline_search_roots(command, str(tmp_path)) == []
    _services(tmp_path)
    assert scope.scope_problem(["tests/services"], command, str(tmp_path)) is None


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


# ---------- `--round-test` の実行集合（#880 の AC4） ----------

def _services(tmp_path):
    (tmp_path / "tests" / "services").mkdir(parents=True)
    (tmp_path / "tests" / "services" / "test_one.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run-scope-tests.sh").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "run_tests.py").write_text("", encoding="utf-8")


def test_a_test_file_is_a_round_test_root(scope, tmp_path):
    _services(tmp_path)
    assert scope.round_test_roots(
        "pytest tests/services/test_one.py", str(tmp_path)) == [
        "tests/services/test_one.py"]


@pytest.mark.parametrize("command", [
    "pytest tests/services/test_one.py::test_a",
    "pytest tests/services/test_one.py::TestA::test_b -q",
])
def test_a_node_id_counts_its_file_as_a_round_test_root(scope, tmp_path, command):
    """ノード ID はファイルの部分を起点に数える。数えないと起点が空になり、全体を覆うとみなす。"""
    _services(tmp_path)
    assert scope.round_test_roots(command, str(tmp_path)) == [
        "tests/services/test_one.py"]


def test_a_node_id_narrower_than_the_scope_stops(refactor_lib, scope, tmp_path):
    """ラウンドのテストの置き場所の一部しか走らせないノード ID は関門で止める。"""
    _services(tmp_path)
    problem = scope.scope_problem(
        ["src", "tests/services"], "pytest tests/services/test_one.py::test_a",
        str(tmp_path), round_test=True)
    assert problem is not None and "--round-test" in problem


def test_an_option_value_and_the_work_root_are_not_round_test_roots(scope, tmp_path):
    """`--project .` の `.` はオプションの値で、作業ディレクトリの根でもある。"""
    _services(tmp_path)
    assert scope.round_test_roots(
        "uv run --project . pytest tests/services/test_one.py", str(tmp_path)) == [
        "tests/services/test_one.py"]
    assert scope.round_test_roots("pytest . -q", str(tmp_path)) == []


@pytest.mark.parametrize("command", [
    "bash scripts/run-scope-tests.sh", "python scripts/run_tests.py",
])
def test_a_wrapper_script_is_not_a_round_test_root(scope, tmp_path, command):
    _services(tmp_path)
    assert scope.round_test_roots(command, str(tmp_path)) == []


@pytest.mark.parametrize("command, expected", [
    ("pytest --verbose tests/services", ["tests/services"]),
    ("pytest -q --tb short tests/services/test_one.py", ["tests/services/test_one.py"]),
    ("uv run --project scripts --with pytest pytest --lf tests/services", ["tests/services"]),
    ("pytest --rootdir scripts -p no:cacheprovider tests/services", ["tests/services"]),
])
def test_a_flag_without_a_value_keeps_the_next_round_test_root(scope, tmp_path, command, expected):
    """値を取らないオプションの直後の対象を、オプションの値として消さない。

    消すと起点が空になり、全体を覆うとみなして範囲の外だけを走らせるコマンドが関門を通る。
    値を取ると分かっているオプション（`--project` / `--rootdir` / `-p`）の直後だけを除く。
    """
    _services(tmp_path)
    assert scope.round_test_roots(command, str(tmp_path)) == expected


def test_a_flag_before_an_outside_target_stops(refactor_lib, scope, tmp_path):
    """真偽のオプションの直後の範囲外の対象だけを走らせるコマンドは止める。"""
    _services(tmp_path)
    problem = scope.scope_problem(
        ["src", "tests/services"], "pytest --verbose scripts", str(tmp_path),
        round_test=True)
    assert problem is not None and "--round-test" in problem


def test_a_directory_is_a_round_test_root(scope, tmp_path):
    _services(tmp_path)
    assert scope.round_test_roots(
        "uv run --with pytest pytest tests/services -q", str(tmp_path)) == [
        "tests/services"]


@pytest.mark.parametrize("command", [
    "pytest tests/services/test_one.py",
    "uv run --project . pytest tests/services/test_one.py",
    "pytest scripts",
])
def test_a_round_test_narrower_than_the_scope_tests_stops(refactor_lib, scope, tmp_path, command):
    """AC4 — 置き場所それぞれについて、起点のどれかが同じか祖先でなければ止める。"""
    _services(tmp_path)
    problem = scope.scope_problem(
        ["src", "tests/services"], command, str(tmp_path), round_test=True)
    assert problem is not None and "--round-test" in problem
    with pytest.raises(SystemExit) as e:
        scope.require_scope_covers_tests(
            ["src", "tests/services"], command, str(tmp_path), round_test=True)
    assert e.value.code == refactor_lib.ABORT


@pytest.mark.parametrize("command", [
    "bash scripts/run-scope-tests.sh",
    "pytest -q",
    "pytest tests/services",
    "pytest tests",
])
def test_a_round_test_covering_the_scope_tests_passes(scope, tmp_path, command):
    """起点の無いコマンドは全体を覆うとみなす。"""
    _services(tmp_path)
    assert scope.scope_problem(
        ["src", "tests/services"], command, str(tmp_path), round_test=True) is None


# ---------- `--round-test` の案内（#880 の AC5） ----------

@pytest.mark.parametrize("baseline, expected", [
    ("pytest -q", True),
    ("pytest tests", True),
    ("pytest tests/services", False),
])
def test_the_round_test_hint(scope, tmp_path, baseline, expected):
    """起点が無いか範囲より広いときだけ、1 行の案内を返す。"""
    _services(tmp_path)
    hint = scope.round_test_hint(
        None, baseline, ["src", "tests/services"], str(tmp_path))
    assert (hint is not None) is expected
    if expected:
        assert "--round-test" in hint and "\n" not in hint


def test_no_round_test_hint_when_the_round_test_is_given(scope, tmp_path):
    _services(tmp_path)
    assert scope.round_test_hint(
        "pytest tests/services", "pytest -q", ["src", "tests/services"],
        str(tmp_path)) is None
