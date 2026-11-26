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
- 読み取りから公開までを書き先のロックで直列にする。並べて打った書き込みが同じ旧い本文から組んで、先の変更を消さないため。
  ロックは利用者ごとの状態の保存先（`worker-write/locks`）に置く（共有の保存先に置くと、ほかの利用者が名前を消せる）。
  ロックは書き先の要約で選ぶ固定の数（`LOCK_STRIPES`）のファイルで、名前は消さない。消すと、消す前に開いた書き込みと後に
  作った書き込みが別の inode でロックを取るためである。数を固定するので、保存先と書き先がいくつ増えてもファイルは増えない
  （同じロックに当たった別の書き先どうしは待ち合う）
- 全文の置き換えは、打ち直しても結果が同じなので、記録を書かず、前の本文も読まない
- `append`・`edit` は操作の識別子（`op`）を持つ。識別子は 1 回の `ndf worker run` の中だけで通じる。記録は起動ごとの
  保存先（`worker-write/runs/<起動の識別子>/<書き先の要約>/op-<識別子の要約>.json`）に置き、別の起動へは引き継がない。
  起動の識別子は `ndf worker run` が起動ごとに作って子へ渡し（`RUN_ENV`）、終えるときにその保存先だけを消す
  （`run_records`。強制終了した起動の保存先は残るが、後の起動は読まない）。記録は操作ごとの 1 ファイルなので、済んだかは
  名前を 1 つ引いて分かり、過去の操作の数によらない
- 公開したかは、本文の一致ではなく公開した inode で決める。`worker_paths.staged_write` が中身を書いた一時ファイルの
  inode（番号と更新時刻）を公開の前に知らせるので、記録を「公開の前」（`pending`）で書いてから公開し、公開した後に
  「公開した」（`done`）へ書き換える。公開に失敗したら「失敗」（`failed`）にする。どの状態も入力の要約を持つので、同じ
  識別子を別の入力に使えば拒む
- 打ち直しは、`done` なら書かずに済んだと返し、`failed` なら組み直して書く。`pending`（記録を書き換える前に止まった）なら、
  書き先の名前が記録した inode を指していれば公開した、一時ファイルが記録した inode のまま残っていれば公開していない。
  どちらでもなければ、公開した後に置き換えられたのかを決められないので、書かずに止める（MVV の Value 3・13）
- 費用: 追記・一部の置き換えの成功 1 回あたり、記録の公開が 2 回（公開の前と後）と本文の公開が 1 回で、ロックはその間だけ
  持つ。記録の大きさは操作の数によらない。比べた方式は次のとおり
  - 記録を 1 回だけ書き、公開の確定を次の書き込みに任せる: 記録の公開は 1 回だが、書き先ごとの記録が操作の数だけ大きくなり
    （累積の読み書きは操作の数の 2 乗）、別の起動の書き込みが確定しないと本文の一致に頼ることになる
  - 本文の一致で決める: 記録の公開は 1 回だが、ほかの書き込みが同じ本文を公開した場合と見分けられない
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import secrets
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
# 起動の識別子のバイト数
RUN_TOKEN_BYTES = 16
# `ndf worker run` が起動ごとに作り、子の `ndf worker write` へ渡す起動の識別子の環境変数（16 バイトの 16 進）
RUN_ENV = "NDF_WORKER_WRITE_RUN"
# 操作の記録の状態: 公開の前・公開した・公開の前に失敗した（打ち直すと書く）
PENDING, DONE, FAILED = "pending", "done", "failed"
# 操作の記録が持つ値
RECORD_KEYS = ("op", "digest", "staged", "ino", "mtime_ns", "after")
# 書き先のロックの数。書き先の要約から 1 つを選ぶので、ロックのファイルは書き先がいくつあってもこの数を超えない
LOCK_STRIPES = 64
# `worker_paths.staged_write` の一時ディレクトリの名前
STAGED_DIR = re.compile(r"\.ndf-[0-9a-f]{16}")


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
--op は操作ごとに新しい語にする。同じ起動の中で、応答を受け取れずに同じ --op と本文で打ち直しても、重ねて書かずに済んだと返す。
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
    wr.add_argument(
        "--path",
        required=True,
        help="`ndf worker run` で起動された worker の中で使う。その起動に渡された結果の保存先の配下のファイルを指定する。"
        "本文は標準入力に渡し、既定では全文を置き換える",
    )
    how = wr.add_mutually_exclusive_group()
    how.add_argument("--append", action="store_true", help="本文を末尾へ追記する。--op が必須で、--edit と同時に指定できない")
    how.add_argument(
        "--edit",
        action="store_true",
        help="標準入力で、`old` と `new` の文字列を持つオブジェクトの空でない JSON 配列を渡す。配列の順に置き換え、各 `old` は"
        "その時点の本文にちょうど 1 か所ある空でない文字列にする。--op が必須で、--append と同時に指定できない",
    )
    wr.add_argument(
        "--op",
        help="--append・--edit の操作ごとに新しくする識別子。同じ ndf worker run の起動の中で、応答を受け取れなかった操作を"
        "同じ識別子と本文で打ち直すと、重ねて書かない。操作の記録は別の起動へ引き継がないので、新しい起動での打ち直しには効かない",
    )
    wr.set_defaults(fn=functools.partial(_cmd_write, tool=tool))


def _cmd_write(a: argparse.Namespace, tool: str) -> int:
    """標準入力の本文で、`ndf worker run` が渡した保存先の配下の `--path` のファイルを書く。"""
    body = sys.stdin.read()
    to = write_target(a.path)
    mode = "append" if a.append else "edit" if a.edit else "overwrite"
    refused = f"ファイル {to.path} を書けない"
    try:
        wrote = write(to, body, mode, a.op)
    except worker_paths.EntryError as exc:
        raise WorkerError("invalid", f"{refused}: {exc}") from None
    except durable_locks.StoreInUse as exc:
        raise WorkerError(exc.kind, f"{refused}: {exc}") from None
    except OSError as exc:  # 利用者の入力: 無い・書けない保存先は呼び出しの誤り
        raise WorkerError("invalid", f"{refused}: {exc.strerror or exc}") from None
    how = MODES[mode]
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

    `append`・`edit` に `op` が無い・同じ `op` で入力が違うなら `WorkerError`（invalid）。公開したかを決められなかった
    操作の打ち直しと、起動の識別子が無い（`ndf worker run` の外）呼び出しは `WorkerError`（precondition）。
    """
    if mode in OP_MODES and not op:
        raise WorkerError("invalid", f"--{mode} には操作の識別子 --op を渡す（打ち直しても重ねて書かないため）")
    name = to.parts[-1]
    key = _op_digest(f"{to.expected[0]}:{to.expected[1]}:{'/'.join(to.parts)}")
    with _opened(to) as wd, _target_lock(key, to.path):
        if mode not in OP_MODES:
            worker_paths.write_file(wd, name, body)  # 全文の置き換えは打ち直しても結果が同じなので、記録も前の本文も要らない
            return True
        with _run_area(key) as private:
            return _write_once(wd, private, to, Request(body, mode, str(op)))


class Request(NamedTuple):
    """`append`・`edit` の 1 回の操作の入力。"""

    body: str
    mode: str
    op: str


def _write_once(wd: worker_paths.WorkDir, private: worker_paths.WorkDir, to: Target, req: Request) -> bool:
    """操作 `req.op` を 1 回だけ公開する。記録の状態で打ち直しを見分け、済んでいれば偽、書いたら真。"""
    name, path, body, mode, op = to.parts[-1], to.path, req.body, req.mode, req.op
    record = f"op-{_op_digest(op)}.json"
    digest = _op_digest(f"{mode}\0{body}")
    row = _load_record(private, record, digest, op, path)
    if row is not None and row["state"] == DONE:
        return False
    current = worker_paths.read_file(wd, name)
    if row is not None and row["state"] == PENDING:
        evidence = _published(wd, name, current, row)
        if evidence is None:
            raise WorkerError(
                "precondition",
                f"操作 {op!r} を {path} へ公開したかを決められない（公開の直前か直後に止まり、その後にファイルが置き換わった）。"
                "ファイルを読み、変更が載っていないと確かめたときだけ、別の --op で打ち直す",
            )
        if evidence:
            _mark(private, record, row | {"state": DONE})
            return False
        _drop_staged(wd, row)  # 公開の前に止まった一時ファイルを片付けてから組み直す
    changed = _composed("" if current is None else current, current is None, body, mode, path)
    staged: dict[str, Any] = {}

    def note(rel: str, st: os.stat_result) -> None:
        staged.update({"op": op, "digest": digest, "staged": rel, "ino": st.st_ino, "mtime_ns": st.st_mtime_ns})
        staged["after"] = _op_digest(changed)
        _mark(private, record, staged | {"state": PENDING})

    try:
        with worker_paths.staged_write(wd, name, changed, on_staged=note):
            pass
    except BaseException:
        if staged:  # 公開した inode が名前に載っていれば済んだ、載っていなければ打ち直せる
            # 確かめられない・記録を書けないときは公開の前の記録のまま残し、打ち直しが公開した inode から決める
            with contextlib.suppress(OSError, ValueError):  # noqa: NDF402 ファイル: 元の失敗を上げる
                done = _published(wd, name, worker_paths.read_file(wd, name), staged) is True
                _mark(private, record, staged | {"state": DONE if done else FAILED})
        raise
    _mark(private, record, staged | {"state": DONE})
    return True


def _load_record(private: worker_paths.WorkDir, record: str, digest: str, op: str, path: Path) -> Optional[dict[str, Any]]:
    """操作の記録を読む。無ければ None。読めない記録・同じ `op` で入力が違う記録は `WorkerError`（invalid）。

    公開の前に失敗した操作（`FAILED`）の記録も入力の要約を持つので、同じ識別子を別の入力に使えば拒む。
    """
    raw = worker_paths.read_file(private, record, owned=True)
    if raw is None:
        return None
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        row = None
    if not (isinstance(row, dict) and row.get("state") in (PENDING, DONE, FAILED) and all(k in row for k in RECORD_KEYS)):
        raise WorkerError("invalid", f"操作 {op!r} の記録を読めない（新しい操作には別の --op を渡す）")
    if row["digest"] != digest:
        raise WorkerError("invalid", f"操作 {op!r} は {path} へ別の入力で打たれている（新しい操作には別の --op を渡す）")
    return row


def _published(wd: worker_paths.WorkDir, name: str, current: Optional[str], row: dict[str, Any]) -> Optional[bool]:
    """公開の前の記録 `row` の操作が公開したかを、公開した inode で決める。公開したなら真、していないなら偽、決められなければ None。

    書き先の名前が記録した inode（番号と更新時刻）を指し、本文が組んだ後の要約と同じなら、この操作が公開した。一時ファイルが
    記録した inode のまま残っていれば、公開の前に止まった。どちらでもなければ、公開した後に置き換えられたのか、公開の前に
    止まって一時ファイルを片付けた後に記録を書けなかったのかを決められない。本文の一致だけでは、ほかの書き込みが同じ本文を
    公開した場合と見分けられないので使わない。
    """
    try:
        st = os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:
        st = None
    if st is not None and (st.st_ino, st.st_mtime_ns) == (row["ino"], row["mtime_ns"]) and current is not None:
        if _op_digest(current) == row["after"]:
            return True
    left = _staged_left(wd, row)
    return False if left else None


def _staged_left(wd: worker_paths.WorkDir, row: dict[str, Any]) -> bool:
    """記録した一時ファイルが、記録した inode のまま残っていれば真。一時ディレクトリはリンクをたどらずに開く。"""
    tmp, _, leaf = str(row["staged"]).partition("/")
    if not STAGED_DIR.fullmatch(tmp) or leaf != worker_paths.STAGED_NAME:
        return False
    try:
        fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    except OSError:  # noqa: NDF401 ファイル: 開けない一時ディレクトリは残っていないと見る
        return False
    try:
        st = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無ければ残っていない
        return False
    finally:
        os.close(fd)
    return (st.st_ino, st.st_mtime_ns) == (row["ino"], row["mtime_ns"])


def _drop_staged(wd: worker_paths.WorkDir, row: dict[str, Any]) -> None:
    """公開の前に止まった操作の一時ファイルと一時ディレクトリを消す（記録した inode のときだけ）。消せなければそのまま残す。"""
    tmp, _, leaf = str(row["staged"]).partition("/")
    with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 片付けられなくても組み直しは進める（残るのは私有の一時ディレクトリ）
        fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
        try:
            st = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            if (st.st_ino, st.st_mtime_ns) == (row["ino"], row["mtime_ns"]):
                os.unlink(leaf, dir_fd=fd)
        finally:
            os.close(fd)
        os.rmdir(tmp, dir_fd=wd.fd)


def _mark(private: worker_paths.WorkDir, record: str, row: dict[str, Any]) -> None:
    worker_paths.write_file(private, record, json.dumps(row, ensure_ascii=False), private=True)


def _area_root() -> Path:
    return identity.load().state_dir / AREA_DIR


@contextlib.contextmanager
def _target_lock(key: str, target: Path) -> Iterator[None]:
    """書き先 `key` の、固定の数（`LOCK_STRIPES`）に分けたロックの 1 つを排他で持つ（`_held`）。ロックの保存先がほかの利用者に変えられるなら `EntryUnsafe`。"""
    with worker_paths.open_or_create(_area_root() / "locks") as locks:
        worker_paths.check_unshared(locks)
        with _held(locks, f"{int(key[:8], 16) % LOCK_STRIPES}.lock", target, "書き込み"):
            yield


@contextlib.contextmanager
def _run_area(key: str) -> Iterator[worker_paths.WorkDir]:
    """この起動の、書き先 `key` の記録の保存先を開く（無ければ作る）。起動の識別子が無ければ `WorkerError`（precondition）。"""
    run = os.environ.get(RUN_ENV, "")
    if len(run) != RUN_TOKEN_BYTES * 2 or any(c not in "0123456789abcdef" for c in run):
        raise WorkerError("precondition", "ndf worker run の起動の識別子が無い（ndf worker run が起動した worker の中でだけ書ける）")
    with worker_paths.open_or_create(_area_root() / "runs" / run / key) as wd:
        worker_paths.check_unshared(wd)
        yield wd


@contextlib.contextmanager
def run_records() -> Iterator[None]:
    """`ndf worker run` の間、子へ起動の識別子（`RUN_ENV`）を渡し、終えたらこの起動の記録の保存先だけを消す。

    保存先を起動ごとに分けるので、同じ保存先を使う別の起動の記録を消さず、強制終了した起動の記録を後の起動が読まない。
    """
    run = secrets.token_hex(RUN_TOKEN_BYTES)
    before = os.environ.get(RUN_ENV)
    os.environ[RUN_ENV] = run
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(RUN_ENV, None)
        else:
            os.environ[RUN_ENV] = before
        shutil.rmtree(_area_root() / "runs" / run, ignore_errors=True)


def _op_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
