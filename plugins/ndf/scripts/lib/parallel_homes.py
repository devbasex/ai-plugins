"""計画の所在の一覧（#2733 の決定 1）: マシンにある並列の計画の置き場を指す一覧。

置き場は `<状態の根>/parallel/homes/<置き場の sha256 の先頭 12 字>.json`（`{"home": <絶対パス>, "at": <時刻>}`）。所在 1 つに
つき 1 ファイルで、一時ファイルからの置き換えで書く。消さない。一覧は置き場を指すだけで、計画の中身を写さない（I4）。
別のリポジトリのセッションが、レーンの判定と計画の外の判定に使う。書くのは `parallel.py init`・`start`・`lane add` だけ。

問いの hook（10 秒の上限）も読むため、ファイルだけで読む（pydantic と DBOS を import しない）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from parallel_files import PARALLEL_DIR
from state_home import container_home


def homes_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    return container_home(env) / "parallel" / "homes"


def _abs(p: Path) -> Path:
    try:
        return Path(p).resolve()
    except OSError:
        return Path(os.path.abspath(p))


def record(home: Path, env: Optional[Mapping[str, str]] = None) -> Path:
    """計画の置き場 `home`（メインディレクトリ）を一覧へ書く（同じ置き場は 1 ファイルを書き直す）。"""
    home = _abs(home)
    d = homes_dir(env)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / f"{hashlib.sha256(str(home).encode('utf-8')).hexdigest()[:12]}.json"
    data = {"home": str(home), "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".home-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return p


def _usable(home: Path) -> bool:
    """指す先に計画の置き場があり、持ち主が自分か（他の利用者の置き場は読み飛ばす。非機能のセキュリティ）。"""
    root = home / PARALLEL_DIR
    try:
        return root.is_dir() and (not hasattr(os, "getuid") or root.stat().st_uid == os.getuid())
    except OSError:
        return False


def homes(env: Optional[Mapping[str, str]] = None) -> list[Path]:
    """一覧にある計画の置き場（並ぶ順。重なりは 1 つ）。指す先に計画の置き場が無い・持ち主の違う所在は読み飛ばす（I4）。"""
    d = homes_dir(env)
    out: list[Path] = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return []
    for f in files:
        try:
            got = json.loads(f.read_text(encoding="utf-8")).get("home")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(got, str) or not os.path.isabs(got):
            continue
        h = Path(got)
        if h not in out and _usable(h):
            out.append(h)
    return out


def search_homes(main: Optional[Path], env: Optional[Mapping[str, str]] = None) -> list[Path]:
    """レーンの判定で探す置き場: 自分のメインディレクトリ（`main`）を先に、一覧の置き場を後に並べる（重なりは 1 つ）。"""
    first = [_abs(Path(main))] if main is not None else []
    return [*first, *[h for h in homes(env) if h not in first]]
