#!/usr/bin/env python3
"""Codex の Skill ごとの `agents/openai.yaml` を生成・確認する（`scripts/build-runtime-plugins.sh` の一部）。

明示指示専用の Skill（frontmatter の `disable-model-invocation: true`）だけに、暗黙の起動を止める policy と、
`argument-hint` があれば明示起動時の既定の文を書く。frontmatter は `lib/yamlio.py`（ruamel.yaml）で読む
（#1142 の D8）。

    python3 scripts/lib/codex_skill_policies.py <skills ディレクトリ> <配る Skill の基準> <true|false>

配る Skill の基準は、配る Skill の表（`manifests/skills.json`。11.0.0 からの NDF）か `manifests/codex-skills.txt`
（playwright-kit）である。表は行の `runtimes` に `codex` を持つ Skill を配り、policy の保存先（Skill の保存先から）を
`runtimes.codex.policy` に持つ（null なら書かない）。`codex-skills.txt` の保存先は `agents/openai.yaml` である。
3 つ目が `true` なら書かずに確かめ、食い違いを標準エラーに出して終了コード 1 で終わる。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（docs/specifications/ndf-rebuild-libraries.md の決定 19）

require("yamlio")
import yamlio  # noqa: E402


def yaml_double_quoted(value: str) -> str:
    """YAML の二重引用符の文字列（JSON の文字列は YAML の二重引用符の形でもある）。"""
    return json.dumps(value, ensure_ascii=False)


RUNTIME = "codex"
# `codex-skills.txt` を持つ family の policy の保存先（Skill の保存先から）
DEFAULT_POLICY = "agents/openai.yaml"
# policy の保存先に許す拡張子（生成物は YAML）
POLICY_SUFFIXES = (".yaml", ".yml")


def _inside_skill_dir(policy: object) -> str:
    """policy の保存先が Skill のディレクトリの中を指す相対パスかを確かめて返す。外を指せば ValueError（生成先の外へ書かない・消さない）。"""
    if not isinstance(policy, str) or not policy:
        raise ValueError(f"runtimes.{RUNTIME}.policy が空でない文字列でない: {policy!r}")
    rel = Path(policy)
    if rel.is_absolute() or ".." in rel.parts or any(ch in policy for ch in "*?["):
        raise ValueError(f"runtimes.{RUNTIME}.policy は Skill のディレクトリの中の相対パスにする: {policy!r}")
    # 保存先のファイルはこの生成が持つ（中身によらず上書きし、余分は消す）。Skill の本文（SKILL.md）・参照の文書・スクリプトと
    # 衝突しないよう、policy の YAML の名前に限る
    if rel.suffix not in POLICY_SUFFIXES or rel.name == "SKILL.md":
        raise ValueError(f"runtimes.{RUNTIME}.policy は YAML のファイル（{' / '.join(POLICY_SUFFIXES)}）にする: {policy!r}")
    return policy


def published_skills(manifest_path: Path) -> tuple[set[str], str | None]:
    """(Codex へ配る Skill の名前, policy の保存先)。表の `runtimes.codex.policy` が null なら保存先は None。"""
    if manifest_path.suffix == ".json":
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        policy = (data["runtimes"].get(RUNTIME) or {}).get("policy")
        names = {row["name"] for row in data["skills"] if RUNTIME in row["runtimes"]}
        return names, None if policy is None else _inside_skill_dir(policy)
    names = {
        line.split("#", 1)[0].strip() for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.split("#", 1)[0].strip()
    }
    return names, DEFAULT_POLICY


def expected_policies(skills_dir: Path, published: set[str], rel: str = DEFAULT_POLICY) -> dict[Path, str]:
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
        expected[skill_dir / rel] = "\n".join(lines) + "\n"
    return expected


def _report_differences(expected: dict[Path, str], stale: set[Path]) -> bool:
    """書かずに確かめる。欠け・古さ・余分を全件、標準エラーに出す。食い違いがあれば偽。"""
    ok = True
    for path, content in expected.items():
        if not path.is_file():
            print(f"Generated file missing: {path}", file=sys.stderr)
            ok = False
        elif path.read_text(encoding="utf-8") != content:
            print(f"Generated file is out of date: {path}", file=sys.stderr)
            ok = False
    for path in sorted(stale):
        print(f"Generated file is stale: {path}", file=sys.stderr)
        ok = False
    return ok


def _write_policies(expected: dict[Path, str], stale: set[Path]) -> bool:
    """期待する生成物を書き、余分を名前順に消す（空になったディレクトリも消す）。常に真。"""
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path in sorted(stale):
        path.unlink()
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
    return True


def sync(skills_dir: Path, manifest_path: Path, check: bool) -> bool:
    """生成物を書く（`check` なら確かめる）。食い違いがあれば偽。"""
    published, rel = published_skills(manifest_path)
    if rel is None:
        return True
    expected = expected_policies(skills_dir, published, rel)
    stale = set(skills_dir.glob(f"*/{rel}")) - set(expected)
    return (_report_differences if check else _write_policies)(expected, stale)


def main(argv: list[str]) -> int:
    skills_dir, manifest_path, check = Path(argv[0]), Path(argv[1]), argv[2] == "true"
    try:
        return 0 if sync(skills_dir, manifest_path, check) else 1
    except yamlio.YamlError as exc:
        print(f"frontmatter を読めない: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"配る Skill の基準を読めない: {manifest_path}: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
