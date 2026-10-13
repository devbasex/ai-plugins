#!/usr/bin/env python3
"""使用量の帳簿の集計（#1953 の入出力の契約 C3）。帳簿を 1 つ選び、期間・スプリント・呼び出し元ごとの合計を出す。

    usage-report.py --ledger <claude|codex|kiro|agy|jev>
                    [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--milestone <番号|名前>]
                    [--by caller|day|plan] [--repo <owner/repo> | --all-repos] [--format json|table]

- 期間は JST の日付で、両端を含む。`--milestone` はマイルストーンの `created_at` から `closed_at`（開いていれば今）
- 出力は帳簿ごとに分かれ、帳簿をまたぐ合計を持たない。Claude の帳簿は「サブエージェントだけ」「全体」
  「会話の記録だけ」を並べる。閉じた課題の数を並べる（GitHub Issues でなければ `unknown`）
- 既定は今のリポジトリの帳簿のファイルだけを読む（`--all-repos` で全リポジトリ）
- 終了コード: 0 集計した（帳簿が無い・空も 0 件として 0）/ 2 引数の誤り
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import usage_aggregate  # noqa: E402
import usage_ledger  # noqa: E402
from transcript_agents import config_root  # noqa: E402


def num(v) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.4f}"


def top_level(path: str) -> Path:
    """`path` を含む git の作業ツリーの根（課題の置き場の設定 `.ndf/project.json` を読む）。git の外ならそのまま。"""
    p = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else path).resolve()


def report_table(out: dict) -> str:
    """表の形。使用量の列は帳簿の鍵のまま並べる。"""
    keys = sorted({k for g in out["groups"] for k in g["usage"]} | set(out["totals"]["usage"]))
    head = f"| {out['ledger']} | 回数 | 不明 | " + " | ".join(keys) + " |"
    lines = [
        f"帳簿: {out['ledger']}・リポジトリ: {out['repo']}・期間: {out['period']['since'] or '最初'} 〜 {out['period']['until'] or '最後'}",
        f"閉じた課題: {out['closed_issues']}",
        "",
        head,
        "| --- | ---: | ---: | " + " | ".join("---:" for _ in keys) + " |",
    ]
    for g in [*out["groups"], {"key": "合計", **out["totals"]}]:
        cells = " | ".join(num(g["usage"].get(k, 0)) for k in keys)
        lines.append(f"| {g['key']} | {g['calls']} | {g['unknown_calls']} | {cells} |")
    if "claude" in out:
        c = out["claude"]
        lines.append("")
        for name, label in (("subagent_only", "サブエージェントだけ"), ("total", "全体"), ("transcript_only", "会話の記録だけ")):
            u = c[name]["usage"]
            text = " / ".join(f"{k} {num(u.get(k, 0))}" for k in usage_aggregate.CLAUDE_KEYS)
            lines.append(f"- {label}: {c[name]['calls']} 件（{text}）")
        lines.append(f"- 数えられない: {c['uncountable_note']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True, choices=usage_ledger.RUNTIMES)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--milestone")
    ap.add_argument("--by", choices=usage_aggregate.GROUP_BY, default="caller")
    who = ap.add_mutually_exclusive_group()
    who.add_argument("--repo", help="owner/repo（既定は今のリポジトリ）")
    who.add_argument("--all-repos", action="store_true")
    ap.add_argument("--format", choices=("json", "table"), default="json")
    ap.add_argument("--root", default=".", help="課題とマイルストーンを引くリポジトリ（既定は今のディレクトリ）")
    a = ap.parse_args(argv)
    for d in (a.since, a.until):
        if d and not usage_aggregate.is_date(d):
            ap.error(f"日付は YYYY-MM-DD: {d}")
    if a.milestone and (a.since or a.until):
        ap.error("--milestone と --since / --until は一緒に渡さない")
    root = top_level(a.root)
    repo = None if a.all_repos else (a.repo.replace("/", "__", 1) if a.repo else usage_ledger.repo_key(str(root)))
    try:
        out = usage_aggregate.usage_report(
            a.ledger,
            root=root,
            since=a.since,
            until=a.until,
            milestone=a.milestone,
            by=a.by,
            repo=repo,
            projects=config_root() / "projects",
        )
    except ValueError as e:
        ap.error(str(e))
    print(report_table(out) if a.format == "table" else json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
