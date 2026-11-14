"""子の claude を起動するアカウントの指定と、使用率のキャッシュの読み取り（#2464・#3336・#3337）。

指定は `<状態の置き場>/relay/accounts/pinned.json` に 1 つだけ持つ（`ndf relay account use <名前>`）。選ぶ順は
環境変数 `NDF_CLAUDE_ACCOUNT` → 指定 → 無し（claude の既定）で、中継の起動と supervise の子の `claude -p` は
どちらも `chosen` で選ぶ（`session_env.child_env` が子へ渡す）。指定のファイルが壊れていれば `AccountError` を
送出し、呼び出し元が理由を出して止まる（読めない指定を黙って既定へ倒さない）。

使用率は使用率のセンサー（hook。M3a）が `usage.json` へ書くキャッシュで、中継は読むだけである（レーンの数だけ
API を呼ばない）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf.domains.relay import account_decide, signals

NAME_RE = signals.NAME_RE


class AccountError(ValueError):
    """アカウントの名前の形が違う・指定のファイルが壊れている。"""


def accounts_dir(state_dir: Path) -> Path:
    return state_dir / "relay" / "accounts"


def pin_path(state_dir: Path) -> Path:
    return accounts_dir(state_dir) / "pinned.json"


def usage_path(state_dir: Path) -> Path:
    return accounts_dir(state_dir) / "usage.json"


def pinned(state_dir: Path) -> Optional[str]:
    """指定のアカウントの名前。指定が無ければ None、壊れていれば `AccountError`。"""
    path = pin_path(state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountError(f"アカウントの指定を読めない（{path}）: {exc}。ndf relay account clear で消してから指定し直す") from exc
    name = data.get("account") if isinstance(data, dict) else None
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise AccountError(f"アカウントの指定の形が違う（{path}）。ndf relay account clear で消してから指定し直す")
    return name


def use(state_dir: Path, name: str) -> None:
    """指定を書く。"""
    if not NAME_RE.match(name):
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    _write_json(pin_path(state_dir), {"account": name})


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


def usages(state_dir: Path) -> list[account_decide.Usage]:
    """使用率のキャッシュ。無ければ空（センサーがまだ書いていない）、壊れていれば `AccountError`。"""
    path = usage_path(state_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountError(f"使用率のキャッシュを読めない（{path}）: {exc}") from exc
    rows = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise AccountError(f"使用率のキャッシュの形が違う（{path}）: accounts の配列が無い")
    return [_usage(row, path) for row in rows]


def _usage(row: Any, path: Path) -> account_decide.Usage:
    try:
        return account_decide.Usage(
            name=str(row["name"]),
            percent=float(row["percent"]),
            limited=bool(row["limited"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountError(f"使用率のキャッシュの行の形が違う（{path}）: {row!r}") from exc


def write_usages(state_dir: Path, rows: list[account_decide.Usage]) -> None:
    """使用率のキャッシュを書く（センサーとテストが使う）。"""
    _write_json(usage_path(state_dir), {"accounts": [r._asdict() for r in rows]})


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dict(data), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
