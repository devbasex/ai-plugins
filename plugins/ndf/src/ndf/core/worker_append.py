"""報告への追記（`ndf worker append`）の排他と、保存先の配下のファイルの書き込み（`ndf worker write`。#4590）。

追記は、報告の読み取り・公開と完了マーカーの作成を、呼んだ利用者の追記どうしで直列にする。

ロックは利用者ごとの状態の保存先（`PluginIdentity.state_dir`）の 1 つのファイル（`LOCK_FILE`）で取る。報告そのものを持つと、
報告を読めるだけの利用者も排他のロックを取って追記を止められ、公開のたびに inode が替わる。報告の隣に置くと、共有の保存先
（グループが書き込めるディレクトリにも報告を書ける）では、ほかの利用者がロックの名前を消せる。追記は短いので、報告ごとに分けない。

`ndf worker write` は、標準入力の本文で `ndf worker run` が渡した保存先の配下のファイルを全文で置き換える。保存先は `.git` の下に
あることが多く、Claude Code は `Write`・`Edit` の Tool での書き込みを機密ファイルとして拒む（許可の規則を足しても拒む）ため、
worker は Bash からこのサブコマンドで書く。worker の外（保存先の識別情報が無い）と保存先の外へは書かない。保存先はリンクを
たどらずに開いて識別子を先に突き合わせ（`check_held`）、サブディレクトリはその記述子から 1 要素ずつ開くので、突き合わせの後に
名前を差し替えられても、起動時に確かめた保存先の外へ書かない。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.contracts import result, worker_paths
from ndf.contracts.exit_codes import OK
from ndf.core import durable_locks

LOCK_FILE = "worker-append.lock"


class WorkerError(Exception):
    """受け渡しの前提が無い・呼び出しの誤り。`kind` は停止理由の種類。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def check_held(wd: worker_paths.WorkDir, doing: str) -> None:
    """worker の中なら `wd` を `ndf worker run` が開いた保存先と、`ndf contracts emit --out` と同じ識別子で突き合わせる（`held_dir_id`）。

    違えば `WorkerError`（precondition）。共有の保存先の名前を差し替えられても、起動時に確かめた保存先の外へ書かない。
    """
    expected = worker_paths.held_dir_id(wd.path)
    if expected is None:
        return
    try:
        worker_paths.check_dir_id(wd, expected)
    except worker_paths.EntryUnsafe:
        raise WorkerError("precondition", f"{wd.path} で{doing}: 起動時に確かめた保存先と違う（差し替えられた）") from None


def append(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths, body: str, *, done: bool) -> bool:
    """報告の末尾へ `body` を足し、`done` なら続けて完了マーカーを作る。報告が無ければ何もせず偽。

    待つ上限は `durable_locks.LOCK_WAIT` で、当たれば何も書かずに `durable_locks.StoreInUse`。
    """
    if worker_paths.entry_state(wd, paths.report.name) is None:
        return False
    with _held(paths):
        text = worker_paths.read_file(wd, paths.report.name)
        if text is None:
            return False
        worker_paths.write_file(wd, paths.report.name, text + body)
        if done:
            worker_paths.write_file(wd, paths.done.name, "")
        return True


@contextlib.contextmanager
def _held(paths: worker_paths.ReportPaths) -> Iterator[None]:
    """追記のロックを排他で持つ。上限まで待っても取れなければ `StoreInUse`、ロックの名前をほかの利用者が消せるなら `EntryUnsafe`。"""
    with worker_paths.open_or_create(identity.load().state_dir) as state:
        worker_paths.check_unshared(state)
        fd = worker_paths.open_lock(state, LOCK_FILE)
    try:
        durable_locks.wait_lock(
            lambda: _try_lock(fd),
            paths.report,
            lambda limit: f"報告のロックを {limit:g} 秒待っても取れない: {paths.report}",
        )
        yield
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 別の追記が持っていれば取れなかったと返す
        return False
    return True


def add_write(sub: Any, tool: str) -> None:
    """`ndf worker write` の解析器を足す。結果 JSON の `tool` は `tool`。"""
    wr = sub.add_parser("write", help="標準入力の本文で保存先の配下のファイルを全文で置き換える（Write・Edit の Tool の代わり）")
    wr.add_argument(
        "--path",
        required=True,
        help="`ndf worker run` で起動された worker の中で使う。その起動に渡された結果の保存先の配下のファイルを指定する。"
        "本文は標準入力に渡し、全文を置き換える",
    )
    wr.set_defaults(fn=functools.partial(_cmd_write, tool=tool))


def _cmd_write(a: argparse.Namespace, tool: str) -> int:
    """標準入力の本文で、`ndf worker run` が渡した保存先の配下の `--path` のファイルを全文で置き換える。"""
    body = sys.stdin.read()
    base, parts = held_target(a.path)
    path = base.joinpath(*parts)
    try:
        with contextlib.ExitStack() as stack:
            wd = stack.enter_context(worker_paths.open_workdir(base))
            check_held(wd, "ファイルを書けない")
            for part in parts[:-1]:
                wd = stack.enter_context(worker_paths.open_subdir(wd, part))
            worker_paths.write_file(wd, parts[-1], body)
    except worker_paths.EntryError as exc:
        raise WorkerError("invalid", f"ファイル {path} へ書き込めない: {exc}") from None
    except OSError as exc:  # 利用者の入力: 無い・書けない保存先は呼び出しの誤り
        raise WorkerError("invalid", f"ファイル {path} へ書き込めない: {exc.strerror or exc}") from None
    return result.emit(result.result(tool, OK, f"ファイル {path} を書いた"))


def held_target(raw: str) -> tuple[Path, tuple[str, ...]]:
    """`--path` の書き先（保存先と、そこからの相対の要素）。

    worker の外なら `WorkerError`（precondition）、保存先の外・`..` を含む・保存先そのものなら invalid。
    """
    held = worker_paths.held_dir()
    if held is None:
        raise WorkerError("precondition", f"ファイル {raw} へ書き込めない: worker の中（ndf worker run が保存先を渡した起動）でだけ書ける")
    base = held[0]
    path = Path(raw).absolute()
    if ".." not in path.parts and not path.is_relative_to(base):
        # 保存先は祖先のリンクを解いたパスで渡る（`_result_dir`）。解く前のパスも比べるときだけ親を解き、書くときは保存先の記述子からたどる
        path = Path(os.path.realpath(path.parent)) / path.name
    if ".." in path.parts or not path.is_relative_to(base) or path == base:
        raise WorkerError("invalid", f"ファイル {raw} へ書き込めない: worker に渡した保存先 {base} の配下のファイルだけを書ける")
    return base, path.relative_to(base).parts
