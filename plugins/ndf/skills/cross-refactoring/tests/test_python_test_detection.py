"""名前が `test_` で始まりテストの関数を持たない `.py` をプロダクションコードとして扱う（#2516）。

試走の候補・テストの置き場所・行数と危険フラグ・コミットの事実が、同じ見分け方になることを確かめる。
"""

from __future__ import annotations

import subprocess

import pytest

PRODUCTION = "def choose():\n    return 'test'\n"
WITH_TEST = "def test_one():\n    assert True\n"


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _write(repo, files):
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _commit(repo, files, message="c"):
    _write(repo, files)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    _commit(
        repo,
        {
            "lib/__init__.py": "",
            "lib/test_x.py": PRODUCTION,
            "lib/core.py": "from lib.test_x import choose\n",
            "tests/test_y.py": WITH_TEST,
            "tests/test_helpers.py": "def make():\n    return 1\n",
            "colo/test_z.py": WITH_TEST,
            "colo/test_async.py": "async def test_a():\n    pass\n",
            "colo/test_method.py": "class TestA:\n    def test_m(self):\n        pass\n",
        },
        "init",
    )
    return repo


# ---------- 中身の判定 ----------


@pytest.mark.parametrize(
    "text, lacks",
    [
        (PRODUCTION, True),
        ("", True),
        (WITH_TEST, False),
        ("async def test_a():\n    pass\n", False),
        ("class TestA:\n    def test_m(self):\n        pass\n", False),
        ("x = 'def test_'  # 行頭でない\n", True),
    ],
)
def test_the_text_lacks_tests_only_without_a_test_definition_line(pathkinds, text, lacks):
    assert pathkinds.python_text_lacks_tests(text) is lacks


def test_an_unreadable_or_non_python_file_keeps_the_name_rule(pathkinds, repo):
    """I3: 読めない `.py`（削除済み）と `work` を渡さない呼び出しは名前の形のまま。"""
    assert pathkinds.is_test_path("lib/test_gone.py", str(repo)) is True
    assert pathkinds.is_test_path("lib/test_x.py") is True
    assert pathkinds.is_test_path("lib/test_x.py", str(repo)) is False
    assert pathkinds.is_test_path("colo/test_z.py", str(repo)) is True


def test_files_outside_the_test_name_or_python_are_not_read(pathkinds, repo, monkeypatch):
    """I5: 名前がテストの形でない `.py` とテストの形の `.ts` は読まない。"""

    def boom(*a, **k):
        raise AssertionError("読んではいけない")

    monkeypatch.setattr(pathkinds.pathlib.Path, "read_text", boom)
    assert pathkinds.is_test_path("lib/core.py", str(repo)) is False
    assert pathkinds.is_test_path("web/a.spec.ts", str(repo)) is True
    assert pathkinds.is_test_path("tests/test_helpers.py", str(repo)) is True


# ---------- 試走の候補と置き場所 ----------


def test_a_production_test_named_file_is_not_a_probe_candidate(scope, repo):
    """受け入れ条件 1・4・決定 5: 本体と同じ場所でも tests/ の配下でも、テストの関数を持たないものは候補に入らない。"""
    assert scope.test_files_under("lib", str(repo)) == []
    assert scope.test_files_under(".", str(repo)) == [
        "colo/test_async.py",
        "colo/test_method.py",
        "colo/test_z.py",
        "tests/test_y.py",
    ]


def test_a_location_with_only_production_test_named_files_is_not_a_test_location(scope, repo):
    """受け入れ条件 2・4・I4: ファイルでも配下でも当たらない。tests/ と同じ場所のテストは置き場所のまま。"""
    assert scope.test_locations(["lib/test_x.py", "lib", "colo", "tests"], str(repo)) == ["colo", "tests"]
    assert scope.is_test_location("lib", str(repo)) is False
    assert scope.is_test_location("colo/test_z.py", str(repo)) is True
    assert scope.is_test_location("lib/test_new.py", str(repo)) is True, "まだ無いファイルは名前で読む"


def test_the_probe_candidates_and_is_test_path_agree(scope, pathkinds, repo):
    """I1: 置き場所の配下でないファイルについて、候補と `is_test_path` の答えが一致する。"""
    candidates = set(scope.test_files_under(".", str(repo)))
    for path in ["lib/test_x.py", "colo/test_z.py", "colo/test_async.py", "colo/test_method.py"]:
        assert (path in candidates) is pathkinds.is_test_path(path, str(repo))


# ---------- 行数と危険フラグ ----------


def test_a_production_test_named_file_counts_as_production_code(pathkinds, repo):
    """受け入れ条件 5・I4: `lib/test_x.py` は数え、`tests/test_helpers.py` は数えない。"""
    base = _git("rev-parse", "HEAD", cwd=repo)
    _commit(repo, {"lib/test_x.py": PRODUCTION + "X = 1\n", "tests/test_helpers.py": "def make():\n    return 2\n"})
    assert pathkinds.production_code_changes(str(repo), base) == [("lib/test_x.py", 1)]


def test_danger_flags_look_at_a_production_test_named_file(danger, repo):
    """受け入れ条件 5・I4: D3・D4 の対象に入る。tests/ の配下の補助は入らない。"""
    item = {"path": "lib/test_x.py", "symbol": "", "tests": []}
    out = danger.item_flags(str(repo), item, [], ["lib/test_x.py"], ["lib/test_x.py", "tests"], ["tests/test_y.py"], False)
    assert out["flags"] == ["D3", "D4"]
    helper = {"path": "tests/test_helpers.py", "symbol": "", "tests": []}
    assert danger.item_flags(str(repo), helper, [], ["tests/test_helpers.py"], ["tests"], ["tests/test_y.py"], False)["flags"] == []


def test_the_add_tests_intake_treats_a_production_test_named_file_as_other(cmd_implement, repo):
    """受け入れ条件 5: テストの追加のコミットが `lib/test_x.py` を変えるとテスト以外を変えたと読む。"""
    fact = {"sha": "abc1234", "exists": True, "test_status": "pass", "touches_tests": True, "diff_lines": 1}
    fact["trailers"] = {"Item-Id": "I-001", "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"}
    fact["test_changes"] = {}
    fact["files"] = ["lib/test_x.py", "colo/test_z.py"]
    problem = cmd_implement._test_commit_problem([fact], [], {"worktrees": {"work": str(repo)}})
    assert problem == "テストの追加がテスト以外のファイルを変えています（lib/test_x.py）"


# ---------- コミットの事実 ----------


def test_a_commit_that_removes_every_test_function_stays_a_test_change(gitfacts, repo):
    """決定 3: 前の版がテストの関数を持てば、作業ディレクトリで消えていてもテストの変更として残る。"""
    sha = _commit(repo, {"colo/test_z.py": PRODUCTION})
    changes = gitfacts.commit_test_changes(str(repo), sha)
    assert changes == {"colo/test_z.py": (["def test_one():\n", "    assert True"], ["def choose():\n", "    return 'test'"])}
    assert gitfacts.commit_touches_tests(changes) is True


def test_a_commit_to_a_production_test_named_file_is_not_a_test_change(gitfacts, repo):
    sha = _commit(repo, {"lib/test_x.py": PRODUCTION + "X = 1\n"})
    assert gitfacts.commit_test_changes(str(repo), sha) == {}
    assert gitfacts.commit_touches_tests({}) is False


def test_commit_facts_read_each_version_once(gitfacts, repo, monkeypatch):
    """決定 3: テストの名前の `.py` 1 本につき、前後の版を読む `git show` はそれぞれ 1 回だけ。"""
    sha = _commit(repo, {"colo/test_z.py": WITH_TEST + "\ndef test_more():\n    pass\n"})
    reads: list[str] = []
    real = gitfacts.git_out

    def counting(work, args, *a, **k):
        if args[0] == "show" and len(args) == 2 and ":" in args[1]:
            reads.append(args[1])
        return real(work, args, *a, **k)

    monkeypatch.setattr(gitfacts, "git_out", counting)
    facts = gitfacts.collect_commit_facts(str(repo), [sha], {sha}, "", "main")
    assert facts[0]["touches_tests"] is True and list(facts[0]["test_changes"]) == ["colo/test_z.py"]
    assert sorted(reads) == sorted([f"{sha}^:colo/test_z.py", f"{sha}:colo/test_z.py"])
