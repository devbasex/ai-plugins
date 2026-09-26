"""PR の分類（design / code）ごとの収束の既定と差分の渡し方（#1005）。

- 設計 PR は `--max-rounds` を渡さないとき上限 3 ラウンド。code は今のまま 12
- 設計 PR の 2 ラウンド目以降は、前のラウンドからの変更だけを担当へ渡す。code は全差分
- 設計文書が 1,000 行を超えたら init が知らせる（止めない）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from classifications import (default_max_rounds, diff_scope, oversized_design_docs,  # noqa: E402
                             review_kind)
import review_lib
import review_lib.commands.init
import review_lib.github
import review_lib.workspace

PR = 7300
REPO = "o/r"
LAUNCH = SCRIPTS / "launch-reviewer.sh"


# ---------------- 分類と既定 ----------------

@pytest.mark.parametrize("branch, cats, kind", [
    ("design/issue-1", ["common", "code"], "design"),
    ("feat/x", ["common", "docs_only", "design"], "design"),
    ("feat/x", ["common", "design", "code"], "code"),
    ("feat/x", ["common", "code"], "code"),
    (None, [], "code"),
])
def test_review_kind(branch, cats, kind):
    assert review_kind(branch, cats) == kind


def test_defaults_by_kind():
    assert default_max_rounds("design") == 3 and default_max_rounds("code") == 12
    assert diff_scope("design", 1) == "full" and diff_scope("design", 2) == "since_previous"
    assert diff_scope("code", 3) == "full"


def test_oversized_design_docs(tmp_path):
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "1-design.md").write_text("x\n" * 1001)
    (tmp_path / "issues" / "2-design.md").write_text("x\n" * 1000)
    (tmp_path / "issues" / "3-requirements.md").write_text("x\n" * 2000)
    got = oversized_design_docs(tmp_path, ["issues/1-design.md", "issues/2-design.md",
                                           "issues/3-requirements.md", "issues/9-design.md"])
    assert got == [{"path": "issues/1-design.md", "lines": 1001}]


# ---------------- init が既定を置く ----------------

@pytest.fixture()
def init_env(monkeypatch, state_mod, tmp_path, fake_gh):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    worktree = tmp_path / "wt"
    (worktree / "issues").mkdir(parents=True)
    (worktree / "issues" / "1-design.md").write_text("x\n" * 1200)
    monkeypatch.setattr(review_lib, "_sh", lambda cmd, check=True: "takemi")
    monkeypatch.setattr(review_lib.workspace, "_create_worktree", lambda *a: None)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda p: True)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
    real_run = subprocess.run

    def _run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and "fetch-pr-comments.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)
    fake_gh.set_rules([
        {"match": f"repos/{REPO}/pulls/{PR}/files", "stdout": "", "exit": 1},
        {"match": f"pr view {PR} --json files",
         "stdout": json.dumps({"files": [{"path": "issues/1-design.md", "changeType": "ADDED"}]})},
    ])

    def run(branch, max_rounds=None):
        monkeypatch.setattr(
            review_lib.github, "_fetch_pr_metadata",
            lambda pr, repo=None: review_lib.github.PrMetadata(
                REPO, "takemi", branch, "abc123", "develop", False, 4000, None))
        review_lib.commands.init.cmd_init(argparse.Namespace(
            pr=PR, max_rounds=max_rounds, rotate_after=None, only=None, worktree=str(worktree),
            focus=None, extra_instructions_file=None, host="claude"))
        return json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))

    return run


def test_design_pr_defaults_to_three_rounds_and_warns(init_env, capsys):
    st = init_env("design/issue-1")
    assert st["review_kind"] == "design" and st["max_rounds"] == 3
    assert st["design_doc_oversize"] == [{"path": "issues/1-design.md", "lines": 1200}]
    assert "issues/1-design.md" in capsys.readouterr().err


def test_explicit_max_rounds_wins(init_env):
    assert init_env("design/issue-1", max_rounds=5)["max_rounds"] == 5


# ---------------- 担当へ渡す差分 ----------------

def _prompt(tmp_path, kind, rnd, rounds):
    state = {"current_pr": PR, "repo": REPO, "worktree_path": str(tmp_path), "review_kind": kind,
             "rounds": rounds}
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    p = subprocess.run(["bash", str(LAUNCH), "codex", str(PR), str(rnd)], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                            "CROSS_REVIEW_TMP_DIR": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    return (tmp_path / f"codex-review-pr{PR}-prompt.md").read_text()


TWO_ROUNDS = [{"round": 1, "head_sha": "a" * 40}, {"round": 2, "head_sha": "b" * 40}]


def test_design_round_two_gets_only_the_previous_changes(tmp_path):
    prompt = _prompt(tmp_path, "design", 2, TWO_ROUNDS)
    assert f"diff {'a' * 40} {'b' * 40}" in prompt


@pytest.mark.parametrize("kind, rnd, rounds", [
    ("code", 2, TWO_ROUNDS),
    ("design", 1, TWO_ROUNDS[:1]),
])
def test_code_and_first_round_get_the_full_diff(tmp_path, kind, rnd, rounds):
    assert f"diff {'a' * 40}" not in _prompt(tmp_path, kind, rnd, rounds)
