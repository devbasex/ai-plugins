"""cleanup の記録を数えて metrics を作る（`merged-steps.py` の _cleanup_result から分けた）。"""

from __future__ import annotations


def cleanup_metrics(items, reclaim):
    """記録から swept の印を外し、自分の分と掃除の分を数えた metrics を返す。"""
    # 値の一致でなく印で分ける（同じパスの kept が両方に並んでも、片方へ寄せない）
    marks = [i.pop("swept", False) for i in items]
    swept = [i for i, m in zip(items, marks) if m]
    own = [i for i, m in zip(items, marks) if not m]
    count = {k: sum(1 for i in own if i["result"] == k) for k in ("removed", "deleted", "absent", "kept", "stopped")}
    return {
        "removed_worktrees": count["removed"],
        "deleted_branches": count["deleted"],
        "absent_branches": count["absent"],
        "kept": count["kept"],
        "stopped": count["stopped"],
        "swept_removed": sum(1 for i in swept if i["result"] == "removed"),
        "swept_kept": sum(1 for i in swept if i["result"] == "kept"),
        **reclaim,
    }
