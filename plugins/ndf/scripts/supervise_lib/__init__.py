"""プランの実行（supervise.py）の中身（#1142 の C1）。分け方は issues/issue-1142-design-modules.md の supervise_lib の節。"""
import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parent.parent / "lib")  # 各モジュールが import するライブラリ（step_result ほか）
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
