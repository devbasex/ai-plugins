"""旧い値の読み替え（#2620 の決定 5）。版を上げる前に書かれた記録の値を、読む時点で新しい値へ変換する。

旧い値の正本は `scripts/data/legacy-values.json` だけである。Python の読み手は記録を読んだ直後に `normalize` を
1 回呼び、後の処理は新しい値だけを扱う。シェルの読み手は同じ表を `jq` で引く。既存の記録は書き換えない。

表に誤りがあれば（1 つの集合で旧い値が重なる・新しい値が旧い値の欄に現れる）`StepError`（終了コード 2）で止まる。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from step_result import EXIT_UNREADABLE, StepError

TABLE = Path(__file__).resolve().parents[1] / "data" / "legacy-values.json"
PREFIX_SETS = ("record_stage_prefix",)  # 前方一致で変換する集合（値が後ろに理由を持つ）


def _bad(msg: str) -> StepError:
    return StepError(f"旧い値の読み替えの表（{TABLE.name}）: {msg}", EXIT_UNREADABLE)


def _no_duplicates(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise _bad(f"同じ鍵が 2 度ある: {', '.join(dup)}")
    return dict(pairs)


def parse(text: str) -> dict[str, dict[str, str]]:
    """表の本文を読み、形を確かめて `{集合: {旧い値: 新しい値}}` を返す。"""
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates)
    except ValueError as e:
        raise _bad(f"JSON として読めない: {e}") from e
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("sets"), dict):
        raise _bad("version 1 と sets を持つオブジェクトで書く")
    for name, pairs in raw["sets"].items():
        if not isinstance(pairs, dict) or not all(isinstance(v, str) and v for v in pairs.values()):
            raise _bad(f"{name} は旧い値から新しい値への対応で書く")
        both = sorted(set(pairs) & set(pairs.values()))
        if both:
            raise _bad(f"{name} の新しい値が旧い値の欄にもある: {', '.join(both)}")
    return raw["sets"]


@lru_cache(maxsize=1)
def table() -> dict[str, dict[str, str]]:
    try:
        return parse(TABLE.read_text(encoding="utf-8"))
    except OSError as e:
        raise _bad(f"読めない: {e}") from e


def normalize(name: str, value):
    """値の集合 `name` の旧い値を新しい値へ変換する。表に無い値と文字列でない値はそのまま返す。"""
    sets = table()
    if name not in sets:
        raise _bad(f"知らない値の集合: {name}")
    if not isinstance(value, str):
        return value
    if name in PREFIX_SETS:
        for old, new in sets[name].items():
            if value.startswith(old):
                return new + value[len(old) :]
        return value
    return sets[name].get(value, value)


def old_values(name: str) -> list[str]:
    """値の集合の旧い値の一覧（本文の中の見出しを探す読み手が使う）。"""
    return list(table().get(name, {}))
