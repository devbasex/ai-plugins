"""検査のプランが `--scope` へ渡す範囲（#1484）と、副命令 `refactor scope-args`・`refactor state`。"""

from __future__ import annotations

import pytest

from ndf.domains.crossrefactor import cli, range_decide, scope


def test_scope_keeps_code_files_and_drops_documents_and_covered_items() -> None:
    got = range_decide.compose(["src/a.py", "src/sub/b.py", "docs/guide.md"], ["tests/"], {}, ["src/sub"])
    assert got == ["src/a.py", "tests", "src/sub"]


def test_scope_uses_the_nearest_test_directory_when_none_is_declared() -> None:
    near = range_decide.near_candidates("pkg/mod/a.py")
    assert near.index("pkg/mod/tests") < near.index("pkg/tests") < near.index("tests")
    assert range_decide.near_candidates("pkg/tests/test_a.py") == []
    got = range_decide.compose(["pkg/mod/a.py"], ["."], {"pkg/mod/a.py": "pkg/tests"}, [])
    assert got == ["pkg/mod/a.py", "pkg/tests"]


def test_scope_args_reads_the_diff_and_skips_removed_files(gitwork) -> None:
    base = gitwork.make({"pkg/mod/a.py": "a = 1\n", "pkg/old.py": "o = 1\n", "pkg/tests/test_a.py": "def test_a():\n    pass\n"})
    gitwork.git("rm", "-q", "pkg/old.py")
    gitwork.commit({"pkg/mod/a.py": "a = 2\n", "README.md": "# r\n"}, "変更")
    assert scope.scope_args(gitwork.work, base, [], []) == ["pkg/mod/a.py", "pkg/tests"]


def test_state_command_reports_an_empty_run(gitwork, capsys, last_result, monkeypatch) -> None:
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    gitwork.make({"a.py": "a = 1\n"})
    code = cli.main(["state", "--pr", "3", "--work", str(gitwork.work)])
    got = last_result(capsys.readouterr().out)
    assert code == 0 and got["status"] == "ok"
    assert got["items"][0]["kind"] == "state" and got["metrics"]["open"] == 0


# 現状固定: scope_args の各分岐（宣言の有無・全体の宣言・明示の範囲・空の差分・git の失敗）と、同じ包み方の tracked
SCOPED = {
    "pkg/mod/a.py": "a = 1\n",
    "pkg/mod/tests/test_a.py": "x = 1\n",
    "pkg/tests/t.py": "x = 1\n",
    "lib/b.py": "b = 1\n",
    "docs/x.md": "# x\n",
}


@pytest.fixture
def scoped(gitwork):
    base = gitwork.make(SCOPED)
    gitwork.commit({"pkg/mod/a.py": "a = 2\n", "lib/b.py": "b = 2\n", "docs/x.md": "# y\n"})
    return gitwork, base


@pytest.mark.parametrize(
    ("declared", "explicit", "expected"),
    [
        ([], [], ["lib/b.py", "pkg/mod/a.py", "pkg/mod/tests"]),
        (["."], [], ["lib/b.py", "pkg/mod/a.py", "pkg/mod/tests"]),
        (["tests/"], [], ["lib/b.py", "pkg/mod/a.py", "tests"]),
        ([".", "tests/"], ["lib"], ["pkg/mod/a.py", "tests", "lib"]),
        ([], ["extra/"], ["lib/b.py", "pkg/mod/a.py", "pkg/mod/tests", "extra"]),
    ],
)
def test_scope_args_current_behavior(scoped, declared, explicit, expected) -> None:
    gitwork, base = scoped
    assert scope.scope_args(gitwork.work, base, declared, explicit) == expected


def test_scope_args_with_no_diff_is_empty(scoped) -> None:
    gitwork, _ = scoped
    assert scope.scope_args(gitwork.work, "HEAD", [], []) == []


def test_scope_args_wraps_a_git_failure_in_scope_error(scoped) -> None:
    gitwork, _ = scoped
    with pytest.raises(scope.ScopeError, match="nope"):
        scope.scope_args(gitwork.work, "nope", [], [])


def test_tracked_wraps_a_git_failure_in_scope_error(tmp_path) -> None:
    with pytest.raises(scope.ScopeError, match="git を起動できない"):
        scope.tracked(tmp_path / "none", ["."])
