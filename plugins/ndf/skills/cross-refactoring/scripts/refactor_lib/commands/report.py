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
import run_metrics
import statefile

from .. import info
from ..gitfacts import safe_int
from ..measure import summary_extra
from ..outbound import plan_reference
from ..paths import load_state
from ..proposals import duplicate_rate
from ..rounds import finish_outer_rounds, STRUCTURE, TEST, entry_kind, item_kind, item_label, rounds_of_kind
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
    path, state = load_state(args.id)
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
    if len(rounds_of_kind(rounds, STRUCTURE)) >= state["max_outer_rounds"]:
        finish_outer_rounds(path, state, "max_outer_rounds")
        sys.exit(1)
    if last.get("adopted") == 0:
        finish_outer_rounds(path, state, "no_more_proposals")
        sys.exit(1)
    previous = rounds_of_kind(rounds[:-1], STRUCTURE)
    if previous:
        # **同じ種類どうしで測る。** 鍵の形が種類で違うため、テスト整備ラウンドを
        # 相手にすると重なりが常に 0 になり、収束の判定が働かない。
        rate = duplicate_rate(
            [tuple(k) for k in last.get("proposal_keys") or []],
            [tuple(k) for k in previous[-1].get("proposal_keys") or []],
        )
        if rate >= DUPLICATE_RATE_THRESHOLD:
            info(f"提案の重複率が {rate:.0%} で、前ラウンドとほぼ同じです")
            finish_outer_rounds(path, state, "duplicate_proposals")
            sys.exit(1)


def _advance_test_rounds(
    path: pathlib.Path, state: dict[str, Any], last: dict[str, Any]
) -> None:
    """テスト整備ラウンドを続けるか、構造改善の提案ラウンドへ移るかを決める。

    収束の条件は**採用 0 件**（提案ラウンドと同じ形）。上限に達したときは採用が
    残っていても移る。**どちらで移ったかを記録する**（収束して終わったのか、
    歯止めで止まったのかを報告で読み分けるため）。
    """
    done = len(rounds_of_kind(state["rounds"], TEST))
    limit = safe_int(state.get("max_test_rounds"), DEFAULT_MAX_TEST_ROUNDS)
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



def cmd_status(args: argparse.Namespace) -> None:
    """現在の状態を人が読む形で出す。"""
    _, state = load_state(args.id)
    print(f"# cross-refactoring rf{state['id']}（{state['repo']} #{state['current_pr']}）")
    print(f"ホスト: {state['host']}（{state['host_detection']}）")
    # **母集合は 1 つである**（#727 の決定 5）。提案と適用は同じ参加者で回す。
    print(f"参加者（提案と適用）: {' / '.join(state['runtimes'])}")
    print(f"局面: {state['phase']} / 提案ラウンド {state['outer_round']} "
          f"/ {state['max_outer_rounds']}")
    print(f"終了理由: {state.get('final') or '（未終了）'}")
    print()
    print(_round_table(state))


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — ラウンド表・項目表・見送り項目・指標を出す。"""
    path, state = load_state(args.id)
    _print_header(state)
    print()
    print("## ラウンド")
    print()
    print(_round_table(state))
    print()
    print("## 改善項目")
    print()
    print(_item_table(state))
    print()
    _print_participants(state)
    # **取り消した項目の内訳は書かない**（#436 決定 6-b）。件数だけ述べ、内訳は
    # 改修計画へ譲る。同じ一覧を 2 か所に置くと、片方だけが古くなる。
    print()
    _print_deferred(state)
    if args.metrics:
        _print_metrics(state)
    # **最後の行に置く**（#662 の AC23）。作業ツリーを消した後に要約を探す手がかりになる。
    print()
    _print_run_metrics(path, state)


def _print_header(state: dict[str, Any]) -> None:
    """見出し行と実行メタ情報（対象範囲・終了理由・改修計画・着手前テスト等）を出す。"""
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- ホスト: {state['host']}（{state['host_detection']}）")
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(f"- 終了理由: {state.get('final') or '（未終了）'}")
    # **生の URL で書く**（#436 決定 6-b）。Markdown のリンクにすると、読み手の
    # 画面から URL を取り出せない。
    print(f"- 改修計画: {plan_reference(state)}")
    if state.get("test_rounds_final"):
        print(f"- テスト整備の終わり方: {state['test_rounds_final']}")
    baseline = state.get("baseline_test") or {}
    print(f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}"
          f"（{baseline.get('status')}）")
    gate = state.get("final_gate") or {}
    if gate:
        print(f"- 最終ゲート: {gate.get('mode') or '—'}"
              f"（{gate.get('status') or '未実行'}"
              f" / 修正 {gate.get('fix_rounds', 0)} 回）")


def _print_participants(state: dict[str, Any]) -> None:
    """「参加した者」の節を出す（#727 の F6）。

    途中から誰を外したか・誰が確認を通らなかったかを、完了報告だけで読めるように
    する。参加者の記録を持たない状態ファイル（この変更の前に始めた実行）では
    「記録なし」と出す。
    """
    print("## 参加した者")
    print()
    p = state.get("participants")
    if not p:
        print("- 使える者: 記録なし")
        print()
        return

    def _names(values: Any) -> str:
        return " / ".join(values) if values else "なし"

    unavailable = p.get("unavailable") or {}
    if unavailable:
        failed = " / ".join(f"{n}（{d}）" for n, d in unavailable.items())
    elif p.get("probe_skipped"):
        failed = "確認を飛ばした（NDF_SKIP_AUTH_CHECK）"
    else:
        failed = "なし"
    print(f"- 母集合: {_names(p.get('pool'))}")
    print(f"- 使える者: {_names(p.get('available'))}")
    print(f"- --exclude で外した者: {_names(p.get('excluded'))}")
    print(f"- --exclude で指定したが既定の母集合に無かった者: {_names(p.get('ignored_exclude'))}")
    print(f"- --include で足した者: {_names(p.get('included'))}")
    print(f"- 確認を通らなかった者: {failed}")
    changes = state.get("resume_changes") or []
    if not changes:
        print("- 再開で変えた値: なし")
    else:
        print("- 再開で変えた値:")
        for c in changes:
            print(f"  - {c.get('at')} {c.get('field')}: "
                  f"{_change_value(c.get('from'))} → {_change_value(c.get('to'))}")
    print()


def _change_value(value: Any) -> str:
    """再開で変えた値の 1 つを 1 行へ収める。参加者の記録は使える者だけを出す。"""
    if isinstance(value, dict) and "available" in value:
        return " / ".join(value.get("available") or []) or "なし"
    return str(value)


def _print_deferred(state: dict[str, Any]) -> None:
    """見送り節（件数と改修計画への参照）を出す。"""
    print("## 見送った提案")
    print()
    print(f"- 件数: {len(state['deferred_items'])} 件")
    print(f"- 内訳: 改修計画にある — {plan_reference(state)}")


def _print_metrics(state: dict[str, Any]) -> None:
    """指標節を出す（`args.metrics` が真のときだけ呼ぶ）。"""
    print()
    print("# 指標")
    print()
    print(metrics_lib.format_report(metrics_lib.aggregate(state)))


def _print_run_metrics(path: pathlib.Path, state: dict[str, Any]) -> None:
    """run_metrics の要約 1 行を出す。"""
    print(run_metrics.report_line(path, state, "cross-refactoring", summary_extra))


def _round_table(state: dict[str, Any]) -> str:
    """ラウンド表。**レビュー担当の列を持たない**（#727 の決定 6）。

    レビュー工程は #436 で消えた。古い状態ファイルがレビュー担当を持っていても出さない。
    """
    lines = [
        "| R | 種類 | 実装担当 | モデル | 採用 | 適用 | 見送り | 修正 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for entry in state["rounds"]:
        lines.append(
            f"| {entry['round']} | "
            f"{'テスト整備' if entry_kind(entry) == TEST else '構造改善'} | "
            f"{entry.get('impl', '—')} | "
            f"{models_lib.label((entry.get('impl_model') or {}).get('requested'))} | "
            f"{entry.get('adopted', 0)} | {len(entry.get('apply', {}).get('applied', []))} | "
            f"{len(entry.get('apply', {}).get('failed', []))} | {entry.get('fix_rounds', 0)} |"
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
