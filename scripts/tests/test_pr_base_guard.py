"""既定ブランチ宛の Pull Request の分岐元を検査する（issue #202）。

配布のチャネルを分けるリポジトリでは、正式版のブランチへ直に Pull Request を出さない。
判定は宣言に起点が書かれていて、そのブランチが origin にあるときだけ働く。書く前・作る前は
成功で通す。時期を人の手で合わせずに済ませるためである。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from branch_repo_helpers import push_branch

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "check-pr-base.sh"



def declare(repo: Path, body: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.json").write_text(json.dumps(body), encoding="utf-8")


def guard(repo: Path, head_ref: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD), head_ref], cwd=str(repo), capture_output=True, text=True
    )


def test_without_declaration_passes(origin_repo: Path) -> None:
    """宣言が無いリポジトリでは検査しない。"""
    assert guard(origin_repo, "feature/x").returncode == 0


def test_without_base_branch_passes(origin_repo: Path) -> None:
    """チャネルを分けていないリポジトリでは検査しない。"""
    declare(origin_repo, {"version": 1})
    assert guard(origin_repo, "feature/x").returncode == 0


def test_unsupported_version_passes(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 99, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    assert guard(origin_repo, "feature/x").returncode == 0


def test_absent_base_branch_passes(origin_repo: Path) -> None:
    """起点ブランチをまだ作っていない間は、すべての Pull Request を通す。"""
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    assert guard(origin_repo, "feature/x").returncode == 0


def test_branch_with_matching_tail_does_not_enable_the_guard(origin_repo: Path) -> None:
    """origin への問い合わせは、完全な参照名で照合する。

    `git ls-remote` のパターンは参照名の末尾に一致するため、`develop` とだけ渡すと
    `refs/heads/feature/develop` にも一致する（実測）。起点ブランチが未作成のまま
    検査が有効にならないことを見る。
    """
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "feature/develop")
    assert guard(origin_repo, "feature/x").returncode == 0


def test_from_base_branch_passes(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    assert guard(origin_repo, "develop").returncode == 0


def test_from_other_branch_fails(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    got = guard(origin_repo, "feature/x")
    assert got.returncode != 0
    message = got.stdout + got.stderr
    assert "develop" in message
    assert "--base" in message


def test_missing_argument_fails(origin_repo: Path) -> None:
    """分岐元を渡し忘れたときは、通さずに使い方を出す。"""
    got = subprocess.run(
        ["bash", str(GUARD)], cwd=str(origin_repo), capture_output=True, text=True
    )
    assert got.returncode == 2
    assert "使い方" in got.stdout + got.stderr
