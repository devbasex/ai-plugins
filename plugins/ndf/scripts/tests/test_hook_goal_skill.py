"""hook_lib/goal_skill.py と `hook.py goal-skill` の試験（#1492）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from hook_lib import goal_skill  # noqa: E402


def _run(stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "hook.py"), "goal-skill"], input=stdin, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize(
    ("prompt", "name", "args"),
    [
        ("/goal /ndf:development-workflow #895", "ndf:development-workflow", "#895"),
        ("/goal /ndf:development-workflow #928 .ndf/handoff/issue-928.md の続きから", "ndf:development-workflow", "#928 .ndf/handoff/issue-928.md の続きから"),
        ("/goal /ndf:restart", "ndf:restart", ""),
        ("/goal /ndf:development-workflow #1\n2 行目", "ndf:development-workflow", "#1\n2 行目"),
        ("/goal /nosuch:skill #1", "nosuch:skill", "#1"),
    ],
)
def test_parse_reads_the_condition_skill_and_the_rest(prompt, name, args):
    got = goal_skill.parse(prompt)
    assert got is not None and got.name == name and got.args == args


@pytest.mark.parametrize(
    "prompt",
    ["/goal 引継ぎ文書の続きから", "/ndf:development-workflow #895", "/goal ndf:x", "/goal /x", "", "/goal 続きは /ndf:development-workflow で", None],
)
def test_parse_ignores_other_prompts(prompt):
    assert goal_skill.parse(prompt) is None


def test_the_hook_tells_to_load_the_skill_with_the_rest_as_arguments():
    out = _run(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "/goal /ndf:development-workflow #928 .ndf/handoff/issue-928.md の続きから"}))
    assert out.returncode == 0
    spec = json.loads(out.stdout)["hookSpecificOutput"]
    assert spec["hookEventName"] == "UserPromptSubmit"
    assert "`ndf:development-workflow`" in spec["additionalContext"]
    assert "`#928 .ndf/handoff/issue-928.md の続きから`" in spec["additionalContext"]


def test_the_hook_drops_the_argument_part_when_the_rest_is_empty():
    ctx = goal_skill.context({"prompt": "/goal /ndf:restart"})
    assert ctx is not None and "引数" not in ctx


@pytest.mark.parametrize("stdin", ["", "not json", "[]", json.dumps({"prompt": 1}), json.dumps({}), json.dumps({"prompt": "/goal 続きから"})])
def test_the_hook_passes_silently(stdin):
    out = _run(stdin)
    assert out.returncode == 0 and out.stdout == ""
