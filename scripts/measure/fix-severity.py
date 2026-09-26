#!/usr/bin/env python3
"""`/ndf:fix` のまとめのコメントを重要度別に集計し、#1287 の前提 9 の判定を出す（受け入れ条件 8）。

読むだけ。本文は出力に写さない（件数と PR 番号だけ）。

    python3 scripts/measure/fix-severity.py --repo devbasex/ai-plugins --since 2026-10-01 [--until 2026-10-15]
        [--max-minor-ratio 0.10] [--max-minor-only-per-pr 0.05] [--out <JSON>]

期間は PR のまとめのコメントの作成日時で切る（`--since` を含み `--until` を含まない。日付だけなら UTC の 0 時）。
スレッドも、最初のコメントの作成日時が同じ期間に入るものだけを数える。

出力の鍵:
  summaries / prs          まとめのコメントの件数と、それを持つ PR の本数
  fixed_by_severity        まとめの「対応件数」の critical / major / minor の和
  minor_ratio              minor ÷ 3 つの和
  minor_only_rounds        minor が 1 以上で、critical と major が 0 のまとめの件数
  waived                   「基準外の見送り: N 件」の行の和
  posted_by_severity       スレッドの最初のコメントの `[重要度 / …]` の件数
  posted_minor_ratio       posted の minor と nit の和 ÷ 全件（担当が基準外を書かなくなったか）
  raised_from_minor        最初のラベルが minor / nit で、「対応しました」の返信を持つスレッドの数
  verdict                  minor_ratio ≤ 上限 かつ minor_only_rounds ≤ 上限 × prs
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_MARK = "/ndf:fix サマリ"  # 見出しの形は版で変わったため、印だけで見分ける（起票時の集計と同じ）
COUNTS = re.compile(r"critical=(\d+)\s*/\s*major=(\d+)\s*/\s*minor=(\d+)")
WAIVED = re.compile(r"^基準外の見送り:\s*(\d+)\s*件", re.M)
LABEL = re.compile(r"^\s*\[(critical|major|minor|nit)\s*/", re.I)
FIXED_REPLY = "対応しました"
LEVELS = ("critical", "major", "minor", "nit")

QUERY = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:30,after:$after){
pageInfo{hasNextPage endCursor}
nodes{... on PullRequest{number comments(first:100){nodes{body createdAt}}
reviewThreads(first:100){nodes{comments(first:30){nodes{body createdAt}}}}}}}}"""


# --- 解析（純粋な関数。単体テストはここだけを縛る） ------------------------------

def parse_summary(body: str) -> dict | None:
    """まとめのコメントなら件数を返す。まとめでなければ None。"""
    if SUMMARY_MARK not in (body or ""):
        return None
    m = COUNTS.search(body)
    if not m:
        return None
    w = WAIVED.search(body)
    return {"critical": int(m[1]), "major": int(m[2]), "minor": int(m[3]), "waived": int(w[1]) if w else 0}


def thread_label(bodies: list[str]) -> str | None:
    m = LABEL.match(bodies[0]) if bodies else None
    return m[1].lower() if m else None


def aggregate(summaries: list[tuple[int, str]], threads: list[list[str]],
              max_minor_ratio: float = 0.10, max_minor_only_per_pr: float = 0.05) -> dict:
    """まとめのコメント（PR 番号と本文）とスレッド（コメントの本文の列）から集計する。"""
    parsed = [(pr, s) for pr, s in ((pr, parse_summary(b)) for pr, b in summaries) if s]
    fixed = {k: sum(s[k] for _, s in parsed) for k in ("critical", "major", "minor")}
    total = sum(fixed.values())
    prs = len({pr for pr, _ in parsed})
    minor_only = sum(1 for _, s in parsed if s["minor"] > 0 and s["critical"] == 0 and s["major"] == 0)
    posted = {k: 0 for k in LEVELS}
    raised = 0
    for t in threads:
        label = thread_label(t)
        if label is None:
            continue
        posted[label] += 1
        if label in ("minor", "nit") and any(b.lstrip().startswith(FIXED_REPLY) for b in t[1:]):
            raised += 1
    posted_total = sum(posted.values())
    minor_ratio = fixed["minor"] / total if total else 0.0
    return {
        "summaries": len(parsed), "prs": prs, "fixed_by_severity": fixed,
        "minor_ratio": round(minor_ratio, 4), "minor_only_rounds": minor_only,
        "waived": sum(s["waived"] for _, s in parsed),
        "posted_by_severity": posted,
        "posted_minor_ratio": round((posted["minor"] + posted["nit"]) / posted_total, 4) if posted_total else 0.0,
        "raised_from_minor": raised,
        "thresholds": {"max_minor_ratio": max_minor_ratio, "max_minor_only_per_pr": max_minor_only_per_pr},
        "verdict": minor_ratio <= max_minor_ratio and minor_only <= max_minor_only_per_pr * prs,
    }


def parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def in_range(created: str, since: datetime, until: datetime | None) -> bool:
    t = parse_time(created)
    return t >= since and (until is None or t < until)


def collect(nodes: list[dict], since: datetime, until: datetime | None) -> tuple[list, list]:
    """GraphQL の PR の列から、期間内のまとめと、期間内に始まったスレッドの本文の列を取り出す。"""
    summaries, threads = [], []
    for n in nodes:
        if not isinstance(n, dict) or "number" not in n:
            continue
        for c in (n.get("comments") or {}).get("nodes") or []:
            if c.get("createdAt") and in_range(c["createdAt"], since, until):
                summaries.append((n["number"], c.get("body") or ""))
        for t in (n.get("reviewThreads") or {}).get("nodes") or []:
            cs = (t.get("comments") or {}).get("nodes") or []
            if cs and cs[0].get("createdAt") and in_range(cs[0]["createdAt"], since, until):
                threads.append([c.get("body") or "" for c in cs])
    return summaries, threads


# --- 取得 -----------------------------------------------------------------------

def fetch(repo: str, since: datetime) -> list[dict]:
    q = f"repo:{repo} is:pr updated:>={since.date().isoformat()}"
    nodes, after = [], None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"q={q}"]
        if after:
            args += ["-F", f"after={after}"]
        p = subprocess.run(args, capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit(f"gh api graphql が失敗した: {p.stderr.strip()[:300]}")
        s = json.loads(p.stdout)["data"]["search"]
        nodes += s["nodes"]
        if not s["pageInfo"]["hasNextPage"]:
            return nodes
        after = s["pageInfo"]["endCursor"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--max-minor-ratio", type=float, default=0.10)
    ap.add_argument("--max-minor-only-per-pr", type=float, default=0.05)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    since, until = parse_time(a.since), parse_time(a.until) if a.until else None
    summaries, threads = collect(fetch(a.repo, since), since, until)
    res = {"repo": a.repo, "since": a.since, "until": a.until,
           **aggregate(summaries, threads, a.max_minor_ratio, a.max_minor_only_per_pr)}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
