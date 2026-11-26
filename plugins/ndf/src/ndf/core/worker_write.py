"""保存先の配下のファイルの書き込み（`ndf worker write`。#4590）。

保存先は `.git` の下にあることが多く、Claude Code は `Write`・`Edit` の Tool での書き込みを機密ファイルとして拒む
（許可の規則を足しても拒む）。worker は Bash からこのサブコマンドで書く。

- 書けるのは `ndf worker run` が渡した保存先（`identity.WORKER_RESULT_DIR_ENV`）の配下のファイルだけである。識別情報が無い
  （worker の外）か、保存先の外（`..` を含むものも）なら書かない。保存先はリンクをたどらずに開いて識別子を先に突き合わせ、
  サブディレクトリはその記述子から 1 要素ずつ開く（`open_subdir`）。名前を根から解き直さないので、突き合わせの後に
  保存先やその親の名前を差し替えられても、起動時に確かめた保存先の外へ書かない
- 既定は全文の置き換え。`append` は末尾へ足し、`edit` は `{"old": ..., "new": ...}` の配列で 1 か所ずつ置き換える。
  一部の直しに全文を出し直させない（MVV の Value 2。出力の token は利用者の費用である）
- 読み取りから公開までを `worker_append.held` のロックで直列にする（並べて打った書き込みどうしで同じ旧い本文から組んで、
  先の変更を消さない）
- `append`・`edit` は操作の識別子（`op`）を持つ。済んだ識別子と入力の要約を書き先の隣の記録（`ops_name`）に残し、同じ
  識別子と入力を打ち直したら書かずに済んだと返す（MVV の Value 3。応答を受け取れずに打ち直しても追記を重ねない）。
  同じ識別子で入力が違えば拒む
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.contracts import worker_paths
from ndf.core import worker_append

# 書き方 → 結果の要約に載せる語
MODES = {"replace": "全文の置き換え", "append": "末尾への追記", "edit": "一部の置き換え"}
# 操作の識別子を要る書き方（打ち直すと結果が変わるもの）
OP_MODES = frozenset({"append", "edit"})
# 済んだ操作の記録の名前（`.<書き先の名前><接尾辞>`。書き先と同じディレクトリ）
OPS_SUFFIX = ".ops.jsonl"


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
    with _opened(to) as wd, worker_append.held(to.path, "書き込み"):
        if mode not in OP_MODES:
            worker_paths.write_file(wd, name, body)
            return True
        ops = ops_name(name)
        log = worker_paths.read_file(wd, ops, owned=True) or ""
        digest = hashlib.sha256(f"{mode}\0{body}".encode()).hexdigest()
        if _applied(log, op, digest, to.path):
            return False
        worker_paths.write_file(wd, name, _changed(worker_paths.read_file(wd, name), body, mode, to.path))
        row = json.dumps({"op": op, "digest": digest}, ensure_ascii=False)
        worker_paths.write_file(wd, ops, f"{log}{row}\n", private=True)
    return True


def ops_name(name: str) -> str:
    """書き先 `name` の済んだ操作の記録の名前。"""
    return f".{name}{OPS_SUFFIX}"


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


def _applied(log: str, op: Optional[str], digest: str, path: Path) -> bool:
    """`op` が記録 `log` に済んでいれば真。同じ `op` で入力の要約が違えば `WriteRefused`（invalid）。読めない行は飛ばす。"""
    for line in log.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:  # noqa: NDF401 ファイル: 途中で切れた行は済んだ操作に数えない
            continue
        if isinstance(row, dict) and row.get("op") == op:
            if row.get("digest") != digest:
                raise WriteRefused("invalid", f"操作 {op} は {path} へ別の入力で済んでいる（新しい操作には別の --op を渡す）")
            return True
    return False


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
