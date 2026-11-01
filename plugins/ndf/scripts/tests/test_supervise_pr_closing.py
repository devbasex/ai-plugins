"""supervise_lib/pr.py の _publish: 既定ブランチ宛の本文の closing keywords を閉じる予定の参照へ置き換える（#2228 の I7・I8）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import gh_call  # noqa: E402
from closing import closing_words  # noqa: E402
from supervise_lib.pr import PrStep  # noqa: E402

BODY = "## 閉じる課題\n\nCloses #12\nCloses #13\n"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """origin の HEAD が main の作業場所（develop も origin にある）。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "origin", "main", "main:develop")
    git(root, "fetch", "-q", "origin")
    git(root, "remote", "set-head", "origin", "main")
    return root


def publish(monkeypatch, repo, base, found=""):
    calls = []

    def fake(args, stdin=None, cwd=None):
        calls.append(args)
        out = found if args[:2] == ["pr", "list"] else ("https://github.com/o/r/pull/5\n" if args[:2] == ["pr", "create"] else "")
        return gh_call.GhResult(0, out, "")

    monkeypatch.setattr(gh_call, "gh", fake)
    ctx = SimpleNamespace(cwd=str(repo), plan={}, state=SimpleNamespace(cur={}))
    ok, _ = PrStep()._publish(ctx, base, "feat/x", "題", BODY)
    assert ok
    sent = next(c for c in calls if c[:2] in (["pr", "create"], ["pr", "edit"]))
    return sent[sent.index("--body") + 1], ctx.state.cur


def test_a_new_pr_to_the_default_branch_gets_deferred_references(monkeypatch, repo):
    body, cur = publish(monkeypatch, repo, "main")
    assert closing_words(body) == [] and "最終工程の後に閉じる: #13" in body
    assert cur["deferred_closing"] == ["#12", "#13"]


def test_an_existing_pr_is_judged_by_its_own_base(monkeypatch, repo):
    body, _ = publish(monkeypatch, repo, "develop", found="https://github.com/o/r/pull/5\tmain\n")
    assert closing_words(body) == []


@pytest.mark.parametrize("base, found", [("develop", ""), ("main", "https://github.com/o/r/pull/5\tdevelop\n")])
def test_a_pr_to_another_branch_keeps_the_closing_keywords(monkeypatch, repo, base, found):
    body, cur = publish(monkeypatch, repo, base, found=found)
    assert closing_words(body) == ["Closes #12", "Closes #13"] and "deferred_closing" not in cur
