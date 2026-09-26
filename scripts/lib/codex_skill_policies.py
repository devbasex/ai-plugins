#!/usr/bin/env python3
"""Codex の Skill ごとの `agents/openai.yaml` を生成・確認する（`scripts/build-runtime-plugins.sh` の一部）。

明示指示専用の Skill（frontmatter の `disable-model-invocation: true`）だけに、暗黙の起動を止める policy と、
`argument-hint` があれば明示起動時の既定の文を書く。frontmatter は `lib/yamlio.py`（ruamel.yaml）で読む
（#1142 の D8）。

    python3 scripts/lib/codex_skill_policies.py <skills ディレクトリ> <codex-skills.txt> <true|false>

3 つ目が `true` なら書かずに確かめ、食い違いを標準エラーに出して終了コード 1 で終わる。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("yamlio")
import yamlio  # noqa: E402


def yaml_double_quoted(value: str) -> str:
    """YAML の二重引用符の文字列（JSON の文字列は YAML の二重引用符の形でもある）。"""
    return json.dumps(value, ensure_ascii=False)


def published_skills(manifest_path: Path) -> set[str]:
    return {line.split("#", 1)[0].strip()
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()}


def expected_policies(skills_dir: Path, published: set[str]) -> dict[Path, str]:
    expected: dict[Path, str] = {}
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file() or skill_dir.name not in published:
            continue
        fields = yamlio.read_front_matter(skill_md.read_text(encoding="utf-8"), str(skill_md))
        if fields.get("disable-model-invocation") is not True:
            continue
        lines = ["policy:", "  allow_implicit_invocation: false"]
        argument_hint = fields.get("argument-hint")
        if argument_hint:
            lines += ["interface:", f"  default_prompt: {yaml_double_quoted(str(argument_hint))}"]
        expected[skill_dir / "agents" / "openai.yaml"] = "\n".join(lines) + "\n"
    return expected


def sync(skills_dir: Path, manifest_path: Path, check: bool) -> bool:
    """生成物を書く（`check` なら確かめる）。食い違いがあれば偽。"""
    expected = expected_policies(skills_dir, published_skills(manifest_path))
    stale = set(skills_dir.glob("*/agents/openai.yaml")) - set(expected)
    ok = True
    for path, content in expected.items():
        if check:
            if not path.is_file():
                print(f"Generated file missing: {path}", file=sys.stderr)
                ok = False
            elif path.read_text(encoding="utf-8") != content:
                print(f"Generated file is out of date: {path}", file=sys.stderr)
                ok = False
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    for path in sorted(stale):
        if check:
            print(f"Generated file is stale: {path}", file=sys.stderr)
            ok = False
        else:
            path.unlink()
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
    return ok


def main(argv: list[str]) -> int:
    skills_dir, manifest_path, check = Path(argv[0]), Path(argv[1]), argv[2] == "true"
    try:
        return 0 if sync(skills_dir, manifest_path, check) else 1
    except yamlio.YamlError as exc:
        print(f"frontmatter を読めない: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
