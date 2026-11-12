"""危険フラグの全体テストを最終ゲートへ寄せる（#933 決定 22・#1334 F5・#3048）。`commands/converge.py` が呼ぶ。

**どの戦略でも、検証の巡の中では全体テストを走らせない。** 全体テスト 1 回が数十分かかるリポジトリでは、巡ごとに
走らせると工程が数時間に延びる。立った危険フラグと項目を `whole_test.deferred` に残し、最終ゲートの全体テスト
（手元か CI）の 1 回で確かめる。最終ゲートが落ちたら、寄せた項目を修正の向け先として依頼に載せる（`commands/gate.py`）。
"""

from __future__ import annotations

import pathlib
from typing import Any

import statefile

from . import info


def defer_to_final_gate(path: pathlib.Path, state: dict[str, Any], record: dict[str, Any], flags: list[str], flagged: list[str]) -> None:
    """危険フラグの全体テストを最終ゲートの 1 回へ寄せる（決定 7・I8・#3048）。フラグと項目は巡をまたいで足し合わせる。"""
    deferred = record.setdefault("deferred", {"flags": [], "items": []})
    deferred["flags"] = sorted(set(deferred.get("flags") or []) | set(flags))
    deferred["items"] = list(dict.fromkeys(list(deferred.get("items") or []) + flagged))
    record["resolution"] = "deferred"
    statefile.save(path, state)
    info(f"⏭ 危険フラグ（{', '.join(flags)}）が立ちました。全体テストは巡の中で走らせず、最終ゲートへ寄せます（項目 {', '.join(flagged)}）")
