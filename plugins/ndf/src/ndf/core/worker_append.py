"""worker が保存先へ書く手（`ndf worker append`・`ndf worker write`）の排他と書き込み。

報告への追記（`append`）:

ロックは利用者ごとの状態の保存先（`PluginIdentity.state_dir`）の 1 つのファイル（`LOCK_FILE`）で取る。報告そのものを持つと、
報告を読めるだけの利用者も排他のロックを取って追記を止められ、公開のたびに inode が替わる。報告の隣に置くと、共有の保存先
（グループが書き込めるディレクトリにも報告を書ける）では、ほかの利用者がロックの名前を消せる。追記は短いので、報告ごとに分けない。

保存先の配下のファイルの書き込み（`ndf worker write`。#4590）:

保存先は `.git` の下にあることが多く、Claude Code は `Write`・`Edit` の Tool での書き込みを機密ファイルとして拒む
（許可の規則を足しても拒む）。worker は Bash からこのサブコマンドで書く。

- 書けるのは `ndf worker run` が渡した保存先（`identity.WORKER_RESULT_DIR_ENV`）の配下のファイルだけである。識別情報が無い
  （worker の外）か、保存先の外（`..` を含むものも）なら書かない。保存先はリンクをたどらずに開いて識別子を先に突き合わせ、
  サブディレクトリはその記述子から 1 要素ずつ開く（`open_subdir`）。名前を根から解き直さないので、突き合わせの後に
  保存先やその親の名前を差し替えられても、起動時に確かめた保存先の外へ書かない
- 既定は全文の置き換え。`append` は末尾へ足し、`edit` は `{"old": ..., "new": ...}` の配列で 1 か所ずつ置き換える。
  一部の直しに全文を出し直させない（MVV の Value 2。出力の token は利用者の費用である）
- 済んだ操作の記録とロックは、利用者ごとの状態の保存先の下の、書き先ごとのディレクトリ
  （`area`。`worker-write/<保存先の st_dev>-<st_ino>/<書き先の要約>`）に置く。共有の保存先に置くと、名前を変えられる
  ほかの利用者が記録を消して打ち直しを重ねさせられ、1 つのロックを全部の書き込みで分け合うと別のレーンの書き込みまで待つ
- 読み取りから公開までを書き先ごとのロックで直列にする（並べて打った書き込みどうしで同じ旧い本文から組んで、先の変更を消さない）
- `append`・`edit` は操作の識別子（`op`）を持つ。操作ごとに 1 つの記録（`op-<識別子の要約>.json`）を作るので、済んだかは
  名前を 1 つ引いて分かり、新しい操作は自分の記録だけを書く。記録は本文より先に書き、入力の要約と、組む前・組んだ後の
  本文の要約を持つ。打ち直しでは、本文が組む前のままなら（本文を公開する前に止まった）もう一度組んで書き、そうでなければ
  書かずに済んだと返す（MVV の Value 3。応答を受け取れずに打ち直しても追記を重ねない）。同じ識別子で入力が違えば拒む
- 記録は `ndf worker run` が終えるときに保存先ごとまとめて消す（`clear_records`）
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.contracts import result, worker_paths
from ndf.contracts.exit_codes import OK
from ndf.core import durable_locks

LOCK_FILE = "worker-append.lock"
# `ndf worker write` の書き方 → 結果の要約に載せる語
MODES = {"overwrite": "全文の置き換え", "append": "末尾への追記", "edit": "一部の置き換え"}
# 操作の識別子を要る書き方（打ち直すと結果が変わるもの）
OP_MODES = frozenset({"append", "edit"})
# 記録とロックを置く、利用者ごとの状態の保存先の下のディレクトリ
AREA_DIR = "worker-write"
# 書き先ごとのロックの名前
WRITE_LOCK_FILE = "worker-write.lock"


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
    with worker_paths.open_or_create(identity.load().state_dir) as state:
        worker_paths.check_unshared(state)
        with _held(state, LOCK_FILE, paths.report, "報告"):
            text = worker_paths.read_file(wd, paths.report.name)
            if text is None:
                return False
            worker_paths.write_file(wd, paths.report.name, text + body)
            if done:
                worker_paths.write_file(wd, paths.done.name, "")
            return True


# `ndf worker write --help` の説明。書き方の詳細は規則文に載せずここに置く（全 worker の起動に載る token を増やさない）
WRITE_HELP = """\
標準入力の本文で、worker に渡した保存先（.git の下）の配下のファイルを書く（Write・Edit の Tool の代わり）。

  全文の置き換え:  ndf worker write --path <ファイル> <<'NDF_BODY_7f3a'
  末尾への追記:    ndf worker write --path <ファイル> --append --op <識別子> <<'NDF_BODY_7f3a'
  一部の置き換え:  ndf worker write --path <ファイル> --edit --op <識別子> <<'NDF_BODY_7f3a'
                   [{"old": "<1 か所に決まる元の文>", "new": "<直した文>"}]

全文を渡すのは作り直すときだけにし、書き足すなら --append、一部を直すなら --edit で差分だけを渡す。
--op は操作ごとに新しい語にする。応答を受け取れずに同じ --op と本文で打ち直しても、重ねて書かずに済んだと返す。
区切り語は引用符で囲む（囲まないと本文の $(...)・$変数・バッククォートがシェルに解釈される）。
PR 本文・Issue から写した文を含むことがあるので、本文に区切り語だけの行が無い語を選ぶ。
"""


def add_write(sub: Any, tool: str) -> None:
    """`ndf worker write` の解析器を足す。結果 JSON の `tool` は `tool`。"""
    wr = sub.add_parser(
        "write",
        help="標準入力の本文で保存先の配下のファイルを書く（Write・Edit の Tool の代わり）",
        description=WRITE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wr.add_argument("--path", required=True)
    how = wr.add_mutually_exclusive_group()
    how.add_argument("--append", dest="mode", action="store_const", const="append", help="末尾へ足す")
    how.add_argument(
        "--edit", dest="mode", action="store_const", const="edit", help='標準入力の [{"old": ..., "new": ...}] で 1 か所ずつ置き換える'
    )
    wr.add_argument("--op", help="--append・--edit の操作の識別子（打ち直しても重ねて書かない）")
    wr.set_defaults(mode="overwrite", fn=functools.partial(_cmd_write, tool=tool))


def _cmd_write(a: argparse.Namespace, tool: str) -> int:
    """標準入力の本文で、`ndf worker run` が渡した保存先の配下の `--path` のファイルを書く。"""
    body = sys.stdin.read()
    to = write_target(a.path)
    refused = f"ファイル {to.path} を書けない"
    try:
        wrote = write(to, body, a.mode, a.op)
    except worker_paths.EntryError as exc:
        raise WorkerError("invalid", f"{refused}: {exc}") from None
    except durable_locks.StoreInUse as exc:
        raise WorkerError(exc.kind, f"{refused}: {exc}") from None
    except OSError as exc:  # 利用者の入力: 無い・書けない保存先は呼び出しの誤り
        raise WorkerError("invalid", f"{refused}: {exc.strerror or exc}") from None
    how = MODES[a.mode]
    summary = (
        f"ファイル {to.path} を書いた（{how}）" if wrote else f"ファイル {to.path} への同じ操作は済んでいた（{how}。書き直していない）"
    )
    return result.emit(result.result(tool, OK, summary))


class Target(NamedTuple):
    """書き先。渡された保存先のパスと識別子、保存先からの相対の要素。"""

    base: Path
    expected: tuple[int, int]
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.base.joinpath(*self.parts)


def write_target(raw: str) -> Target:
    """`--path` の書き先。worker の外なら `WorkerError`（precondition）、保存先の外・`..` を含む・保存先そのものなら invalid。"""
    held = worker_paths.held_dir()
    if held is None:
        raise WorkerError("precondition", f"ファイル {raw} を書けない: worker の中（ndf worker run が保存先を渡した起動）でだけ書ける")
    base, expected = held
    path = Path(raw).absolute()
    if ".." not in path.parts and not path.is_relative_to(base):
        # 保存先は祖先のリンクを解いたパスで渡る（`_result_dir`）。解く前のパスも比べるときだけ親を解き、書くときは保存先の記述子からたどる
        path = Path(os.path.realpath(path.parent)) / path.name
    if ".." in path.parts or not path.is_relative_to(base) or path == base:
        raise WorkerError("invalid", f"ファイル {raw} を書けない: worker に渡した保存先 {base} の配下のファイルだけを書ける")
    return Target(base, expected, path.relative_to(base).parts)


def write(to: Target, body: str, mode: str, op: Optional[str]) -> bool:
    """`body` を書き方 `mode` で書く。同じ操作が済んでいれば書かずに偽、書いたら真。

    `append`・`edit` に `op` が無い・同じ `op` で入力が違うなら `WorkerError`（invalid）。
    """
    if mode in OP_MODES and not op:
        raise WorkerError("invalid", f"--{mode} には操作の識別子 --op を渡す（打ち直しても重ねて書かないため）")
    name = to.parts[-1]
    with _opened(to) as wd, area(to) as private, _held(private, WRITE_LOCK_FILE, to.path, "書き込み"):
        if mode not in OP_MODES:
            worker_paths.write_file(wd, name, body)
            return True
        current = worker_paths.read_file(wd, name)
        text = "" if current is None else current
        record = f"op-{_op_digest(str(op))}.json"
        seen = worker_paths.read_file(private, record, owned=True)
        digest = _op_digest(f"{mode}\0{body}")
        if seen is not None and not _redo(seen, digest, text, op, to.path):
            return False
        changed = _composed(text, current is None, body, mode, to.path)
        row = {"op": op, "digest": digest, "before": _op_digest(text), "after": _op_digest(changed)}
        worker_paths.write_file(private, record, json.dumps(row, ensure_ascii=False), private=True)
        worker_paths.write_file(wd, name, changed)
    return True


@contextlib.contextmanager
def area(to: Target) -> Iterator[worker_paths.WorkDir]:
    """書き先の記録とロックを置く、利用者ごとの状態の保存先の下のディレクトリを開く（無ければ作る）。

    ほかの利用者が名前を作る・差し替えることのできるディレクトリなら `EntryUnsafe`（`shared`。`check_unshared`）。
    """
    key = _op_digest(f"{to.expected[0]}:{to.expected[1]}:{'/'.join(to.parts)}")
    with worker_paths.open_or_create(_held_area(to.expected) / key) as wd:
        worker_paths.check_unshared(wd)
        yield wd


def clear_records(held: tuple[int, int]) -> None:
    """保存先 `held`（`(st_dev, st_ino)`）の書き込みの記録とロックを消す（`ndf worker run` が終えるとき）。無ければ何もしない。"""
    shutil.rmtree(_held_area(held), ignore_errors=True)


def _held_area(held: tuple[int, int]) -> Path:
    return identity.load().state_dir / AREA_DIR / f"{held[0]}-{held[1]}"


def _op_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _redo(seen: str, digest: str, text: str, op: Optional[str], path: Path) -> bool:
    """記録が済んだ操作 `op` の打ち直しで、本文が組む前のままなら真（公開の前に止まったので組み直す）、組んだ後なら偽。

    同じ `op` で入力の要約が違えば `WorkerError`（invalid）。読めない記録は、組む前の本文と比べられないので組み直す側に倒さず
    `WorkerError`（invalid）で止める（重ねて書かない）。
    """
    try:
        row = json.loads(seen)
    except json.JSONDecodeError:
        row = None
    if not isinstance(row, dict) or not isinstance(row.get("before"), str):
        raise WorkerError("invalid", f"操作 {op!r} の記録を読めない（新しい操作には別の --op を渡す）")
    if row.get("digest") != digest:
        raise WorkerError("invalid", f"操作 {op!r} は {path} へ別の入力で済んでいる（新しい操作には別の --op を渡す）")
    return _op_digest(text) == row["before"] and row["before"] != row.get("after")


@contextlib.contextmanager
def _held(private: worker_paths.WorkDir, lock: str, target: Path, what: str) -> Iterator[None]:
    """`private` のロック `lock` を排他で持つ。上限まで待っても取れなければ `durable_locks.StoreInUse`。"""
    fd = worker_paths.open_lock(private, lock)
    try:
        durable_locks.wait_lock(lambda: _try_lock(fd), target, lambda limit: f"{what}のロックを {limit:g} 秒待っても取れない: {target}")
        yield
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 別の書き込みが持っていれば取れなかったと返す
        return False
    return True


@contextlib.contextmanager
def _opened(to: Target) -> Iterator[worker_paths.WorkDir]:
    """保存先を開いて識別子を突き合わせ（`check_held`）、書き先の親までを記述子からたどって開く。違えば `WorkerError`（precondition）。"""
    with contextlib.ExitStack() as stack:
        wd = stack.enter_context(worker_paths.open_workdir(to.base))
        check_held(wd, "ファイルを書けない")
        for part in to.parts[:-1]:
            wd = stack.enter_context(worker_paths.open_subdir(wd, part))
        yield wd


def _composed(text: str, missing: bool, body: str, mode: str, path: Path) -> str:
    """`append`・`edit` の書き込み後の本文。`edit` は無いファイル・形の違う入力・1 か所に決まらない `old` を拒む。"""
    if mode == "append":
        return text + body
    if missing:
        raise WorkerError("invalid", f"ファイル {path} が無い（--edit は既存のファイルだけを直す）")
    try:
        edits = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WorkerError("invalid", f"--edit の入力が JSON でない: {exc}") from None
    if not isinstance(edits, list) or not all(_is_edit(e) for e in edits) or not edits:
        raise WorkerError("invalid", '--edit の入力は {"old": <文字列>, "new": <文字列>} の空でない配列にする')
    for i, edit in enumerate(edits):
        count = text.count(edit["old"])
        if count != 1:
            raise WorkerError("invalid", f"--edit の {i} 番目の old が {path} に {count} か所ある（1 か所に決まる文にする）")
        text = text.replace(edit["old"], edit["new"], 1)
    return text


def _is_edit(edit: Any) -> bool:
    """`edit` の 1 件（`old` が空でない文字列、`new` が文字列）なら真。"""
    return isinstance(edit, dict) and isinstance(edit.get("old"), str) and bool(edit["old"]) and isinstance(edit.get("new"), str)
