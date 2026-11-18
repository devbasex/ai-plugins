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
    return subprocess.run(
        [sys.executable, str(GENERATOR), str(skills_dir), str(manifest), "true" if check else "false"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_explicit_only_skills_get_a_policy_and_the_hint(tmp_path):
    skills_dir, manifest = _tree(
        tmp_path,
        {
            "a": 'disable-model-invocation: true\nargument-hint: "<PR 番号> [\\"x\\"]"\n',
            "b": "description: plain\n",
        },
    )
    assert _run(skills_dir, manifest).returncode == 0
    assert (skills_dir / "a/agents/openai.yaml").read_text(encoding="utf-8") == (
        'policy:\n  allow_implicit_invocation: false\ninterface:\n  default_prompt: "<PR 番号> [\\"x\\"]"\n'
    )
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


def _table(tmp_path: Path, skills: dict[str, list[str]], policy: str | None = "agents/openai.yaml") -> Path:
    """配る Skill の表（manifests/skills.json の形）。値は Skill ごとの配るランタイム。"""
    import json

    table = tmp_path / "skills.json"
    table.write_text(
        json.dumps(
            {
                "runtimes": {"claude": {"definition": None, "policy": None}, "codex": {"definition": None, "policy": policy}},
                "skills": [{"name": n, "runtimes": rts} for n, rts in skills.items()],
            }
        ),
        encoding="utf-8",
    )
    return table


def test_the_skill_table_decides_what_codex_gets(tmp_path):
    """表の行が codex を持つ Skill にだけ policy を書く。claude だけの行には書かない。"""
    skills_dir, _ = _tree(tmp_path, {"a": "disable-model-invocation: true\n", "c": "disable-model-invocation: true\n"})
    table = _table(tmp_path, {"a": ["claude", "codex"], "c": ["claude"]})
    assert _run(skills_dir, table).returncode == 0
    assert (skills_dir / "a/agents/openai.yaml").is_file()
    assert not (skills_dir / "c/agents").exists()
    assert _run(skills_dir, table, check=True).returncode == 0


def test_the_table_policy_path_is_used_and_a_null_policy_writes_nothing(tmp_path):
    """policy の保存先は表の runtimes.codex.policy から読む。null なら何も書かない。"""
    skills_dir, _ = _tree(tmp_path, {"a": "disable-model-invocation: true\n"})
    assert _run(skills_dir, _table(tmp_path, {"a": ["codex"]}, policy="meta/p.yaml")).returncode == 0
    assert (skills_dir / "a/meta/p.yaml").is_file() and not (skills_dir / "a/agents").exists()
    (skills_dir / "a/meta/p.yaml").unlink()
    assert _run(skills_dir, _table(tmp_path, {"a": ["codex"]}, policy=None)).returncode == 0
    assert not (skills_dir / "a/meta/p.yaml").exists()
