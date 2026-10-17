"""範囲の PR と承認資料の「含む PR」の組み立て（#1274・#1885）。`release-steps.py` の changelog・notes・
changed-plugins・approval-facts が同じものを使う（範囲を求めるのはここだけ）。

範囲の PR は、直前の正式版のタグから終点までにベースブランチへ入った PR（`lib/merged_range.py` の `merges_in`）と、
そのうち merge commit で入った PR の 2 つ目の親の側（スプリントブランチ）へ merge commit で入った PR
（スプリント経由の PR。`merged_range.nested_merges`）の和。版上げの PR（head が `release/` で始まる）と PR でない
番号を外し、`--prs` との和を番号の昇順に並べる。notes --approval は承認資料の「含む PR」の欄から番号と経由を読み、
範囲を求め直さない。

使う側は `lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import approved_commit as ac
import gh_call
import merged_range
from step_result import EXIT_PRECONDITION, StepError

ROW = "含む PR"
RELEASE_PREFIX = "release/"  # 配布の工程が作る版上げのブランチ（release/v<版>）
VIA = "スプリント PR #{} 経由"  # 「含む PR」の行の括弧の中の印（スプリント経由の PR）
NO_CHANGE = "配布物の変化なし"  # 「含む PR」の行の括弧の中の印（`## 利用者向けの変化` が「無し」だけの PR）
_VIA = re.compile(r"・スプリント PR #(\d+) 経由")
PER_PAGE = 100


@dataclass(frozen=True)
class IncludedPr:
    """範囲の PR 1 本。via はスプリント経由ならスプリント PR の番号、ベースブランチへ直接入った PR と `--prs` は None。"""

    number: int
    via: int | None = None


def _kept(m: merged_range.Merge) -> bool:
    return bool(m.branch) and not m.branch.startswith(RELEASE_PREFIX)


def release_range(root: Path, prev: str, to: str) -> list[IncludedPr]:
    """`prev..to` の範囲の PR（スプリント経由の PR を含む。番号の昇順）。git か GitHub を読めなければ StepError。"""
    top = merged_range.merges_in(root, prev, to)
    nested = {m.number: [x for x in merged_range.nested_merges(root, m) if _kept(x)] for m in top if m.branch is not None}
    out: dict[int, IncludedPr] = {}
    for m in merged_range.with_heads(root, top):
        if not _kept(m):
            continue
        out[m.number] = IncludedPr(m.number)
        for x in nested.get(m.number, []):
            out.setdefault(x.number, IncludedPr(x.number, m.number))
    return sorted(out.values(), key=lambda p: p.number)


def range_prs(root: Path, prev: str, to: str) -> list[int]:
    """`prev..to` の範囲の PR の番号。"""
    return [p.number for p in release_range(root, prev, to)]


def with_extra(found, extra) -> list[IncludedPr]:
    """範囲の PR と `--prs` の和（同じ番号は 1 つ。範囲にある番号は範囲の経由を保つ）を番号の昇順で。"""
    out = {p.number: p for p in found}
    for n in extra or ():
        out.setdefault(int(n), IncludedPr(int(n)))
    return sorted(out.values(), key=lambda p: p.number)


def included_prs(found, extra) -> list[int]:
    """範囲の PR の番号と `--prs` の和（同じ番号は 1 つ）を番号の昇順で。"""
    return sorted({*(p.number if isinstance(p, IncludedPr) else p for p in found), *extra})


def row(n: int, title: str, facts: list[str], via: int | None, no_change: bool) -> str:
    """「含む PR」の 1 行。行の頭は `#<番号>`、括弧の中は facts（状態・merge commit・CI）の後に印を足す。"""
    marks = [*facts, *([VIA.format(via)] if via else []), *([NO_CHANGE] if no_change else [])]
    return f"#{n} {title}（{'・'.join(marks)}）"


def _cell(path):
    try:
        _, rows = ac._cells(path)  # 承認資料の表の読み取りは approved_commit の 1 か所に置く
    except ValueError as e:
        raise StepError(f"{e}（先に approval-facts を走らせる）", EXIT_PRECONDITION)
    if ROW not in rows:
        raise StepError(f"承認資料 {path} に「{ROW}」の欄が無い（approval-facts で作り直す）", EXIT_PRECONDITION)
    cell = rows[ROW][1]
    return [] if cell == "—" else [line.strip() for line in cell.split("<br>")]


def read_included_rows(path) -> list[IncludedPr]:
    """承認資料の「含む PR」の各行の `#N` とスプリント経由の印。印の無い行（古い資料）は経由なしとして読む。
    資料か欄が無い: StepError（3）。`#N` で始まらない行がある: StepError（2）。"""
    out = []
    for line in _cell(path):
        m = re.match(r"#(\d+)\b", line)
        if not m:
            raise StepError(f"承認資料の「{ROW}」の行を読めない: {line[:100]}", 2)
        via = _VIA.search(line)
        out.append(IncludedPr(int(m.group(1)), int(via.group(1)) if via else None))
    return out


def read_included(path) -> list[int]:
    """承認資料の「含む PR」の欄の各行の先頭の `#N`。欄が `—` なら空。"""
    return [p.number for p in read_included_rows(path)]


_FIELDS = "number title body state url headRefName mergeCommit { oid } closingIssuesReferences(first: 20) { nodes { number } }"


def pr_fields(numbers, cwd: str | None = None, comments: bool = False) -> tuple[dict[int, dict | None] | None, str]:
    """PR の番号 → `gh pr view --json number,title,body,state,url,headRefName,mergeCommit,closingIssuesReferences`
    （comments なら `comments` も）と同じ形の dict。GraphQL の別名で 100 件ずつまとめて読む（1 PR 1 回の起動にしない）。
    PR でない番号は None。読めなければ (None, 理由)（呼ぶ側が PR ごとの読み出しへ倒す）。"""
    fields = _FIELDS + (" comments(last: 100) { nodes { body } }" if comments else "")
    out: dict[int, dict | None] = {}
    nums = list(dict.fromkeys(int(n) for n in numbers))
    for i in range(0, len(nums), PER_PAGE):
        chunk = nums[i : i + PER_PAGE]
        alias = " ".join(f"p{n}: pullRequest(number: {n}) {{ {fields} }}" for n in chunk)
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {alias} }} }}"
        r = gh_call.gh(["api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", f"query={query}"], cwd=cwd)
        try:
            data = json.loads(r.stdout)["data"]["repository"]
        except (ValueError, KeyError, TypeError):
            data = None
        if not isinstance(data, dict):
            return None, ("gh が無い" if r.returncode == 127 else (r.stderr or r.stdout).strip()[:300] or "応答を読めない")
        for n in chunk:
            d = data.get(f"p{n}")
            if isinstance(d, dict):
                d = {
                    **d,
                    "closingIssuesReferences": list((d.get("closingIssuesReferences") or {}).get("nodes") or []),
                    **({"comments": list((d.get("comments") or {}).get("nodes") or [])} if comments else {}),
                }
            out[n] = d if isinstance(d, dict) else None
    return out, ""
