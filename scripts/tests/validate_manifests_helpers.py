"""定義ファイルの検査（`scripts/validate-runtime-plugins.sh` の Python ブロック）のテスト補助。

検査の本体はシェルスクリプトの中のヒアドキュメントに埋まっている。シェルスクリプト全体を
動かすと `claude plugin validate` や Kiro の installer まで走り、確かめたい突き合わせと
関係の無い理由で結果が変わる。そこでヒアドキュメントの本文だけを取り出し、一時ディレクトリへ
作った木に対して実行する。実物の定義ファイルは読むだけで、書き換えない。

版数（9.3.0）と Skill 数（5 / 3）は実物（9.6.0 / 33 / 31）と重ならない値にしてある。
テストが実物の値へ依存していないことを、値そのもので示すためである。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE = REPO_ROOT / "scripts/validate-runtime-plugins.sh"

# ヒアドキュメントの開始行と終端。検査の本体はこの 2 行に挟まれている。
HEREDOC_START = 'run python3 - "$ROOT_DIR" "${FAMILIES[@]}" <<\'PY\''
HEREDOC_END = "PY"

FAMILY = "ndf"
VERSION = "9.3.0"

# ランタイムごとの配布 Skill 数を別の値にして、取り違えを検出できるようにする。
MANIFESTS = {
    "claude": ["alpha", "bravo", "charlie", "delta", "echo"],
    "codex": ["alpha", "bravo", "charlie"],
}


def extract_checker() -> str:
    """シェルスクリプトから検査の本体（Python）を取り出す。

    取り出せないときは落とす。素通りさせると、ヒアドキュメントの書き方が変わったときに
    テストが空の本文を実行して「通った」と報告する。
    """
    lines = VALIDATE.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(HEREDOC_START)
    except ValueError:  # pragma: no cover - 取り出せないこと自体が検査対象
        raise AssertionError(
            f"{VALIDATE} に検査の本体のヒアドキュメント（{HEREDOC_START}）が無い"
        )
    end = lines.index(HEREDOC_END, start + 1)
    return "\n".join(lines[start + 1 : end]) + "\n"


def description(version: str, skill_count: int) -> str:
    """定義ファイルの `description` の書き方。版数と Skill 数の両方を含める。"""
    return f"Fixture plugin (v{version}): {skill_count} focused skills for tests."


def build_tree(base: Path, version: str = VERSION, described: str | None = None) -> Path:
    """検査が読む定義ファイルだけを備えた木を作り、その根を返す。

    `described` を渡すと、`description` に書く版数だけを `version` と別にできる。
    接尾辞を落とした書き方が落ちることは、この食い違いでしか作れない。
    """
    described = version if described is None else described
    root = base / "repo"
    ndf = root / f"plugins/{FAMILY}"

    (ndf / "manifests").mkdir(parents=True)
    for runtime, skills in MANIFESTS.items():
        body = "# コメント行と空行は数えない\n\n" + "".join(f"{name}\n" for name in skills)
        (ndf / f"manifests/{runtime}-skills.txt").write_text(body, encoding="utf-8")

    for name in MANIFESTS["claude"]:
        (ndf / "skills" / name).mkdir(parents=True)
        (ndf / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    for runtime, manifest_dir in (("claude", ".claude-plugin"), ("codex", ".codex-plugin")):
        (ndf / manifest_dir).mkdir(parents=True)
        (ndf / manifest_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": FAMILY,
                    "version": version,
                    "description": description(described, len(MANIFESTS[runtime])),
                    "skills": [f"./skills/{name}" for name in MANIFESTS[runtime]],
                },
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

    # MCP プラグインの検査はディレクトリを走査する。空でも存在していないと読めない。
    (root / "plugins/mcp").mkdir(parents=True)
    return root


def run_check(root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """取り出した検査を子プロセスとして実行し、終了コードと出力を観測する。"""
    checker = tmp_path / "validate_manifests.py"
    checker.write_text(extract_checker(), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(checker), str(root), FAMILY],
        capture_output=True,
        text=True,
        check=False,
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr
