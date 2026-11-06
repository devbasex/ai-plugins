"""token の換算費用の単価の正本（#893・#2262 の決定 10）。

input の単価を 1 とした倍率で持つ。cache read だけはモデルで倍率が違うため `READ_RATES` で引く。
デプロイする hook（`hook_lib/token_guard.py` の判定）と、根の `scripts/token-usage.py` の両方がここを読む。

出典: 公式の料金表 https://platform.claude.com/docs/en/about-claude/pricing と claude-api の Skill
（2026-09-24 に確認。Opus 5.5 は input $4 / cache read $0.20、Fable 5.1 は $10 / $0.25）。
"""

from __future__ import annotations

# 換算費用の重み（input=1）。w5 / w1h は 5 分 / 1 時間のキャッシュの書き込み
WEIGHTS = {"inp": 1.0, "w5": 1.25, "w1h": 2.0, "out": 5.0}
# cache read の倍率（そのモデルの input 単価との比）。`message.model` の先頭で引き、無いモデルは READ_RATE_DEFAULT
READ_RATES = (("claude-opus-5-5", 0.05), ("claude-fable-5-1", 0.025))
READ_RATE_DEFAULT = 0.1


def read_rate(model: str | None) -> float:
    """モデルの cache read の倍率。"""
    for prefix, rate in READ_RATES:
        if (model or "").startswith(prefix):
            return rate
    return READ_RATE_DEFAULT
