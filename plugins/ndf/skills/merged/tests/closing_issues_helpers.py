"""閉じる語の読み取りのテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# **実体はプラグインルート直下の共通層にある**（#424）。`merged` と gate の両方が
# ここから読むため、Skill の下に写しを置かない。
SCRIPT = Path(__file__).resolve().parents[3] / "scripts/lib/closing-issues.sh"
DEFAULT_REPO = "devbasex/ai-plugins"


def read(body: str, repo: str | None = DEFAULT_REPO, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """本文を標準入力から渡して、取り出された閉じる先を返す。"""
    args = ["bash", str(SCRIPT)]
    if repo is not None:
        args += ["--repo", repo]
    return subprocess.run(
        args, input=body, capture_output=True, text=True, cwd=str(cwd) if cwd else None
    )


def entries(body: str, repo: str | None = DEFAULT_REPO, cwd: Path | None = None) -> list[tuple[str, str]]:
    """`<所有者>/<リポジトリ>` と `<番号>` の組を、現れた順に返す。"""
    result = read(body, repo=repo, cwd=cwd)
    assert result.returncode == 0, result.stderr
    return [tuple(line.split("\t")) for line in result.stdout.splitlines() if line]


def numbers(body: str, repo: str | None = DEFAULT_REPO) -> list[str]:
    return [number for _repo, number in entries(body, repo=repo)]
