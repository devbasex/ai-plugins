"""記録の PR とスプリントの PR の読み取りと、閉じる課題・参照だけの課題の集め方（`sprint-close.py` から分けた）。

記録の形は `lib/dist_record.py`、closing keywords の読み取りは `lib/closing-issues.sh` と `lib/closing.py` が持つ。
出す（emit）のは呼び手である。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import gh_call
from closing import referenced_numbers
from step_result import EXIT_UNREADABLE, StepError

CLOSING_ISSUES = Path(__file__).resolve().parent.parent / "lib" / "closing-issues.sh"


_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")


def read_record(root, repo, n):
    """記録の PR の本文とコメントを投稿の順に 1 つの文字列にする。

    (連結した文字列, 部分ごとの (先頭の行の番号, コメントの識別子) の並び) を返す。本文の識別子は None、コメントの
    識別子は `comments[].url` の `#issuecomment-<識別子>` の数字（読めなければ None）。"""
    p = gh_call.gh(["pr", "view", str(n), "--repo", repo, "--json", "body,comments"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"記録の PR #{n} を読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise StepError(f"記録の PR #{n} の出力を読めない", EXIT_UNREADABLE)
    comments = [c or {} for c in d.get("comments") or []]
    parts = [(d.get("body") or "", None)]
    for c in comments:
        m = _COMMENT_ID.search(c.get("url") or "")
        parts.append((c.get("body") or "", int(m.group(1)) if m else None))
    starts, line = [], 0
    for text, cid in parts:
        starts.append((line, cid))
        line += len(text.split("\n"))
    return "\n".join(t for t, _ in parts), starts


def _read_sprint_pr(root, repo, n):
    """スプリントの PR の題と本文を 1 回で読む。"""
    p = gh_call.gh(["pr", "view", str(n), "--repo", repo, "--json", "title,body"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"スプリントの PR #{n} を読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise StepError(f"スプリントの PR #{n} の出力を読めない", EXIT_UNREADABLE)
    return d.get("title") or "", d.get("body") or ""


def sprint_issues(root, repo, prs):
    """(closing keywords が指す課題, 題と本文が参照する記録のリポジトリの番号) を返す。

    closing keywords は本文だけから読む（GitHub は題の closing keywords で課題を閉じない）。"""
    seen, out, refs = set(), [], []
    for n in prs:
        title, body = _read_sprint_pr(root, repo, n)
        refs += [r for r in referenced_numbers(title + "\n" + body, repo) if r not in refs]
        c = subprocess.run(["bash", str(CLOSING_ISSUES), "--repo", repo], cwd=root, input=body, capture_output=True, text=True)
        for ln in c.stdout.splitlines():
            if "\t" not in ln:
                continue
            r, num = ln.split("\t", 1)
            key = (r.strip(), int(num.strip()))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out, refs


NOT_GIVEN = "closing keywords（GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）も --issues も無い参照"


def referenced_items(root, repo, refs, issues):
    """参照だけの課題の行。1 番号につき状態と URL を 1 回読み、Pull Request と閉じた課題は行にしない。"""
    items = []
    for n in refs:
        if (repo, n) in issues:
            continue
        p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state,url", "-q", "[.state, .url] | @tsv"], cwd=root)
        state, _, url = p.stdout.strip().partition("\t")
        base = {"kind": "issue", "repo": repo, "number": n, "result": "kept_open"}
        if p.returncode != 0 or not state:
            items.append({**base, "reason": f"状態を読めない（{NOT_GIVEN}）"})
        elif "/pull/" not in url and state != "CLOSED":
            items.append({**base, "reason": NOT_GIVEN})
    return items
