"""範囲テストの語の並びの組み立て（#933 I8）。"""
from __future__ import annotations

import importlib
import shlex

import pytest


@pytest.fixture(scope="module")
def testcmd(refactor):
    return importlib.import_module("refactor_lib.testcmd")


REPO_COMMAND = ("uv run --project plugins/playwright-kit/skills/playwright-kit-ops "
                "--with pytest pytest plugins/ndf/skills/cross-refactoring -q")


@pytest.fixture
def work(tmp_path):
    for rel in ("src/pkg/a.py", "tests/unit/test_a.py", "tests/unit/test_b.py",
                "scripts/run-scope-tests.sh", "backend/Makefile"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("command, expected", [
    ("pytest -q", 0),
    ("python -m pytest tests", 2),
    ("python3 -m pytest", 2),
    ("npx jest", 1),
    ("npx --yes vitest run", 2),
    ("poetry run pytest", 2),
    ("uv run --with=pytest pytest", 3),
    (REPO_COMMAND, 6),
])
def test_runner_index_known(testcmd, command, expected):
    assert testcmd.runner_index(shlex.split(command)) == expected


@pytest.mark.parametrize("command", [
    "make -C backend test", "cargo test", "bash scripts/run-scope-tests.sh",
    "npm --prefix backend test", "go test ./...", "uv run --project pytest", "",
    "pytest 'unclosed",
])
def test_unknown_runners(testcmd, command):
    assert not testcmd.is_known(command)


@pytest.fixture
def repo_layout(tmp_path):
    """このリポジトリの手順が指す 2 つのディレクトリだけを持つ根。

    本物の根は使わない。導入先へ写した Skill から走らせても同じ結果になるようにする（#975）。
    """
    for rel in ("plugins/playwright-kit/skills/playwright-kit-ops",
                "plugins/ndf/skills/cross-refactoring"):
        (tmp_path / rel).mkdir(parents=True)
    return tmp_path


def test_repo_round_test_command_reads_one_target(testcmd, repo_layout):
    """このリポジトリの手順が使う形を、そのまま通す。"""
    words = shlex.split(REPO_COMMAND)
    idx = testcmd.runner_index(words)
    assert words[idx] == "pytest"
    # --project の値・--with の値（pytest）は対象でない。対象は Skill のディレクトリ 1 つ
    assert testcmd.target_indices(words, idx, str(repo_layout)) == [7]
    target = "plugins/ndf/skills/cross-refactoring/tests/test_budget.py::test_reserve_matches_the_917_example"
    assert testcmd.build(REPO_COMMAND, [target], str(repo_layout)) == [
        "uv", "run", "--project", "plugins/playwright-kit/skills/playwright-kit-ops",
        "--with", "pytest", "pytest", target, "-q"]


def test_build_replaces_or_appends(testcmd, work):
    w = str(work)
    assert testcmd.build("pytest tests -q -k slow", ["tests/unit/test_a.py"], w) == [
        "pytest", "tests/unit/test_a.py", "-q", "-k", "slow"]
    assert testcmd.build("pytest -q tests/unit/test_a.py tests/unit/test_b.py", ["tests/unit"], w) == [
        "pytest", "-q", "tests/unit"]
    # 対象の語が無ければ末尾へ足す。-k の値・`.`・絶対パスは対象でない
    assert testcmd.build("pytest -k tests . /abs -q", ["tests/unit/test_a.py"], w) == [
        "pytest", "-k", "tests", ".", "/abs", "-q", "tests/unit/test_a.py"]
    assert testcmd.build("make -C backend test", ["tests/unit/test_a.py"], w) is None


def test_valid_targets(testcmd, work):
    w, scope = str(work), ["src", "tests"]
    assert testcmd.valid_targets(["tests/unit/test_a.py::TestA::test_x", "tests/unit"], w, scope)
    for bad in ([], ["tests/unit/test_a.py;rm"], ["tests/unit/test_a.py $(x)"],
                ["tests/unit/missing.py"], ["src/pkg/a.py"], ["/tmp/tests"],
                ["../tests/unit/test_a.py"], ["tests/unit/test_a.py\n"]):
        assert not testcmd.valid_targets(bad, w, scope), bad
    # --scope にテストの置き場所が無ければ使えない
    assert not testcmd.valid_targets(["tests/unit/test_a.py"], w, ["src"])


def test_limited_command_sources(testcmd, work):
    w = str(work)
    base = {"target_scope": ["src", "tests"],
            "baseline_test": {"command": "pytest -q"},
            "round_test": {"command": "pytest tests/unit -q"}}
    assert testcmd.limited_command(base, ["tests/unit/test_a.py"], w) == (
        ["pytest", "tests/unit/test_a.py", "-q"], "targets")
    # 対象が不正なら --round-test をそのまま使う
    assert testcmd.limited_command(base, ["nope.py"], w) == (
        ["pytest", "tests/unit", "-q"], "round_test")
    # --round-test が既知でなくても、そのまま使う
    wrapped = dict(base, round_test={"command": "bash scripts/run-scope-tests.sh"})
    assert testcmd.limited_command(wrapped, ["tests/unit/test_a.py"], w) == (
        ["bash", "scripts/run-scope-tests.sh"], "round_test")
    # --round-test が無ければ --baseline-test を元に組み立てる
    no_round = dict(base, round_test=None)
    assert testcmd.limited_command(no_round, ["tests/unit/test_a.py"], w) == (
        ["pytest", "-q", "tests/unit/test_a.py"], "targets")
    # 組み立てられず --round-test も無ければ、--baseline-test をそのまま使わない
    assert testcmd.limited_command(no_round, [], w) == (None, "none")
    unknown = dict(no_round, baseline_test={"command": "make test"})
    assert testcmd.limited_command(unknown, ["tests/unit/test_a.py"], w) == (None, "none")


def test_a_test_the_item_adds_may_be_a_target_before_it_exists(testcmd, work):
    """I11: 計画の時点では足すテストがまだ無い。その項目の `tests` にあれば対象に使える。"""
    w, scope = str(work), ["src", "tests"]
    assert not testcmd.valid_targets(["tests/unit/test_new.py"], w, scope)
    assert testcmd.valid_targets(["tests/unit/test_new.py::test_x"], w, scope,
                                 planned=["tests/unit/test_new.py"])
    # 足すテストでも範囲の外・シェルの文字は通さない
    assert not testcmd.valid_targets(["src/new_test.py"], w, scope, planned=["src/new_test.py"])
    assert not testcmd.valid_targets(["tests/unit/t;x.py"], w, scope, planned=["tests/unit/t;x.py"])
    base = {"target_scope": scope, "baseline_test": {"command": "pytest -q"}, "round_test": None}
    assert testcmd.limited_command(base, ["tests/unit/test_new.py"], w,
                                   ["tests/unit/test_new.py"]) == (
        ["pytest", "-q", "tests/unit/test_new.py"], "targets")


# ---------- 全体のテストで落ちたテストの取り出しと走らせ直し（#933 I14） ----------

# pytest 9 の `-q` の末尾の要約（実際に走らせて写した形）。xdist の有無・収集の失敗・
# 値に ` - ` を含むパラメータの 3 つを含む。
PYTEST_SUMMARY = """\
=========================== short test summary info ============================
FAILED tests/test_a.py::test_p[a b] - AssertionError: assert 'a b' == 'x'
FAILED tests/test_a.py::test_bad - assert 1 == 2
FAILED tests/test_a.py::test_p[c - d] - AssertionError: assert 'c - d' == 'x'
ERROR tests/test_b.py - ImportError while importing test module '/tmp/claude-...
ERROR tests/test_a.py::test_err - RuntimeError: x
ERROR tests/test_c.py
3 failed, 1 passed, 2 errors in 0.20s
"""


def test_failed_nodes_reads_the_pytest_summary(testcmd):
    assert testcmd.failed_nodes(PYTEST_SUMMARY) == [
        "tests/test_a.py::test_p[a b]",
        "tests/test_a.py::test_bad",
        "tests/test_a.py::test_p[c - d]",
        "tests/test_b.py",
        "tests/test_a.py::test_err",
        "tests/test_c.py",
    ]


def test_failed_nodes_ignores_other_error_lines(testcmd):
    """`ERROR: file or directory not found` は落ちたテストではない。"""
    assert testcmd.failed_nodes("ERROR: file or directory not found: x\n\n") == []


@pytest.mark.parametrize("command, expected", [
    ("pytest -q tests", ["pytest", "-q", "tests/unit/test_a.py::test_x"]),
    ("pytest . -q -n 4", ["pytest", "-q", "-n", "4", "tests/unit/test_a.py::test_x"]),
    ("uv run --with pytest pytest -q", ["uv", "run", "--with", "pytest", "pytest", "-q",
                                        "tests/unit/test_a.py::test_x"]),
])
def test_rerun_command_runs_only_the_failed_tests(testcmd, work, command, expected):
    (work / "tests").mkdir(exist_ok=True)
    assert testcmd.rerun_command(command, ["tests/unit/test_a.py::test_x"], str(work)) == expected


@pytest.mark.parametrize("command", ["npx jest", "vitest run", "make test", "env pytest -q"])
def test_rerun_command_is_none_outside_pytest(testcmd, work, command):
    assert testcmd.rerun_command(command, ["tests/unit/test_a.py"], str(work)) is None
