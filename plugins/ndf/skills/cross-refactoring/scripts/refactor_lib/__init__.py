"""cross-refactoring の状態管理 CLI を構成するモジュール群。

入口は同じディレクトリの `refactor.py` で、ここには全モジュールが使う土台だけを
置く。共通層（`plugins/ndf/scripts/lib`）の読み込みと、進行の合図（`info`）と
中断（`die`）である。
"""
from __future__ import annotations

import pathlib
import sys

# 共通層はプラグインルート直下にある。**`.resolve()` を通す。** Kiro CLI は
# `.kiro/skills/<名前>` を symlink にするため、解かずに `parents[]` を数えると
# `.kiro` で止まってプラグインルートへ届かない。
_COMMON_LIB = str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "lib")
if _COMMON_LIB not in sys.path:
    sys.path.insert(0, _COMMON_LIB)

import statefile  # noqa: E402

info = statefile.info

# 中断の終了コード。**「全件失敗」（2）と区別する。** 進行スクリプトは 2 なら次の
# 提案ラウンドへ進み、4 なら進行そのものを止める。区別しないと、取り消しに失敗した
# 状態を「全件失敗」として握り潰し、**検証を通っていない変更を Pull Request に
# 残したまま**次の提案が始まる（実測）。
ABORT = 4


def die(msg: str, code: int = ABORT) -> None:
    """中断して終了する。既定は「中断」を表す終了コード。"""
    statefile.die(msg, code)
