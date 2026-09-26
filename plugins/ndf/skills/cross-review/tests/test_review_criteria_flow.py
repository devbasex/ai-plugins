"""指摘の基準が init → 担当の起動へ渡る（#1287）。

init は PR の作業ツリーの重点の宣言（`.ndf/review.json`）を読み、状態ファイルの `review_criteria` に
レビュー担当への節を写す。`launch-reviewer.sh` はその節を指示へ差し込み、節が無ければ宣言を
読まない既定の節（基準 3 の無い形）を差し込む。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess

import pytest
import review_lib.commands.init

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts/launch-reviewer.sh"
PR = 6120
OLD_FLUSH_LINE = "重要度が minor のものも書く"


def _declare(root: pathlib.Path, text: str) -> None:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    (root / ".ndf" / "review.json").write_text(text, encoding="utf-8")


def _prompt(tmp_path: pathlib.Path, **state_over) -> str:
    state = {"current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_path),
             "rounds": [{"round": 1, "head_sha": "a" * 40}]}
    state.update(state_over)
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state, ensure_ascii=False))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    p = subprocess.run(["bash", str(SCRIPT), "codex", str(PR), "1"], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                            "CROSS_REVIEW_TMP_DIR": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    return (tmp_path / f"codex-review-pr{PR}-prompt.md").read_text(encoding="utf-8")


def _criteria_numbers(prompt: str) -> list[str]:
    block = prompt.split("## 指摘の基準\n", 1)[1].split("\n## ", 1)[0]
    return [ln[0] for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.")]


def test_the_prompt_carries_the_criteria_from_the_state(tmp_path):
    crit = review_lib.commands.init.review_criteria
    _declare(tmp_path / "wt", json.dumps({"version": 1, "focus": ["起動の回数を増やす変更"]}))
    block = crit.as_state(crit.load_focus(tmp_path / "wt"))
    prompt = _prompt(tmp_path, review_criteria=block)
    assert OLD_FLUSH_LINE not in prompt
    assert _criteria_numbers(prompt) == ["1", "2", "3", "4"]
    assert "起動の回数を増やす変更" in prompt


def test_the_prompt_falls_back_to_the_default_criteria(tmp_path):
    # 状態ファイルに節が無い（前の版からの再開）。作業ツリーに宣言があっても読まない（I8）
    _declare(tmp_path, json.dumps({"version": 1, "focus": ["作業ツリーの重点"]}))
    prompt = _prompt(tmp_path)
    assert OLD_FLUSH_LINE not in prompt
    assert _criteria_numbers(prompt) == ["1", "2", "4"]
    assert "作業ツリーの重点" not in prompt


@pytest.fixture()
def resumable(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    wt = tmp_path / "wt"
    wt.mkdir()
    state_file = tmp_path / f"cross-review-pr{PR}-state.json"
    state_file.write_text(json.dumps({
        "current_pr": PR, "repo": "o/r", "tmp_dir": str(tmp_path), "head_branch": "feat/x",
        "base_branch": "develop", "pr_author": "a", "viewer_login": "a", "is_own_pr": True,
        "event_downgrade": True, "worktree_path": str(wt), "auto_review_categories": ["code"],
        "auto_review_instructions": "観点", "review_instructions": "観点", "rounds": [],
        "carried_over": None, "final": None,
        "review_criteria": {"status": "none", "focus": [], "error": None, "reviewer_block": "古い節"},
    }), encoding="utf-8")
    return wt, state_file


def _resume(fake_gh):
    fake_gh.set_rules([{"match": "graphql", "stdout": ""}])
    review_lib.commands.init.cmd_init(argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=None, focus=None, extra_instructions_file=None))


def test_resume_rewrites_the_criteria_from_the_declaration(resumable, fake_gh, capsys):
    wt, state_file = resumable
    _declare(wt, json.dumps({"version": 1, "focus": ["重点 A"]}))
    _resume(fake_gh)
    assert "REVIEW_FOCUS=declared" in capsys.readouterr().out
    crit = json.loads(state_file.read_text(encoding="utf-8"))["review_criteria"]
    assert crit["status"] == "declared" and crit["focus"] == ["重点 A"] and "重点 A" in crit["reviewer_block"]


def test_resume_with_a_broken_declaration_continues(resumable, fake_gh, capsys):
    wt, state_file = resumable
    _declare(wt, "{broken")
    _resume(fake_gh)
    out = capsys.readouterr()
    assert "REVIEW_FOCUS=unreadable" in out.out and "RESUMED=1" in out.out
    assert "基準 1・2・4" in out.err
    crit = json.loads(state_file.read_text(encoding="utf-8"))["review_criteria"]
    assert crit["status"] == "unreadable" and crit["error"] and "3. " not in crit["reviewer_block"]


def test_new_state_criteria_without_declaration_has_no_criterion_3(tmp_path):
    crit = review_lib.commands.init._review_criteria(tmp_path)
    assert crit["status"] == "none" and crit["focus"] == []
    assert "\n3. " not in crit["reviewer_block"] and "\n4. " in crit["reviewer_block"]
