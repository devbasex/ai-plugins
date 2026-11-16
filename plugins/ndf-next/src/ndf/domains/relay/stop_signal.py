"""中継のラッパーへの停止シグナルファイル（`ndf relay stop`・利用者の `touch` が置き、ラッパーが読む）。

| ファイル | 書き手 | 中身 |
| --- | --- | --- |
| `stop` | `ndf relay stop`・利用者の `touch` | 停止シグナルファイル（中身は空）。中継は次の区間を始めず、今のセッションが終わると終わる |

名前は工程が書くファイルの表（`ndf-files` の `relay-stop`）から引く。表を読むため、hook の経路から読む
`signals` には置かない（hook の import の表 `contracts/hooks/imports.json` は `ndf.contracts` を禁じる）。
"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import facts

# 利用者が `touch` する名前でもある
STOP_FILE = Path(facts.value("ndf-files", "relay-stop", "path")).name


def has_stop(directory: Path) -> bool:
    return (directory / STOP_FILE).is_file()


def place_stop(directory: Path) -> None:
    """停止シグナルファイルを置く。ラッパーの置き場が無ければ `OSError`（作らない）。"""
    (directory / STOP_FILE).touch(mode=0o600)


def drop_stop(directory: Path) -> None:
    """停止シグナルファイルを消す（無くてもよい）。"""
    (directory / STOP_FILE).unlink(missing_ok=True)
