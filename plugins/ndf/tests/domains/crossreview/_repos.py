"""cross-review のテストの補助: 一時の bare リポジトリ（origin）と clone、結果 JSON の読み取り。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import git_sandbox
import pytest
from git_sandbox import sh
from result_json import last_result

from ndf.adapters import git
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


def count_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """以後の git の起動の引数を、起動のたびに足すリスト（出力をバイト列で読む起動も数える）。"""
    calls: list[list[str]] = []
    start = git._start

    def counted(args: list[str], *rest: Any, **named: Any) -> Any:
        calls.append(args)
        return start(args, *rest, **named)

    monkeypatch.setattr(git, "_start", counted)
    return calls


def make_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    main = tmp_path / "main"
    sh(tmp_path, "clone", "-q", str(origin), str(main))
    commit(main, "doc.md", "# 設計\n\n## セクション A\n\n本文\n", "初め")
    sh(main, "push", "-q", "origin", "HEAD:refs/heads/main", f"HEAD:refs/heads/{BRANCH}")
    sh(main, "fetch", "-q", "origin")
    other = tmp_path / "other"
    sh(tmp_path, "clone", "-q", "--branch", BRANCH, str(origin), str(other))
    return Repos(origin, main, other)


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    """サブコマンド `cross-review` を打ち、終了コードと結果 JSON を返す。"""
    code = cli.main(list(argv))
    return code, last_result(capsys.readouterr().out)
