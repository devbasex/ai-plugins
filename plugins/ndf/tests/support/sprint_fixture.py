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
    """params.json の worker の既定を偽の起動に替える（プランに載ったことを見分けるため）。"""
    _set_worker(monkeypatch, ["ndf-worker"])


def without_worker(monkeypatch) -> None:
    """params.json の worker の既定を外す（差し込みがどの出所からも決まらない場合を作るため）。"""
    _set_worker(monkeypatch, None)


def _set_worker(monkeypatch, default: list[str] | None) -> None:
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {**held, "worker": replace(held["worker"], default=default)})
