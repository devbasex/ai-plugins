"""環境が無いときの hook のエントリポイント（#3345 の決定 89・91・IH2・IH4）。標準ライブラリだけを読む。

登録の command は、環境の python が無ければシステムの `python3` でこのファイルを起動する。`hooks/entry.py` のパスは
登録の command が `shift` で外すので、`argv[1]` がランタイム、`argv[2]` が事象になり、`hooks/entry.py` と同じ位置で読む。
本体は `ndf.domains.hooks.boot.run_boot` にある（`strict` の guard と、SessionStart のときの環境の用意の起動だけを行う）。
本体を読めないとき（システムの `python3` が古いなど）も、出力なし・終了コード 0 で通す。
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
        from ndf.domains.hooks.boot import run_boot
    except Exception:  # noqa: BLE001 ファイル: 本体を読めない python（古い版など）でも Tool の呼び出しを止めない（IH4）
        return 0
    return run_boot(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
