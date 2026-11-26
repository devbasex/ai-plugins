"""起動の承認のハッシュと記録（#1959）。

承認の対象は呼び手（`launch_settings`）が読んだ値を引数で受け、このモジュールは `launch_settings` を読まない。承認の記録は利用者の状態の
保存先にだけ置き（リポジトリのファイルは承認の根拠にしない）、追記だけで、所有者だけが読める。同じリポジトリのキーとハッシュの最後の行が
`approve` なら承認済みである。hook からも読むので、モジュールの頭では標準ライブラリと `identity` だけを読み込む。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf import identity

APPROVALS = "launch-approvals"


class Action(StrEnum):
    """承認の記録の操作。"""

    APPROVE = "approve"
    REVOKE = "revoke"


def canonical_sha(value: Any) -> str:
    """キーの順にそろえた JSON の SHA-256。字下げとキーの順の違いでは変わらない。"""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def target_digest(permissions: Mapping[str, Any], files: Mapping[str, Any]) -> str:
    """承認の対象（`permissions` の全体と、パスから定義ファイルの中身への対応）のハッシュ。"""
    return canonical_sha({"permissions": permissions, "files": files})


def repo_key(root: Path) -> str:
    """リポジトリのキー: git の共通ディレクトリの実体の絶対パス（リンクされた worktree は同じキー）。git の外は `root` の実体。"""
    from ndf.adapters import git

    try:
        return str(git.Git(root).common_dir())
    except (git.GitError, OSError):
        return os.path.realpath(root)


def state_file(file_id: str) -> Path:
    """状態の保存先の記録のファイル（表 ndf-files の `file_id` の行）。"""
    from ndf.contracts import facts

    return identity.load().state_dir / facts.value("ndf-files", file_id, "path")


def _whole_rows(path: Path) -> list[dict[str, Any]]:
    """改行で終わる行だけを読み、壊れた行を飛ばす。読めなければ空（承認していないと扱う）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows = (_parsed_row(line) for line in text.splitlines(keepends=True) if line.endswith("\n"))
    return [row for row in rows if row is not None]


def _parsed_row(line: str) -> Optional[dict[str, Any]]:
    """1 行の JSON のオブジェクト。書きかけや壊れた行は None（読む側はロックを取らないため、追記の途中の行がありうる）。"""
    try:
        row = json.loads(line)
    except ValueError:  # noqa: NDF401 ファイル: 読む側はロックを取らないので、追記の途中の行と壊れた行は飛ばす
        return None
    return row if isinstance(row, dict) else None


def is_approved(repo: str, sha256: str) -> bool:
    """同じリポジトリのキーとハッシュの最後の行が `approve` か。読む側はロックを取らない。"""
    last = None
    for row in _whole_rows(state_file(APPROVALS)):
        if row.get("repo") == repo and row.get("sha256") == sha256:
            last = row.get("action")
    return last == Action.APPROVE


def records_lock(path: Path) -> Any:
    """記録のファイルの錠（`<ファイル>.lock`）。待ちの上限は `launch.approvals-lock`。"""
    from filelock import FileLock

    from ndf.contracts import limits

    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock", timeout=limits.seconds("launch.approvals-lock"))


def write_row(path: Path, row: Mapping[str, Any]) -> None:
    """1 行を追記する（錠は呼び手が持つ）。ファイルは所有者だけが読める（0600）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append(repo: str, sha256: str, action: Action, approver: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """承認の記録へ 1 行足して返す。`approver` と `request_id` は承認の行にだけ置く。書けなければ `OSError` か錠の待ちの超過。"""
    action = Action(action)
    row: dict[str, Any] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "repo": repo, "sha256": sha256, "action": str(action)}
    if action is Action.APPROVE:
        row.update({k: v for k, v in (("approver", approver), ("request_id", request_id)) if v is not None})
    path = state_file(APPROVALS)
    with records_lock(path):
        write_row(path, row)
    return row
