"""標準出力の最後の行の結果 JSON。"""

# テストは `from result_json import last_result` で読む。fixture `last_result`（`tests/conftest.py`）も同じ関数を返す。

from __future__ import annotations

import json


def last_result(stdout: str) -> dict:
    """標準出力の最後の 1 行（前後の空白を除いた後）を結果 JSON として読む。"""
    return json.loads(stdout.strip().splitlines()[-1])
