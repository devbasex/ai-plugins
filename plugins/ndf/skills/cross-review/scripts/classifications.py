"""cross-review の区分に関する共有定義（#156、#732）。

収束の判定（`state.py`）と効果の測定（`measure.py`）が同じ区分を数えるための
唯一の定義を置く。片方だけに区分を足すと、判定が数えた指摘を測定が採らず、
その方式の再現率が実際より低く出る（`test_measure.py` が両者の一致を固定する）。
"""
from __future__ import annotations

# 収束の判定が数える区分（#156、#732）。**残る 3 つは数えない。** 数えないのは、誤りだと
# 示された棄却と、承認を妨げない軽微な指摘だけである。棄却した指摘を数えると、そのぶん
# ラウンドが増える（#69 で同じ論点が 5 ラウンド続いた事象）。
COUNTED_CLASSIFICATIONS = ("verified_blocking", "needs_human_judgment", "unrefuted")
