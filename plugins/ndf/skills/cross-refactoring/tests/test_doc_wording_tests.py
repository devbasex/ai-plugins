"""テストの追加と実装の取り込みが、`.md` の文言を固定するテストの追加を弾くことのテスト（#723 / #933）。

**提案の基準だけでは、実装担当が書いたテストに混じったときに止まらない。** 取り込みで
`target` が `.md` の提案を見送るのに加えて、追加したテストの行が**追跡している `.md`**
を指していればその項目を取り消す（実装計画 I5）。`.md` で終わる文字列をすべて弾く形は採らない
（一時ファイルの `.md` を入力に渡すテストは残す）。
"""
from __future__ import annotations

import subprocess

import pytest

TRACKED = ["plugins/ndf/skills/x/SKILL.md", "README.md"]
REASON = "文書の文言を固定するテストは足さない"


def _change(before: list[str], added: list[str]) -> tuple[list[str], list[str]]:
    """`test_changes` の 1 件（変更前後の行）。`added` を末尾へ足した形にする。"""
    return before, before + added


def _fact(changes: dict, sha: str = "abc1234") -> dict:
    return {
        "sha": sha, "exists": True, "test_status": "pass", "touches_tests": True,
        "diff_lines": 3, "files": sorted(changes),
        "trailers": {"Item-Id": "I-001", "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
        "test_changes": changes,
    }


def _hit_files(verify, facts, work=None) -> list[str]:
    return sorted({path for path, _ in verify.doc_wording_tests(facts, TRACKED, work)})


# ---------- 追加行のリテラル ----------

def test_only_literals_naming_a_tracked_markdown_hit(verify):
    """追跡している `.md` のパス・その末尾が一致するものだけが当たる。"""
    facts = [_fact({
        "tests/test_full.py": _change([], ['    p = ROOT / "plugins/ndf/skills/x/SKILL.md"\n']),
        "tests/test_tail.py": _change([], ['    p = ROOT / "x/SKILL.md"\n']),
        "tests/test_tmp.py": _change([], ['    p = tmp_path / "a.md"\n']),
    })]
    assert _hit_files(verify, facts) == ["tests/test_full.py", "tests/test_tail.py"]


def test_a_literal_only_in_unchanged_lines_does_not_hit(verify):
    """見るのは追加行だけ。既存の行の文字列は、この項目が足したものではない。"""
    facts = [_fact({
        "tests/test_old.py": _change(['P = "README.md"\n'], ["def test_x():\n", "    pass\n"]),
    })]
    assert _hit_files(verify, facts) == []


# ---------- 定数と import を経由する ----------

def test_an_added_line_using_an_existing_constant_hits(verify):
    """`SKILL = ROOT / "SKILL.md"` の形の定数を、追加行が使っていれば当たる。"""
    before = ['SKILL = ROOT / "SKILL.md"\n', "\n"]
    facts = [_fact({
        "tests/test_skill.py": _change(
            before, ["def test_x():\n", '    assert "手順" in SKILL.read_text()\n']),
    })]
    assert verify.doc_wording_tests(facts, TRACKED) == [("tests/test_skill.py", "SKILL.md")]


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """補助モジュールだけを先に置き、テストのファイルを次のコミットで足す作業ディレクトリ。"""
    work = tmp_path / "repo"
    (work / "tests").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "t@e.st", cwd=work)
    _git("config", "user.name", "test", cwd=work)
    (work / "README.md").write_text("# x\n")
    (work / "tests" / "doc_helpers.py").write_text(
        "import pathlib\nROOT = pathlib.Path(__file__).parents[1]\n"
        'DOC = ROOT / "README.md"\nTMP = "a.md"\n')
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "init", cwd=work)
    return work


def _commit_test(work, body: str) -> str:
    (work / "tests" / "test_doc.py").write_text(body)
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "add test", cwd=work)
    return _git("rev-parse", "HEAD", cwd=work).stdout.strip()


def test_an_added_line_using_a_constant_imported_from_a_helper_hits(verify, gitfacts, repo):
    """同じディレクトリの補助モジュールから import した定数も追う。

    補助モジュールはこのコミットで触っていないため、git から読む。
    """
    sha = _commit_test(repo, (
        "from doc_helpers import DOC, TMP\n\n"
        "def test_doc():\n"
        '    assert "x" in DOC.read_text()\n'))
    facts = [{"sha": sha, "exists": True,
              "test_changes": gitfacts.commit_test_changes(str(repo), sha)}]
    hits = verify.doc_wording_tests(facts, ["README.md"], str(repo))
    assert hits == [("tests/test_doc.py", "README.md")]


def test_an_imported_constant_naming_an_untracked_file_does_not_hit(verify, gitfacts, repo):
    sha = _commit_test(repo, (
        "from doc_helpers import TMP\n\n"
        "def test_tmp(tmp_path):\n"
        "    (tmp_path / TMP).write_text('x')\n"))
    facts = [{"sha": sha, "exists": True,
              "test_changes": gitfacts.commit_test_changes(str(repo), sha)}]
    assert verify.doc_wording_tests(facts, ["README.md"], str(repo)) == []


def test_the_tracked_markdown_list_comes_from_git(gitfacts, repo):
    assert gitfacts.tracked_markdown(str(repo)) == ["README.md"]


# ---------- テストの追加と実装の取り込みへの配線 ----------

ITEM = {"id": "I-001", "technique": "", "estimated_diff_lines": 100, "path": "tests/test_full.py"}


def _state(tmp_path) -> dict:
    return {"worktrees": {"work": str(tmp_path)}}


def test_the_implement_intake_rejects_the_item_with_the_reason(cmd_implement, tmp_path):
    facts = [_fact({"tests/test_full.py": _change([], ['    p = ROOT / "README.md"\n'])})]
    problem = cmd_implement._implement_problem(ITEM, facts, [], TRACKED, _state(tmp_path))
    assert problem is not None
    assert problem.startswith(REASON)
    assert "tests/test_full.py: README.md" in problem


def test_the_implement_intake_passes_a_temporary_markdown(cmd_implement, tmp_path):
    facts = [_fact({"tests/test_tmp.py": _change([], ['    p = tmp_path / "a.md"\n'])})]
    assert cmd_implement._implement_problem(ITEM, facts, [], TRACKED, _state(tmp_path)) is None


def test_the_add_tests_intake_rejects_the_item_with_the_reason(cmd_implement, tmp_path):
    facts = [_fact({"tests/test_full.py": _change([], ['    p = ROOT / "README.md"\n'])})]
    facts[0]["files"] = ["tests/test_full.py"]
    problem = cmd_implement._test_commit_problem(facts, [], TRACKED, _state(tmp_path))
    assert problem is not None and problem.startswith(REASON)
