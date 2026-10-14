"""指摘どうしの照合（位置・近傍・本文）。cross-review と fix が同じ照合を使う。

新しい指摘の数え方・振動の検知（cross-review の `review_lib.matching`）と、総評だけの指摘の
再提出の照合（`body_findings.pending`）が、この 1 か所の判定を使う。照合を 2 つ持つと、
片方だけが更新されたときに数え方と収束の判定が食い違う。
"""

from __future__ import annotations

import re

# 指摘の位置がずれても同じ箇所として数える幅。修正で前後にずれる幅として、同じ処理の
# 範囲の中の移動を拾い、隣の指摘まで巻き込まない値を採る。**この値の根拠となる実測は
# まだ無い。** 出力へ内訳を出すのは、実測を集めるためである。
OSCILLATION_NEAR_LINES = 3
# 本文を比べる長さ。先頭だけを見るのは、末尾の言い回しの揺れで別物にならないようにするため。
OSCILLATION_BODY_CHARS = 80
# 正規化で落とすもの。**文字と数字は言語を問わず残す。** 指摘の本文は日本語で書かれるため、
# ASCII の英数字だけを残すと本文が空になり、別の指摘どうしが一致してしまう。
OSCILLATION_DROP = re.compile(r"[^\w]", re.UNICODE)


def normalized_body(body: object) -> str:
    """指摘の本文を、行番号・引用符・記号の違いで別物にならない形へ揃える。"""
    if not isinstance(body, str):
        return ""
    return OSCILLATION_DROP.sub("", body.lower())[:OSCILLATION_BODY_CHARS]


def match_kind(
    current_key: tuple[str, int, str],
    previous_keys: list[tuple[str, int, str]],
) -> str | None:
    """前の指摘の並びに対する、指摘 1 件の一致の種別を返す。

    鍵は (ファイル, 行, 正規化した本文)。同じファイルに限った候補の全体に対して、
    完全一致 → 近傍 → 本文の優先順で判定し、"exact" / "near" / "body" / None を返す。
    """
    path, line, body = current_key
    same_file = [q for q in previous_keys if q[0] == path]
    if any(line == q[1] for q in same_file):
        return "exact"
    if any(abs(line - q[1]) <= OSCILLATION_NEAR_LINES for q in same_file):
        return "near"
    if body and any(body == q[2] for q in same_file):
        return "body"
    return None
