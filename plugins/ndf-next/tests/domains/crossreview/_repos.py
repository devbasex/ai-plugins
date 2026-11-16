"""cross-review のテストの補助: 一時の bare リポジトリ（origin）と clone、結果 JSON の読み取り。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import git_sandbox
import pytest
from git_sandbox import sh

from ndf.domains.crossreview import cli

BRANCH = "feat/x"


def commit(cwd: Path, name: str, text: str, message: str) -> str:
    (cwd / name).parent.mkdir(parents=True, exist_ok=True)
    (cwd / name).write_text(text, encoding="utf-8")
    sh(cwd, "add", name)
    sh(cwd, "commit", "-q", "-m", message)
    return sh(cwd, "rev-parse", "HEAD").strip()


@dataclass(frozen=True)
class Repos:
    """origin（bare）・主の clone・別の書き手の clone。"""

    origin: Path
    main: Path
    other: Path

    def add_worktree(self, path: Path) -> Path:
        sh(self.main, "worktree", "add", "-q", "-B", f"review/{path.name}", str(path), f"origin/{BRANCH}")
        return path


def make_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    main = tmp_path / "main"
    sh(tmp_path, "clone", "-q", str(origin), str(main))
    commit(main, "doc.md", "# 設計\n\n## 節 A\n\n本文\n", "初め")
    sh(main, "push", "-q", "origin", "HEAD:refs/heads/main", f"HEAD:refs/heads/{BRANCH}")
    sh(main, "fetch", "-q", "origin")
    other = tmp_path / "other"
    sh(tmp_path, "clone", "-q", "--branch", BRANCH, str(origin), str(other))
    return Repos(origin, main, other)


def last_result(out: str) -> dict:
    """標準出力の最後の行の結果 JSON。"""
    return json.loads(out.strip().splitlines()[-1])


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    """副命令 `cross-review` を打ち、終了コードと結果 JSON を返す。"""
    code = cli.main(list(argv))
    return code, last_result(capsys.readouterr().out)
