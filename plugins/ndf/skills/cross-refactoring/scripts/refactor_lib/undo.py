"""取り消しの実行（#933 の AC10 AC15、#1482）。どのコミットを消し・積み直し・戻すかは `ledger` が決める。

**未公開のコミットは積み直しの起点へ `reset --hard` して、残すコミットだけを古い順に積み直す。**
公開済みのコミットは書き換えず、取り消す改善項目のコミットだけを新しい順に `git revert` する
（共通原則 C4）。前の取り消しの積み直しは「残すコミット」として置き換わるだけなので、取り消しを
何度くり返してもコミット数は伸びない（#1399）。

積み直しが衝突したら、消す・戻すコミットが触ったファイルを触った改善項目まで 1 段だけ広げる
（`widened`。#1237）。広げても衝突したら、HEAD を取り消しの前へ戻して終了コード 4 で止まる。
全件の取り消しへは進まない。

| `mode` | 何を取り消したか |
| --- | --- |
| `item` | 指定した項目と、どの項目にも属さないコミット |
| `widened` | 上に加えて、同じファイルを触った項目 |
| `skip` | 取り消すコミットが無かった（git に触れない） |

**中断しても再開できる形で記録する。** 着手の前に取り消しの前の HEAD（`pending_drop.before`）を保存し、
記録を終えたら消す。残ったまま再開したら、その HEAD へ戻してから計画を作り直す。

入口は `drop`（改善項目の単位）と `discard`（結果を残さなかった起動・手順を外れた修正の範囲）の 2 つ。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

import statefile

from . import die, info, ledger
from .items import find_item, item_shas
from .paths import git_out, work_dir
from .worktree import replay_commits, reset_hard, revert_range


@dataclass
class _Outcome:
    mapping: dict[str, str] = field(default_factory=dict)  # 積み直した {元の SHA: 新しい SHA}
    reverts: list[str] = field(default_factory=list)  # 作った revert のコミット
    conflict: Optional[str] = None  # 積み直せなかった・戻せなかったコミット


def _execute(work: str, plan: ledger.RebuildPlan) -> _Outcome:
    """計画を git で打つ。衝突したら HEAD を取り消しの前へ戻して返す。"""
    outcome = _Outcome()
    if plan.remove:
        reset_hard(work, plan.origin)
        outcome.mapping, outcome.conflict = replay_commits(work, plan.replay)
    if outcome.conflict is None:
        for sha in plan.revert:
            outcome.conflict = revert_range(work, [sha])
            if outcome.conflict:
                break
            outcome.reverts.append(git_out(work, ["rev-parse", "HEAD"]) or "")
    if outcome.conflict:
        reset_hard(work, plan.before)
    return outcome


def _point_map(plan: ledger.RebuildPlan, mapping: dict[str, str], head: str) -> dict[str, str]:
    """起点より後を指す地点の新しい値。元の履歴でその地点以前にある最後の残すコミットの新しい SHA、
    無ければ積み直しの起点（設計の決定 10）。取り消しの前の HEAD は取り消しの後の HEAD へ移す。"""
    points: dict[str, str] = {}
    last = plan.origin
    for sha in plan.span:
        last = mapping.get(sha, last)
        points[sha] = last
    points[plan.before] = head
    return points


def _remap(state: dict[str, Any], work: str, mapping: dict[str, str], points: dict[str, str]) -> None:
    """積み直しで変わった SHA を、項目のコミットと保存済みの地点へ書き戻す。

    **書き戻さないと、次の取り消しとマージ処理が履歴に無い SHA を指す。**
    """

    def full(sha: Any) -> Any:
        return (git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"]) or sha) if isinstance(sha, str) and sha else sha

    for item in state.get("items") or []:
        commits = item.get("commits") or {}
        for key in ("test", "implement"):
            if commits.get(key):
                commits[key] = mapping.get(full(commits[key]), commits[key])
        commits["fix"] = [mapping.get(full(s), s) for s in commits.get("fix") or []]
    for record in (state.get("phases") or {}).values():
        if isinstance(record, dict) and record.get("base_sha"):
            record["base_sha"] = points.get(full(record["base_sha"]), record["base_sha"])
    fix = state.get("fix")
    if isinstance(fix, dict) and fix.get("base_sha"):
        fix["base_sha"] = points.get(full(fix["base_sha"]), fix["base_sha"])
    gate = state.get("final_gate")
    if isinstance(gate, dict):
        if gate.get("fix_base_sha"):
            gate["fix_base_sha"] = points.get(full(gate["fix_base_sha"]), gate["fix_base_sha"])
        if gate.get("fix_commits"):
            gate["fix_commits"] = [mapping.get(full(s), s) for s in gate["fix_commits"]]
    ledger.remap_orchestrator_commits(state, mapping)


def _close_commitless(state: dict[str, Any], targets: list[str], reason: str) -> list[str]:
    """コミットを持たない対象を git に触れず閉じ、残る対象を返す。"""
    remaining = list(targets)
    for item_id in [i for i in remaining if not item_shas(find_item(state, i))]:
        ledger.mark_dropped(find_item(state, item_id), reason)
        remaining.remove(item_id)
    return remaining


def _record(
    path: pathlib.Path,
    state: dict[str, Any],
    plan: ledger.RebuildPlan,
    outcome: _Outcome,
    targets: list[str],
    reason: str,
) -> dict[str, Any]:
    work = work_dir(state)
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    # 台帳の SHA を書き直してから revert を足す（同じ取り消しで作った revert は書き直さない）
    _remap(state, work, outcome.mapping, _point_map(plan, outcome.mapping, head))
    for sha in outcome.reverts:
        ledger.note_orchestrator_commit(state, sha)
    mode = "widened" if plan.widened else "item"
    for item_id in plan.dropped:
        item = find_item(state, item_id, required=False)
        if item is not None and ledger.is_live(item):
            ledger.mark_dropped(item, reason if item_id in targets else f"{reason}（{mode} の取り消しに巻き込まれた）")
    record = {
        "at": statefile.now(),
        "mode": mode,
        "reason": reason,
        "dropped": plan.dropped,
        "extra": plan.stray,
        "origin": plan.origin,
        "removed": len(plan.remove),
        "replayed": len(outcome.mapping),
        "reverted": len(outcome.reverts),
    }
    state.setdefault("drops", []).append(record)
    state["pending_drop"] = None
    statefile.save(path, state)
    info(f"↩ 取り消し: 消した {record['removed']} / 積み直した {record['replayed']} / 戻した {record['reverted']} コミット（{mode}）")
    return record


def _give_up(path: pathlib.Path, state: dict[str, Any], plan: ledger.RebuildPlan, conflict: str) -> None:
    """広げても積み直せないとき。HEAD は `_execute` が取り消しの前へ戻してある。"""
    state["pending_drop"] = None
    statefile.save(path, state)
    widened = ", ".join(plan.widened) or "なし"
    die(
        f"同じファイルを触った項目まで広げても積み直せません: {conflict[:12]}（広げた項目: {widened}）。HEAD を {plan.before[:12]} へ戻しました"
    )


def _rebuild(path: pathlib.Path, state: dict[str, Any], targets: list[str], reason: str) -> dict[str, Any]:
    work = work_dir(state)
    plan = ledger.plan_rebuild(state, work, targets)
    if plan.error:
        die(plan.error)
    if plan.empty():
        for item_id in targets:
            ledger.mark_dropped(find_item(state, item_id), reason)
        statefile.save(path, state)
        return {"mode": "skip", "dropped": sorted(targets), "removed": 0, "replayed": 0, "reverted": 0}

    state["pending_drop"] = {"items": targets, "reason": reason, "before": plan.before}
    statefile.save(path, state)
    outcome = _execute(work, plan)
    if outcome.conflict:
        wide = ledger.plan_rebuild(state, work, targets, widen=True)
        if not wide.widened:
            _give_up(path, state, wide, outcome.conflict)
        info(f"⚠ 積み直しが衝突したため、同じファイルを触った {len(wide.widened)} 件（{', '.join(wide.widened)}）も取り消します")
        plan, outcome = wide, _execute(work, wide)
        if outcome.conflict:
            _give_up(path, state, plan, outcome.conflict)
    return _record(path, state, plan, outcome, targets, reason)


def drop(path: pathlib.Path, state: dict[str, Any], item_ids: list[str], reason: str) -> dict[str, Any]:
    """改善項目（と、どの項目にも属さないコミット）を取り消す。

    戻り値は `{"mode", "dropped", "removed", "replayed", "reverted", ...}`。取り消した項目は
    `status: reverted`、`failure_reason` に理由を持つ。**見送り（`deferred_items`）へ入れるかは
    呼び出し側が決める**（`test_failed` / `not_done` は見送り、検証の失敗は項目の状態だけ）。
    """
    targets = [i for i in item_ids if ledger.is_live(find_item(state, i, required=False))]
    targets = _close_commitless(state, targets, reason)
    return _rebuild(path, state, targets, reason)


def discard(path: pathlib.Path, state: dict[str, Any], reason: str) -> dict[str, Any]:
    """どの改善項目にも記録されていないコミットを消す（結果を残さなかった起動・手順を外れた修正の範囲）。

    範囲の起点を受け取らない。記録されていないコミットは判定が `stray` とし、積み直しで消える。
    """
    return _rebuild(path, state, [], reason)


def resume_pending_drop(path: pathlib.Path, state: dict[str, Any]) -> None:
    """前回終わらなかった取り消しを、取り消しの前の HEAD へ戻してからやり直す（I7）。

    `before` を持たない旧い記録は今の HEAD から判定し直す。旧い方式が積んだ revert は記録に無いため
    `stray` として消える。
    """
    pending = state.get("pending_drop")
    if not pending:
        return
    info("↻ 前回終わらなかった取り消しをやり直します")
    before = pending.get("before")
    if before:
        reset_hard(work_dir(state), before)
    drop(path, state, list(pending.get("items") or []), str(pending.get("reason") or ""))
