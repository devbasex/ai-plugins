"""SKILL.md の本文に位置引数（`$0`〜`$9`）を書くとチェックが止める（#990）。

Claude Code は Skill の本文の `$0` `$1` … を起動時の引数へ置き換える。bash の関数や
awk の位置引数として書くと、起動引数に化けて手順が壊れる。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-frontmatter.py"
CODE = "portability/positional-arg"


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    skills_dir = tmp_path / "plugins/ndf/skills"
    (skills_dir / "probe").mkdir(parents=True)
    (skills_dir / "probe" / "SKILL.md").write_text(
        '---\nname: probe\ndescription: "Probe the checker. Use when testing."\n---\n\n'
        f"# probe\n\n{body}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(CHECKER), "--skills-dir", str(skills_dir)],
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("line", [
    'phase() { echo "$1"; }',
    "awk '{ print $0 }' file",
    'local phase=${1}',
])
def test_a_positional_argument_in_the_body_is_an_error(tmp_path, line):
    r = _run(tmp_path, f"```bash\n{line}\n```")
    assert r.returncode != 0
    assert CODE in r.stdout + r.stderr


@pytest.mark.parametrize("line", ['echo "$ARGUMENTS"', 'echo "$PHASE" "${ID}"', "費用は 5 ドル"])
def test_named_variables_pass(tmp_path, line):
    r = _run(tmp_path, f"```bash\n{line}\n```")
    assert CODE not in r.stdout + r.stderr
