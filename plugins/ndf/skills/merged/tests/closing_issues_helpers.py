"""閉じる語の読み取りのテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/closing-issues.sh"


def read(body: str) -> subprocess.CompletedProcess:
    """本文を標準入力から渡して、取り出された番号を返す。"""
    return subprocess.run(
        ["bash", str(SCRIPT)], input=body, capture_output=True, text=True
    )


def numbers(body: str) -> list[str]:
    result = read(body)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()
