"""提案ラウンドの収束判定と、状態の出力。

`advance` / `status` / `report` を持つ。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

import metrics as metrics_lib
import models as models_lib
import statefile

from .. import info
from ..gitfacts import _safe_int
from ..paths import _load
from ..proposals import duplicate_rate
from ..rounds import STRUCTURE, TEST, entry_kind, item_kind, item_label
from ..vocabulary import DEFAULT_MAX_TEST_ROUNDS, DUPLICATE_RATE_THRESHOLD


def cmd_advance(args: argparse.Namespace) -> None:
    """ラウンドの繰り返しを続けるか判定する。

    終了コード: 0 = 続ける / 1 = 終了。

    **テスト整備ラウンドから提案ラウンドへの切り替えもここで決める。** 判定を
    1 か所へ置き、ラウンドを開く側は宣言に従うだけにする。**テスト整備の側で
    終了はしない**（構造改善の提案ラウンドがこの後に続く）。

    提案ラウンドの終了条件は 3 つ。採用 0 件 / 上限到達 / 前ラウンドとの提案
    重複率がしきい値以上。**同じ提案が毎ラウンド出続けて終わらない**ことを防ぐ。
    """
    path, state = _load(args.id)
    rounds = state["rounds"]
    if state.get("final"):
        info(f"終了済みです（{state['final']}）")
        sys.exit(1)
    if not rounds:
        return
    last = rounds[-1]
    if entry_kind(last) == TEST:
        _advance_test_rounds(path, state, last)
        return
    if len(_of_kind(rounds, STRUCTURE)) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)
    if last.get("adopted") == 0:
        _finish(path, state, "no_more_proposals")
        sys.exit(1)
    previous = _of_kind(rounds[:-1], STRUCTURE)
    if previous:
        # **同じ種類どうしで測る。** 鍵の形が種類で違うため、テスト整備ラウンドを
        # 相手にすると重なりが常に 0 になり、収束の判定が働かない。
        rate = duplicate_rate(
            [tuple(k) for k in last.get("proposal_keys") or []],
            [tuple(k) for k in previous[-1].get("proposal_keys") or []],
        )
        if rate >= DUPLICATE_RATE_THRESHOLD:
            info(f"提案の重複率が {rate:.0%} で、前ラウンドとほぼ同じです")
            _finish(path, state, "duplicate_proposals")
            sys.exit(1)


def _of_kind(rounds: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """その種類のラウンドだけを取り出す。上限はそれぞれ別に数える。"""
    return [r for r in rounds if entry_kind(r) == kind]


def _advance_test_rounds(
    path: pathlib.Path, state: dict[str, Any], last: dict[str, Any]
) -> None:
    """テスト整備ラウンドを続けるか、構造改善の提案ラウンドへ移るかを決める。

    収束の条件は**採用 0 件**（提案ラウンドと同じ形）。上限に達したときは採用が
    残っていても移る。**どちらで移ったかを記録する**（収束して終わったのか、
    歯止めで止まったのかを報告で読み分けるため）。
    """
    done = len(_of_kind(state["rounds"], TEST))
    limit = _safe_int(state.get("max_test_rounds"), DEFAULT_MAX_TEST_ROUNDS)
    if last.get("adopted") == 0:
        reason = "no_more_test_proposals"
        note = "足すべきテストの提案が出なくなりました"
    elif done >= limit:
        reason = "max_test_rounds"
        note = f"テスト整備ラウンドが上限 {limit} に達しました"
    else:
        info(f"テスト整備ラウンド {done} / {limit} — 続けます")
        return
    state["round_kind"] = STRUCTURE
    state["test_rounds_final"] = reason
    statefile.save(path, state)
    info(f"{note}。構造改善の提案ラウンドへ進みます")


def _finish(path: pathlib.Path, state: dict[str, Any], reason: str) -> None:
    state["final"] = reason
    state["ended_at"] = statefile.now()
    state["phase"] = "final"
    statefile.save(path, state)
    info(f"提案ラウンドの繰り返しを終了します（理由: {reason}）")


def cmd_status(args: argparse.Namespace) -> None:
    """現在の状態を人が読む形で出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring rf{state['id']}（{state['repo']} #{state['current_pr']}）")
    print(f"ホスト: {state['host']}（{state['host_detection']}）")
    print(f"提案・レビュー: {' / '.join(state['runtimes'])}")
    print(f"適用の母集合: {' / '.join(state['impl_capable'])}")
    print(f"局面: {state['phase']} / 提案ラウンド {state['outer_round']} "
          f"/ {state['max_outer_rounds']}")
    print(f"終了理由: {state.get('final') or '（未終了）'}")
    print()
    print(_round_table(state))


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — ラウンド表・項目表・見送り項目・指標を出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- ホスト: {state['host']}（{state['host_detection']}）")
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(f"- 終了理由: {state.get('final') or '（未終了）'}")
    if state.get("test_rounds_final"):
        print(f"- テスト整備の終わり方: {state['test_rounds_final']}")
    baseline = state.get("baseline_test") or {}
    print(f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}"
          f"（{baseline.get('status')}）")
    print()
    print("## ラウンド")
    print()
    print(_round_table(state))
    print()
    print("## 改善項目")
    print()
    print(_item_table(state))
    if state["deferred_items"]:
        print()
        print("## 見送った提案")
        print()
        print("| ラウンド | 対象 | 兆候・経路 | 理由 |")
        print("| --- | --- | --- | --- |")
        for d in state["deferred_items"]:
            kind_label = d.get("case") if item_kind(d) == TEST else d.get("smell")
            print(f"| {d.get('round', '—')} | {item_label(d)} | "
                  f"{kind_label or '—'} | {d.get('defer_reason', '—')} |")
    if args.metrics:
        print()
        print("# 指標")
        print()
        print(metrics_lib.format_report(metrics_lib.aggregate(state)))


def _round_table(state: dict[str, Any]) -> str:
    lines = [
        "| R | 種類 | 実装担当 | モデル | レビュー担当 | モデル | 採用 | 適用 | 見送り | 修正 | 初回承認 |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for entry in state["rounds"]:
        reviewers = entry.get("reviewers", [])
        reviewer_models = entry.get("reviewer_models") or {}
        reviews = entry.get("reviews") or []
        first_approved = "—"
        if reviews:
            first_approved = (
                "はい" if all(reviews[0].get(r) == "APPROVE" for r in reviewers) else "いいえ"
            )
        lines.append(
            f"| {entry['round']} | "
            f"{'テスト整備' if entry_kind(entry) == TEST else '構造改善'} | "
            f"{entry.get('impl', '—')} | "
            f"{models_lib.label((entry.get('impl_model') or {}).get('requested'))} | "
            f"{' / '.join(reviewers) or '—'} | "
            f"{' / '.join(models_lib.label((reviewer_models.get(r) or {}).get('requested')) for r in reviewers) or '—'} | "
            f"{entry.get('adopted', 0)} | {len(entry.get('apply', {}).get('applied', []))} | "
            f"{len(entry.get('apply', {}).get('failed', []))} | {entry.get('fix_rounds', 0)} | "
            f"{first_approved} |"
        )
    return "\n".join(lines) if state["rounds"] else "（ラウンドなし）"


def _item_table(state: dict[str, Any]) -> str:
    if not state["items"]:
        return "（改善項目なし）"
    lines = [
        "| ID | 対象 | 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for item in state["items"]:
        if item_kind(item) == TEST:
            first, second, severity = item.get("case"), item.get("level"), "—"
        else:
            first, second = item.get("smell"), item.get("technique")
            severity = item.get("severity") or "—"
        lines.append(
            f"| {item['item_id']} | {item_label(item)} | "
            f"{first or '—'} | {second or '—'} | {severity} | "
            f"{'/'.join(item.get('proposed_by', []))} | {item['status']} | "
            f"{len(item.get('commits') or [])} |"
        )
    return "\n".join(lines)
