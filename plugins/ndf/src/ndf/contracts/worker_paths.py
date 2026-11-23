"""worker の報告の保存先のパスと、リンクをたどらない読み書き。

4 つのファイル（プロンプト・報告・完了マーカー・作業の結果ファイル）の名前の形と接尾辞は
このモジュールだけが持つ。手順書・テスト・使う側のサブコマンドは字面を持たずに `report_paths` を呼ぶ。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
PROMPT_SUFFIX = ".prompt.md"
REPORT_SUFFIX = ".report.md"
DONE_SUFFIX = ".done"
RESULT_SUFFIX = ".result.json"


class NameInvalid(ValueError):
    """worker の名前が `[A-Za-z0-9._-]` の 1〜64 文字でない。"""


class ReportPaths(NamedTuple):
    """1 人の worker が読み書きする 4 つのファイルの絶対パス。"""

    prompt: Path
    report: Path
    done: Path
    result: Path


def report_paths(directory: Path, name: str) -> ReportPaths:
    """作業ディレクトリと worker の名前から 4 つの絶対パスを組む。ファイルの有無は見ない。

    リンクを解くのは `directory` だけで、名前を足した後のパスには当てない。
    """
    if not NAME_RE.fullmatch(name):
        raise NameInvalid(f"worker の名前は [A-Za-z0-9._-] の 1〜64 文字にする: {name!r}")
    base = directory.resolve()
    report = base / f"{name}{REPORT_SUFFIX}"
    return ReportPaths(
        prompt=base / f"{name}{PROMPT_SUFFIX}",
        report=report,
        done=report.with_name(report.name + DONE_SUFFIX),
        result=base / f"{name}{RESULT_SUFFIX}",
    )
