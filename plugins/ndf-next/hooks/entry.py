"""環境の python の hook のエントリポイント（#3345 の決定 89・IH2・IH4）。

登録の command（`hooks/<ランタイム>.json`。`ndf hooks render` が作る）が、用意済みの環境の python でこのファイルを起動する。
`argv[1]` がランタイム、`argv[2]` が事象、標準入力が payload で、本体は `ndf.domains.hooks.main.run_entry` にある。
エントリポイントのシェルのスクリプトと `uv run` は通らない。本体を読めないときも、出力なし・終了コード 0 で通す。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

# このファイルから 1 つ上がプラグインの根。src をパスの先頭へ足す（呼び出し元のディレクトリは足さない）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main(argv: List[str]) -> int:
    """いつも 0。"""
    try:
        from ndf.domains.hooks.main import run_entry
    except Exception:  # noqa: BLE001 ファイル: 本体を読めない環境（壊れた・古い）でも Tool の呼び出しを止めない（IH4）
        return 0
    return run_entry(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
