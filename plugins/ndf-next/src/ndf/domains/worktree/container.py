"""コンテナの実行系（docker）と、宣言のコマンド（`sh -c`）の起動。

起動するのはこのモジュールの `Shell` だけで、起動の関数は差し替えられる（テストは docker を起動せずに出力を渡す）。
コンテナの実行系のコマンドは環境変数 `NDF_DOCKER_COMMAND` で差し替えられる（既定は `docker`）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ndf.adapters.git import Completed

# 実行系のコマンドを差し替える環境変数と、その既定
DOCKER_ENV = "NDF_DOCKER_COMMAND"
DOCKER = "docker"
# compose のプロジェクトのラベル
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
_PROJECT_UNSAFE = re.compile(r"[^a-z0-9_-]")

Runner = Callable[[list[str], Optional[Path], Optional[Mapping[str, str]]], Completed]
Attached = Callable[[list[str], Optional[Path], Optional[Mapping[str, str]]], int]


def run_process(argv: list[str], cwd: Optional[Path], env: Optional[Mapping[str, str]]) -> Completed:
    """コマンドを 1 回起動する（既定の起動の関数）。`env` は今の環境へ重ねる値。"""
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                              env={**os.environ, **env} if env else None)  # fmt: skip
    except OSError as exc:
        return Completed(127, "", f"{argv[0]} を起動できない: {exc}")
    return Completed(proc.returncode, proc.stdout, proc.stderr)


def run_attached(argv: list[str], cwd: Optional[Path], env: Optional[Mapping[str, str]]) -> int:
    """コマンドを標準入出力を引き継いで 1 回起動し、終了コードを返す（テストの出力をそのまま見せる）。"""
    try:
        return subprocess.run(argv, cwd=cwd, check=False, env={**os.environ, **env} if env else None).returncode
    except OSError as exc:
        raise ContainerError(f"{argv[0]} を起動できない: {exc}") from exc


class ContainerError(Exception):
    """コマンドを起動できない。"""


def compose_project(name: str) -> Optional[str]:
    """compose のプロジェクト名（実行系と同じく小文字へ揃え、`a-z0-9_-` 以外と先頭の `-`・`_` を落とす）。空なら `None`。"""
    got = _PROJECT_UNSAFE.sub("", name.lower()).lstrip("-_")
    return got if got else None


@dataclass(frozen=True)
class Shell:
    """宣言のコマンドとコンテナの実行系の起動。"""

    runner: Runner = run_process
    attached: Attached = run_attached
    docker: str = DOCKER

    @classmethod
    def from_env(cls) -> "Shell":
        value = os.environ.get(DOCKER_ENV)
        return cls(docker=value) if value else cls()

    def has_docker(self) -> bool:
        return shutil.which(self.docker) is not None

    def sh(self, command: str, cwd: Path, env: Optional[Mapping[str, str]] = None) -> Completed:
        """宣言のコマンドを `sh -c` で打つ。"""
        return self.runner(["sh", "-c", command], cwd, env)

    def docker_run(self, *args: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Completed:
        return self.runner([self.docker, *args], cwd, env)

    def containers(self, project: str, service: Optional[str] = None) -> list[str]:
        """プロジェクト（とサービス）の動いているコンテナの名前。読めなければ空。"""
        filters = ["--filter", f"label={PROJECT_LABEL}={project}"]
        if service:
            filters += ["--filter", f"label={SERVICE_LABEL}={service}"]
        done = self.docker_run("ps", *filters, "--format", "{{.Names}}")
        return [n for n in done.stdout.split() if n] if done.returncode == 0 else []
