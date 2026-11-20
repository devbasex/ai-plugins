"""cross-refactoring のテストに共通する補助: 使い捨ての git の作業場所（fixture `gitwork`）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class GitWork:
    """1 つの作業場所。`make` で最初のコミットを作り、`commit` で変更を積む。"""

    def __init__(self, work: Path) -> None:
        self.work = work

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=self.work, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def write(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def make(self, files: dict[str, str]) -> str:
        self.work.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        return self.commit(files, "base")

    def commit(self, files: dict[str, str], message: str = "change") -> str:
        """ファイルを書いてコミットし、コミットの SHA を返す。"""
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def gitwork(tmp_path: Path) -> GitWork:
    return GitWork(tmp_path / "work")


@pytest.fixture(autouse=True)
def _runtimes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """担当すべてを起動できる前提にする（学習の設定のその場の確かめ `training.ensure` は HTTP を打つ。#3907）。

    始める前の確かめそのものを見るテストは `training.ensure` を上書きする。
    """
    from ndf.adapters import training

    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: None)
