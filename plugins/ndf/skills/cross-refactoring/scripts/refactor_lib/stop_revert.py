"""打ち切りの後の取り消し（#1669 決定 1・8〜11）。`commands/gate.py` が、工程の 1 つとして起動したとき（`--workflow-step`）だけ呼ぶ。

最終ゲート修正を打ち切った後、全体テストを落とすコミットをスプリントのブランチに残さずに終えるため、次の順に 1 度ずつ取り消す（I7）。

| 案 | 何をするか | 次へ進むとき |
| --- | --- | --- |
| A | 原因の判定（`culprit.judge`）の順に `undo.drop` で 1 件ずつ取り消し、落ちたテストを手元で走らせ直して通った時点で止める | 積み直しの衝突・候補が尽きた・締め切り（`limits.stop_revert_end_at`）・走らせ直す語が無い |
| B | リファクタリング計画の起点（`plan.base_sha`）の木へ worktree と index を合わせ、コミットを 1 本積む | コミットを作れない → 終了コード 4 |

どちらも push 済みの履歴を書き換えない（`git revert`・積み直し・新しいコミットだけ。C4）。積んだ後の公開と確かめ直しは呼ぶ側
（`FINAL_GATE=recheck`）が行う。案 B の後の最終ゲートでも落ちたら、取り消しを重ねずに終了コード 4 で止まる。
記録は `final_gate.stop_revert`（`plan`・`reverted`・`fallback_reason`・`base_sha`・`commit`・`end_at`・`at`）に残す。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import statefile

from . import culprit, die, info, ledger, timeline, worktree
from .items import live_items
from .paths import git_out, work_dir
from .undo import ON_CONFLICT_RAISE
from .worktree import reset_hard

REASON_A = "最終ゲート修正を打ち切った後に、原因の項目として取り消した"
REASON_B = "最終ゲート修正を打ち切った後に、着手前の木へ戻した（案 B）"


def revert_after_cutoff(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> bool:
    """案 A、だめなら案 B を積む。積んだら真（呼ぶ側が push して確かめ直す）。案 B を使った後なら終了コード 4 で止まる。"""
    record = gate.get("stop_revert") or {}
    if record.get("plan") == "B":
        die(
            "着手前の木へ戻した（案 B）後の最終ゲートでも落ちました。変更起因ではないか揺れのため、これ以上は取り消しません。判断が要ります"
        )
    if record.get("plan") == "A":
        why: Optional[str] = "案 A で取り消した後の最終ゲートでも落ちた"
    else:
        why = _plan_a(path, state, gate)
        if why is None:
            return True
    _plan_b(path, state, gate, why)
    return True


def _plan_a(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> Optional[str]:
    """案 A。落ちたテストが通ったら `None`、案 B へ進むならその理由を返す。"""
    end = timeline.stop_revert_end(state)
    record = gate["stop_revert"] = {"plan": "A", "reverted": [], "end_at": end.isoformat() if end else None, "at": statefile.now()}
    verdict = gate.get("triage") or {}
    rerun = culprit.rerun_of(verdict)
    if not verdict.get("caused") or not rerun:
        return "落ちたテストを走らせ直す語が無い（変更起因を挙げられない）"
    found = culprit.judge(state, gate, verdict, end)
    narrowed = culprit.revert_in_order(
        path, state, found.order, REASON_A, culprit.rerun_passes(state, rerun, end), on_conflict=ON_CONFLICT_RAISE, deadline=end
    )
    record["reverted"] = narrowed.reverted
    statefile.save(path, state)
    if narrowed.passed:
        info(f"↩ 打ち切りの後に原因の項目を取り消し、落ちたテストが通りました（{', '.join(narrowed.reverted)}）")
        return None
    return {
        culprit.CUT_CONFLICT: "取り消しの積み直しが衝突した",
        culprit.CUT_DEADLINE: "打ち切りの後の取り消しの締め切りを過ぎた",
    }.get(str(narrowed.cut), "候補を取り消し尽くしても落ちたテストが通らない")


def _plan_b(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], why: str) -> None:
    """案 B。`plan.base_sha` の木へ戻すコミットを 1 本積み、残った項目をすべて取り消した記録にする。"""
    work = work_dir(state)
    base = str((state.get("plan") or {}).get("base_sha") or "")
    record = gate.setdefault("stop_revert", {})
    record.update({"plan": "B", "fallback_reason": why, "base_sha": base or None, "at": statefile.now()})
    statefile.save(path, state)
    worktree.stop_if_dirty(state, work, "着手前の木へ戻す")  # read-tree -u --reset と失敗時の reset --hard が上書きする
    info(f"⚠ 案 A で通せませんでした（{why}）。着手前の木（{base[:12] or '不明'}）へ戻すコミットを積みます")
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    message = f"Revert: cross-refactoring の改善を着手前の木へ戻す（{why}）"
    # 案 A の最後の走らせ直しのキャッシュを、戻した後に読ませない（#1806 決定 6）
    worktree.wait_past_rewrite()
    try:
        if (
            not base
            or not culprit.git_ok(work, ["read-tree", "-u", "--reset", base])
            or not culprit.git_ok(work, ["commit", "-q", "-m", message])
        ):
            reset_hard(work, head)
            die(f"着手前の木（{base[:12] or '不明'}）へ戻すコミットを作れませんでした。判断が要ります")
    finally:
        worktree.mark_rewritten()
    sha = git_out(work, ["rev-parse", "HEAD"]) or ""
    record["commit"] = sha
    ledger.note_orchestrator_commit(state, sha)
    for item in live_items(state):
        item["failure_reason"] = REASON_B
        ledger.mark_dropped(item, REASON_B)
        record.setdefault("reverted", []).append(item["id"])
    statefile.save(path, state)


def record_line(record: dict[str, Any]) -> str:
    """報告の 1 行。打ち切りの後の取り消しの案と、取り消した項目。"""
    text = f"案 {record.get('plan')}・取り消した項目 {', '.join(record.get('reverted') or []) or 'なし'}"
    if record.get("fallback_reason"):
        text += f"・案 B へ移った理由 {record['fallback_reason']}"
    return text
