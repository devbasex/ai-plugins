"""テストの git の隔離（作者・コミッターと、system・global の設定を読まない）と、準備のための git の呼び出し。

テストは `import git_sandbox` で読む。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def isolate_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git の作者・コミッターを固定し、system と global の設定を読ませない（global は `tmp_path` の下）。"""
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
    }.items():
        monkeypatch.setenv(key, value)


def sh(cwd: Path, *args: str, strip: bool = False) -> str:
    """git を打って標準出力を返す（テストの準備だけに使う）。"""
    # strip なら前後の空白を除く（_drive_world の sh だけが除いていた）
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
    return out.strip() if strip else out
