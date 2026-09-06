"""改修計画（`--plan-file`）の書き出し先と本文。

提案の理由と手順は状態ファイルにしか残らず、差分からは除外される。公開の直前に
進行側が書き出すため、パスの正規化と本文の組み立てをここに置く。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from . import die
from .vocabulary import DEFAULT_PLAN_DIR, ITEM_STATUS_LABELS


def default_plan_file(pr: int) -> str:
    """改修計画を書き出す既定のパス。"""
    return f"{DEFAULT_PLAN_DIR}/refactoring-plan-rf{pr}.md"


def normalize_plan_file(value: Optional[str]) -> str:
    """改修計画の書き出し先を検証して正規化する。空文字は「記録しない」。

    **作業ディレクトリの外へ書かせない。** 進行側は利用者のリポジトリを触るので、
    絶対パスと親へ抜ける経路は受け取った時点で拒む。

    正規化するのは、判定に使うパスを git の出力と揃えるためでもある。
    `./issues/plan.md` のまま持つと、`git status` が返す `issues/plan.md` と
    一致せず、公開のコミットメッセージが取り違えられる。
    """
    rel = str(value or "").strip()
    if not rel:
        return ""
    if os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
        die(f"--plan-file には相対パスを指定してください: {rel}", code=4)
    normalized = os.path.normpath(rel)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        die(
            f"--plan-file が作業ディレクトリの外を指しています: {rel}",
            code=4,
        )
    return normalized


def format_plan(state: dict[str, Any]) -> str:
    """改修計画の本文を組み立てる。**同じ状態からは同じ本文が出る。**

    提案の理由と手順は状態ファイルにしか残らず、そのディレクトリは差分から
    除外される。Pull Request を読む側からは、なぜ直したのかも、どう直す計画
    だったのかも見えない。ここで差分の中へ置く。
    """
    baseline = state.get("baseline_test") or {}
    lines = [
        f"# 改修計画 — {state['repo']} #{state['current_pr']}",
        "",
        "`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。",
        "理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。",
        "",
        f"- 対象範囲: {', '.join(state.get('target_scope') or []) or '（未指定）'}",
        f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}",
        "",
    ]
    for entry in state.get("rounds") or []:
        lines.extend(_plan_round_section(state, entry))
    if not (state.get("rounds") or []):
        lines.append("（改善項目なし）")
    return "\n".join(lines).rstrip() + "\n"


def _plan_round_section(state: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    """1 ラウンド分の見出しと、そのラウンドの改善項目を並べる。"""
    reviewers = " / ".join(entry.get("reviewers") or []) or "—"
    lines = [
        f"## ラウンド {entry['round']}"
        f"（実装 {entry.get('impl', '—')} / レビュー {reviewers}）",
        "",
    ]
    items = [i for i in state.get("items") or [] if i.get("round") == entry["round"]]
    if not items:
        lines.extend(["（採用した改善項目なし）", ""])
        return lines
    for item in items:
        lines.extend(_plan_item_section(item))
    return lines


def _plan_item_section(item: dict[str, Any]) -> list[str]:
    """改善項目 1 件の見出し・要約表・理由・手順。"""
    status = ITEM_STATUS_LABELS.get(item.get("status"), item.get("status") or "—")
    return [
        f"### {item['item_id']} — `{item['path']}#{item['symbol']}`",
        "",
        "| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |",
        "| --- | --- | --- | --- | --- | ---: |",
        f"| {item['smell']} | {item['technique']} | {item['severity']} | "
        f"{' / '.join(item.get('proposed_by') or []) or '—'} | {status} | "
        f"{len(item.get('commits') or [])} |",
        "",
        f"**なぜ**: {item.get('rationale') or '（記録なし）'}",
        "",
        f"**手順**: {item.get('plan') or '（記録なし）'}",
        "",
    ]
