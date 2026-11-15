"""メインディレクトリの排他: index か HEAD を動かす git の操作（マージ・クリーンアップ・ブランチの追従）を 1 本ずつにする。

並列の計画のレーンが同じメインディレクトリを同時に動かすと `.git/index.lock` が衝突する（#2436）。排他のファイルは
git の共通ディレクトリの `ndf-main.lock` で、`flock` で取る（シェルの `flock` と互いを待つ）。待つ上限はコントラクトの
`merged.main-lock` の秒である。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from filelock import FileLock

from ndf.adapters.git import Git
from ndf.contracts import limits

# 排他のファイルの名前（git の共通ディレクトリの下）
LOCK_NAME = "ndf-main.lock"
# 待つ上限のコントラクトの名前
POLICY = "merged.main-lock"


def lock_file(git: Git) -> Path:
    """排他のファイルの保存先（`<git の共通ディレクトリ>/ndf-main.lock`）。"""
    return git.common_dir() / LOCK_NAME


@contextmanager
def held(git: Git, timeout: Optional[float] = None) -> Iterator[None]:
    """排他を取って中を実行する。上限まで取れなければ `filelock.Timeout`。"""
    wait = limits.seconds(POLICY) if timeout is None else timeout
    with FileLock(str(lock_file(git)), timeout=wait):
        yield
