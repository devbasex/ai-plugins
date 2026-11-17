"""リリースの経路（`reviewed.release_prod`・`reviewed.release_dev`・`strategy_route.release_selected`）へ同じ組で渡る値（`release-steps.py`
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
    sync: bool = True  # 版上げの PR をベースブランチへ入れる段がある（`strategy_route.route_of` の `sync`。trunk は偽）

    def prod_metrics(self, **extra) -> dict:
        """本番の release の計測の初期形（どの経路にも共通の 8 鍵）。`extra` で上書きし、無い鍵は後ろへ足す。"""
        return {
            "channel": "prod",
            "version": self.a.version,
            "release_pr": None,
            "sync_pr": None,
            "main_pr": None,
            "tag": None,
            "merge_commit": None,
            "plugins": ",".join(self.plugins),
            **extra,
        }
