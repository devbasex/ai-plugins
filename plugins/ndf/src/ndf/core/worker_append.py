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
- 読み取りから公開までを、書き先ごとのロック（`_target_lock`。`worker-write/locks/<書き先の要約>.lock`）で直列にする。
  並べて打った書き込みどうしが同じ旧い本文から組んで、先の変更を消さないため。ロックは利用者ごとの状態の保存先に置く
  （共有の保存先に置くと、名前を変えられるほかの利用者が消せる）。ロックの名前は消さない。消すと、消す前に開いた書き込みと
  後に作った書き込みが別の inode でロックを取り、直列にならないためである（書き先ごとに空のファイルが 1 つ残る）
- `append`・`edit` は操作の識別子（`op`）を持つ。済んだ操作の記録は起動ごとの保存先（`worker-write/runs/<起動の識別子>/`）
  の、書き先ごとの 1 つのファイル（`OPS_FILE`）に置く。起動の識別子は `ndf worker run` が起動ごとに作って子へ渡し
  （`RUN_ENV`）、終えるときにその保存先だけを消す（`run_records`）。同じ保存先を使う別の起動の記録は消さず、強制終了した
  起動の記録を新しい起動は読まない（強制終了した起動の保存先は残る）
- 記録のファイルは、済んだ操作（`done`）、公開したかを決められなかった操作（`unknown`）、公開の前の 1 つの操作
  （`pending`。組む前と組んだ後の書き先の状態の要約を持つ）を持つ。状態の要約は、ファイルが無いことと空のファイルとを
  分ける（`_target_state`）
- 書き込みはどれも、自分の変更の前に、残っている `pending` を今の書き先の状態で確定する（`_settle_pending`）。組んだ後と同じなら
  公開した（`done`）、組む前と同じなら公開していない（記録から外し、打ち直すと書く）、どちらでもなければ `unknown` にする。
  ロックで直列にしているので、`pending` の後に書き先を変えられるのは確定を通らない手（Tool やほかのコマンド）だけである。
  後の書き込みが本文を組む前に戻しても、その書き込みが先に確定しているので、打ち直しで取り消された操作を組み直さない
  記録は起動ごとなので、同じ書き先へ別の起動が書いても、この起動の `pending` は確定されない。別の起動が書き先を変えれば、
  この起動の打ち直しは `unknown` で止まる。ただし、別の起動が書き先をちょうど組む前の状態へ戻したときだけは、組み直す
- 打ち直しは、`done` なら書かずに済んだと返し、`unknown` なら書かずに止める（MVV の Value 3・13。重ねず、確かめずに済んだと
  返さない）。同じ識別子で入力が違えば拒む
- 費用: 追記・一部の置き換えの成功 1 回あたり、記録の公開が 1 回と本文の公開が 1 回で、ロックはその 2 回の間だけ持つ。
  公開の後に `done` を書く方式は記録の公開が 2 回になる。本文の一時ファイルの名前を記録して公開を名前の有無で見分ける方式は
  記録の公開が 1 回で済むが、`worker_paths.write_file` の一時ファイルを外へ出す必要がある。記録のファイルは起動の間の、
  その書き先の操作の数だけ大きくなる（1 件はおよそ 140 バイト）
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
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
# 書き先ごとの、済んだ操作の記録のファイルの名前
OPS_FILE = "ops.json"
# 書き先の状態の要約で、ファイルが無いことを表す値（sha256 の 16 進と重ならない）
ABSENT = "no-file"
# 公開の前の操作の記録が持つ値
PENDING_KEYS = ("op", "digest", "before", "after")


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
    wr.add_argument("--op", help="--append・--edit の操作ごとに新しくする識別子（同じ識別子で打ち直しても重ねて書かない）")
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
    with _opened(to) as wd, _target_lock(key, to.path), _run_area(key) as private:
        current = worker_paths.read_file(wd, name)
        ops = _load_ops(private, to.path)
        settled = _settle_pending(ops, _target_state(current))
        if mode not in OP_MODES:
            if settled:
                _save_ops(private, ops)
            worker_paths.write_file(wd, name, body)
            return True
        op_key, digest = _op_digest(str(op)), _op_digest(f"{mode}\0{body}")
        if _op_done(ops, op_key, digest, op, to.path):
            if settled:
                _save_ops(private, ops)
            return False
        changed = _composed("" if current is None else current, current is None, body, mode, to.path)
        ops["pending"] = {"op": op_key, "digest": digest, "before": _target_state(current), "after": _target_state(changed)}
        _save_ops(private, ops)
        worker_paths.write_file(wd, name, changed)
    return True


def _target_state(text: Optional[str]) -> str:
    """書き先の状態の要約。ファイルが無ければ `ABSENT`、あれば本文の sha256（空のファイルと無いファイルを分ける）。"""
    return ABSENT if text is None else _op_digest(text)


def _settle_pending(ops: dict[str, Any], now: str) -> bool:
    """残っている `pending` を今の書き先の状態 `now` で確定し、記録を変えたら真。

    組んだ後と同じなら `done`、組む前と同じなら外す（公開していないので打ち直しで書く）、どちらでもなければ `unknown`。
    """
    pending = ops.pop("pending", None)
    if pending is None:
        return False
    if now == pending["after"]:
        ops["done"][pending["op"]] = pending["digest"]
    elif now != pending["before"]:
        ops["unknown"][pending["op"]] = pending["digest"]
    return True


def _op_done(ops: dict[str, Any], op_key: str, digest: str, op: Optional[str], path: Path) -> bool:
    """操作が済んでいれば真、記録に無ければ偽。入力が違えば invalid、公開したかを決められなかった操作なら precondition。"""
    for bucket in ("done", "unknown"):
        if op_key not in ops[bucket]:
            continue
        if ops[bucket][op_key] != digest:
            raise WorkerError("invalid", f"操作 {op!r} は {path} へ別の入力で打たれている（新しい操作には別の --op を渡す）")
        if bucket == "done":
            return True
        raise WorkerError(
            "precondition",
            f"操作 {op!r} を {path} へ公開したかを決められない（公開の前か直後に止まり、その後に ndf worker write の外でファイルが"
            "変わった）。ファイルを読んで変更が載っていないと確かめたときだけ、別の --op で打ち直す",
        )
    return False


def _load_ops(private: worker_paths.WorkDir, path: Path) -> dict[str, Any]:
    """書き先の記録を読む。無ければ空。形の違う記録は、済んだかを決められないので `WorkerError`（invalid）で止める。"""
    raw = worker_paths.read_file(private, OPS_FILE, owned=True)
    if raw is None:
        return {"done": {}, "unknown": {}}
    try:
        ops = json.loads(raw)
    except json.JSONDecodeError:
        ops = None
    pending = ops.get("pending") if isinstance(ops, dict) else None
    if not (
        isinstance(ops, dict)
        and isinstance(ops.get("done"), dict)
        and isinstance(ops.get("unknown"), dict)
        and (pending is None or (isinstance(pending, dict) and all(isinstance(pending.get(k), str) for k in PENDING_KEYS)))
    ):
        raise WorkerError("invalid", f"{path} への操作の記録を読めない")
    return ops


def _save_ops(private: worker_paths.WorkDir, ops: dict[str, Any]) -> None:
    worker_paths.write_file(private, OPS_FILE, json.dumps(ops, ensure_ascii=False), private=True)


def _area_root() -> Path:
    return identity.load().state_dir / AREA_DIR


@contextlib.contextmanager
def _target_lock(key: str, target: Path) -> Iterator[None]:
    """書き先 `key` のロックを排他で持つ（`_held`）。ロックの保存先がほかの利用者に変えられるなら `EntryUnsafe`。"""
    with worker_paths.open_or_create(_area_root() / "locks") as locks:
        worker_paths.check_unshared(locks)
        with _held(locks, f"{key}.lock", target, "書き込み"):
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
