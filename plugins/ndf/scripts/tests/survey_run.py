"""design-survey.py の `check` を子のプロセスで起動し、終了コードと最後の行の JSON を返す（設計の検査のテストの共通の補助）。

`--root` は渡した引数の後ろに置く。標準出力が空なら JSON の代わりに None を返す。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "design-survey.py"


def run_survey_check(root, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), "check", *args, "--root", str(root)], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    return p.returncode, out
