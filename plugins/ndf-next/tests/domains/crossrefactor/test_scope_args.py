"""検査のプランが `--scope` へ渡す範囲（#1484）と、副命令 `refactor scope-args`・`refactor state`。"""

from __future__ import annotations

from ndf.domains.crossrefactor import cli, range_decide, scope


def test_scope_keeps_code_files_and_drops_documents_and_covered_items() -> None:
    got = range_decide.compose(
        ["src/a.py", "src/sub/b.py", "docs/guide.md"], ["tests/"], {}, ["src/sub"]
    )
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
