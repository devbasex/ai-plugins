"""CI のジョブのログから落ちたテストを読むパーサーの一覧（#3616。入出力を持たない）。

パーサーは suite の `runner`（設定ファイルの `test.suites[].runner`）で選ぶ。対応表 `PARSERS` が唯一の置き場で、
パーサーを足すときは表へ 1 行足すだけにし、CI の結果を読む処理（`ndf.adapters.ci`）を書き換えない。
登録の無い `runner` のログは読まない（ほかのランナーのログに pytest の形を当てない）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Optional

# ログの本文 → 落ちたテスト（`<ファイル>::<名前>`。重ねない・昇順）
LogParser = Callable[[str], list[str]]

# pytest の落ちた行（ログの各行の先頭の時刻を除いた後）。`FAILED <パス>::<名前> - <理由>`・`ERROR <パス>`
_PYTEST_FAILED = re.compile(r"(?:^|\s)(?:FAILED|ERROR) (?P<node>[^\s:]+\.py(?:::[^\s]+)?)")


def pytest_failures(log_text: str) -> list[str]:
    """pytest のログの `FAILED`・`ERROR` の行から node id を並べる（重ねない・昇順）。"""
    return sorted({m.group("node") for m in _PYTEST_FAILED.finditer(log_text)})


# `runner` の名前 → ログのパーサー
PARSERS: dict[str, LogParser] = {"pytest": pytest_failures}


def parser_of(runner: Optional[str]) -> Optional[LogParser]:
    """`runner` に登録されたログのパーサー。登録の無い名前と None には None。"""
    return PARSERS.get(runner) if runner is not None else None
