"""定義ファイルのチェック（`scripts/validate-runtime-plugins.sh` が呼ぶ `scripts/lib/validate_manifests.py`）のテスト補助。

シェルスクリプト全体を動かすと `claude plugin validate` や Kiro の installer まで走り、確かめたい
突き合わせと関係の無い理由で結果が変わる。そこでチェックの本体だけを、一時ディレクトリへ作った木に
対して実行する。実物の定義ファイルは読むだけで、書き換えない。

版数（9.3.0）と Skill 数（5 / 3）は実物と重ならない値にしてある。
テストが実物の値へ依存していないことを、値そのもので示すためである。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE = REPO_ROOT / "scripts/validate-runtime-plugins.sh"
VERSION_PATTERN_SOURCE = REPO_ROOT / "scripts/lib/version_pattern.py"

# チェックの本体（validate-runtime-plugins.sh が呼ぶ）
MANIFEST_CHECKER = REPO_ROOT / "scripts/lib/validate_manifests.py"
FAMILY = "ndf"
VERSION = "9.3.0"

# ランタイムごとの配布 Skill 数を別の値にして、取り違えを検出できるようにする。
MANIFESTS = {
    "claude": ["alpha", "bravo", "charlie", "delta", "echo"],
    "codex": ["alpha", "bravo", "charlie"],
}


# 配る Skill の表の置き場と、ランタイムごとの Skill の並びを書く定義（None は並びを書かない）
SKILL_TABLE = "manifests/skills.json"
DEFINITIONS = {"claude": None, "codex": ".codex-plugin/plugin.json"}


def skill_table() -> dict:
    """`MANIFESTS` を配る Skill の表の形にする。行ごとに Skill と、それを配るランタイムを持つ。"""
    names = sorted({name for skills in MANIFESTS.values() for name in skills})
    return {
        "runtimes": {runtime: {"definition": DEFINITIONS[runtime], "policy": None} for runtime in MANIFESTS},
        "skills": [{"name": name, "runtimes": [r for r in MANIFESTS if name in MANIFESTS[r]]} for name in names],
    }


def description(version: str, skill_count: int) -> str:
    """定義ファイルの `description` の書き方。版数と Skill 数の両方を含める。"""
    return f"Fixture plugin (v{version}): {skill_count} focused skills for tests."


def build_tree(base: Path, version: str = VERSION, described: str | None = None) -> Path:
    """チェックが読む定義ファイルだけを備えた木を作り、その根を返す。

    `described` を渡すと、`description` に書く版数だけを `version` と別にできる。
    接尾辞を落とした書き方が落ちることは、この食い違いでしか作れない。
    """
    described = version if described is None else described
    root = base / "repo"
    ndf = root / f"plugins/{FAMILY}"

    # チェックの本体は根の下の共有の定義から版数の書式を読む。木の側にも同じファイルを置く。
    # **複製ではなく実物を複製する。** テスト用に書式を書き写すと、書式の定義が 2 つに戻る。
    (root / "scripts/lib").mkdir(parents=True)
    (root / "scripts/lib/version_pattern.py").write_text(VERSION_PATTERN_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")

    # 配る Skill の表（`manifests/skills.json`。11.0.0 からの NDF の形）。claude は並びを書かず skills/ を
    # すべて読み、codex は `.codex-plugin/plugin.json` に並びを書く
    (ndf / "manifests").mkdir(parents=True)
    (ndf / SKILL_TABLE).write_text(json.dumps(skill_table(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name in MANIFESTS["claude"]:
        (ndf / "skills" / name).mkdir(parents=True)
        (ndf / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    for runtime, manifest_dir in (("claude", ".claude-plugin"), ("codex", ".codex-plugin")):
        (ndf / manifest_dir).mkdir(parents=True)
        definition = {"name": FAMILY, "version": version, "description": description(described, len(MANIFESTS[runtime]))}
        if DEFINITIONS[runtime] is not None:
            definition["skills"] = [f"./skills/{name}" for name in MANIFESTS[runtime]]
        (ndf / manifest_dir / "plugin.json").write_text(
            json.dumps(
                definition,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "name": "fixture-marketplace",
                "plugins": [
                    {
                        "name": FAMILY,
                        "source": f"./plugins/{FAMILY}",
                        "description": description(described, len(MANIFESTS["claude"])),
                        "policy": {"installation": "AVAILABLE"},
                        "category": "Productivity",
                        "interface": {"displayName": "Fixture"},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # MCP プラグインのチェックはディレクトリを走査する。空でも存在していないと読めない。
    (root / "plugins/mcp").mkdir(parents=True)
    return root


def run_check(root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """取り出したチェックを子プロセスとして実行し、終了コードと出力を観測する。"""
    return subprocess.run(
        [sys.executable, str(MANIFEST_CHECKER), str(root), FAMILY],
        capture_output=True,
        text=True,
        check=False,
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr
