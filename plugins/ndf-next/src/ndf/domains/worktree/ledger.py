"""テスト環境の割り当ての台帳（`<git の共通ディレクトリ>/ndf/worktree-registry.json`）。

割り当て 1 件は `{id, worktree, branch, environment, slot, ports, assigned_at, last_used_at, released_at, expose,
golden_tag?}` で、解放しても行を消さず `released_at` を書く（履歴は追記で持つ）。今の割り当ては worktree ごとの
未解放の最後の 1 件である。読み書きは台帳の排他（同じ置き場の `.lock`）の中で行い、空きの判定と行の追加を
同じ排他の中で行う（排他の外で空きを読むと、同時に走った別のプロセスと同じスロットを掴む）。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from filelock import FileLock

from ndf.adapters.git import Git
from ndf.domains.worktree import atomic, testenv_decide

# 台帳の置き場（git の共通ディレクトリの下）と、排他を待つ上限の秒
LEDGER = Path("ndf") / "worktree-registry.json"
LOCK_WAIT = 30
Row = dict[str, Any]
# 台帳の時刻の書式（書く側の `now` と、読む側の `testenv_sweep` が同じ書式を使う）
STAMP = "%Y-%m-%dT%H:%M:%SZ"
# 割り当ての ID と証跡の置き場に付ける時刻の書式
COMPACT_STAMP = "%Y%m%dT%H%M%SZ"


def active(row: Row) -> bool:
    """未解放の割り当てか（`released_at` が無い）。"""
    return row.get("released_at") is None


def exposing(row: Row) -> bool:
    """公開が開いているか（解放の後も閉じるまで開いている）。"""
    return isinstance(row.get("expose"), dict) and row["expose"].get("closed_at") is None


def now() -> str:
    return time.strftime(STAMP, time.gmtime())


def ledger_path(git: Git) -> Path:
    return git.common_dir() / LEDGER


class Ledger:
    """1 つのリポジトリの台帳。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def of(cls, git: Git) -> "Ledger":
        return cls(ledger_path(git))

    def rows(self) -> list[Row]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        rows = data.get("assignments") if isinstance(data, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    @contextmanager
    def editing(self) -> Iterator[list[Row]]:
        """排他を取り、行を読んで渡し、抜けるときに書き戻す（同じディレクトリの一時ファイルから付け替える）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=LOCK_WAIT):
            rows = self.rows()
            yield rows
            atomic.write_text(self.path, json.dumps({"assignments": rows}, ensure_ascii=False, indent=1))

    def current(self, worktree: str) -> Optional[Row]:
        """worktree の今の割り当て（未解放の最後の 1 件）。"""
        return latest(self.rows(), worktree, active)

    def acquire(self, worktree: str, branch: str, environment: str) -> Optional[Row]:
        """割り当てを取る。既にあればそれを返す。空きが無ければ `None`。"""
        with self.editing() as rows:
            got = latest(rows, worktree, active)
            if got is not None:
                return got
            slot = testenv_decide.free_slot(r["slot"] for r in rows if active(r))
            if slot is None:
                return None
            row = {"id": f"{time.strftime(COMPACT_STAMP, time.gmtime())}-{os.getpid()}", "worktree": worktree, "branch": branch,
                   "environment": environment, "slot": slot, "ports": {}, "assigned_at": now(), "last_used_at": now(),
                   "released_at": None, "expose": None}  # fmt: skip
            rows.append(row)
            return row

    def update(self, worktree: str, change: Callable[[Row], None], pick: Optional[Callable[[Row], bool]] = None) -> bool:
        """worktree の今の割り当て（か `pick` に合う最後の 1 件）を `change` で書き換える。無ければ偽。"""
        with self.editing() as rows:
            row = latest(rows, worktree, pick if pick is not None else active)
            if row is None:
                return False
            change(row)
            return True


def latest(rows: list[Row], worktree: str, pick: Callable[[Row], bool]) -> Optional[Row]:
    found = [r for r in rows if r.get("worktree") == worktree and pick(r)]
    return found[-1] if found else None
