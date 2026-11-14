"""棚卸しの状態の置き場と JSON の読み書き。

置き場は 引数 → 環境変数（置き場の表 `backlog-state` の `env`）→ メインディレクトリ（既定ブランチの worktree）の
`.ndf/backlog/state` の順に決め、その下の `<repo>/` に書く。どの worktree から打っても同じ位置になり、マシンを
再起動しても消えない。一時ディレクトリへは倒さない（#1640・#1595 の関連 1）。

ファイルの名前は工程が書くファイルの表（`ndf-files` の `place` が `backlog-state` の行）から引く。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git, GitError
from ndf.contracts import facts

FILES = "ndf-files"


class StateError(Exception):
    """置き場を決められない・記録が壊れている。`kind` は止まった理由の種類。"""

    def __init__(self, detail: str, kind: str) -> None:
        super().__init__(detail)
        self.kind = kind


def main_dir(cwd: Path) -> Path:
    """メインディレクトリ（`git worktree list` の先頭の worktree）。git の外なら `StateError`。"""
    try:
        out = Git(cwd).run("worktree", "list", "--porcelain")
    except (GitError, OSError) as exc:
        raise StateError(f"メインディレクトリを解けない（git の外か git が無い）: {exc}", "precondition") from exc
    first = next((line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")), None)
    if first is None:
        raise StateError("git worktree list が worktree を返さない", "precondition")
    return Path(first)


def repo_key(repo: str) -> str:
    """リポジトリ（`<所有者>/<名前>`）の置き場の鍵。"""
    return repo.replace("/", "__")


def state_dir(cwd: Path, repo: str, arg: Optional[str] = None) -> Path:
    """状態のディレクトリ（作って返す）。作れなければ `StateError`。"""
    env = facts.value("places", "backlog-state", "env")
    if arg:
        base = Path(arg)
    elif os.environ.get(env):
        base = Path(os.environ[env])
    else:
        base = main_dir(cwd) / facts.value("places", "backlog-state", "path")
    path = base / repo_key(repo)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StateError(f"状態の置き場を作れない: {path}: {exc}", "precondition") from exc
    return path


def file_name(file_id: str, **parts: str) -> str:
    """表の行のパスから `<repo>/` を除いたファイルの名前（`<id>` などは `parts` で埋める）。"""
    path = facts.value(FILES, file_id, "path").split("/", 1)[1]
    for key, val in parts.items():
        path = path.replace(f"<{key}>", val)
    return path


def read(sd: Path, file_id: str, missing: Any, **parts: str) -> Any:
    """記録を読む。無ければ `missing`。壊れていれば `StateError`（空として続けない）。"""
    path = sd / file_name(file_id, **parts)
    if not path.exists():
        return missing
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"記録が読めない: {path}: {exc}", "invalid") from exc


def write(sd: Path, file_id: str, data: Any, **parts: str) -> Path:
    """記録を一時の名前で書いてから置き換える（書きかけを残さない）。"""
    path = sd / file_name(file_id, **parts)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=sd)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def append(sd: Path, file_id: str, row: dict[str, Any]) -> Path:
    """行の記録（JSON Lines）へ 1 行を足す。"""
    path = sd / file_name(file_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_lines(sd: Path, file_id: str) -> list[dict[str, Any]]:
    """行の記録を読む。無ければ空。壊れた行があれば `StateError`。"""
    path = sd / file_name(file_id)
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except ValueError as exc:
        raise StateError(f"記録の行が読めない: {path}: {exc}", "invalid") from exc
