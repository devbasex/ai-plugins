#!/usr/bin/env python3
"""review-terms-count.py（試行）: 設計 PR のレビューの指摘を、語・定義と食い違いの語の並びで数える。

    python3 review-terms-count.py <PR> [--repo OWNER/REPO]
    python3 review-terms-count.py --comments-file comments.json [--reviews-file reviews.json]

数えるのは返信を除いたインラインの指摘（`pulls/<PR>/comments` のうち `in_reply_to_id` の無いもの）の本文で、
下の語を含めば当たりとする。1 件が両方に当たれば両方に数える。ラウンド数は `pulls/<PR>/reviews` の本文の
先頭 `## 🤖 cross-review | round <N>` の最大値である（見つからなければ 0）。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "review-terms-count"`）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from step_result import EXIT_UNREADABLE, StepError, emit, main_with, result  # noqa: E402

TOOL = "review-terms-count"
TERMS = {
    "terms": ("用語", "語", "呼び", "名前", "名称", "定義", "意味", "表記", "揺れ"),
    "mismatch": ("食い違", "矛盾", "一致しない", "整合", "ずれ", "異なる", "合わない"),
}
ROUND = re.compile(r"^## 🤖 cross-review \| round (\d+)")


def gh_json(path: str) -> list:
    p = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"gh api {path} が失敗: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    pages = json.loads(p.stdout)
    return [x for page in pages for x in (page if isinstance(page, list) else [page])]


def read_file(path: str) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"{path} を読めない: {e}", EXIT_UNREADABLE)
    return data if isinstance(data, list) else []


def count(comments: list, reviews: list) -> dict:
    findings = [c for c in comments if isinstance(c, dict) and not c.get("in_reply_to_id")]
    hits = {k: 0 for k in TERMS}
    for c in findings:
        body = str(c.get("body") or "")
        for k, words in TERMS.items():
            if any(w in body for w in words):
                hits[k] += 1
    rounds = [int(m.group(1)) for r in reviews if isinstance(r, dict)
              for m in [ROUND.match(str(r.get("body") or ""))] if m]
    return {"findings": len(findings), **hits, "rounds": max(rounds, default=0)}


def cmd(a):
    if a.comments_file:
        comments = read_file(a.comments_file)
        reviews = read_file(a.reviews_file) if a.reviews_file else []
    else:
        if not a.pr or not a.repo:
            raise StepError("PR と --repo か、--comments-file を渡す", EXIT_UNREADABLE)
        comments = gh_json(f"repos/{a.repo}/pulls/{a.pr}/comments")
        reviews = gh_json(f"repos/{a.repo}/pulls/{a.pr}/reviews")
    m = count(comments, reviews)
    emit(result(TOOL, "ok", f"指摘 {m['findings']} 件のうち語・定義 {m['terms']} 件・食い違い {m['mismatch']} 件"
                            f"（{m['rounds']} ラウンド）", [], m))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", nargs="?")
    ap.add_argument("--repo")
    ap.add_argument("--comments-file")
    ap.add_argument("--reviews-file")
    ap.set_defaults(func=cmd)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
