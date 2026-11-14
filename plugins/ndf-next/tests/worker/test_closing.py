"""push の前の closing keywords の照合（#3129）と、その副命令。"""

from __future__ import annotations

import json
import subprocess

import pytest

from ndf.adapters import git
from ndf.contracts import causes
from ndf.core import worker


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    _git(tmp_path, "tag", "base")
    return tmp_path


@pytest.mark.parametrize(
    "text", ["Fix: #12 直す", "closes #3", "Resolved owner/repo#9", "fixes  #1", "Fix:#12", "Fixes https://github.com/o/r/issues/12"]
)
def test_closing_words_hit(text):
    assert worker.closing_words(text)


@pytest.mark.parametrize("text", ["Fix: 直す （#12）", "fixture #12", "Refs #3", "prefix #4"])
def test_reference_forms_do_not_hit(text):
    assert worker.closing_words(text) == ()


def test_closing_commits_find_subject_and_body(repo):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Fix: #12 直す")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "整える", "-m", "Closes #3")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Fix: 直す （#12）", "-m", "fixture #12")
    hits = worker.closing_commits(git.Git(repo), "base")
    assert [h.subject for h in hits] == ["整える", "Fix: #12 直す"]
    assert hits[0].words == ("Closes #3",)


def test_closing_commits_report_an_unknown_base(repo):
    with pytest.raises(git.GitError):
        worker.closing_commits(git.Git(repo), "no-such-ref")


def test_closing_check_refuses_before_push(repo, capsys):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Fix: #12 直す")
    code = worker.main(["closing-check", "--base", "base", "--cwd", str(repo)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["cause"]["kind"] == "refused" and code == causes.exit_code("refused")
    assert out["items"][0]["words"] == ["Fix: #12"]


def test_closing_check_passes_reference_forms(repo, capsys):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "直す （#12）")
    assert worker.main(["closing-check", "--base", "base", "--cwd", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out.strip())["status"] == "ok"
