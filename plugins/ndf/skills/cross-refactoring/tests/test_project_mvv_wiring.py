"""プロジェクト MVV（#1366）の cross-refactoring への配線: 提案と改修計画のプロンプトの `RF_MVV` と、根拠の項目（`mvv_basis`）。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import make_state_v2

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
LAUNCH = SCRIPTS / "launch-cli.sh"
RUNTIME = "codex"
BLOCK = "# NDF の共通原則\n\n判断の節の目印\n"


@pytest.fixture
def render(refactor, tmp_path):
    vocabulary = sys.modules["refactor_lib.vocabulary"].vocabulary()
    work = tmp_path / "work"
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / RUNTIME).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub_dir / RUNTIME).chmod(0o755)

    def _render(phase: str) -> str:
        path = make_state_v2(
            tmp_path, work, runtimes=[RUNTIME], vocabulary=vocabulary, project_mvv={"ref": {"status": "none"}, "block": BLOCK}
        )
        subprocess.run(
            [str(LAUNCH), RUNTIME, phase, "130"],
            env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(path.parent), "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
            check=True,
            capture_output=True,
            text=True,
        )
        return (path.parent / f"{RUNTIME}-{phase}-rf130-prompt.md").read_text(encoding="utf-8")

    return _render


@pytest.mark.parametrize("phase", ["propose", "plan"])
def test_propose_and_plan_prompts_carry_the_mvv_block(render, phase):
    prompt = render(phase)
    assert "判断の節の目印" in prompt and "$RF_MVV" not in prompt and "mvv_basis" in prompt


def test_proposals_keep_and_merge_the_basis(proposals):
    raw = {"path": "a.py", "symbol": "f", "smell": "long_method", "technique": "extract_method", "severity": "major"}
    got, _ = proposals.build_candidates({"claude": [{**raw, "mvv_basis": ["Value 1"]}], "codex": [{**raw, "mvv_basis": ["Value 6"]}]})
    assert got[0]["mvv_basis"] == ["Value 1", "Value 6"]
