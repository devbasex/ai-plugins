"""リリースの経路（`reviewed.release_prod`・`reviewed.release_dev`・`promotion.release_selected`）へ同じ組で渡る値（`release-steps.py`
の release が 1 度組む）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseCtx:
    root: Path  # 作業ツリーの根
    a: object  # release の引数
    plugin: str  # 版を決めるプラグイン
    plugins: list[str]  # 出すプラグインの並び
    branch: str  # リリースブランチ
    base: str  # ベースブランチ（開発版のチャネル）
    prod: str  # 本番チャネル
