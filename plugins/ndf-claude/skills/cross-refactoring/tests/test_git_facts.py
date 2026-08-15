"""git から事実を取る経路を、**実際の git リポジトリ**で確かめるテスト。

他のテストは `collect_commit_facts()` を差し替えるため、git の呼び出し方そのものが
間違っていても気付けない。ここだけは本物の git を通す。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def work(tmp_path):
    """1 コミットだけある作業ディレクトリ。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def f():\n    return 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


def _commit(repo, message, files):
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


TRAILERS = (
    "\n\nItem-Id: R1-001\nRound: 1\nImpl-Runtime: codex\nImpl-Model: gpt-5.5"
)


def test_facts_come_from_a_real_repository(refactor, work):
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    first = _commit(work, "Test: 現状固定テストを足す" + TRAILERS,
                    {"tests/test_foo.py": "def test_f():\n    assert True\n"})
    second = _commit(work, "Refactor: extract_method" + TRAILERS,
                     {"src/foo.py": "def _one():\n    return 1\n\n\ndef f():\n"
                                    "    return _one()\n"})

    ordered = refactor.commits_in_range(str(work), base, "HEAD")
    assert ordered == [second, first], "新しい順で返っていない"

    facts = refactor.collect_commit_facts(
        str(work), [first, second], set(ordered), "true", "main"
    )
    assert [f["sha"] for f in facts] == [first, second]
    assert all(f["exists"] for f in facts)
    assert facts[0]["trailers"] == {
        "Item-Id": "R1-001", "Round": "1",
        "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5",
    }
    # 現状固定テストの追加が先行している
    assert facts[0]["touches_tests"] is True
    assert facts[1]["touches_tests"] is False
    assert facts[0]["diff_lines"] > 0 and facts[1]["diff_lines"] > 0
    assert all(f["test_status"] == "pass" for f in facts)
    # テスト実行のあとも元のブランチへ戻っている
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).stdout.strip() == "main"


def test_missing_trailers_are_seen_as_missing(refactor, work):
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Refactor: トレーラーなし", {"src/foo.py": "def f():\n    return 2\n"})
    facts = refactor.collect_commit_facts(
        str(work), [sha], {sha}, "true", "main"
    )
    problem = refactor.verify_commit_trailers(facts[0])
    assert problem is not None and "Item-Id" in problem
    assert base != sha


def test_commit_outside_the_range_is_rejected(refactor, work):
    """起点より前のコミットを申告しても実在扱いにしない。"""
    old = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    base = _commit(work, "Chore: 起点" + TRAILERS, {"src/bar.py": "y = 1\n"})
    new = _commit(work, "Refactor: 対象" + TRAILERS, {"src/foo.py": "def f():\n    return 3\n"})

    ordered = refactor.commits_in_range(str(work), base, "HEAD")
    assert ordered == [new]
    facts = refactor.collect_commit_facts(
        str(work), [old], set(ordered), "true", "main"
    )
    assert facts[0]["exists"] is False


def test_failing_test_is_detected_by_running_it(refactor, work):
    """`test_status` は実際に走らせて決まる。申告では決まらない。"""
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Refactor: 壊した" + TRAILERS, {"src/foo.py": "def f():\n    return 9\n"})
    facts = refactor.collect_commit_facts(
        str(work), [sha], {sha}, "false", "main"
    )
    assert facts[0]["test_status"] == "fail"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).stdout.strip() == "main"
    assert base != sha


def test_fix_commits_pass_verification_through_real_git(refactor, work):
    """修正コミットが git 経由の検証を通ること。

    範囲に空集合を渡していた頃は、全ての修正コミットが必ず不正扱いになっていた。
    """
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Fix: レビュー指摘の反映" + TRAILERS,
                  {"src/foo.py": "def f():\n    return 1  # 直した\n"})
    ordered = refactor.commits_in_range(str(work), base, "HEAD")
    facts = refactor.collect_commit_facts(
        str(work), [sha], set(ordered), "true", "main"
    )
    assert refactor.verify_fix_commit(facts[0]) is None
