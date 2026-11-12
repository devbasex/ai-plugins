"""実行環境が帰属の段落を足したコミットから記名を読む（#553）。

**一時リポジトリで実際に git を実行する。** 段落の切り分けは git の判定
（`git interpret-trailers --parse`）に委ねているため、差し替えた出力で確かめても
本番の読み方を確かめたことにならない。
"""

from __future__ import annotations

import subprocess

import pytest

REQUIRED = ("Item-Id", "Impl-Runtime", "Impl-Model")

_SIGNED = """Refactor: extract_method — src/foo.py#Bar.handle

変更の説明。

Item-Id: I-001
Impl-Runtime: claude
Impl-Model: claude-opus-5

Co-Authored-By: Claude Opus 5 <noreply@example.test>
"""


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """コミットを積める一時リポジトリ。"""
    path = tmp_path / "repo"
    path.mkdir()
    _git("init", "-q", "-b", "main", cwd=path)
    _git("config", "user.email", "t@e.st", cwd=path)
    _git("config", "user.name", "test", cwd=path)
    (path / "src").mkdir()
    (path / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-qm", "init", cwd=path)
    return path


@pytest.fixture
def commit(repo):
    """メッセージを渡してコミットを作り、その完全な識別子を返す。"""
    counter = {"n": 0}

    def _make(message: str) -> str:
        counter["n"] += 1
        (repo / "src" / f"f{counter['n']}.py").write_text("y = 1\n", encoding="utf-8")
        _git("add", "-A", cwd=repo)
        _git("commit", "-q", "-m", message, cwd=repo)
        return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    return _make


def test_a_signature_paragraph_after_the_required_ones_is_skipped(trailers, repo, commit):
    """AC32: 必須の記名の後ろに帰属の段落が付いても 3 つとも読める。"""
    sha = commit(_SIGNED)

    found = trailers.commit_trailers(str(repo), sha)

    assert [found.get(k) for k in REQUIRED] == ["I-001", "claude", "claude-opus-5"]


def test_two_attribution_lines_in_one_paragraph_are_also_skipped(trailers, repo, commit):
    """AC33: 帰属の段落が 2 行でも 3 つとも読める。"""
    sha = commit(_SIGNED + "Claude-Session: https://example.test/session_1\n")

    found = trailers.commit_trailers(str(repo), sha)

    assert all(found.get(k) for k in REQUIRED)
    assert found["Claude-Session"] == "https://example.test/session_1"


def test_a_prose_paragraph_stops_the_reading(trailers, repo, commit):
    """AC34: 散文の段落より前にある記名の形の行は読まない。"""
    sha = commit(
        "Refactor: 題名\n\n"
        "Item-Id: I-001\n"
        "Impl-Runtime: claude\nImpl-Model: claude-opus-5\n\n"
        "この段落は説明の散文です。\n\n"
        "Co-Authored-By: Someone <s@e.st>\n"
    )

    found = trailers.commit_trailers(str(repo), sha)

    assert found == {"Co-Authored-By": "Someone <s@e.st>"}


def test_a_mixed_last_paragraph_is_not_read(trailers, repo, commit):
    """AC35: 散文と記名の形が混ざる段落は、git が記名の段落と判定しない。"""
    sha = commit("Refactor: 題名\n\nここは説明です。\nこちらも説明です。\nさらに説明です。\nRound: 3\n")

    found = trailers.commit_trailers(str(repo), sha)

    assert found == {}


def test_the_paragraph_nearest_the_end_wins(trailers, repo, commit):
    """AC36: 同じ鍵が 2 つの段落にあれば、末尾に近い方の値を採る。"""
    sha = commit("Refactor: 題名\n\nItem-Id: I-001\nImpl-Runtime: claude\nImpl-Model: claude-opus-5\n\nImpl-Model: claude-opus-5-later\n")

    found = trailers.commit_trailers(str(repo), sha)

    assert found["Impl-Model"] == "claude-opus-5-later"
    assert found["Item-Id"] == "I-001"


def test_a_subject_shaped_like_a_trailer_is_not_read(trailers, repo, commit):
    """AC38: 題名が記名の形でも、1 段落目は判定に掛けない。"""
    sha = commit("Round: 本文の題名\n\nItem-Id: I-002\n")

    found = trailers.commit_trailers(str(repo), sha)

    assert found == {"Item-Id": "I-002"}


def test_trailer_value_reads_one_key_or_returns_empty(trailers, repo, commit):
    """trailer_value: ある鍵は値を、無い鍵は空を返す。"""
    sha = commit(_SIGNED)

    assert trailers.trailer_value(str(repo), sha, "Item-Id") == "I-001"
    assert trailers.trailer_value(str(repo), sha, "Round") == ""


def test_a_commit_with_an_attribution_paragraph_passes_the_implement_check(gitfacts, cmd_implement, repo, commit):
    """AC37: 帰属の段落が付いたコミットは、記名の欠落で取り消されない。"""
    sha = commit(_SIGNED)
    facts = gitfacts.collect_commit_facts(str(repo), [sha], {sha}, "", "main")

    problem = cmd_implement._implement_problem(
        {"id": "I-001", "estimated_diff_lines": 100, "technique": "extract_method"},
        facts,
        [],
        {"worktrees": {"work": str(repo)}},
    )

    assert problem is None


def test_a_commit_without_the_item_trailer_is_rejected(gitfacts, cmd_implement, repo, commit):
    """I4: 項目とコミットの対応は `Item-Id` だけで決める。欠ければ手順違反。"""
    sha = commit("Refactor: 題名\n\nImpl-Runtime: claude\nImpl-Model: m\n")
    facts = gitfacts.collect_commit_facts(str(repo), [sha], {sha}, "", "main")

    problem = cmd_implement._implement_problem(
        {"id": "I-001", "estimated_diff_lines": 100, "technique": "extract_method"},
        facts,
        [],
        {"worktrees": {"work": str(repo)}},
    )

    # 取り消さず差し戻しに残す（#2457 の棚卸し #23）
    assert problem is not None and problem[0] == "trailers" and "Item-Id" in problem[1]
