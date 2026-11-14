"""ローカルの動作検証の判断（純粋な関数）: 載っているコードの照合と、相乗りか分離かの提示。

- `verify`: ローカル環境に載っているブランチ（宣言の `localenv.branch_probe` の出力）と対象のブランチを比べる。
  「未起動（読めない）」と「不一致」を分ける（次の手が違う）
- `mode`: 変更のパスが宣言の `localenv.isolate_when`（glob）に当たれば分離、当たらなければ相乗り
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, NamedTuple, Optional

# 照合の 3 つの結果
MATCH = "match"
MISMATCH = "mismatch"
NOT_RUNNING = "not_running"
# 提示の 2 つの結果
SHARE = "share"
ISOLATE = "isolate"


class Verdict(NamedTuple):
    result: str
    text: str


def verify(loaded: Optional[str], branch: Optional[str]) -> Verdict:
    """`loaded` はローカル環境に載っているブランチ（読めなければ `None`）、`branch` は対象のブランチ。"""
    value = loaded.strip() if loaded is not None else ""
    if not value or not branch:
        return Verdict(NOT_RUNNING, "未起動か適用外: ローカル環境に載っているブランチを読めない")
    if value == branch:
        return Verdict(MATCH, f"一致: ローカル環境に {branch} が載っている")
    return Verdict(MISMATCH, f"不一致: ローカル環境に載っているのは {value} で、対象は {branch}")


def mode(changed: Iterable[str], patterns: list[str]) -> tuple[Verdict, list[str]]:
    """提示と、条件に当たった `<パス>（<条件>）` の一覧。1 つのパスは 1 度だけ載せる。"""
    if not patterns:
        return Verdict(SHARE, "相乗り: 分離を促す条件が宣言されていない"), []
    hits = []
    for path in changed:
        hit = next((p for p in patterns if p and fnmatch.fnmatchcase(path, p)), None)
        if path and hit is not None:
            hits.append(f"{path}（{hit}）")
    if hits:
        return Verdict(ISOLATE, "分離: 次の変更が分離を促す条件に当たる"), hits
    return Verdict(SHARE, "相乗り: 分離を促す条件に当たる変更は無い"), []
