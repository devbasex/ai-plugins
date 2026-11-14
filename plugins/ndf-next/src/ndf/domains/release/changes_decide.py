"""変わったファイルから、プラグインごとの変化と承認資料に載せる変化を決める（純粋な関数）。

- プラグインの置き場はマーケットプレイスの登録（`plugins.Plugin.dir`）から渡され、名前で決め打たない（#1756）
- プラグインの下の変化を、リリース対象の変化とリリース対象に関わらない変化（宣言 `undistributed` の形に当たるファイル。既定は
  テスト）に分ける。リリース対象に関わらない変化だけのプラグインは、宣言 `bump_undistributed` が真のときだけ版を上げる（#2217）
- 「利用者向けの変化なし」は、リリース対象のファイルの変化が 1 件も無いことだけで決める。PR 本文の文は読まない（#2601）
- 指示書の変化は、指示書のファイル（事実の表 `instruction-files`）と宣言の置き場（`.ndf/`）の下の変化を並べる。
  `.ndf/` の設定の変化（C7）も人が承認資料で見られる（#3110）
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

import pathspec

from ndf import identity
from ndf.contracts import facts

# リリース対象に関わらないファイルの既定の形（プラグインの根からの gitignore の形）
DEFAULT_UNDISTRIBUTED = ("tests/", "**/tests/", "**/test_*.py", "**/conftest.py")
# 指示書のファイルの表
INSTRUCTION_FILES = "instruction-files"


class Change(NamedTuple):
    """1 つのプラグインの変化。パスはリポジトリの根から。"""

    plugin: str
    distributed: tuple[str, ...]
    undistributed: tuple[str, ...]

    @property
    def touched(self) -> bool:
        return bool(self.distributed or self.undistributed)


def _under(path: str, directory: str) -> str | None:
    """`path` が `directory` の下なら、その中の相対パス。下でなければ None。"""
    prefix = directory.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def split(paths: Iterable[str], plugin: str, directory: str, undistributed: Sequence[str]) -> Change:
    """1 つのプラグインの下の変化を、リリース対象とそれ以外に分ける。"""
    spec = pathspec.GitIgnoreSpec.from_lines(undistributed)
    inside = [(p, rel) for p in sorted(set(paths)) if (rel := _under(p, directory)) is not None]
    return Change(
        plugin,
        tuple(p for p, rel in inside if not spec.match_file(rel)),
        tuple(p for p, rel in inside if spec.match_file(rel)),
    )


def changes(paths: Iterable[str], plugin_dirs: dict[str, str], undistributed: Sequence[str]) -> list[Change]:
    """プラグインごとの変化（変化のあるプラグインだけ。名前の順）。"""
    listed = sorted(set(paths))
    got = [split(listed, name, plugin_dirs[name], undistributed) for name in sorted(plugin_dirs)]
    return [c for c in got if c.touched]


def needs_bump(change: Change, bump_undistributed: bool) -> bool:
    """版を上げるか。リリース対象の変化があれば上げ、リリース対象に関わらない変化だけなら宣言に従う（#2217）。"""
    if change.distributed:
        return True
    return bump_undistributed and bool(change.undistributed)


def distribution_changed(found: Iterable[Change]) -> bool:
    """リリース対象の変化があるか（#2601。PR 本文の「利用者向けの変化」は読まない）。"""
    return any(c.distributed for c in found)


def instruction_changes(paths: Iterable[str]) -> list[str]:
    """指示書と宣言の置き場（`.ndf/`）の下の変化（#3110）。"""
    names = set(facts.ids(INSTRUCTION_FILES))
    decl = identity.FIXED_WORDS["declaration_dir"] + "/"
    return sorted({p for p in paths if p in names or p.startswith(decl)})
