"""採り直し（`readopt`、#1743 の F7・決定 10）。

検証（直しの試行を含む）が終わった後、実装の予算の残り（実効の予算 − 採っていて取り消していない項目の見積りの和。
#2457 の I1・I3）に入る候補を、計画と同じ順位（`budget.rank_key`）と選び方（`budget.select`。入らない候補は飛ばして
次を見る）で採り直す。実装時間の実績は使わない。候補は `budget` で見送った候補と、コミットが無かった持ち越しの項目
だけである（I15）。1 度採り直した後もコミットが無く持ち越しに戻った項目は採らずに `not_done` で見送る（持ち越しは
見積りの消費に数えないため、採り直しを重ねても残りが減らず、前進しない実装の CLI を繰り返す）。見送りへ変える項目のテストのコミットのうち、差し戻しに `test_failed` か
`non_test_files` を持つものは打ち消す（落ちるテストや検証を受けていない本番の変更を最終ゲートと公開へ流さない）。見積り・範囲テスト・等級・`risk` は
`merge-plan` が計画の時点で書いた値を使い、**LLM を呼ばない**（I14）。D5（`public_io`）は実装担当の `risk` を使う。

| 終了コード | 意味 |
| --- | --- |
| 0 | 1 件以上を採り直した。`TESTS_NEEDED=0|1` を出し、テストの追加・実装・検証をもう 1 巡回す |
| 2 | 入る候補が無い（持ち越しの項目を `not_done` で見送った）か、計画が無い。最終ゲートへ |

`phase` を `final` にするのは実行全体でここだけである（最終ゲートの入口の `final-gate` を除く）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

import statefile

from .. import budget, clock, info, ledger, undo
from ..items import CARRIED, DEFERRED, PLANNED, TESTED, defer, new_items
from ..paths import full_commit, git_out, load_state, work_dir
from ..vocabulary import DEFER_BUDGET, DEFER_NOT_DONE

SELECTED = "selected"
NO_FIT = "no_fit"
# 採るときに前の巡の記録を `phases_history` へ移す手順（巡ごとに取り込みと所要を数える）
ROUND_PHASES = ("add-tests", "implement", "verify", "fix")
NOT_DONE_REASON = "コミットが無く、実装の予算の残りに入らない"
# 採り直した後もコミットが無く持ち越しに戻った項目は、もう採り直さない（前進しない実装の CLI を繰り返さない）
NO_PROGRESS_REASON = "採り直した後もコミットが無く、持ち越しに戻った（自動の採り直しを止めて人へ渡す）"


def _budget_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    """`budget` で見送った候補（計画の時点の見積りを持つもの）。"""
    ids = {d.get("item_id") for d in state.get("deferred_items") or [] if d.get("defer_reason") == DEFER_BUDGET}
    return [c for c in state.get("candidates") or [] if c.get("id") in ids and c.get("estimate")]


def _archive_round(state: dict[str, Any], round_no: int) -> None:
    """前の巡の手順の記録と、最終ゲートへ寄せた危険フラグの記録を履歴へ移す。"""
    phases = state.get("phases") or {}
    history = state.setdefault("phases_history", [])
    for name in ROUND_PHASES:
        if name in phases:
            history.append({"round": round_no, "name": name, "record": phases.pop(name)})
    whole = state.get("whole_test")
    if whole:
        state.setdefault("whole_test_history", []).append({"round": round_no, "record": state.pop("whole_test")})


def _reopen(item: dict[str, Any], round_no: int) -> None:
    """持ち越しの項目を、新しい巡の未着手の項目へ戻す。

    持ち越しは項目のコミットを取り消さない（#2457）。テストの追加のコミットは HEAD に残るため、項目の記録から外すと
    どの項目にも属さないコミットになり、公開の直前の台帳の照合（`ledger.unpublishable`）で止まる。テストのコミットを
    持つ項目はそれを持ったまま `tested` へ戻し、テストを足し直させない。"""
    old = item.get("commits") or {}
    test, test_extra = old.get("test"), list(old.get("test_extra") or [])
    commits: dict[str, Any] = {"test": test, "implement": [], "fix": []}
    if test_extra:
        commits["test_extra"] = test_extra
    seconds = {"test": item["seconds"]["test"]} if test and "test" in (item.get("seconds") or {}) else {}
    item.update(
        {
            "status": TESTED if test else PLANNED,
            "round": round_no,
            "commits": commits,
            "seconds": seconds,
            "fix_count": 0,
            "danger": [],
            "readopted": int(item.get("readopted") or 0) + 1,
        }
    )
    dropped = ["failure_reason", "danger_checked", "danger_hits", "diff_lines", "review_scope_judgements"]
    if not test:
        dropped.append("review_test_judgements")
    for key in dropped:
        item.pop(key, None)


def _adopt(state: dict[str, Any], selected: list[dict[str, Any]], carried: list[dict[str, Any]], round_no: int) -> list[str]:
    """採った候補を項目にし、採った項目の ID を返す。`budget` の見送りの記録は外す。"""
    ids: list[str] = []
    items = state.setdefault("items", [])
    carried_ids = {id(i) for i in carried}
    for candidate in selected:
        if id(candidate) in carried_ids:
            _reopen(candidate, round_no)
            ids.append(candidate["id"])
            continue
        item = new_items([candidate], first_rank=len(items) + 1, round_no=round_no)[0]
        item["public_io"], item["public_io_source"] = bool(item.get("risk")), "runtime"
        items.append(item)
        ids.append(item["id"])
        state["deferred_items"] = [
            d
            for d in state.get("deferred_items") or []
            if not (d.get("item_id") == candidate["id"] and d.get("defer_reason") == DEFER_BUDGET)
        ]
    return ids


def _revert_unverified_tests(path: Any, state: dict[str, Any], deferred: list[dict[str, Any]]) -> None:
    """見送りへ変えた項目のうち、差し戻しに `test_failed` か `non_test_files` を持つテストのコミットを打ち消す。

    実装が採られないまま見送ると、今のコードで落ちるテストが HEAD に残って最終ゲートの全体テストを落とすか、
    検証を受けていない本番のファイルの変更が公開へ流れる。打ち消した元のコミットと revert は
    `undo.revert_unattributed` が残すコミットとして台帳へ記録し、項目のテストのコミットからは外す
    （`unverified_reverted` に残す）。打ち消しが衝突したら（後の項目が同じファイルを書き換えた）実行全体を止めず、
    HEAD を戻したうえでコミットを残すコミットとして台帳へ記録し、項目に `unverified_kept` を残して最終ゲートの
    全体テストに判定を任せる（readopt は最終ゲートの直前で必ず通るため、止めると再開しても同じ衝突で止まり続ける）。"""
    targets = {item["id"]: ledger.unverified_tests(item) for item in deferred}
    targets = {k: v for k, v in targets.items() if v}
    if not targets:
        return
    work = work_dir(state)
    wanted = {full_commit(work, sha): sha for shas in targets.values() for sha in shas}
    order = (git_out(work, ["rev-list", "--reverse", "HEAD"]) or "").split()
    shas = [wanted[sha] for sha in order if sha in wanted]
    done = undo.revert_unattributed(path, state, shas, "見送りの項目の検証を受けていないテスト", stop_on_conflict=False)
    for item in deferred:
        if item["id"] not in targets:
            continue
        if not done:
            for sha in targets[item["id"]]:
                ledger.note_orchestrator_commit(state, full_commit(work, sha))
            item["unverified_kept"] = targets[item["id"]]
            item["failure_reason"] = (
                f"{item.get('failure_reason') or ''}（未検証のテストが残っている。打ち消しが衝突したため最終ゲートの全体テストで判定する）"
            )
            continue
        commits = item.setdefault("commits", {})
        item["unverified_reverted"] = targets[item["id"]]
        commits["test"] = None
        commits.pop("test_extra", None)
    if not done:
        info(
            f"⚠ 見送りへ変えた {', '.join(targets)} の検証を受けていないテストのコミット {len(shas)} 件は打ち消せず（衝突）、残して最終ゲートで判定します"
        )
        return
    info(
        f"↩ 見送りへ変えた {', '.join(targets)} のテストのコミット {len(shas)} 件を打ち消しました（今のコードで落ちる・テスト以外を変える）"
    )


def _give_up_repeats(path: Any, state: dict[str, Any], carried: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """採り直した後も持ち越しに戻った項目を `not_done` で見送り、まだ採り直していない持ち越しの項目を返す。

    持ち越しは見積りの消費に数えないため、同じ項目を採り直し続けても予算の残りが減らず、実装できない項目が毎巡
    同じ条件で選ばれる。1 度採り直して前進しなかった項目は、報告の見送りに理由を残して人へ渡す。"""
    retry, given_up = [], []
    for item in carried:
        if not item.get("readopted"):
            retry.append(item)
            continue
        defer(state, item, DEFER_NOT_DONE, NO_PROGRESS_REASON)
        item["status"] = DEFERRED
        item["failure_reason"] = NO_PROGRESS_REASON
        given_up.append(item)
        info(f"⚠ {item['id']} は採り直した後もコミットが無く持ち越しに戻りました。もう採り直さず、見送りとして人へ渡します")
    _revert_unverified_tests(path, state, given_up)
    return retry


def _close(path: Any, state: dict[str, Any], record: dict[str, Any], available: float, shortest: Optional[float]) -> None:
    """入る候補が無い。持ち越しの項目を `not_done` で見送り、最終ゲートへ移す。"""
    closed = []
    for item in state.get("items") or []:
        if item.get("status") == CARRIED:
            # 状態を見送りにする前に見送りへ足す（#1658 の I7）
            defer(state, item, DEFER_NOT_DONE, NOT_DONE_REASON)
            item["status"] = DEFERRED
            item["failure_reason"] = NOT_DONE_REASON
            closed.append(item)
    _revert_unverified_tests(path, state, closed)
    record.update({"reason": NO_FIT, "selected": []})
    state["readopt"]["events"].append(record)
    state["phase"] = "final"
    statefile.save(path, state)
    smallest = f"最も短い候補の見積り {shortest:.1f} 分" if shortest is not None else "候補なし"
    info(f"採り直し: 実装の予算の残り {available:.1f} 分に入る候補がありません（{smallest}）。最終ゲートへ進みます")
    sys.exit(2)


def _replayed(state: dict[str, Any]) -> bool:
    """叩き直しなら最後の記録を返して真（採り直しを重ねない）。`no_fit` の記録なら終了コード 2 で抜ける。"""
    readopt = state.get("readopt") or {}
    events = readopt.get("events") or []
    last = events[-1] if events else None
    if state.get("phase") == "readopt" or last is None:
        return False
    if last.get("reason") == SELECTED and int(last.get("round") or 0) == int(readopt.get("round") or 1):
        info(f"↻ 採り直しは記録済みです（{len(last.get('selected') or [])} 件。巡 {last.get('round')}）")
        statefile.emit(TESTS_NEEDED=1 if any(i.get("tests") and i.get("status") == PLANNED for i in state.get("items") or []) else 0)
        return True
    if last.get("reason") == NO_FIT:
        info("↻ 採り直しの判定は記録済みです（入る候補が無い）。最終ゲートへ進みます")
        sys.exit(2)
    return False


def cmd_readopt(args: argparse.Namespace) -> None:
    """検証の後、実装の予算の残りに入る見送りの候補を採り直す。"""
    path, state = load_state(args.id)
    if not state.get("plan"):
        info("採り直しの対象がありません（リファクタリング計画の前）。最終ゲートへ進みます")
        sys.exit(2)
    readopt = state.setdefault("readopt", {"round": 1, "events": []})
    readopt.setdefault("events", [])
    if _replayed(state):
        return
    round_no = int(readopt.get("round") or 1)
    now = clock.now()
    carried = _give_up_repeats(path, state, [i for i in state.get("items") or [] if i.get("status") == CARRIED])
    plan_budget = state.get("budget") or {"minutes": int(state.get("budget_minutes") or 0)}
    # 持ち越しの項目は採り直す候補なので、採っている見積りの和に数えない
    available = budget.readopt_left(plan_budget, [i for i in state.get("items") or [] if i.get("status") != CARRIED])
    ranked = sorted([*carried, *_budget_candidates(state)], key=budget.rank_key)
    record = {"at": clock.iso(now), "round": round_no, "available_minutes": round(available, 2), "carried": [i["id"] for i in carried]}
    selected = budget.select(ranked, available)[0] if available > 0 and state.get("phase") == "readopt" else []
    if not selected:
        shortest = min((budget.estimate_total(c.get("estimate") or {}) for c in ranked), default=None)
        _close(path, state, record, available, shortest)
        return
    _archive_round(state, round_no)
    ids = _adopt(state, selected, carried, round_no + 1)
    readopt["round"] = round_no + 1
    record.update({"round": round_no + 1, "reason": SELECTED, "selected": ids})
    readopt["events"].append(record)
    tests_needed = any(i.get("tests") and i.get("status") == PLANNED for i in state["items"] if i["id"] in ids)
    state["phase"] = "add-tests" if tests_needed else "implement"
    statefile.save(path, state)
    left = len(_budget_candidates(state))
    info(
        f"採り直し: 実装の予算の残り {available:.1f} 分に {len(ids)} 件（{', '.join(ids)}）を採りました（巡 {round_no + 1}。採らずに残った budget の候補 {left} 件）"
    )
    statefile.emit(TESTS_NEEDED=1 if tests_needed else 0)
