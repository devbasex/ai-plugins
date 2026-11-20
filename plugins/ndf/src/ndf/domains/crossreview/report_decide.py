"""cross-review の記録を PR へ残すコメントの本文（#3939）。

状態ファイル（`store`）のラウンド・修正の記録・指摘のジャーナルだけから組む。`run` の終わりと、状態ファイルから書き足す
`ndf cross-review report` が同じ本文を作る。状態ファイルはセッションの作業場所にあって消えるため、PR に残すのはこの本文である。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf import identity
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import store

# コメントの先頭のマーカー（ほかのコメントと見分ける）
MARKER = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-review-report -->"
# 状態ファイルのキー（書いた記録のコメントの URL。次からは同じコメントを書き換える）
COMMENT_KEY = "report_comment"
# コメントの URL の末尾の id
_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")
# 終わり方の表示
FINAL_LABEL = {
    fin.CONVERGED: "収束した",
    fin.MAX_ROUNDS: "ラウンドの上限で止まった",
    fin.OSCILLATING: "振動で止まった",
    fin.ERROR: "誤りで止まった",
}
# 指摘の終わり方の表示
OUTCOME_LABEL = {fin.FIXED: "直した", fin.SKIPPED: "見送った", fin.UNRESOLVED: "未解決"}


def comment_id(url: Optional[str]) -> Optional[int]:
    """記録のコメントの URL から id（読めなければ None）。"""
    found = _COMMENT_ID.search(url) if url else None
    return int(found.group(1)) if found else None


def _cell(value: Any) -> str:
    """表のセル。改行と縦棒で表が崩れないようにする。無ければ「—」。"""
    if value is None or value == "":
        return "—"
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _short(sha: Any) -> str:
    return f"`{sha[:9]}`" if isinstance(sha, str) and sha else "—"


def final_of(data: Mapping[str, Any]) -> Optional[str]:
    """状態ファイルの最後のラウンドの判定から読む終わり方（修正の途中で終わった起動は None）。"""
    verdict = store.last_round(data).get("verdict")
    return verdict if verdict in fin.FINALS else None


def _round_rows(rounds: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = [
        "| ラウンド | レビューした head | 範囲 | 判定 | 修正のコミット | 送った | 直した指摘 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rounds:
        fix = r.get("fix") if isinstance(r.get("fix"), Mapping) else {}
        fixed = fix.get("fixed") if isinstance(fix.get("fixed"), list) else []
        pushed = "—" if not fix else ("送った" if fix.get("pushed") else "送れていない")
        rows.append(
            f"| {_cell(r.get('round'))} | {_short(r.get('head'))} | {_cell(r.get('scope'))} | {_cell(r.get('verdict'))} "
            f"| {_short(fix.get('commit'))} | {pushed} | {_cell('・'.join(map(str, fixed)))} |"
        )
    return rows


def _finding_rows(findings: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = ["| 番号 | 担当 | 場所 | 指摘 | 終わり方 | 見送った理由 |", "| --- | --- | --- | --- | --- | --- |"]
    for f in findings:
        outcome = f.get("outcome")
        shown = OUTCOME_LABEL[outcome] if outcome in OUTCOME_LABEL else outcome
        rows.append(
            f"| {_cell(f.get('id'))} | {_cell(f.get('by'))} | {_cell(f.get('section'))} | {_cell(f.get('text'))} "
            f"| {_cell(shown)} | {_cell(f.get('reason'))} |"
        )
    return rows


def comment(pr: int, data: Mapping[str, Any], final: Optional[str], warnings: Sequence[str] = ()) -> str:
    """PR へ残すコメントの本文。`final` は終わり方（`finish_decide.FINALS`。分からなければ None）。"""
    rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
    findings = [f for f in store.list_of(data, store.FINDINGS) if isinstance(f, Mapping)]
    counts = {key: sum(1 for f in findings if f.get("outcome") == key) for key in OUTCOME_LABEL}
    label = FINAL_LABEL[final] if final in FINAL_LABEL else "途中（終わり方の記録が無い）"
    lines = [
        MARKER,
        f"## cross-review の記録（PR #{pr}）",
        "",
        f"終わり方: {label}。ラウンド {len(rounds)}・指摘 {len(findings)}"
        f"（直した {counts[fin.FIXED]}・見送った {counts[fin.SKIPPED]}・未解決 {counts[fin.UNRESOLVED]}）",
        "",
        "### ラウンドと修正",
        "",
        *(_round_rows(rounds) if rounds else ["ラウンドの記録は無い。"]),
        "",
        "### 指摘",
        "",
        *(_finding_rows(findings) if findings else ["指摘は無い。"]),
    ]
    if warnings:
        lines += ["", "### 注意", "", *(f"- {_cell(w)}" for w in warnings)]
    return "\n".join(lines) + "\n"
