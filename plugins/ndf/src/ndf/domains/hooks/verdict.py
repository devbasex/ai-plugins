"""guard の判定の型。標準ライブラリだけで書く（`strict` の guard と環境が無いときのエントリポイントも使う）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 判定の種類。強い順（まとめるときは強いものを 1 つ残す）
KINDS = ("deny", "pushback", "advise", "pass")
# 理由（reason）を持つ判定の種類。ほかは案内の文（context）を持つ
REASON_KINDS = ("deny", "pushback")


@dataclass(frozen=True)
class Verdict:
    """1 つの guard の判定。

    - `kind`: `pass`（何もしない）・`advise`（通して文脈に文を入れる）・`deny`（Tool の呼び出しを止める）・
      `pushback`（Stop を変更を要求して続けさせる）
    - `reason`: 止める・変更を要求する理由（会話へ入る）
    - `context`: 案内の文（会話へ入る）
    - `key`: 同じ拒否を数える対象（読む範囲・コマンドの hash など）。空なら理由だけで数える
    - `notes`: 計測に残すマーカー（会話へ入れない）
    - `sole`: まとめる判定にこれがあれば、同じ種類のほかの文を入れずこの文だけを出す（ほかの指示より先に従う手順で、
      連結すると注入の上限で末尾が切れ得るもの。運用上限の引継ぎ）
    """

    kind: str
    reason: str = ""
    context: str = ""
    key: str = ""
    notes: dict = field(default_factory=dict)
    sole: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"知らない判定の種類: {self.kind}")

    @property
    def text(self) -> str:
        """会話へ入る文。"""
        return self.reason if self.kind in REASON_KINDS else self.context


PASS = Verdict("pass")


def passing(**notes: object) -> Verdict:
    """何もしない判定。`notes` は計測に残すマーカー。"""
    return Verdict("pass", notes=notes)


def deny(reason: str, key: str = "", notes: Optional[dict] = None, sole: bool = False) -> Verdict:
    return Verdict("deny", reason=reason, key=key, notes=notes if notes is not None else {}, sole=sole)


def pushback(reason: str) -> Verdict:
    return Verdict("pushback", reason=reason)


def advise(context: str) -> Verdict:
    return Verdict("advise", context=context)


def strength(kind: str) -> int:
    """強さの順位。小さいほど強い。"""
    return KINDS.index(kind)
