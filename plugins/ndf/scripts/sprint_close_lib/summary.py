"""課題を閉じる処理（`sprint-close.py`）の件数・要約と、閉じなかった課題の欠け（#2337 の I14）。"""

from __future__ import annotations


def close_summary(items, prs, dry_run, referenced):
    kinds = ("closed", "already_closed", "failed", "closed_unverified", "kept_open", "would_close")
    count = {k: sum(1 for i in items if i["result"] == k) for k in kinds}
    metrics = {"issues": len(items), **count, "referenced_only": referenced, "prs": len(prs)}
    summary = (
        f"閉じた {count['closed']} 件・既に閉じていた {count['already_closed']} 件・"
        f"失敗 {count['failed']} 件・閉じているが条件を満たさない {count['closed_unverified']} 件・"
        f"開いたまま {count['kept_open']} 件（うち参照だけ {referenced} 件）"
    )
    if dry_run:
        summary += f"（試行。閉じる予定 {count['would_close']} 件）"
    return count, metrics, summary


NOT_CLOSED = ("kept_open", "closed_unverified", "failed")


def not_closed_gaps(targets):
    """閉じる対象のうち閉じなかった課題の欠け（参照だけの課題は渡さない。#2337 の I14）。"""
    return [
        {"kind": "issue", "name": f"{i['repo']}#{i['number']}", "reason": i.get("reason") or i["result"]}
        for i in targets
        if i["result"] in NOT_CLOSED
    ]
