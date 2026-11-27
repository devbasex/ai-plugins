"""状態の保存先の JSON の記録を読み書きする（`runner`・`steps`・`queue_run`・`cli` が使う）。

書くときは一時の名前へ書いてから置き換え、読む側に書きかけを見せない。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import worker_paths


# NDF が書いた記録の JSON が、記録の種別を持つキー。消す前にこの値で NDF の記録かを確かめる
RECORD_KIND_KEY = "ndf_record"
# 承認の記録の保存先のファイル。前の 2 つ（と `units_files.REVIEWED_HEAD`）は設計の Pull Request のレビューが書き、後の 1 つは `units check` が書く
REVIEWED_PR = "reviewed-pr"
REVIEWED_BASE = "reviewed-base"
CHECKED_FILE = "checked.json"
# `checked.json` の記録の種別（`RECORD_KIND_KEY` の値）。種別の違うファイルは消さず、上書きもしない
CHECKED_KIND = "design-units-check"


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 無い・書きかけの記録は無いものとして扱う
        return None
    return found if isinstance(found, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class RecordRefused(Exception):
    """記録の保存先に、NDF の記録と確かめられないファイルがある。"""


def discard_record(wd: worker_paths.WorkDir, name: str, kind: str) -> None:
    """開いたディレクトリ `wd` の直下の、前に書いた `name` を消す。

    種別 `kind` の記録と読めないもの（シンボリックリンク・ハードリンクを含む）は消さずに `RecordRefused`。
    """
    try:
        held = worker_paths.read_file(wd, name)
        found = None if held is None else json.loads(held)
    except (worker_paths.EntryError, json.JSONDecodeError):
        held, found = "", None
    if held is None:
        return
    if not isinstance(found, dict) or found.get(RECORD_KIND_KEY) != kind:
        raise RecordRefused(f"{wd.path / name} は NDF が書いた記録と確かめられないので消さない（中身を調べて退けてから打ち直す）")
    os.unlink(name, dir_fd=wd.fd)


def publish_record(wd: worker_paths.WorkDir, name: str, kind: str, value: dict[str, Any]) -> None:
    """種別 `kind` の記録を、一意の一時の名前へ書き終えてから、`name` が空いているときだけ `wd` の直下へ公開する。"""
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=wd.fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({RECORD_KIND_KEY: kind, **value}, out, ensure_ascii=False)
        os.link(tmp, name, src_dir_fd=wd.fd, dst_dir_fd=wd.fd, follow_symlinks=False)
    except FileExistsError:
        raise RecordRefused(f"{wd.path / name} が書く前に置かれたので上書きしない（中身を調べて退けてから打ち直す）") from None
    finally:
        os.unlink(tmp, dir_fd=wd.fd)
