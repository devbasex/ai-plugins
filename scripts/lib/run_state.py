"""テストのセッションの一時の置き場（`<tmp>/ndf-tests-*`）の片付け（#2229）。

11.0.0 の入れ替え（#3304）で 10 系のプラグインの `run_state.sweep_states` から根へ移した。根の `conftest.py` が
テストのセッションの終わりに呼ぶ、種類 `test-tmp` の片付けだけを持つ。止められたセッションが残した置き場を、
最終更新から寿命の窓（24 時間）を過ぎたものだけ消す。例外を外へ出さない。標準ライブラリだけで書く。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

WINDOW = 24 * 3600  # 寿命の窓（秒。10 系の `run_owner.WINDOW` と同じ値）
PATTERN = "ndf-tests-*"


def sweep_test_tmp(tmp: Path | None = None, now: float | None = None) -> list[Path]:
    """寿命の窓を過ぎた `<tmp>/ndf-tests-*` を消し、消したパスを返す。"""
    tmp = Path(tempfile.gettempdir()) if tmp is None else Path(tmp)
    now = time.time() if now is None else now
    removed = []
    for p in sorted(tmp.glob(PATTERN)):
        try:
            if now - p.lstat().st_mtime <= WINDOW:
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            continue
        removed.append(p)
    return removed
