"""land_read.py: land が読む事実の解き方（`release_lib/land.py` から分けた。#3672）。

- `store_version`: 宣言の版数の置き場から版数を引く（版上げの PR かの判定と、本番ブランチの版数）
- `parse_landed`: 早送りの取り込みのコミットの trailer から、PR の取り込みの記録を引く
- `merged_or_tip`: 本番の取り込みのコミット。読めなければ本番ブランチの先端
"""

from __future__ import annotations

import json

TRAILER_PR = "NDF-Landed-PR"
TRAILER_HEAD = "NDF-Approved-Head"
TRAILER_TIP = "NDF-Tip"
TRAILER_TRAIL = "NDF-Approval-Trail"  # 承認の証跡の URL（早送りの取り込みのコミットが承認を指す）


def manifest_paths(strategy) -> list[str]:
    """版数の置き場が `plugin` のとき、版数を持つ `plugin.json` の候補（測る部品と同じ 2 つの置き場）。"""
    name = strategy.section["release"].get("plugin")
    return [f"plugins/{name}/.claude-plugin/plugin.json", ".claude-plugin/plugin.json"] if name else [".claude-plugin/plugin.json"]


def store_version(strategy, show) -> str | None:
    """`show(path)` で読めるファイルから版数を引く。置き場が `none`・読めないときは None。"""
    import json_pick

    store = strategy.section["release"]["version_store"]
    if store["kind"] == "none":
        return None
    rows = [(p, "/version") for p in manifest_paths(strategy)] if store["kind"] == "plugin" else [(store["path"], store["pointer"])]
    for path, pointer in rows:
        text = show(path)
        if text is None:
            continue
        try:
            value, found = json_pick.resolve_pointer(json.loads(text), pointer)
        except ValueError:
            return None
        return value if found and isinstance(value, str) and value else None
    return None


def parse_landed(log: str, pr: int) -> dict | None:
    """`git log --format=%H%x00%T%x00%P%x00<trailers>%x1e` の出力から、PR の取り込みの記録の最新の 1 件を引く。"""
    for row in log.split("\x1e"):
        parts = row.strip("\n").split("\x00")
        if len(parts) < 4:
            continue
        sha, tree, parents, trail = parts[0].strip(), parts[1], parts[2].split(), parts[3]
        values = dict(line.split(": ", 1) for line in trail.splitlines() if ": " in line)
        if values.get(TRAILER_PR, "").strip() == str(pr):
            return {
                "commit": sha,
                "tree": tree,
                "head": values.get(TRAILER_HEAD, "").strip(),
                "tip": values.get(TRAILER_TIP, "").strip(),
                "first_parent": parents[0] if parents else "",
            }
    return None


def merged_or_tip(root, merge: str | None, prod: str, git) -> str:
    """本番の取り込みのコミット `merge`。読めなければ（None・空）`origin/<prod>` の先端。`git` は呼び手の git（テストが差し替える）。"""
    return merge or git(root, "rev-parse", f"origin/{prod}").stdout.strip()
