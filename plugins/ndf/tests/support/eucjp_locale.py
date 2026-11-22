"""非 UTF-8 のロケール（`ja_JP.eucJP`）を使い捨ての保存先に作り、その環境変数を返す（#4029）。

`localedef` で `LOCPATH` の下へ作るので、システムのロケールを入れなくてよい。`localedef` か元データが無ければテストを飛ばす。
`tests/core/plan/test_queue_run.py` と `tests/progress/test_invoke.py` が使う。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NAME = "ja_JP.eucJP"
ENCODING = "euc_jp"


def environ(root: Path) -> dict[str, str]:
    """`root` の下に `ja_JP.eucJP` を作り、それを使う環境変数（`PYTHONPATH`・UTF-8 モードの指定・ASCII でない値を外した写し）を返す。"""
    if shutil.which("localedef") is None:
        pytest.skip("localedef が無い")
    if not all(str(p).isascii() for p in (sys.executable, sys.prefix, root)):
        pytest.skip("ASCII でないパスは EUC-JP のロケールで読み違える")
    root.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["localedef", "-i", "ja_JP", "-f", "EUC-JP", str(root / NAME)], capture_output=True, text=True, check=False)
    if not (root / NAME).is_dir():
        pytest.skip(f"ja_JP.eucJP を作れない: {done.stderr[-500:]}")
    dropped = ("PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING", "NDF_PROGRESS_DB")
    # UTF-8 の日本語を持つ値（呼び出し元のパスなど）は EUC-JP のロケールの子へ渡せないので外す
    env = {k: v for k, v in os.environ.items() if k not in dropped and v.isascii()}
    env.update(LOCPATH=str(root), LC_ALL=NAME, LANG=NAME)
    check = [sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"]
    got = subprocess.run(check, env=env, capture_output=True, text=True, check=False).stdout.strip()
    if got.replace("-", "_").lower() != ENCODING:
        pytest.skip(f"ja_JP.eucJP のロケールが効かない（{got}）")
    return env
