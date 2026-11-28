"""Issue（か PR）の本文の書き換え: 番号ごとのロックの中で読み、自分のセクションだけを差し替える（#4735）。

確認の記録（`ndf loop acceptance`）・進捗記録（`ndf.progress.records`）・クリーンアップの記録
（`ndf.domains.records.cleanup_stage`）が同じ本文を PATCH で丸ごと書き換えるので、3 つともここを通す。ロックの外で読んだ
本文は PATCH しない。画面の編集と別のマシンの書き込みはロックの外にあり、本文の更新には「読んだ後に変わっていなければ書く」という指定が
無いので防げない（消えた内容は GitHub の編集履歴から戻す）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import NamedTuple, Optional, Protocol

from filelock import FileLock, Timeout

from ndf import identity
from ndf.adapters.git import Git
from ndf.adapters.github import GitHub
from ndf.contracts import limits


class BodyStore(Protocol):
    """本文の読み書きの差し替え口。"""

    def read(self, number: int) -> str: ...

    def write(self, number: int, body: str) -> None: ...


@dataclass(frozen=True)
class GitHubBodies:
    """GitHub の本文の読み書き。"""

    hub: GitHub

    def read(self, number: int) -> str:
        return self.hub.issue_body(number).body

    def write(self, number: int, body: str) -> None:
        self.hub.rest("PATCH", f"/repos/{self.hub.repo}/issues/{int(number)}", {"body": body})


class Rewritten(NamedTuple):
    """書き換えの結果。最後に読んだか書いた本文・1 度でも書いたか・確かめが通ったか（`verify` が無ければ真）。"""

    body: str
    wrote: bool
    settled: bool


class LockError(OSError):
    """ロックのディレクトリかファイルを用意できない（権限が無いなど）。"""


@dataclass
class BodyLocks:
    """番号ごとの本文のロックの保存先。Git の共通ディレクトリの下に置き、同じリポジトリの worktree のすべてが共有する。

    まとめて書く側は 1 つを作って全部の番号に使い回す。共通ディレクトリを求める git の起動は、最初に使うときの 1 回で済む。
    """

    cwd: Path

    @cached_property
    def directory(self) -> Path:
        return Git(self.cwd).common_dir() / identity.load().name

    def path(self, issue: int) -> Path:
        return self.directory / f"acceptance-{int(issue)}.lock"


def rewrite(
    store: BodyStore,
    locks: BodyLocks,
    issue: int,
    change: Callable[[str], str],
    *,
    verify: Optional[Callable[[str], bool]] = None,
    retries: int = 0,
) -> Rewritten:
    """番号のロックの中で本文を読み、`change` で差し替え、変わったときだけ PATCH する。

    `verify` を渡すと、読んだ本文が通れば書かず、書いた後は読み直して確かめ、通らなければ読んだ本文から `retries` 回まで
    やり直す。`change` は同じ入力で同じ本文を返すこと。ロックを待ち切れなければ `filelock.Timeout`、ロックを用意できなければ
    `LockError`、読み書きの失敗は `store` の例外（GitHub は `GitHubError`）をそのまま返す。
    """
    path = locks.path(issue)
    lock = FileLock(str(path), timeout=limits.seconds("locks.append"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock.acquire()
    except Timeout:  # `Timeout` は `OSError` の下位なので、先に通す
        raise
    except OSError as exc:
        raise LockError(f"本文のロックを用意できない: {exc}") from exc
    try:
        body, wrote = store.read(issue), False
        for _ in range(1 + retries):
            if verify is not None and verify(body):
                break
            new = change(body)
            if new == body:
                break
            store.write(issue, new)
            wrote = True
            if verify is None:
                return Rewritten(new, True, True)
            body = store.read(issue)
        return Rewritten(body, wrote, verify is None or verify(body))
    finally:
        lock.release()
