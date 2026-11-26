"""保存先の配下のファイルの書き込み（`ndf worker write`。#4590）。

保存先は `.git` の下にあることが多く、Claude Code は `Write`・`Edit` の Tool での書き込みを機密ファイルとして拒む
（許可の規則を足しても拒む）。worker は Bash からこのサブコマンドで書く。

- 書けるのは `ndf worker run` が渡した保存先（`identity.WORKER_RESULT_DIR_ENV`）の配下のファイルだけである。識別情報が無い
  （worker の外）か、保存先の外（`..` を含むものも）なら書かない。保存先はリンクをたどらずに開いて識別子を先に突き合わせ、
  サブディレクトリはその記述子から 1 要素ずつ開く（`open_subdir`）。名前を根から解き直さないので、突き合わせの後に
  保存先やその親の名前を差し替えられても、起動時に確かめた保存先の外へ書かない
- 既定は全文の置き換え。`append` は末尾へ足し、`edit` は `{"old": ..., "new": ...}` の配列で 1 か所ずつ置き換える。
  一部の直しに全文を出し直させない（MVV の Value 2。出力の token は利用者の費用である）
- 済んだ操作の記録とロックは、利用者ごとの状態の保存先（`PluginIdentity.state_dir`）の下の、書き先ごとのディレクトリ
  （`area`。`worker-write/<保存先の st_dev>-<st_ino>/<書き先の要約>`）に置く。共有の保存先に置くと、名前を変えられる
  ほかの利用者が記録を消して打ち直しを重ねさせられ、1 つのロックを全部の書き込みで分け合うと別のレーンの書き込みまで待つ
- 読み取りから公開までを書き先ごとのロックで直列にする（並べて打った書き込みどうしで同じ旧い本文から組んで、先の変更を消さない）
- `append`・`edit` は操作の識別子（`op`）を持つ。操作ごとに 1 つの記録（`op-<識別子の要約>.json`）を作るので、済んだかは
  名前を 1 つ引いて分かり、新しい操作は自分の記録だけを書く。記録は本文より先に書き、入力の要約と、組む前・組んだ後の
  本文の要約を持つ。打ち直しでは、本文が組む前のままなら（本文を公開する前に止まった）もう一度組んで書き、そうでなければ
  書かずに済んだと返す（MVV の Value 3。応答を受け取れずに打ち直しても追記を重ねない）。同じ識別子で入力が違えば拒む
- 記録は `ndf worker run` が終えるときに保存先ごとまとめて消す（`clear`）
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.contracts import worker_paths
from ndf.core import durable_locks

# 書き方 → 結果の要約に載せる語
MODES = {"replace": "全文の置き換え", "append": "末尾への追記", "edit": "一部の置き換え"}
# 操作の識別子を要る書き方（打ち直すと結果が変わるもの）
OP_MODES = frozenset({"append", "edit"})
# 記録とロックを置く、利用者ごとの状態の保存先の下のディレクトリ
AREA_DIR = "worker-write"
# 書き先ごとのロックの名前
LOCK_FILE = "lock"


class WriteRefused(Exception):
    """書けない。`kind` は停止理由の種類（precondition・invalid）。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class Target(NamedTuple):
    """書き先。渡された保存先のパスと識別子、保存先からの相対の要素。"""

    base: Path
    expected: tuple[int, int]
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.base.joinpath(*self.parts)


def target(raw: str) -> Target:
    """`--path` の書き先。worker の外なら `WriteRefused`（precondition）、保存先の外・`..` を含む・保存先そのものなら invalid。"""
    held = worker_paths.held_dir()
    if held is None:
        raise WriteRefused("precondition", f"ファイル {raw} を書けない: worker の中（ndf worker run が保存先を渡した起動）でだけ書ける")
    base, expected = held
    path = Path(raw).absolute()
    if ".." not in path.parts and not path.is_relative_to(base):
        # 保存先は祖先のリンクを解いたパスで渡る（`_result_dir`）。解く前のパスも照合だけは親を解いて比べ、書くときは保存先の記述子からたどる
        path = Path(os.path.realpath(path.parent)) / path.name
    if ".." in path.parts or not path.is_relative_to(base) or path == base:
        raise WriteRefused("invalid", f"ファイル {raw} を書けない: worker に渡した保存先 {base} の配下のファイルだけを書ける")
    return Target(base, expected, path.relative_to(base).parts)


def write(to: Target, body: str, mode: str, op: Optional[str]) -> bool:
    """`body` を書き方 `mode` で書く。同じ操作が済んでいれば書かずに偽、書いたら真。

    `append`・`edit` に `op` が無い・同じ `op` で入力が違うなら `WriteRefused`（invalid）。
    """
    if mode in OP_MODES and not op:
        raise WriteRefused("invalid", f"--{mode} には操作の識別子 --op を渡す（打ち直しても重ねて書かないため）")
    name = to.parts[-1]
    with _opened(to) as wd, area(to) as private, _held(private, to.path):
        if mode not in OP_MODES:
            worker_paths.write_file(wd, name, body)
            return True
        current = worker_paths.read_file(wd, name)
        record = f"op-{_digest(str(op))}.json"
        seen = worker_paths.read_file(private, record, owned=True)
        digest = _digest(f"{mode}\0{body}")
        if seen is not None and not _redo(seen, digest, current, op, to.path):
            return False
        text = _changed(current, body, mode, to.path)
        row = {"op": op, "digest": digest, "before": _digest(current or ""), "after": _digest(text)}
        worker_paths.write_file(private, record, json.dumps(row, ensure_ascii=False), private=True)
        worker_paths.write_file(wd, name, text)
    return True


@contextlib.contextmanager
def area(to: Target) -> Iterator[worker_paths.WorkDir]:
    """書き先の記録とロックを置く、利用者ごとの状態の保存先の下のディレクトリを開く（無ければ作る）。

    ほかの利用者が名前を作る・差し替えることのできるディレクトリなら `EntryUnsafe`（`shared`。`check_unshared`）。
    """
    key = _digest(f"{to.expected[0]}:{to.expected[1]}:{'/'.join(to.parts)}")
    with worker_paths.open_or_create(_held_area(to.expected) / key) as wd:
        worker_paths.check_unshared(wd)
        yield wd


def clear(held: tuple[int, int]) -> None:
    """保存先 `held`（`(st_dev, st_ino)`）の書き込みの記録とロックを消す（`ndf worker run` が終えるとき）。無ければ何もしない。"""
    shutil.rmtree(_held_area(held), ignore_errors=True)


def _held_area(held: tuple[int, int]) -> Path:
    return identity.load().state_dir / AREA_DIR / f"{held[0]}-{held[1]}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _redo(seen: str, digest: str, current: Optional[str], op: Optional[str], path: Path) -> bool:
    """記録が済んだ操作 `op` の打ち直しで、本文が組む前のままなら真（公開の前に止まったので組み直す）、組んだ後なら偽。

    同じ `op` で入力の要約が違えば `WriteRefused`（invalid）。読めない記録は、組む前の本文と比べられないので組み直す側に倒さず
    `WriteRefused`（invalid）で止める（重ねて書かない）。
    """
    try:
        row = json.loads(seen)
    except json.JSONDecodeError:
        row = None
    if not isinstance(row, dict) or not isinstance(row.get("before"), str):
        raise WriteRefused("invalid", f"操作 {op} の記録を読めない（新しい操作には別の --op を渡す）")
    if row.get("digest") != digest:
        raise WriteRefused("invalid", f"操作 {op} は {path} へ別の入力で済んでいる（新しい操作には別の --op を渡す）")
    return _digest(current or "") == row["before"] and row["before"] != row.get("after")


@contextlib.contextmanager
def _held(private: worker_paths.WorkDir, target: Path) -> Iterator[None]:
    """書き先ごとのロックを排他で持つ。上限まで待っても取れなければ `durable_locks.StoreInUse`。"""
    fd = worker_paths.open_lock(private, LOCK_FILE)
    try:
        durable_locks.wait_lock(lambda: _try_lock(fd), target, lambda limit: f"書き込みのロックを {limit:g} 秒待っても取れない: {target}")
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
    """保存先を開いて識別子を突き合わせ、書き先の親までを記述子からたどって開く。違えば `WriteRefused`（precondition）。"""
    with contextlib.ExitStack() as stack:
        wd = stack.enter_context(worker_paths.open_workdir(to.base))
        try:
            worker_paths.check_dir_id(wd, to.expected)
        except worker_paths.EntryUnsafe:
            raise WriteRefused("precondition", f"{wd.path} でファイルを書けない: 起動時に確かめた保存先と違う（差し替えられた）") from None
        for part in to.parts[:-1]:
            wd = stack.enter_context(worker_paths.open_subdir(wd, part))
        yield wd


def _changed(current: Optional[str], body: str, mode: str, path: Path) -> str:
    """`append`・`edit` の書き込み後の本文。`edit` は無いファイル・形の違う入力・1 か所に決まらない `old` を拒む。"""
    if mode == "append":
        return (current or "") + body
    if current is None:
        raise WriteRefused("invalid", f"ファイル {path} が無い（--edit は既存のファイルだけを直す）")
    try:
        edits = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WriteRefused("invalid", f"--edit の入力が JSON でない: {exc}") from None
    if not isinstance(edits, list) or not all(_is_edit(e) for e in edits) or not edits:
        raise WriteRefused("invalid", '--edit の入力は {"old": <文字列>, "new": <文字列>} の空でない配列にする')
    for i, edit in enumerate(edits):
        count = current.count(edit["old"])
        if count != 1:
            raise WriteRefused("invalid", f"--edit の {i} 番目の old が {path} に {count} か所ある（1 か所に決まる文にする）")
        current = current.replace(edit["old"], edit["new"], 1)
    return current


def _is_edit(edit: Any) -> bool:
    """`edit` の 1 件（`old` が空でない文字列、`new` が文字列）なら真。"""
    return isinstance(edit, dict) and isinstance(edit.get("old"), str) and bool(edit["old"]) and isinstance(edit.get("new"), str)
