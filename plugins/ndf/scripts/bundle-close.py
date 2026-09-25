#!/usr/bin/env python3
"""bundle-close.py: mission-close.py の旧名。案内を stderr に出し、引数をそのまま新名へ渡す。

配布物の Skill が旧名で呼ぶ間だけ残す。結果と終了コードは mission-close.py のものである。
"""
import os
import sys
from pathlib import Path

NEW = Path(__file__).resolve().parent / "mission-close.py"

if __name__ == "__main__":
    print("bundle-close.py は mission-close.py へ改名した。mission-close.py を呼ぶ", file=sys.stderr)
    os.execv(sys.executable, [sys.executable, str(NEW), *sys.argv[1:]])
