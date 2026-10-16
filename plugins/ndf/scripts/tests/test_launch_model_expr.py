"""起動のモデルの決め方（#1290 の AC6・決定 10）。

cross-review の `launch-reviewer.sh` / `critique.sh` と cross-refactoring の `launch-cli.sh` は、
引数の明示（`models`）→ 既定のモデルへの切り替え（`participants.default_models`）→ 空の順でモデルを読む。
各スクリプトのモデルを読む行をそのまま bash で走らせ、状態ファイルから得る値を確かめる。
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

SKILLS = pathlib.Path(__file__).resolve().parents[2] / "skills"
LAUNCHERS = [
    SKILLS / "cross-review" / "scripts" / "launch-reviewer.sh",
    SKILLS / "cross-review" / "scripts" / "critique.sh",
    SKILLS / "cross-refactoring" / "scripts" / "launch-cli.sh",
]


def _model_line(path: pathlib.Path) -> str:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip().startswith("MODEL=$(jq")]
    assert len(lines) == 1, path
    return lines[0]


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
@pytest.mark.parametrize(
    ("state", "runtime", "want"),
    [
        ({"participants": {"default_models": {"codex": "gpt-6-astra"}}}, "codex", "gpt-6-astra"),
        ({"models": {"codex": "spec"}, "participants": {"default_models": {}}}, "codex", "spec"),
        ({"models": {"codex": None}, "participants": {"default_models": {"codex": "gpt-6-astra"}}}, "codex", "gpt-6-astra"),
        ({"participants": {"available": ["claude"]}}, "claude", ""),
        ({}, "kiro", ""),
    ],
)
def test_the_launch_model_follows_explicit_then_default(tmp_path, launcher, state, runtime, want):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    script = f'RUNTIME={runtime}; STATE={path}; {_model_line(launcher)}; printf %s "$MODEL"'
    got = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert got.stdout == want
