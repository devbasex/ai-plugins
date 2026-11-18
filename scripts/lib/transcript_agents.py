"""会話の記録の層（conductor / supervisor / worker）とフェーズの語彙（#550・#768）。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の集計（`scripts/token-usage.py`）が使う
層とフェーズの決め方（`layer_of`・`role_of`）と語彙だけを持つ。記録を読む部分はプラグインの側に残る。
標準ライブラリも読まない。
"""

from __future__ import annotations

LAYERS = ("conductor", "supervisor", "worker")
POSTS = ("設計", "実装", "検査", "取り込み", "仕上げ")  # フェーズ（supervisor）
TASKS = ("調査", "修正", "検証", "集計")  # 作業の種類（worker）
OTHER = "その他"
NO_ROLE = "-"

# 工程名 → フェーズ（#768）。`development-workflow/references/agent-layers.md` のフェーズの表の
# 「通す工程」の列を写した。2 つのフェーズに現れる行名（`作業場所の用意`・`デプロイ`）は表で先に現れるフェーズへ写す。
STEP_PHASES = {
    "要求と受け入れ条件": "設計",
    "作業場所の用意": "設計",
    "詳細設計": "設計",
    "ドキュメント再構成": "設計",
    "ドキュメントレビュー": "設計",
    "ドキュメントレビューの後片付け": "実装",
    "計画": "実装",
    "構造改善": "検査",
    "実装レビュー": "検査",
    "完了判定": "検査",
    "Pull Request": "検査",
    "確定仕様化": "取り込み",
    "スプリントの Pull Request のマージ": "取り込み",
    "後片付け": "取り込み",
    "デプロイ": "取り込み",
    "リリース後テスト": "仕上げ",
    "振り返り": "仕上げ",
    "棚卸し": "仕上げ",
}
# 旧い工程名の読み替え（#2620。10 系の `scripts/data/legacy-values.json` の集合 `stage` と同じ値）
LEGACY_STEPS = {"配布": "デプロイ"}


def head_word(description: str | None) -> str:
    """`description` の最初の `: ` より前を返す。"""
    if not description:
        return ""
    head, sep, _ = description.partition(": ")
    return head.strip() if sep else description.strip()


def layer_of(depth: int, description: str | None) -> str:
    """層は深さで決め、深さ 1 だけ `description` の先頭語で分ける（決定 14）。"""
    if depth <= 0:
        return "conductor"
    if depth >= 2:
        return "worker"
    return "worker" if head_word(description) in TASKS else "supervisor"


def role_of(description: str | None, layer: str) -> str:
    """フェーズ（supervisor）または作業の種類（worker）を返す。

    supervisor の先頭語が工程名（`STEP_PHASES`）なら、その工程を通すフェーズへ写す（#768）。
    `: ` を持たない古い形（例 `実装 #540 実行計画`）は全体が語彙に当たらず `その他` に落ちる。
    """
    if layer == "conductor":
        return NO_ROLE
    head = head_word(description)
    if layer == "supervisor":
        if head in POSTS:
            return head
        return STEP_PHASES.get(LEGACY_STEPS.get(head, head), OTHER)
    return head if head in TASKS else OTHER
