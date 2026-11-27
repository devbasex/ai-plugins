"""変わったファイルから、バージョンの単位ごとの変化と承認資料に載せる変化を決める（純粋な関数）。

- 単位の保存先は保存先のアダプター（`version_store.Unit.dir`）から渡され、名前で決め打たない（#1756）。空ならリポジトリ全体
- 単位の下の変化を、リリース対象の変化とリリース対象に関わらない変化に分ける。リリース対象に関わらないファイルの既定は
  テストのファイルの表（`contracts/domains/test-files.json`。役割を問わずすべての行）から作り、設定 `undistributed` の形を足す
  （#3615 の決定 5）。リリース対象に関わらない変化だけの単位は、設定 `bump_undistributed` が真のときだけバージョンを上げる（#2217）
- 「利用者向けの変化なし」は、リリース対象のファイルの変化が 1 件も無いことだけで決める。PR 本文の文は読まない（#2601）
- 指示書の変化は、指示書のファイル（事実の表 `instruction-files`）と設定の保存先（`.ndf/`）の下の変化を並べる。
  `.ndf/` の設定の変化（C7）も人が承認資料で見られる（#3110）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

import pathspec

from ndf import identity
from ndf.contracts import facts

# 指示書のファイルの表
INSTRUCTION_FILES = "instruction-files"


class Change(NamedTuple):
    """1 つのバージョンの単位の変化。パスはリポジトリの根から。"""

    plugin: str
    distributed: tuple[str, ...]
    undistributed: tuple[str, ...]

    @property
    def touched(self) -> bool:
        return bool(self.distributed or self.undistributed)


def default_undistributed() -> tuple[str, ...]:
    """テストのファイルの表のすべての行から作る、リリース対象に関わらないファイルの gitignore の形。

    保存先の行（`dir`）は `<名前>/`（どの深さのそのディレクトリの下も当たる）、名前の行は fnmatch の形のまま（どの深さの
    そのファイル名にも当たる）。"""
    # 表の名前の定数は `crossrefactor.tests_decide.TABLE`。領域をまたいで読まず、最上位の定数として写さない（const-fact）
    return tuple(f"{r['pattern']}/" if r["where"] == "dir" else r["pattern"] for r in facts.rows("test-files"))


def _under(path: str, directory: str) -> str | None:
    """`path` が `directory` の下なら、その中の相対パス。下でなければ None。`directory` が空ならリポジトリの根の下（そのまま）。"""
    if not directory:
        return path
    prefix = directory.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def change_of(paths: Iterable[str], unit: str, directory: str, extra: Sequence[str]) -> Change:
    """1 つの単位の下の変化を、リリース対象とそれ以外に分ける。リリース対象外の形は既定と `extra`（設定の形）の和。"""
    spec = pathspec.GitIgnoreSpec.from_lines([*default_undistributed(), *extra])
    inside = [(p, rel) for p in sorted(set(paths)) if (rel := _under(p, directory)) is not None]
    return Change(
        unit,
        tuple(p for p, rel in inside if not spec.match_file(rel)),
        tuple(p for p, rel in inside if spec.match_file(rel)),
    )


def changes(paths_by_unit: Mapping[str, Sequence[str]], unit_dirs: Mapping[str, str], extra: Sequence[str]) -> list[Change]:
    """単位ごとに、その単位の範囲の変わったファイルを分けた変化（変化のある単位だけ。名前の順）。"""
    got = [change_of(paths_by_unit[name], name, unit_dirs[name], extra) for name in sorted(unit_dirs)]
    return [c for c in got if c.touched]


def needs_bump(change: Change, bump_undistributed: bool) -> bool:
    """バージョンを上げるか。リリース対象の変化があれば上げ、リリース対象に関わらない変化だけなら設定に従う（#2217）。"""
    if change.distributed:
        return True
    return bump_undistributed and bool(change.undistributed)


def distribution_changed(found: Iterable[Change]) -> bool:
    """リリース対象の変化があるか（#2601。PR 本文の「利用者向けの変化」は読まない）。"""
    return any(c.distributed for c in found)


def instruction_changes(paths: Iterable[str]) -> list[str]:
    """指示書と設定の保存先（`.ndf/`）の下の変化（#3110）。"""
    names = set(facts.ids(INSTRUCTION_FILES))
    decl = identity.FIXED_WORDS["ndf_settings_dir"] + "/"
    return sorted({p for p in paths if p in names or p.startswith(decl)})
