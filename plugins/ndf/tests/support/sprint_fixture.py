"""`ndf.progress` の工程の呼び出しのテストが共有する、スプリントの作業場所と worker の既定の差し替え。"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import strategy_fixture

from ndf.core import route


def sprint_repo(tmp_path: Path, project: dict | None = None) -> Path:
    """ブランチ sprint/s1 に居る作業場所（リモート無し）。マージ先はブランチ戦略の設定（web-develop）のベースブランチ develop、
    `project` は `.ndf/project.json` のほかのセクション。"""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "sprint/s1", str(repo)], check=True)
    strategy_fixture.write(repo, "web-develop", project)
    return repo


def with_worker(monkeypatch) -> None:
    """params.json の worker に既定を置く（params.json は既定を持たない）。"""
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {**held, "worker": replace(held["worker"], default=["ndf-worker"])})
