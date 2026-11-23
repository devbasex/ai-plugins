"""文脈の切り替えの 2 つの値（判断開始の値・運用上限）を 1 つの規則で解く（#3995 の決定 1・5）。

値は上限の設定（`contracts/limits/context.decide-from.json`・`contracts/limits/context.limit.json`）だけに置き、環境変数
`NDF_CONTEXT_LIMIT` が運用上限を替える。判断開始の値が運用上限を上回れば同じ値へ下げる。上限の設定の引き方は
呼び出し元が渡す（hook と `ndf relay context` は検証を通さず直に読む `hooks.tables.policy_value`。上限の設定の形は `ndf check`
と CI が確かめる。中継のラッパーは `ndf.contracts.limits.value`）。判定の記録の語
（継続・拒否・再起動と再起動の理由）もここに置く。標準ライブラリだけに依存する。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, NamedTuple

DECIDE_FROM = "context.decide-from"  # 上限の設定の方針の名前（判断開始の値）
LIMIT = "context.limit"  # 上限の設定の方針の名前（運用上限）
DEFAULT, FROM_ENV = "default", "env"
MIN_SAMPLES = 3  # 見込みに使う中央値の標本の下限
CONTINUE, DENY, RESTART = "継続", "拒否", "再起動"  # 判定の記録の verdict の値
# 再起動の理由（判定の記録の列の値。過去の行が持つ値と同じ並び）
REASONS = ("容量", "運用上限", "費用", "独立性", "手動", "カットポイント", "待ち")


class Thresholds(NamedTuple):
    """そのセッションで効く 2 つの値。`sources` は値ごとの出所（default・env）と、無視した環境変数（ignored）。"""

    decide_from: int
    limit: int
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


def _positive_int(raw: str | None) -> int | None:
    """`raw` が正の整数（ASCII の数字だけ）ならその値、そうでなければ None。"""
    if raw and re.fullmatch("[0-9]+", raw) and int(raw) > 0:
        return int(raw)
    return None


def thresholds(environ: Mapping[str, str], value: Callable[[str, str], Any]) -> Thresholds:
    """上限の設定の 2 つの値に `NDF_CONTEXT_LIMIT` を重ね、判断開始の値 ≤ 運用上限にそろえて返す。

    環境変数が正の整数（ASCII の数字だけ）でなければ既定のまま、出所の `ignored` に変数名だけを書く（生値は判定の記録と
    標準出力へ渡さない。誤って入れた認証情報などを残さないため）。上限の設定に行が無ければ
    `value` の例外をそのまま上げる。
    """
    decide_from, limit = int(value(DECIDE_FROM, "count")), int(value(LIMIT, "count"))
    sources = {"decide_from": DEFAULT, "limit": DEFAULT}
    raw = environ.get("NDF_CONTEXT_LIMIT")  # 名前は環境変数の表（contracts/domains/env.json）が持つので定数に写さない
    env_limit = _positive_int(raw)
    if env_limit is not None:
        limit, sources["limit"] = env_limit, FROM_ENV
    elif raw:
        sources["ignored"] = "NDF_CONTEXT_LIMIT"
    if decide_from > limit:
        decide_from, sources["decide_from"] = limit, sources["limit"]
    return Thresholds(decide_from, limit, sources)
