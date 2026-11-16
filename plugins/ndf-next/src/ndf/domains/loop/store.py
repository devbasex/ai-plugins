"""開発ループの記録の置き場と読み書き。

記録はチェックアウトの置き場（`<git の共通ディレクトリ>/<プラグイン名>/state`）の `loops/<ループの名前>/record.jsonl`
に、事象を 1 行 1 件で追記する。読み・判断・追記は 1 つの排他（`record.lock`）の下で行い、2 つのセッションが同じ
状態を読んで食い違う事象を書くことを防ぐ。排他を待つ上限は契約の `sprint-loop-record.lock` の秒である。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from ndf import identity
from ndf.contracts import limits
from ndf.core import durable
from ndf.domains.loop import record_decide as rec

# 置き場の下のディレクトリと、記録・排他のファイルの名前
LOOP_DIR = "loops"
EVENTS_FILE = "record.jsonl"
LOCK_FILE = "record.lock"
# 排他を待つ上限の契約の名前
LOCK_POLICY = "sprint-loop-record.lock"
# ループの名前の形（ディレクトリの名前になる）
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StoreError(Exception):
    """記録を読めない・名前の形が違う。"""


@dataclass(frozen=True)
class LoopStore:
    """ループ 1 本の記録の置き場。"""

    directory: Path

    @property
    def events_path(self) -> Path:
        return self.directory / EVENTS_FILE

    def read(self) -> list[dict[str, Any]]:
        """記録の事象を古い順に読む。記録が無ければ空。壊れた行があれば `StoreError`。"""
        path = self.events_path
        if not path.is_file():
            return []
        out = []
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                rec.check(ev)
            except (ValueError, KeyError, TypeError) as exc:
                raise StoreError(f"{path} の {no} 行目を読めない: {exc}") from None
            out.append(ev)
        return out

    def append(self, events: Sequence[dict[str, Any]]) -> None:
        """事象を末尾へ足す（書き換えない）。"""
        if not events:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n" for ev in events)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(text)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """記録の排他を取って中を実行する。上限まで取れなければ `filelock.Timeout`。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.directory / LOCK_FILE), timeout=limits.seconds(LOCK_POLICY)):
            yield


def check_name(name: str) -> str:
    """ループの名前を確かめて返す。形が違えば `StoreError`。"""
    if not NAME_PATTERN.match(name):
        raise StoreError(f"ループの名前は英数字で始まる 64 字までの英数字・.・_・-: {name!r}")
    return name


def open_store(name: str, cwd: Path) -> LoopStore:
    """作業場所 `cwd` のチェックアウトの、ループ `name` の記録の置き場。"""
    root = durable.store_root(identity.load().name, cwd)
    return LoopStore(root / LOOP_DIR / check_name(name))
