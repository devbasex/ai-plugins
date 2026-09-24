"""実行環境が帰属の段落を足したコミットから記名を読む（#553）。

**一時リポジトリで実際に git を実行する。** 段落の切り分けは git の判定
（`git interpret-trailers --parse`）に委ねているため、差し替えた出力で確かめても
本番の読み方を確かめたことにならない。
"""
from __future__ import annotations

import pytest

from crossref_helpers import run_git

REQUIRED = ("Item-Id", "Impl-Runtime", "Impl-Model")

_SIGNED = """Refactor: extract_method — src/foo.py#Bar.handle

変更の説明。

Item-Id: I-001
Impl-Runtime: claude
Impl-Model: claude-opus-5

Co-Authored-By: Claude Opus 5 <noreply@example.test>
"""


@pytest.fixture
def repo(tmp_path):
    """コミットを積める一時リポジトリ。"""
    path = tmp_path / "repo"
    path.mkdir()
    run_git("init", "-q", "-b", "main", cwd=path)
    run_git("config", "user.email", "t@e.st", cwd=path)
    run_git("config", "user.name", "test", cwd=path)
    (path / "src").mkdir()
    (path / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    run_git("add", "-A", cwd=path)
    run_git("commit", "-qm", "init", cwd=path)
    return path


@pytest.fixture
def commit(repo):
    """メッセージを渡してコミットを作り、その完全な識別子を返す。"""
    counter = {"n": 0}

    def _make(message: str) -> str:
        counter["n"] += 1
        (repo / "src" / f"f{counter['n']}.py").write_text("y = 1\n", encoding="utf-8")
        run_git("add", "-A", cwd=repo)
        run_git("commit", "-q", "-m", message, cwd=repo)
        return run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    return _make


def test_a_signature_paragraph_after_the_required_ones_is_skipped(
    gitfacts, repo, commit
):
    """AC32: 必須の記名の後ろに帰属の段落が付いても 3 つとも読める。"""
    sha = commit(_SIGNED)

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert [trailers.get(k) for k in REQUIRED] == [
        "I-001", "claude", "claude-opus-5"]


def test_two_attribution_lines_in_one_paragraph_are_also_skipped(
    gitfacts, repo, commit
):
    """AC33: 帰属の段落が 2 行でも 3 つとも読める。"""
    sha = commit(
        _SIGNED + "Claude-Session: https://example.test/session_1\n")

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert all(trailers.get(k) for k in REQUIRED)
    assert trailers["Claude-Session"] == "https://example.test/session_1"


def test_a_prose_paragraph_stops_the_reading(gitfacts, repo, commit):
    """AC34: 散文の段落より前にある記名の形の行は読まない。"""
    sha = commit(
        "Refactor: 題名\n\n"
        "Item-Id: I-001\n"
        "Impl-Runtime: claude\nImpl-Model: claude-opus-5\n\n"
        "この段落は説明の散文です。\n\n"
        "Co-Authored-By: Someone <s@e.st>\n"
    )

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert trailers == {"Co-Authored-By": "Someone <s@e.st>"}


def test_a_mixed_last_paragraph_is_not_read(gitfacts, repo, commit):
    """AC35: 散文と記名の形が混ざる段落は、git が記名の段落と判定しない。"""
    sha = commit(
        "Refactor: 題名\n\n"
        "ここは説明です。\nこちらも説明です。\nさらに説明です。\nRound: 3\n"
    )

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert trailers == {}


def test_the_paragraph_nearest_the_end_wins(gitfacts, repo, commit):
    """AC36: 同じ鍵が 2 つの段落にあれば、末尾に近い方の値を採る。"""
    sha = commit(
        "Refactor: 題名\n\n"
        "Item-Id: I-001\n"
        "Impl-Runtime: claude\nImpl-Model: claude-opus-5\n\n"
        "Impl-Model: claude-opus-5-later\n"
    )

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert trailers["Impl-Model"] == "claude-opus-5-later"
    assert trailers["Item-Id"] == "I-001"


def test_a_subject_shaped_like_a_trailer_is_not_read(gitfacts, repo, commit):
    """AC38: 題名が記名の形でも、1 段落目は判定に掛けない。"""
    sha = commit("Round: 本文の題名\n\nItem-Id: I-002\n")

    trailers = gitfacts.commit_trailers(str(repo), sha)

    assert trailers == {"Item-Id": "I-002"}


def test_a_commit_with_an_attribution_paragraph_passes_the_implement_check(
    gitfacts, cmd_implement, repo, commit
):
    """AC37: 帰属の段落が付いたコミットは、記名の欠落で取り消されない。"""
    sha = commit(_SIGNED)
    facts = gitfacts.collect_commit_facts(
        str(repo), [sha], {sha}, "", "main")

    problem = cmd_implement._implement_problem(
        {"id": "I-001", "estimated_diff_lines": 100, "technique": "extract_method"},
        facts, ["src"], [], {"worktrees": {"work": str(repo)}},
    )

    assert problem is None


def test_a_commit_without_the_item_trailer_is_rejected(gitfacts, cmd_implement, repo, commit):
    """I4: 項目とコミットの対応は `Item-Id` だけで決める。欠ければ手順違反。"""
    sha = commit("Refactor: 題名\n\nImpl-Runtime: claude\nImpl-Model: m\n")
    facts = gitfacts.collect_commit_facts(str(repo), [sha], {sha}, "", "main")

    problem = cmd_implement._implement_problem(
        {"id": "I-001", "estimated_diff_lines": 100, "technique": "extract_method"},
        facts, ["src"], [], {"worktrees": {"work": str(repo)}},
    )

    assert problem is not None and "Item-Id" in problem
