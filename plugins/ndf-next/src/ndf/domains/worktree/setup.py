"""worktree の運用をリポジトリへ導入する: 宣言を作る（`init`）・宣言の状態を読む（`check`）。

worktree の運用の仕組みは、リポジトリに宣言（`.ndf/worktree.json`）があるときだけ動く。`init` はその宣言を作る。

- `init`: 最小の宣言（`{"version": 1}`）を書く。既にある宣言は上書きしない（`force` を除く）。読めない宣言があれば
  書かずに止める（直すか消すかは利用者が決める）。宣言と置き場が symlink なら、たどらずに断る（外のファイルを書き換えない）
- 個人設定（`.ndf/worktree.local.json`）を追跡から外す `.ndf/.gitignore` を、無いときだけ作る（書き加えた内容を消さない）
- 書くときは同じディレクトリの一時ファイルへ書いてから付け替える（書いている途中で落ちても半端な宣言を残さない）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ndf.domains.worktree import atomic, declaration

# 宣言の状態
PRESENT = "present"
ABSENT = "absent"
UNREADABLE = "not_readable"
# 個人設定を追跡から外す登録の名前（宣言と同じディレクトリに置き、根の .gitignore に触れない）
IGNORE_FILE = declaration.GITIGNORE


class SetupError(Exception):
    """書けない（symlink・読めない宣言・書き込みの失敗）。"""


@dataclass(frozen=True)
class State:
    state: str
    detail: str = ""
    decl: Optional[declaration.Declaration] = None


def state(main_dir: Path) -> State:
    try:
        decl = declaration.load(main_dir)
    except declaration.DeclarationError as exc:
        return State(UNREADABLE, str(exc))
    return State(ABSENT) if decl is None else State(PRESENT, decl=decl)


@dataclass(frozen=True)
class Initialized:
    path: Path
    written: bool
    warnings: tuple[str, ...] = ()


def init(main_dir: Path, force: bool = False) -> Initialized:
    """宣言を作る。既にあれば書かずに `written=False`。"""
    shared = declaration.shared_file(main_dir)
    if not force:
        got = state(main_dir)
        if got.state == PRESENT:
            return Initialized(shared, False)
        if got.state == UNREADABLE:
            raise SetupError(f"{got.detail}。中身を直すか、要らなければ消してから init を打ち直す")
    for p in (shared.parent, shared):
        if p.is_symlink():
            raise SetupError(f"{p} が symlink（たどらずに終わる）")
    shared.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(shared, json.dumps({"version": declaration.VERSION}, indent=2) + "\n")
    declaration.load(main_dir)
    ignore = shared.parent / IGNORE_FILE
    if ignore.exists() or ignore.is_symlink():
        return Initialized(shared, True)
    try:
        atomic.write_text(ignore, f"# 個人設定。各自の機械の値を書き、コミットしない\n{declaration.local_file(main_dir).name}\n")
    except OSError as exc:  # 宣言は作れている。気づけるよう警告だけを返す
        return Initialized(shared, True, (f"{ignore} を書けない（個人設定を使うなら手で登録する）: {exc}",))
    return Initialized(shared, True)
