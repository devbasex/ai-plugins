"""環境ブランチへ届ける手順が、共通ライブラリと同じ起点ブランチを解決することを検証する。

3 Skill は作業ツリーの仕組みを前提にしないため、手順には共通ライブラリを読み込まずに
動く数行を書いている。写しである以上、両者が食い違う経路が残る。同じ入力に対して同じ
名前を返すことを、ここで突き合わせる（issue #202）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "plugins" / "ndf" / "scripts" / "lib" / "worktree-common.sh"
SKILLS = ROOT / "plugins" / "ndf" / "skills"

# 起点を解決する手順を持つ Skill と、その手順を見分ける目印。
INLINE_SKILLS = ("cherry-pick-pr", "deploy")
MARKER = "base=$(jq"

# 起点を字面で書かない Skill。開発の起点は既定ブランチとは限らない。
LITERAL_SKILLS = ("ndf-policies", "cherry-pick-pr", "deploy")

pytestmark = pytest.mark.skipif(
    any(shutil.which(name) is None for name in ("bash", "jq", "git")),
    reason="bash / jq / git が要る",
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """origin を持つリポジトリ。既定ブランチは main、origin に develop がある。"""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "test@example.com")
    git(main, "config", "user.name", "test")
    git(main, "config", "commit.gpgsign", "false")
    (main / "README.md").write_text("# test\n", encoding="utf-8")
    git(main, "add", "README.md")
    git(main, "commit", "-q", "-m", "init")

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "main")
    git(main, "remote", "set-head", "origin", "main")
    git(main, "branch", "develop")
    git(main, "push", "-q", "origin", "develop")
    git(main, "branch", "-D", "develop")
    return main


def snippet_of(name: str) -> str:
    """起点を解決する bash のコードブロックを取り出す。"""
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    blocks = [
        block
        for block in re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M)
        if MARKER in block
    ]
    assert len(blocks) == 1, f"{name}: 起点を解決する bash が 1 つではない（{len(blocks)} 個）"
    return blocks[0]


def resolve_inline(name: str, repo: Path) -> str:
    got = subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet_of(name)}\nprintf "%s\\n" "$base"\n'],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def resolve_library(repo: Path) -> str:
    got = subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n. "{LIB}"\nwt_base_branch "{repo}"\n'],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def write_declaration(repo: Path, body: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.json").write_text(json.dumps(body), encoding="utf-8")


DECLARATIONS = {
    "宣言なし": None,
    "起点の指定なし": {"version": 1},
    "起点あり": {"version": 1, "base_branch": "develop"},
    "版が対応外": {"version": 99, "base_branch": "develop"},
    "文字列でない": {"version": 1, "base_branch": 7},
}


@pytest.mark.parametrize("name", INLINE_SKILLS)
@pytest.mark.parametrize("case", list(DECLARATIONS))
def test_inline_resolution_matches_library(name: str, case: str, repo: Path) -> None:
    body = DECLARATIONS[case]
    if body is not None:
        write_declaration(repo, body)
    assert resolve_inline(name, repo) == resolve_library(repo)


@pytest.mark.parametrize("name", LITERAL_SKILLS)
def test_default_branch_is_not_hardcoded(name: str) -> None:
    """取り込む先を `origin/main` の字面で書かない。"""
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if "origin/main" in line]
    assert hits == [], f"{name}: {hits}"
