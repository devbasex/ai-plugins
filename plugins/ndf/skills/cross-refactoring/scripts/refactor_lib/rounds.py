"""ラウンドの種類と、種類ごとに変わる項目の扱い。

**階層はすべて「ラウンド」で表す**（#436）。テスト整備ラウンドと提案ラウンドは
同じ形（提案 → 採否 → 適用ラウンド → 検証 → 修正ラウンド）を持ち、**適用ラウンドと
修正ラウンドを共有する**。違うのは集める提案の中身だけである。

種類で変わるのは 3 つ（重複排除と対象外の鍵・外へ出す文章での指し方・見送りの
記録の形）だけなので、ここへ集める。工程の側は種類で分岐しない。
"""
from __future__ import annotations

import pathlib

from typing import Any

import statefile

from . import info
from .paths import git_out

# ラウンドの種類。**宣言の無い状態ファイルは構造改善として読む**（この版より前で
# 始めた実行を、再開の時点でテスト整備へ戻さないため）。
TEST = "test"
STRUCTURE = "structure"


def round_kind(state: dict[str, Any]) -> str:
    """次に開くラウンドの種類。"""
    return TEST if state.get("round_kind") == TEST else STRUCTURE


def entry_kind(entry: dict[str, Any]) -> str:
    """記録済みのラウンド 1 件の種類。"""
    return TEST if entry.get("kind") == TEST else STRUCTURE


def item_kind(item: dict[str, Any]) -> str:
    """項目 1 件の種類。改善項目とテスト項目は同じ一覧に並ぶ。"""
    return TEST if item.get("kind") == TEST else STRUCTURE


def item_key(item: dict[str, Any]) -> tuple[str, ...]:
    """重複排除と対象外の判定に使う鍵（決定 9 / 決定 10）。

    改善項目は `path` + `symbol` + `smell`、**テスト項目は `target` + `case`**
    である。`level` は鍵に入れない（同じ経路を別の階層で 2 度固定させないため）。
    """
    if item_kind(item) == TEST:
        return (str(item.get("target") or ""), str(item.get("case") or ""))
    return (
        str(item.get("path") or ""),
        str(item.get("symbol") or ""),
        str(item.get("smell") or ""),
    )


def item_label(item: dict[str, Any]) -> str:
    """外へ出す文章で項目を指す名前。**内部の識別子だけで書かない。**

    どちらの種類も `<ファイル>#<シンボル>` になる。テスト項目の `path` は
    テストを足す先なので、指す対象は `target`（固定する入口）である。
    """
    if item_kind(item) == TEST:
        return str(item.get("target") or item.get("path") or "?")
    return f"{item.get('path')}#{item.get('symbol')}"


def deferred_record(
    item: dict[str, Any], item_id: str, reason: str
) -> dict[str, Any]:
    """見送り（対象外）の記録。**鍵に要る項目を種類ごとに残す。**

    残さないと、同じ提案が次のラウンドで再び採用され、同じ理由で失敗する。
    """
    record: dict[str, Any] = {
        "item_id": item_id,
        "kind": item_kind(item),
        "path": item.get("path"),
        "round": item.get("round"),
        "defer_reason": reason,
    }
    if item_kind(item) == TEST:
        record.update({"target": item.get("target"), "case": item.get("case")})
    else:
        record.update({"symbol": item.get("symbol"), "smell": item.get("smell")})
    return record

def finish_outer_rounds(path: pathlib.Path, state: dict[str, Any], reason: str) -> None:
    state["final"] = reason
    state["ended_at"] = statefile.now()
    state["phase"] = "final"
    statefile.save(path, state)
    info(f"提案ラウンドの繰り返しを終了します（理由: {reason}）")


# ---------- 適用ラウンドの群の進行 ----------
#
# **群の進行は、適用と検証の両方が読む。** `commands` 層のどちらかに置くと、
# もう一方が横から取り込むことになる（#441）。

def apply_groups(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """このラウンドの適用ラウンド（群）の一覧。

    群を持たない状態ファイル（この版より前）は、**ラウンド全体を 1 つの群**として
    読み、その場で記録する。中断から再開したときに、群の単位が実行のたびに
    変わらないようにするためである。
    """
    groups = entry.get("apply_rounds")
    if groups:
        return groups
    entry["apply_rounds"] = [{
        "apply_round": 1,
        "impl": entry.get("impl"),
        "impl_model": entry.get("impl_model") or {"requested": None, "observed": None},
        "items": list(entry.get("items") or []),
        "status": "pending",
        "base_sha": entry.get("apply_base_sha"),
        "head_sha": None,
        "fix_rounds": entry.get("fix_rounds", 0),
    }]
    entry.setdefault("apply_round", 1)
    return entry["apply_rounds"]

def current_group(entry: dict[str, Any]) -> dict[str, Any]:
    """進行中の適用ラウンド。まだ開いていなければ最初の群を返す。"""
    groups = apply_groups(entry)
    current = entry.get("apply_round") or 1
    for group in groups:
        if group.get("apply_round") == current:
            return group
    return groups[-1]

def phase_after_group(entry: dict[str, Any]) -> str:
    """この群を終えた後のフェーズ。残りの群があれば適用を続ける。"""
    remaining = [
        g for g in entry.get("apply_rounds") or []
        if g.get("status") == "pending"
    ]
    return "apply" if remaining else "propose"


def prepare_fix_phase(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """修正ラウンドへ入る前に、必要な記録を残す。

    - `fix_base_sha`: 修正の範囲の起点。無いと `merge-fix` が範囲を確定できず、
      `fix_rounds` が進まないまま修正と検証を往復し続ける
    - `fix_attempts`: 試行番号。`merge-fix` が「叩き直し」と「次のラウンド」を
      区別するのに使う
    """
    entry["fix_base_sha"] = git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
    entry["fix_attempts"] = entry.get("fix_attempts", 0) + 1
