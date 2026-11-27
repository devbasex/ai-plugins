"""`ndf worker write`: 標準入力の本文で、`ndf worker run` が渡した結果の保存先の配下のファイルを全文で置き換える（#4590）。

結果の保存先（`ndf worker run --result-file` の親）は `.git` の下にあることが多く、Claude Code は `Write`・`Edit` の Tool での
書き込みを機密ファイルとして拒む（許可の規則を足しても拒む）。そこで worker は Bash からこのサブコマンドで書く。

このコマンドが防ぐのは、呼び出し元の誤りで保存先の外へ書くことである。保存先は環境変数（`identity.WORKER_RESULT_DIR_ENV`）の
パスと識別子で受け取り、リンクをたどらずに開いて識別子を突き合わせ（`check_held`）、サブディレクトリはその記述子から 1 要素ずつ
開く。突き合わせの後に名前を差し替えられても、起動時に確かめた保存先の外へは書かない。

環境変数は呼び出し元が自由に変えられるので、呼び出したのが `ndf worker run` の起動した worker だという証明にはならない。呼び出し元は信頼済み
（`ndf worker run` が起動した worker か、それと同じ利用者）であることを前提にする。このコマンドは OS が呼び出し元に許す以上の
書き込みをさせないので、信頼していない呼び出し元から守る仕組みとしては使わない。
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import sys
from typing import Any

from ndf.contracts import result, worker_paths
from ndf.contracts.exit_codes import OK
from ndf.core.worker_guard import WorkerError, check_held

PATH_HELP = (
    "書き込むファイル。書き込み先は `ndf worker run --result-file` の親ディレクトリの配下に限る（`--state-dir` の配下ではない）。"
    "相対パスは、このコマンドを実行する作業ディレクトリから解決する。本文は標準入力に渡し、ファイルの全文を置き換える。"
    "信頼済みの呼び出し元（`ndf worker run` が起動した worker など）が使う。環境変数で受け取った保存先の識別子と、"
    "開いたディレクトリの識別子が一致するかを確かめる。呼び出し元が `ndf worker run` の起動した worker かどうかは検証しない"
)


def add_write(sub: Any, tool: str) -> None:
    """`ndf worker write` の解析器を足す。結果 JSON の `tool` は `tool`。"""
    wr = sub.add_parser("write", help="標準入力の本文で結果の保存先の配下のファイルを全文で置き換える（Write・Edit の Tool の代わり）")
    wr.add_argument("--path", required=True, help=PATH_HELP)
    wr.set_defaults(fn=functools.partial(_cmd_write, tool=tool))


def _cmd_write(a: argparse.Namespace, tool: str) -> int:
    """標準入力の本文で、結果の保存先の配下の `--path` のファイルを全文で置き換える。"""
    body = sys.stdin.read()
    try:
        target = worker_paths.held_target(a.path)
    except worker_paths.OutsideHeldDir as exc:
        raise WorkerError("invalid", f"ファイル {a.path} へ書き込めない: {exc}") from None
    if target is None:
        raise WorkerError(
            "precondition", f"ファイル {a.path} へ書き込めない: worker の中（ndf worker run が結果の保存先を渡した起動）でだけ書ける"
        )
    base, parts = target
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
