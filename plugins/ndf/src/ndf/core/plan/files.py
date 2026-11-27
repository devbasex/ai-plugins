"""状態の保存先の JSON の記録を読み書きする（`runner`・`steps`・`queue_run`・`cli` が使う）。

書くときは一時の名前へ書いてから置き換え、読む側に書きかけを見せない。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


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


def discard_record(target: Path, kind: str) -> None:
    """前に書いた `target` を消す。種別 `kind` の記録と読めないもの（リンクを含む）は消さずに `RecordRefused`。"""
    if not os.path.lexists(target):
        return
    try:
        found = None if target.is_symlink() else read_json(target)
    except UnicodeDecodeError:
        found = None
    if found is None or found.get(RECORD_KIND_KEY) != kind:
        raise RecordRefused(f"{target} は NDF が書いた記録と確かめられないので消さない（中身を調べて退けてから打ち直す）")
    target.unlink()


def publish_record(target: Path, kind: str, value: dict[str, Any]) -> None:
    """種別 `kind` の記録を、一意の一時の名前へ書き終えてから、`target` の名前が空いているときだけ公開する。"""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({RECORD_KIND_KEY: kind, **value}, out, ensure_ascii=False)
        os.link(tmp, target)
    except FileExistsError:
        raise RecordRefused(f"{target} が書く前に置かれたので上書きしない（中身を調べて退けてから打ち直す）") from None
    finally:
        Path(tmp).unlink(missing_ok=True)
