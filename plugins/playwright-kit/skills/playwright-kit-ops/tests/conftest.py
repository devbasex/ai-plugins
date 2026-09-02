"""tests/ から scripts/ と playwright_kit/ の両方を import 可能にする path 設定。

playwright_kit はパッケージなので import 可能。scripts/ 配下の単発スクリプト
(upload_evidence.py 等) はパッケージ化されていないため sys.path 追加が必要。
pytest plugin の自己テスト用に ``pytester`` を有効化する。
"""

from __future__ import annotations

import sys
from pathlib import Path

# ``pytester`` fixture (test 内で別 pytest を実行するためのサンドボックス) を有効化。
# playwright_kit.pytest_plugin の addoption / markers / fixture 動的登録を
# 隔離環境で検証するために使う。
# `pytest_plugins` はリポジトリの根の conftest.py が宣言する。pytest は最上位以外の
# conftest.py での宣言を受け付けず、起点をリポジトリの根に置くと収集が中断する。
# このディレクトリを起点にしたときは pyproject.toml の addopts が `-p pytester` で補う。

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _SKILL_ROOT / "scripts"

for p in (str(_SKILL_ROOT), str(_SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
