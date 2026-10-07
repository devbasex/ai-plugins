"""pr_review_context.py: `pr-review-steps.py collect` の PR の文脈の節を組む（#860）。

取得済みの情報だけから組み、外部へアクセスしない。
"""

from __future__ import annotations

from pathlib import Path


def _thread_lines(threads: list[dict]) -> str:
    if not threads:
        return "なし"
    out = []
    for t in threads:
        where = f"{t.get('path') or '(位置なし)'}:{t.get('line') if t.get('line') is not None else '-'}"
        out.append(f"- `{where}` {str(t.get('body') or '').strip()}")
    return "\n".join(out)


def _pr_sections(root: Path, pr: int, meta: dict, diff_path: str | None, files: list[str], threads: list[dict]) -> list[str]:
    """PR のレビュー担当へ渡す文脈の節。取得済みの情報だけから組み、外部へアクセスしない。"""
    return [
        "## 対象\n\n"
        + "\n".join(
            [
                f"- repo: {meta.get('repo', '')}",
                f"- PR: #{pr} {meta.get('url', '')}",
                f"- 題: {meta.get('title', '')}",
                f"- head の SHA: {meta.get('head_sha', '')}",
                f"- ベースブランチ: {meta.get('base_branch', '')}",
                f"- 作業ディレクトリ: {root}",
            ]
        ),
        "## 受け入れ条件の在りか\n\nPR の本文（下にそのまま載せる）。本文にもプランにも無ければ、その不在を指摘する。\n\n"
        + (str(meta.get("body") or "").strip() or "（本文なし）"),
        f"## 差分\n\n- 差分のファイル: `{diff_path}`\n- 変更ファイル（{len(files)}）:\n" + "\n".join(f"  - `{f}`" for f in files),
        "## 未解決のスレッド\n\n" + _thread_lines(threads) + "\n\n同じ位置へ本文と同じ趣旨の指摘を出さない。同じ位置でも趣旨が違えば出す。",
    ]
