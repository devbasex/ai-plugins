"""子の claude を起動するアカウントの指定（#2464）。

指定は `<状態の置き場>/relay/accounts/pinned.json` に 1 つだけ持つ（`ndf relay account use <名前>`）。選ぶ順は
環境変数 `NDF_CLAUDE_ACCOUNT` → 指定 → 無し（claude の既定）で、中継の起動と supervise の子の `claude -p` は
どちらも `chosen` で選ぶ（`session_env.child_env` が子へ渡す）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, Optional

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class AccountError(ValueError):
    """アカウントの名前の形が違う。"""


def pin_path(state_dir: Path) -> Path:
    return state_dir / "relay" / "accounts" / "pinned.json"


def pinned(state_dir: Path) -> Optional[str]:
    """指定のアカウントの名前。無い・壊れていれば None。"""
    try:
        data = json.loads(pin_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 指定が無ければ claude の既定で起動する
        return None
    name = data.get("account") if isinstance(data, dict) else None
    return name if isinstance(name, str) and NAME_RE.match(name) else None


def use(state_dir: Path, name: str) -> None:
    """指定を書く。"""
    if not NAME_RE.match(name):
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    path = pin_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"account": name}), encoding="utf-8")
    os.replace(tmp, path)


def clear(state_dir: Path) -> bool:
    """指定を消す。消したら真。"""
    path = pin_path(state_dir)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def chosen(environ: Mapping[str, str], state_dir: Path) -> Optional[str]:
    """子を起動するアカウント。環境変数が先で、無ければ指定。"""
    name = environ.get("NDF_CLAUDE_ACCOUNT")
    if name and NAME_RE.match(name):
        return name
    return pinned(state_dir)
