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
import gh_sections
import merged_range
import proc
from release_lib import others, replaced
from release_lib.others import CHANGES_HEADING
from step_result import EXIT_PRECONDITION, StepError

ROW = "含む PR"
RELEASE_PREFIX = "release/"  # 配布の工程が作る版上げのブランチ（release/v<版>）
VIA = "スプリント PR #{} 経由"  # 「含む PR」の行の括弧の中の印（スプリント経由の PR）
NO_CHANGE = "配布物の変化なし"  # 「含む PR」の行の括弧の中の印（`## 利用者向けの変化` が「無し」だけの PR）
_VIA = re.compile(r"・スプリント PR #(\d+) 経由")
PER_PAGE = 100
RISKS_HEADING = "## 未検証・残る危険"  # PR の本文と承認資料の危険の節（承認資料の節は release_lib/materials.py が書く）


@dataclass(frozen=True)
class IncludedPr:
    """範囲の PR 1 本。via はスプリント経由ならスプリント PR の番号、ベースブランチへ直接入った PR と `--prs` は None。"""

    number: int
    via: int | None = None


def _in_range(m: merged_range.Merge) -> bool:
    return bool(m.branch) and not m.branch.startswith(RELEASE_PREFIX)


def release_range(root: Path, prev: str, to: str) -> list[IncludedPr]:
    """`prev..to` の範囲の PR（スプリント経由の PR を含む。番号の昇順）。git か GitHub を読めなければ StepError。"""
    top = merged_range.merges_in(root, prev, to)
    nested = {m.number: [x for x in merged_range.nested_merges(root, m) if _in_range(x)] for m in top if m.branch is not None}
    out: dict[int, IncludedPr] = {}
    for m in merged_range.with_heads(root, top):
        if not _in_range(m):
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


def included_row(n: int, title: str, facts: list[str], via: int | None, no_change: bool) -> str:
    """「含む PR」の 1 行。行の頭は `#<番号>`、括弧の中は facts（状態・merge commit・CI）の後に印を足す。"""
    marks = [*facts, *([VIA.format(via)] if via else []), *([NO_CHANGE] if no_change else [])]
    return f"#{n} {title}（{'・'.join(marks)}）"


def _included_cell(path):
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
    for line in _included_cell(path):
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
        query = _graphql_query(chunk, fields)
        r = gh_call.gh(["api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", f"query={query}"], cwd=cwd)
        data = _read_repository(r)
        if data is None:
            return None, ("gh が無い" if r.returncode == 127 else (r.stderr or r.stdout).strip()[:300] or "応答を読めない")
        for n in chunk:
            d = data.get(f"p{n}")
            out[n] = _normalize(d, comments) if isinstance(d, dict) else None
    return out, ""


def _graphql_query(chunk, fields: str) -> str:
    """chunk の番号を別名 `p<番号>` で並べた 1 本の GraphQL クエリ。"""
    alias = " ".join(f"p{n}: pullRequest(number: {n}) {{ {fields} }}" for n in chunk)
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {alias} }} }}"


def _read_repository(r) -> dict | None:
    """gh api graphql の応答の `data.repository`。読めなければ None。"""
    try:
        data = json.loads(r.stdout)["data"]["repository"]
    except (ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _normalize(d: dict, comments: bool) -> dict:
    """GraphQL の PR 1 件を gh pr view --json と同じ形へ（closingIssuesReferences と comments の nodes を取り出す）。"""
    return {
        **d,
        "closingIssuesReferences": list((d.get("closingIssuesReferences") or {}).get("nodes") or []),
        **({"comments": list((d.get("comments") or {}).get("nodes") or [])} if comments else {}),
    }


def read_prs(root: Path, prs, view, fields: str = "title,body,state", comments: bool = False) -> dict[int, dict]:
    """PR の番号 → gh pr view の出力。`pr_fields` でまとめて読み、読めない・PR でない番号があれば PR ごとの
    `view(n, fields)`（gh pr view --json <fields>）で読み直す（失敗の理由と REST への退避はそちらが持つ）。"""
    ns = list(dict.fromkeys(int(n) for n in prs))
    if not ns:
        return {}
    got, _ = pr_fields(ns, cwd=str(root), comments=comments)
    if got is not None and all(isinstance(got.get(n), dict) for n in ns):
        return got
    return {n: view(n, fields + (",comments" if comments else "")) for n in ns}


def no_change(d) -> bool:
    """PR の本文の `## 利用者向けの変化` が「無し」だけか（配布物の変化なし。#1686）。"""
    return isinstance(d, dict) and gh_sections.says_none(d.get("body") or "", CHANGES_HEADING)


def section_range(root: Path, prev: str | None, base: str | None, extra) -> list[IncludedPr]:
    """CHANGELOG の版の節に並べる PR の候補: 直前の正式版のタグ prev から `origin/<base>` までの範囲の PR と extra
    （`--prs`）の和（#1885）。正式版のタグが無ければ（初めての版）extra だけ。"""
    if not prev:
        return with_extra([], extra)
    proc.git(root, "fetch", "-q", "origin", check=False)
    return with_extra(release_range(root, prev, f"origin/{base}"), extra)


def as_included(prs) -> list[IncludedPr]:
    """番号の列か IncludedPr の列を IncludedPr の列にする（`--prs` だけを渡す古い呼び方も受ける）。"""
    return [p if isinstance(p, IncludedPr) else IncludedPr(int(p)) for p in prs]


def unmerged(d) -> bool:
    """gh pr view の出力がマージされていない PR を指すか。state の無い出力はマージ済みとして扱う。"""
    return isinstance(d, dict) and d.get("state", "MERGED") != "MERGED"


def require_merged(found, prs):
    """マージ済みが 0 件なら、書き込む前に前提エラーで止める（既存の節や欄を空で上書きしない）。"""
    if not found:
        raise StepError(f"渡した PR {' '.join(f'#{n}' for n in prs)} にマージ済みが無い", EXIT_PRECONDITION)
    return found


def titles_of(prs: list[IncludedPr], data: dict, skipped=None) -> list[tuple[int, str]]:
    """PR ごとに (番号, 箇条)。マージされていない PR は載せず、番号を skipped へ足す。スプリント経由の PR
    （変化はスプリント PR の本文が持つ）と配布物の変化なしの PR は箇条にしない（#1885・#1686）。"""
    items, merged = [], []
    for p in prs:
        n, d = p.number, data.get(p.number)
        if unmerged(d):
            skipped is not None and skipped.append(n)
            continue
        try:
            title = d["title"].strip()
        except (TypeError, KeyError, AttributeError):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        merged.append(n)
        if p.via is None and not no_change(d):
            items.append((n, f"- {title}（#{n}）"))
    require_merged(merged, [p.number for p in prs])
    return items


def notes_of(prs: list[IncludedPr], data: dict, skipped=None, replacing=None, unreadable=None) -> list[tuple]:
    """PR ごとに (番号, 利用者向けの変化の箇条, 未検証・残る危険の箇条, 題名で代えたか, 移行の手順の箇条) を返す。
    マージされていない PR は配る中身に入らないため載せず、番号を skipped へ足す。replacing を渡すと本文の
    `## 置き換える箇条` を足し、形に合わない行を unreadable へ items の行として足す（#1834）。
    スプリント経由の PR は変化と移行の手順をスプリント PR の本文に任せて危険だけを返し、配布物の変化なしの PR は
    変化を返さない（#1885・#1686）。"""
    out = []
    for p in prs:
        n, d = p.number, data.get(p.number)
        if unmerged(d):
            skipped is not None and skipped.append(n)
            continue
        if not isinstance(d, dict) or not isinstance(d.get("title"), str):
            raise StepError(f"gh pr view {n} の出力を読めない", 2)
        body = d.get("body") or ""
        items = gh_sections.section_items(body, CHANGES_HEADING, n)
        risks = gh_sections.section_items(body, RISKS_HEADING, n)
        if replacing is not None:
            found, bad = replaced.replacements_in(body, n)
            replacing += found
            unreadable is not None and unreadable.extend(
                {"kind": "replacement", "name": f"#{n}", "result": "unreadable", "text": t} for t in bad
            )
        if p.via is not None or no_change(d):
            out.append((n, [], risks, False, []))
            continue
        out.append((n, items or [f"{d['title'].strip()}（#{n}）"], risks, not items, others.migration_items(body, n)))
    return require_merged(out, [p.number for p in prs])
