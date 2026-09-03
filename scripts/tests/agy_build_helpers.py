"""生成物の同期（`scripts/build-runtime-plugins.sh`）のテスト補助。

生成の本体はシェルスクリプトで、自分の位置からリポジトリの根を決める。実物のリポジトリで
動かすと `dev.agy/skills/` を書き換えてしまうため、一時ディレクトリへ同じ配置の木を作り、
スクリプトを複製してそちらで動かす。**写しではなく実物を複製する**（写しを置くと、生成の
規則が 2 つに分かれる）。

Skill 名は実物（`cherry-pick-pr` など）と重ならない値にしてある。テストが実物の manifest へ
依存していないことを、値そのもので示すためである。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD = REPO_ROOT / "scripts/build-runtime-plugins.sh"

FAMILY = "ndf"
VERSION = "9.3.0"
AGY_SKILLS = ["alpha", "bravo", "charlie"]


def build_tree(base: Path, skills: list[str] | None = None) -> Path:
    """agy の生成に要るものだけを備えた木を作り、その根を返す。"""
    skills = AGY_SKILLS if skills is None else skills
    root = base / "repo"
    plugin = root / f"plugins/{FAMILY}"

    (root / "scripts").mkdir(parents=True)
    (root / "scripts/build-runtime-plugins.sh").write_text(
        BUILD.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # MCP プラグインの同期はディレクトリを走査する。空でも存在していないと読めない。
    (root / "plugins/mcp").mkdir(parents=True)

    (plugin / "manifests").mkdir(parents=True)
    (plugin / "manifests/agy-skills.txt").write_text(
        "# コメント行と空行は数えない\n\n" + "".join(f"{name}\n" for name in skills),
        encoding="utf-8",
    )
    for name in skills:
        (plugin / "skills" / name).mkdir(parents=True)
        (plugin / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (plugin / "agents").mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)

    (plugin / "dev.agy").mkdir(parents=True)
    (plugin / "dev.agy/plugin.json").write_text(
        json.dumps({"name": FAMILY, "version": VERSION}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def run_build(root: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    """複製したスクリプトを木の中で実行し、終了コードと出力を観測する。"""
    args = ["bash", str(root / "scripts/build-runtime-plugins.sh")]
    if check:
        args.append("--check")
    return subprocess.run(args, capture_output=True, text=True, check=False)


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def links_dir(root: Path) -> Path:
    return root / f"plugins/{FAMILY}/dev.agy/skills"
