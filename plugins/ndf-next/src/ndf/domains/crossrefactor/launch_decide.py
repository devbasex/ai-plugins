"""担当の結果ファイルの置き場（#750）。

担当は自分の作業ディレクトリの外を書かない。読み取りの担当（提案・リファクタリング計画・判定）は読み取り用ディレクトリで動くため、
結果ファイルの提出先を書き込み用の作業ディレクトリに置くと、担当が書けずに結果が無いまま終わる。
提出先はどの工程でも担当が動くディレクトリの `.cross_refactoring/` に置き、起動の側が終わった後に一時ディレクトリへ回収する。
`.cross_refactoring/` は差分から外す（`IGNORE_ENTRY`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

from ndf.contracts import facts

PHASES = "loop-phases"
SKILL = "cross-refactoring"
RESULT_DIR = ".cross_refactoring"
IGNORE_ENTRY = f"{RESULT_DIR}/"


def read_only_phases() -> frozenset[str]:
    """読み取り用ディレクトリで動く工程（コードを書かない工程）。表 `loop-phases` の coding が偽の行。"""
    return frozenset(facts.ids(PHASES, skill=SKILL, coding=False))


def coding_phases() -> frozenset[str]:
    """コードを書く工程。表 `loop-phases` の coding が真の行（安全網はこの工程の起動の秒を数える）。"""
    return frozenset(facts.ids(PHASES, skill=SKILL, coding=True))


@dataclass(frozen=True)
class ResultPlace:
    """担当が書く結果ファイル（`submit`）と、起動の側が回収する先（`collect`）。"""

    workdir: PurePath
    submit: PurePath
    collect: PurePath


def workdir_of(phase: str, runtime: str, read_root: PurePath, work: PurePath) -> PurePath:
    """担当が動くディレクトリ。読み取りの工程は `<read_root>/<runtime>`、コードを書く工程は書き込み用の作業ディレクトリ。"""
    return read_root / runtime if phase in read_only_phases() else work


def result_place(phase: str, runtime: str, stem: str, dirs: tuple[PurePath, PurePath, PurePath]) -> ResultPlace:
    """結果ファイルの置き場。`dirs` は (読み取り用ディレクトリの根, 書き込み用の作業ディレクトリ, 回収先の一時ディレクトリ)。

    提出先は担当が動くディレクトリの中にあり、担当は作業ディレクトリの外を触らずに書ける。
    """
    read_root, work, tmp = dirs
    workdir = workdir_of(phase, runtime, read_root, work)
    name = f"{runtime}-{phase}-{stem}-result.json"
    return ResultPlace(workdir, workdir / RESULT_DIR / name, tmp / name)
