"""Codex の `agents/openai.yaml` の生成（scripts/lib/codex_skill_policies.py。#1142 の D8）。

frontmatter を YAML として読む。移す前は `キー: 値` の字面で読み、引用符を外した文字列 `true` を真として扱った。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[1] / "lib" / "codex_skill_policies.py"


def _tree(tmp_path: Path, skills: dict[str, str]) -> tuple[Path, Path]:
    skills_dir = tmp_path / "skills"
    for name, front in skills.items():
        (skills_dir / name).mkdir(parents=True)
        (skills_dir / name / "SKILL.md").write_text(f"---\nname: {name}\n{front}---\n\n# {name}\n", encoding="utf-8")
    manifest = tmp_path / "codex-skills.txt"
    manifest.write_text("# 配る Skill\n" + "".join(f"{n}\n" for n in skills), encoding="utf-8")
    return skills_dir, manifest


def _run(skills_dir: Path, manifest: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GENERATOR), str(skills_dir), str(manifest), "true" if check else "false"],
                          capture_output=True, text=True, check=False)


def test_explicit_only_skills_get_a_policy_and_the_hint(tmp_path):
    skills_dir, manifest = _tree(tmp_path, {
        "a": 'disable-model-invocation: true\nargument-hint: "<PR 番号> [\\"x\\"]"\n',
        "b": "description: plain\n",
    })
    assert _run(skills_dir, manifest).returncode == 0
    assert (skills_dir / "a/agents/openai.yaml").read_text(encoding="utf-8") == (
        'policy:\n  allow_implicit_invocation: false\ninterface:\n  default_prompt: "<PR 番号> [\\"x\\"]"\n')
    assert not (skills_dir / "b/agents").exists()
    assert _run(skills_dir, manifest, check=True).returncode == 0


def test_a_quoted_true_is_a_string_not_a_flag(tmp_path):
    """変わる入力: `disable-model-invocation: "true"` は YAML の文字列で、明示指示専用として扱わない。"""
    skills_dir, manifest = _tree(tmp_path, {"a": 'disable-model-invocation: "true"\n'})
    assert _run(skills_dir, manifest).returncode == 0
    assert not (skills_dir / "a/agents").exists()


def test_check_reports_a_stale_file(tmp_path):
    skills_dir, manifest = _tree(tmp_path, {"a": "description: plain\n"})
    (skills_dir / "a/agents").mkdir()
    (skills_dir / "a/agents/openai.yaml").write_text("policy: {}\n", encoding="utf-8")
    p = _run(skills_dir, manifest, check=True)
    assert p.returncode == 1 and "Generated file is stale" in p.stderr
