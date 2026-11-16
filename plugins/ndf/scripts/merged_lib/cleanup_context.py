"""後片付けの実行文脈（merged-steps.py の cleanup が 1 度決め、PR ごとの片付けへ渡す）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CleanupContext:
    """後片付けの実行文脈。`main_dir` は主ディレクトリで、git を打つ根も兼ねる（消す作業ツリーから打たない）。

    `slug` は一時の置き場の下のリポジトリの名前（決まらなければ `None`）、`wt_base` は一時の置き場、`add` は記録の 1 件を足す。"""

    main_dir: str
    slug: str | None
    wt_base: Path
    add: Callable[..., None]
