"""変更の前の commit の中身を、1 つの `git cat-file --batch` から名前ごとに読む。

1 つの名前の応答を読み終えてから次を問うので、木を下りて要る本文だけを読んでも git の起動は 1 回で済む。
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, NamedTuple, Optional

from ndf.checks.model import CheckError

COMMIT, TREE = "commit", "tree"
# 木の行の mode。通常のファイルは 100644・100755（シンボリックリンク 120000 とサブモジュール 160000 は読まない）
_TREE_MODE, _FILE_MODES = b"40000", b"100"


class GitObject(NamedTuple):
    oid: str
    kind: str
    data: bytes


ReadObject = Callable[[str], Optional[GitObject]]


@contextlib.contextmanager
def opened(root: Path) -> Iterator[ReadObject]:
    """名前（`<commit>:<パス>` や id）のものを読む関数。無い名前には None を返す。git を使えなければ `CheckError`。"""
    argv = ["git", "-C", str(root), "cat-file", "--batch"]
    try:
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            yield functools.partial(read_object, proc.stdin, proc.stdout)
    except OSError as exc:
        raise CheckError(f"git の worktree で実行する: {root}: {exc}") from exc


def read_object(ask: IO[bytes], answer: IO[bytes], name: str) -> Optional[GitObject]:
    """`name` を 1 行で書き、応答を 1 つ読む。改行を含む名前は問わずに None（応答の数が合わなくなる）。"""
    if "\n" in name:
        return None
    ask.write(os.fsencode(name) + b"\n")
    ask.flush()
    head = answer.readline().rstrip(b"\n")
    if not head:
        raise CheckError("git cat-file が応答せずに終わった")
    if head.endswith((b" missing", b" ambiguous")):
        return None
    oid, kind, size = head.decode().split(" ")
    data = answer.read(int(size))
    answer.read(1)  # 本文の後の改行
    return GitObject(oid, kind, data)


def _rows(tree: GitObject) -> Iterator[tuple[bytes, str, str]]:
    """木の行ごとの mode・名前・id（id の幅は木の id から取る。SHA-256 のリポジトリは 32 バイト）。"""
    width, at = len(tree.oid) // 2, 0
    while at < len(tree.data):
        end = tree.data.index(b"\0", at)
        mode, _, name = tree.data[at:end].partition(b" ")
        at = end + 1 + width
        yield mode, os.fsdecode(name), tree.data[end + 1 : at].hex()


def files_under(read: ReadObject, name: str, rel: str) -> dict[str, str]:
    """木 `name`（パスは `rel`）の下の通常のファイルの、パスごとの blob の id。木が無ければ空。本文は読まない。"""
    tree = read(name)
    if tree is None or tree.kind != TREE:
        return {}
    found: dict[str, str] = {}
    for mode, child, oid in _rows(tree):
        if mode == _TREE_MODE:
            found |= files_under(read, oid, f"{rel}/{child}")
        elif mode.startswith(_FILE_MODES):
            found[f"{rel}/{child}"] = oid
    return found


def blob_id(text: str, like: str) -> str:
    """本文 `text` の blob の id（git を起動しない）。形は `like` の id に合わせる（64 桁なら SHA-256）。"""
    data = text.encode()
    return hashlib.new("sha256" if len(like) == 64 else "sha1", b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
