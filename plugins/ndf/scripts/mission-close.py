#!/usr/bin/env python3
"""mission-close.py: 旧名の入口（#1407）。案内を stderr に 1 行出し、引数をそのまま新しい名前のスクリプトへ渡す。

渡し先は lib/legacy_names.py の表が決める。結果と終了コードは渡し先のものである。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import legacy_names  # noqa: E402

if __name__ == "__main__":
    legacy_names.forward(__file__)
