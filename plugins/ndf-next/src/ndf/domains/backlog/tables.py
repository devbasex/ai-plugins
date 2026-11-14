"""順位の表をマイルストーンの説明の `### 順位` の節へ書く。描画と置き換えは `table_decide` が持つ。

長さの上限で拒まれたら（HTTP 422）表の行を半分ずつ減らして書き直す。節の外が変わる書き込みはしない。
"""

from __future__ import annotations

from typing import Any, Optional

from ndf.adapters.github import GitHub, GitHubError
from ndf.domains.backlog import issues_gh
from ndf.domains.backlog import table_decide as td

TOO_LONG = issues_gh.UNPROCESSABLE


def _write_one(gh: GitHub, number: int, desc: str, ms: dict[str, Any]) -> tuple[str, Optional[int]]:
    """1 本のマイルストーンの節を書く。(written / unchanged / failed, 載せた行の数か None)。"""
    limit: Optional[int] = None
    while True:
        new = td.replace_section(desc, td.render(ms, limit))
        if new == desc:
            return "unchanged", limit
        if td.outside(new) != td.outside(desc):
            return "failed", limit
        try:
            issues_gh.set_milestone_description(gh, number, new)
        except GitHubError as exc:
            nxt = td.next_limit(len(ms["rows"]), limit) if exc.status == TOO_LONG else None
            if nxt is None:
                return "failed", limit
            limit = nxt
            continue
        return "written", limit


def write(gh: GitHub, tables: list[dict[str, Any]]) -> dict[str, Any]:
    """順位の表を各マイルストーンの説明へ書く。行も前の表も無いマイルストーンは触らない。"""
    got: dict[str, Any] = {"written": [], "unchanged": [], "failed": [], "truncated": {}}
    current = issues_gh.milestone_rows(gh)
    for ms in tables:
        cur = current[ms["title"]] if ms["title"] in current else None
        if cur is None or (not ms["rows"] and not td.has_section(cur["description"])):
            continue
        kind, limit = _write_one(gh, cur["number"], cur["description"], ms)
        if limit is not None:
            got["truncated"][ms["title"]] = limit
        got[kind].append(ms["title"])
    return got
