"""check-skill-frontmatter.py が frontmatter を YAML として読む（#1142 の D8。lib/yamlio.py）。

移す前は `キー: 値` の行を字面で読み、値の引用符を外すだけだった。YAML として読むため、次の入力で結果が変わる。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-frontmatter.py"


def _run(tmp_path: Path, front: str) -> str:
    skills_dir = tmp_path / "plugins/ndf/skills"
    (skills_dir / "probe").mkdir(parents=True)
    (skills_dir / "probe" / "SKILL.md").write_text(f"---\n{front}---\n\n# probe\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(CHECKER), "--skills-dir", str(skills_dir)], capture_output=True, text=True, check=False)
    return p.stdout + p.stderr


def test_unreadable_yaml_is_an_error_instead_of_a_missing_key(tmp_path):
    out = _run(tmp_path, 'name: probe\ndescription: "Probe. Use when testing."\ncontext: [fork\n')
    assert "[spec/frontmatter] probe: YAML として読めない" in out


def test_escaped_quotes_are_read_as_the_yaml_value(tmp_path):
    """`\\"` は 1 文字として数え、二重引用符で囲んだ description として扱う。"""
    out = _run(tmp_path, 'name: probe\ndescription: "Probe \\"x\\". Use when testing."\n')
    assert "portability/quote" not in out
    assert "ERROR" not in out


def test_boolean_flags_are_read_as_yaml_booleans(tmp_path):
    """`disable-model-invocation: yes` は YAML 1.2 の真偽値ではないため、明示指示専用として扱わない。"""
    out = _run(tmp_path, 'name: probe\ndescription: "Probe. Use when testing."\ndisable-model-invocation: true\n')
    assert "portability/explicit-only" in out
    out = _run(tmp_path / "b", 'name: probe\ndescription: "Probe. Use when testing."\ndisable-model-invocation: yes\n')
    assert "portability/explicit-only" not in out


# 現状固定: _check_ops が description に求める語（I-008）
_EXPLICIT_WORDS = ["明示", "explicit", "Explicit"]
_INJECT_WORDS = [
    "知識として",
    "参照する",
    "実行しない",
    "reference only",
    "do not execute",
    "工程と AI から呼ぶ",
    "called by the workflow",
]


@pytest.mark.parametrize("word", _EXPLICIT_WORDS)
def test_explicit_only_accepts_each_word(tmp_path, word):
    out = _run(tmp_path, f'name: probe\ndescription: "Probe {word}. Use when testing."\ndisable-model-invocation: true\n')
    assert "portability/explicit-only" not in out


def test_explicit_only_rejects_description_without_word(tmp_path):
    out = _run(tmp_path, 'name: probe\ndescription: "Probe EXPLICIT. Use when testing."\ndisable-model-invocation: true\n')
    assert "portability/explicit-only" in out


@pytest.mark.parametrize("word", _INJECT_WORDS)
def test_inject_only_accepts_each_word(tmp_path, word):
    out = _run(tmp_path, f'name: probe\ndescription: "Probe {word}. Use when testing."\nuser-invocable: false\n')
    assert "portability/inject-only" not in out


def test_inject_only_rejects_description_without_word(tmp_path):
    out = _run(tmp_path, 'name: probe\ndescription: "Probe Reference Only. Use when testing."\nuser-invocable: false\n')
    assert "portability/inject-only" in out


@pytest.mark.parametrize("key", ["agent", "background"])
def test_context_fork_is_required_for_agent_and_background(tmp_path, key):
    out = _run(tmp_path, f'name: probe\ndescription: "Probe. Use when testing."\n{key}: x\n')
    assert "ops/context-fork" in out
    out = _run(tmp_path / "b", f'name: probe\ndescription: "Probe. Use when testing."\ncontext: fork\n{key}: x\n')
    assert "ops/context-fork" not in out
