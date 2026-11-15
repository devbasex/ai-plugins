"""機能の対応表のテストの前提。ndf-next の依存の環境（`plugins/ndf-next/uv.lock`）で、リポジトリの根から流す。

対応表のパッケージ（`parity`）をこのディレクトリの親から読めるようにする。根の環境（`pytest .`）は `jsonschema` と
`ndf` を持たないため、根の `conftest.py` がこのディレクトリを収集から外す（#3617 の決定 10）。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
