"""マージ済みの PR の情報の読み取り（`merged-steps.py` の cleanup から分けた）。"""

from __future__ import annotations

import json

import gh_parts


def merged_pr_info(root, n, add):
    """PR n の (ブランチ, マージコミット) を返す。読めない・MERGED でないときは記録して None。"""
    p = gh_parts.gh(["pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root)
    if p.returncode != 0:
        add("pr", f"#{n}", "kept", f"gh pr view が失敗: {p.stderr.strip()[:200]}")
        return None
    try:
        info = json.loads(p.stdout)
    except ValueError:
        add("pr", f"#{n}", "kept", "gh pr view の出力を読めない")
        return None
    branch = info.get("headRefName")
    if info.get("state") != "MERGED":
        add("pr", branch or f"#{n}", "kept", f"#{n} が MERGED でない（{info.get('state')}）")
        return None
    return branch, (info.get("mergeCommit") or {}).get("oid")
