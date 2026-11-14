"""環境が無いときの hook のエントリポイント（#3345 の決定 89・91・IH2・IH4）。標準ライブラリだけを読む。

登録の command は、環境の python が無ければシステムの `python3` でこのファイルを起動する。`hooks/entry.py` のパスは
登録の command が `shift` で外すので、`argv[1]` がランタイム、`argv[2]` が事象になり、`hooks/entry.py` と同じ位置で読む。
本体は `ndf.domains.hooks.boot.run_boot` にある（`strict` の guard と、SessionStart のときの環境の用意の起動だけを行う）。
本体を読めないとき（システムの `python3` が古いなど）も、出力なし・終了コード 0 で通す。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List


def _load_entry():
    """隣の `hooks/entry.py`（標準ライブラリだけを読む）。読むと src がパスの先頭へ足される。

    プラグインの置き場へ `hooks/__pycache__` を書かない（このファイルを起こしたときと同じに、entry.py の pyc を残さない）。
    """
    spec = importlib.util.spec_from_file_location("_ndf_hooks_entry", str(Path(__file__).resolve().with_name("entry.py")))
    module = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


_ENTRY = _load_entry()


def main(argv: List[str]) -> int:
    """いつも 0。"""
    return _ENTRY.call("ndf.domains.hooks.boot", "run_boot", argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
