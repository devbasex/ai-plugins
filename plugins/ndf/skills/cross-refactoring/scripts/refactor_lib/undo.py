"""取り消しの実行（#933 の AC10 AC15、#1482、#2457 決定 6）。

**改善項目の打ち消しは、その項目のコミットだけを新しい順に `git revert` する**（`revert_items`・`drop`）。公開の
前でも後でも同じ手順で、取り込んだベースブランチの変更には触れない（#2323）。衝突したら打ち消しの前の HEAD へ
戻して止める（`UndoConflict`）。衝突した項目を外して続けない。

以下は、どの項目にも記録されていないコミットを消す `discard_range`（結果を残さなかった起動の範囲）だけが使う
積み直しの説明である。

**未公開のコミットは積み直しの起点へ `reset --hard` して、残すコミットだけを古い順に積み直す。**

**積み直しで項目に属するコミット（テスト・実装・修正）が衝突したら、そのコミットを持つ項目だけを外し、
計画を作り直して起点から積み直す**（外した項目。#1793 の R2）。外した項目に依存して後で衝突した項目も同じく
外れる。ファイルが同じというだけでは外さない。公開済みのコミットの revert と、どの項目にも属さないコミット
（オーケストレーター・最終ゲート修正）の積み直しが衝突したときだけ、HEAD を取り消しの前へ戻して終了コード 4 で止まる。

**最終ゲートより前の取り消しで HEAD が変わったら、残った `verified` の項目を `implemented` へ戻す**（確かめ直し。
`recheck`）。検証が新しい HEAD の範囲テストで判定し直す。最終ゲートの中では最終ゲートが HEAD を確かめ直すため戻さない。

| `mode` | 何を取り消したか |
| --- | --- |
| `item` | 指定した項目と、どの項目にも属さないコミット |
| `ejected` | 上に加えて、積み直しで自分のコミットが衝突した項目（`ejected[]` に `item`・`commit`・`by`） |
| `skip` | 取り消すコミットが無かった（git に触れない） |

旧い状態ファイルの `widened`（同じファイルを触った項目まで広げた記録）は読めるが、新しくは書かない。

**中断しても再開できる形で記録する。** 着手の前に取り消しの前の HEAD（`pending_drop.before`）を保存し、
記録を終えたら消す。残ったまま再開したら、その HEAD へ戻してから計画を作り直す。

入口は `drop`（改善項目の単位）と `discard_range`（結果を残さなかった起動・手順を外れた修正の範囲）の 2 つ。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

import statefile

from . import die, info, ledger, worktree
from .items import find_item, implement_shas, item_shas
from .paths import full_commit, git_out, work_dir
from .worktree import replay_commits, reset_hard, revert_range

ON_CONFLICT_STOP = "stop"  # 積み直しの衝突で取り消しを止め、作業を打ち切る
ON_CONFLICT_RAISE = "raise"  # 積み直しの衝突を DropConflict で呼ぶ側へ返す


class DropConflict(Exception):
    """項目に属さないコミットの積み直しか公開済みの revert が衝突した（`on_conflict="raise"` のとき）。
    HEAD は取り消しの前へ戻してある（#1669 決定 11）。"""


@dataclass
class _Outcome:
    mapping: dict[str, str] = field(default_factory=dict)  # 積み直した {元の SHA: 新しい SHA}
    reverts: list[str] = field(default_factory=list)  # 作った revert のコミット
    conflict: Optional[str] = None  # 積み直せなかった・戻せなかったコミット
    in_replay: bool = False  # 衝突が積み直しで起きたか（偽なら公開済みの revert）


def _execute(work: str, plan: ledger.RebuildPlan) -> _Outcome:
    """計画を git で打つ。衝突したら HEAD を取り消しの前へ戻して返す。"""
    outcome = _Outcome()
    if plan.remove:
        reset_hard(work, plan.origin)
        outcome.mapping, outcome.conflict = replay_commits(work, plan.replay)
        outcome.in_replay = outcome.conflict is not None
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
    for item in state.get("items") or []:
        _remap_commits(work, item, mapping)
    for record in (state.get("phases") or {}).values():
        if isinstance(record, dict) and record.get("base_sha"):
            record["base_sha"] = _remap_one(work, points, record["base_sha"])
    fix = state.get("fix")
    if isinstance(fix, dict) and fix.get("base_sha"):
        fix["base_sha"] = _remap_one(work, points, fix["base_sha"])
    gate = state.get("final_gate")
    if isinstance(gate, dict):
        if gate.get("fix_base_sha"):
            gate["fix_base_sha"] = _remap_one(work, points, gate["fix_base_sha"])
        if gate.get("fix_commits"):
            gate["fix_commits"] = _remap_many(work, mapping, gate["fix_commits"])
    ledger.remap_orchestrator_commits(state, mapping)


def _remap_one(work: str, table: dict[str, str], sha: Any) -> Any:
    """SHA を完全な形へ解決して対応表（1 対 1 の SHA か地点）を引く。無ければ元の値。"""
    return table.get(full_commit(work, sha), sha)


def _remap_many(work: str, table: dict[str, str], shas: list[Any]) -> list[Any]:
    return [_remap_one(work, table, s) for s in shas]


def _remap_commits(work: str, item: dict[str, Any], mapping: dict[str, str]) -> None:
    """項目のコミット（test / implement / fix）を書き戻す。"""
    commits = item.get("commits") or {}
    if commits.get("test"):
        commits["test"] = _remap_one(work, mapping, commits["test"])
    if "implement" in commits:
        commits["implement"] = _remap_many(work, mapping, implement_shas(item))
    commits["fix"] = _remap_many(work, mapping, commits.get("fix") or [])


def _close_commitless(state: dict[str, Any], targets: list[str], reason: str) -> list[str]:
    """コミットを持たない対象を git に触れず閉じ、残る対象を返す。"""
    remaining = list(targets)
    for item_id in [i for i in remaining if not item_shas(find_item(state, i))]:
        ledger.mark_dropped(find_item(state, item_id), reason)
        remaining.remove(item_id)
    return remaining


def _ejected_reason(entry: dict[str, Any], reason: str) -> str:
    by = ", ".join(entry.get("by") or []) or reason
    return f"{by} の取り消しで {str(entry['commit'])[:12]} の積み直しが衝突したため外した"


def _recheck(state: dict[str, Any], plan: ledger.RebuildPlan) -> list[str]:
    """最終ゲートより前の取り消しで HEAD が変わったら、残った検証済みの項目を確かめ直しへ戻す（I3）。"""
    if ledger.in_final_gate(state) or plan.empty():
        return []
    return ledger.mark_recheck(state)


def _record(
    path: pathlib.Path,
    state: dict[str, Any],
    plan: ledger.RebuildPlan,
    outcome: _Outcome,
    reason: str,
    ejected: list[dict[str, Any]],
) -> dict[str, Any]:
    work = work_dir(state)
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    # 台帳の SHA を書き直してから revert を足す（同じ取り消しで作った revert は書き直さない）
    _remap(state, work, outcome.mapping, _point_map(plan, outcome.mapping, head))
    for sha in outcome.reverts:
        ledger.note_orchestrator_commit(state, sha)
    mode = "ejected" if ejected else "item"
    for entry in ejected:
        item = find_item(state, entry["item"], required=False)
        if item is not None and ledger.is_live(item):
            # 前の理由（自分の範囲テストの失敗）を残すと、取り消しの理由（衝突）を読み違える（決定 7）
            item["failure_reason"] = _ejected_reason(entry, reason)
            ledger.mark_dropped(item, item["failure_reason"])
    for item_id in plan.dropped:
        item = find_item(state, item_id, required=False)
        if item is not None and ledger.is_live(item):
            ledger.mark_dropped(item, reason)
    record = {
        "at": statefile.now(),
        "mode": mode,
        "reason": reason,
        # 採り直しの巡（#1743）。取り込みの取り消し済みの判定は取り込み名と巡の組で見る
        "round": int((state.get("readopt") or {}).get("round") or 1),
        "dropped": plan.dropped,
        "ejected": ejected,
        "recheck": _recheck(state, plan),
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
    if record["recheck"]:
        info(f"↻ 残った {len(record['recheck'])} 件（{', '.join(record['recheck'])}）を新しい HEAD の範囲テストで確かめ直します")
    return record


def _restore_and_stop(
    path: pathlib.Path, state: dict[str, Any], plan: ledger.RebuildPlan, outcome: _Outcome, on_conflict: str = ON_CONFLICT_STOP
) -> None:
    """項目に属さないコミットの積み直しか公開済みの revert が衝突したとき。HEAD は `_execute` が取り消しの前へ
    戻してある。`on_conflict="raise"` なら例外で返す。"""
    state["pending_drop"] = None
    statefile.save(path, state)
    what = "項目に属さないコミットを積み直せません" if outcome.in_replay else "公開済みのコミットを戻せません"
    message = f"{what}: {str(outcome.conflict)[:12]}。HEAD を {plan.before[:12]} へ戻しました"
    if on_conflict == ON_CONFLICT_RAISE:
        raise DropConflict(message)
    die(message)


def _rebuild(
    path: pathlib.Path, state: dict[str, Any], targets: list[str], reason: str, on_conflict: str = ON_CONFLICT_STOP
) -> dict[str, Any]:
    """`targets` を取り消す。積み直しで項目のコミットが衝突したら、その項目を外して計画を作り直す（R2）。

    1 回の衝突で 1 件を外すため、繰り返しは生きている項目の数の内で終わる。"""
    work = work_dir(state)
    plan = ledger.plan_rebuild(state, work, targets)
    if plan.error:
        die(plan.error)
    if plan.empty():
        for item_id in targets:
            ledger.mark_dropped(find_item(state, item_id), reason)
        statefile.save(path, state)
        return {"mode": "skip", "dropped": sorted(targets), "removed": 0, "replayed": 0, "reverted": 0}

    # 再開で外す項目を計算し直すため、指定した項目だけを残す（I8）
    state["pending_drop"] = {"items": targets, "reason": reason, "before": plan.before}
    statefile.save(path, state)
    ejected: list[dict[str, Any]] = []
    for _ in range(len(state.get("items") or []) + 1):
        outcome = _execute(work, plan)
        if not outcome.conflict:
            break
        owner = ledger.live_owner(state, work, outcome.conflict) if outcome.in_replay else ""
        if not owner or owner in plan.dropped:
            _restore_and_stop(path, state, plan, outcome, on_conflict)
        commit = full_commit(work, outcome.conflict)
        ejected.append({"item": owner, "commit": commit, "by": list(targets)})
        info(f"⚠ {owner} の積み直しが衝突したため外します（{', '.join(targets) or reason} の取り消し・{commit[:12]}）")
        plan = ledger.plan_rebuild(state, work, targets + [e["item"] for e in ejected])
        if plan.error:
            die(plan.error)
    if outcome.conflict:
        _restore_and_stop(path, state, plan, outcome, on_conflict)
    return _record(path, state, plan, outcome, reason, ejected)


class UndoConflict(Exception):
    """項目のコミットの打ち消しが衝突した。HEAD は打ち消しの前へ戻してある（#2457 決定 6・I9）。"""

    def __init__(self, message: str, item_id: str, commit: str):
        super().__init__(message)
        self.item_id, self.commit = item_id, commit


def revert_items(path: pathlib.Path, state: dict[str, Any], item_ids: list[str], reason: str) -> dict[str, Any]:
    """改善項目のコミット（テスト・実装・修正）だけを新しい順に `git revert` で打ち消す（#2457 決定 6・I5）。

    どの記録にも無いコミット（取り込んだベースブランチのマージなど）には触れない。`reset --hard` で外す・
    起点の木へ戻す経路は無い。打ち消しが衝突したら打ち消しの前の HEAD へ戻し、衝突した項目を外さずに
    `UndoConflict` を投げる（I9）。打ち消したコミットはオーケストレーターが残すコミットとして台帳へ記録する。
    """
    work = work_dir(state)
    worktree.wait_past_rewrite()
    try:
        targets = [i for i in item_ids if ledger.is_live(find_item(state, i, required=False))]
        targets = _close_commitless(state, targets, reason)
        # 新しい項目から打ち消す（同じ行を順に書き換えた項目を古い方から戻すと衝突する）
        targets.sort(key=lambda i: int(find_item(state, i).get("rank") or 0), reverse=True)
        if targets:
            # 衝突の巻き戻しは `reset --hard` で、未コミットの変更を戻せずに消す（C4）
            worktree.stop_if_dirty(state, work, "打ち消しの")
        before = git_out(work, ["rev-parse", "HEAD"]) or ""
        reverts: list[str] = []
        for item_id in targets:
            shas = list(reversed([full_commit(work, s) for s in item_shas(find_item(state, item_id))]))
            conflict = revert_range(work, shas)
            if conflict:
                # 項目の状態は全部を打ち消せてから書く。途中で衝突したら HEAD と一緒に開始前のまま残す
                reset_hard(work, before)
                state["pending_drop"] = None
                statefile.save(path, state)
                raise UndoConflict(
                    f"{item_id} のコミット {str(conflict)[:12]} を打ち消せません（衝突）。HEAD を {before[:12]} へ戻しました",
                    item_id,
                    conflict,
                )
            reverts.extend(_new_commits(work, len(shas)))
        for item_id in targets:
            item = find_item(state, item_id)
            item["failure_reason"] = reason
            item["reverted_by_commit"] = True
            ledger.mark_dropped(item, reason)
        for sha in reverts:
            ledger.note_orchestrator_commit(state, sha)
        record = {
            "at": statefile.now(),
            "mode": "revert",
            "reason": reason,
            "round": int((state.get("readopt") or {}).get("round") or 1),
            "dropped": list(item_ids),
            "recheck": (ledger.mark_recheck(state) if reverts and not ledger.in_final_gate(state) else []),
            "reverted": len(reverts),
            "before": before,
        }
        state.setdefault("drops", []).append(record)
        state["pending_drop"] = None
        statefile.save(path, state)
        info(f"↩ 打ち消し: {', '.join(item_ids) or '—'} のコミット {len(reverts)} 件を git revert しました（{reason}）")
        return record
    finally:
        worktree.mark_rewritten()


def revert_unattributed(path: pathlib.Path, state: dict[str, Any], shas: list[str], label: str) -> int:
    """どの項目にも属さないコミット（`shas` は古い順）を新しい順に `git revert` で打ち消し、作ったコミットの数を返す。

    `reset --hard` で外さない（取り込んだベースブランチの変更を消さない。#2457 決定 6）。衝突したら打ち消しの前の HEAD へ
    戻して終了コード 4 で止まる。打ち消したコミットはオーケストレーターが残すコミットとして台帳へ記録し、差し戻しに残す。
    """
    work = work_dir(state)
    # 衝突の巻き戻しは `reset --hard` で、未コミットの変更を戻せずに消す（C4）
    worktree.stop_if_dirty(state, work, "打ち消しの")
    before = git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered = list(reversed(shas))
    worktree.wait_past_rewrite()
    try:
        conflict = revert_range(work, ordered)
        if conflict:
            reset_hard(work, before)
            die(f"どの項目にも属さないコミット {conflict[:12]} を打ち消せません（衝突）。HEAD を {before[:12]} へ戻しました")
        made = _new_commits(work, len(ordered))
        for sha in made:
            ledger.note_orchestrator_commit(state, sha)
        state.setdefault("sendbacks", []).append({"kind": "unattributed", "commits": list(shas), "phase": label, "at": statefile.now()})
        state.setdefault("drops", []).append(
            {
                "at": statefile.now(),
                "mode": "revert",
                "reason": label,
                "round": int((state.get("readopt") or {}).get("round") or 1),
                "dropped": [],
                "reverted": len(made),
            }
        )
        statefile.save(path, state)
        return len(made)
    finally:
        worktree.mark_rewritten()


def _new_commits(work: str, count: int) -> list[str]:
    out = git_out(work, ["rev-list", f"-{count}", "HEAD"]) or ""
    return list(reversed([line for line in out.splitlines() if line]))


def drop(
    path: pathlib.Path, state: dict[str, Any], item_ids: list[str], reason: str, on_conflict: str = ON_CONFLICT_STOP
) -> dict[str, Any]:
    """改善項目を打ち消す（`revert_items` の包み）。衝突したら既定（`stop`）は終了コード 4 で止まり、`raise` なら
    `DropConflict` を投げる。HEAD は打ち消しの前へ戻してある。"""
    try:
        return revert_items(path, state, item_ids, reason)
    except UndoConflict as exc:
        if on_conflict == ON_CONFLICT_RAISE:
            raise DropConflict(str(exc)) from None
        die(str(exc))
        raise


def discard_range(path: pathlib.Path, state: dict[str, Any], reason: str) -> dict[str, Any]:
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
