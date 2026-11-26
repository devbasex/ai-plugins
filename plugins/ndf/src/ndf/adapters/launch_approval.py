"""起動の承認のハッシュと記録（#1959）。

承認の対象は呼び手（`launch_settings`）が読んだ値を引数で受け、このモジュールは `launch_settings` を読まない。承認の記録は利用者の状態の
保存先にだけ置き（リポジトリのファイルは承認の根拠にしない）、追記だけで、所有者だけが読める。同じリポジトリのキーとハッシュの最後の行が
`approve` なら承認済みである。hook からも読むので、モジュールの頭では標準ライブラリと `identity` だけを読み込む。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


def _private_to_me(fd: int) -> bool:
    """開いた記録のファイルが、このユーザーの持ち物の普通のファイルで、ほかのユーザーが読み書きできないか。

    `os.open` の `0o600` は作るときにしか効かないため、復元などで権限が広がった既存のファイルは開いた後に確かめる。
    """
    st = os.fstat(fd)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


# 中断した追記の末尾を閉じるバイト列。UTF-8 として復号できない 1 バイトを含むので、閉じた行は JSON を書き終えていても読まれない
TORN_END = b"\xff\n"


def _whole_rows(path: Path) -> list[dict[str, Any]]:
    """改行で終わる行だけを読み、壊れた行を飛ばす。読めないか所有者だけの形でなければ空（承認していないと扱う）。

    バイト列のまま改行で区切り、行ごとに復号する。1 行の書きかけ（多バイト文字の途中で切れた行など）でほかの行を捨てないためである。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as f:
            if not _private_to_me(f.fileno()):
                return []
            data = f.read()
    except OSError:
        return []
    rows = (_parsed_row(line) for line in data.split(b"\n")[:-1])  # 最後の要素は改行で終わらない書きかけか空
    return [row for row in rows if row is not None]


def _parsed_row(line: bytes) -> Optional[dict[str, Any]]:
    """1 行の JSON のオブジェクト。書きかけや壊れた行は None（読む側はロックを取らないため、追記の途中の行がありうる）。"""
    try:
        row = json.loads(line.decode("utf-8"))
    except ValueError:  # noqa: NDF401 ファイル: 読む側はロックを取らないので、追記の途中の行・復号できない行・壊れた行は飛ばす
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
    """1 行を追記する（錠は呼び手が持つ）。ファイルは所有者だけが読める（0600）。

    既存のファイルが所有者だけの形でなければ書かずに `PermissionError` を投げる（承認者名とリポジトリのパスをほかのユーザーへ見せない）。
    末尾が改行で終わらない書きかけ（中断した追記）が残っていれば、先に `TORN_END` で閉じて次の行と分ける。つなげると読み手が両方を捨て、
    打ち直した操作が反映されない。改行だけで閉じると、JSON を書き終えたところで中断した行が、次の無関係な追記で有効になる。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as f:
        if not _private_to_me(f.fileno()):
            raise PermissionError(f"{path} が所有者だけの読み書きでない。所有者を自分にし、`chmod 600` にしてから打ち直す")
        size = os.fstat(f.fileno()).st_size
        torn = size > 0 and os.pread(f.fileno(), 1, size - 1) != b"\n"
        f.write((TORN_END if torn else b"") + (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))


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
