"""読み手役が確認した記録と、これから出す質問が合うかを決める（#4285 の決定 10）。

質問の前に動く hook も使うので、標準ライブラリだけで書く。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

# 足りないものを表す語（hook の止める理由と `ndf records reading check` の結果に出る）
NO_RECORD = "no_record"
HEAD_MOVED = "head_moved"
# 1 つのブランチの名前（git check-ref-format の規則）。refspec に入れるので、`*` などのパターンと範囲の記号を受けない
_BRANCH = re.compile(r"(?![./])(?!.*(?:\.\.|//|/\.|@\{|\.lock(?:/|\Z)))[^\x00-\x20\x7f~^:?*\[\\\ud800-\udfff]+(?<![./])")


class NewerRow(Exception):
    """読める形より新しいバージョンの行がある。"""


def question_key(questions: Any) -> str:
    """`AskUserQuestion` の `questions` の SHA-256。

    質問の文・複数選択か・選択肢の名前と説明だけを使い、文字列の前後の空白を除く。区分（`metadata`）や見出しは含めない。
    """
    kept = [
        {
            "question": _stripped(q.get("question")),
            "multi": q.get("multiSelect") is True,
            "options": [
                {"label": _stripped(o.get("label")), "description": _stripped(o.get("description"))} for o in _objects(q.get("options"))
            ],
        }
        for q in _objects(questions)
    ]
    text = json.dumps(kept, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _objects(value: Any) -> list[Mapping[str, Any]]:
    """配列の中のオブジェクト。配列でなければ空。"""
    return [x for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []


def _stripped(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def used_row(key: str, repo: str, rows: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """使う記録の行（リポジトリと質問が一致する最後の行）。無ければ None。"""
    found = None
    for row in rows:
        if row["repo"] == repo and row["question_key"] == key:
            found = row
    return found


def missing(key: str, repo: str, rows: Iterable[Mapping[str, Any]], heads: Mapping[int, str], prs: Sequence[int] = ()) -> list[str]:
    """質問を出す前に足りないもの。空なら出してよい。

    `heads` は PR の番号から今の head（調べられなかった PR は持たない）、`prs` は承認する PR の番号（hook は渡さない）。
    """
    row = used_row(key, repo, rows)
    if row is None:
        return [NO_RECORD]
    recorded = {pr["number"]: pr["head"] for pr in row["prs"]}
    words = ["no_pr"] if any(n not in recorded for n in prs) else []
    moved = any(n in heads and heads[n] != head for n, head in recorded.items())
    return words + ([HEAD_MOVED] if moved else [])


def light_rows(text: str, version: int) -> list[dict[str, Any]]:
    """記録のファイルの本文から、hook が使う欄の形の合う行（古い順）。壊れた行は除き、`version` より新しい行があれば `NewerRow`。"""
    rows = []
    for line in text.split("\n"):
        row = _row(line)
        if row is None:
            continue
        found = row.get("version")
        if type(found) is int and found > version:
            raise NewerRow(found)
        if has_used_keys(row):
            rows.append(row)
    return rows


def _row(line: str) -> Optional[dict[str, Any]]:
    try:
        row = json.loads(line) if line.strip() else None
    except ValueError:  # noqa: NDF401 ファイル: 書きかけの行は無いものとして扱う
        return None
    return row if isinstance(row, dict) else None


def has_used_keys(row: Mapping[str, Any]) -> bool:
    """比べるのに使う欄の形が合うか（ブランチは 1 つを表す名前だけ）。書くときも読むときもこれで確かめる。"""
    prs = row.get("prs")
    return (
        isinstance(row.get("repo"), str)
        and isinstance(row.get("question_key"), str)
        and isinstance(prs, list)
        and all(_is_pr(pr) for pr in prs)
    )


def _is_pr(pr: Any) -> bool:
    if not (isinstance(pr, dict) and type(pr.get("number")) is int and isinstance(pr.get("head"), str)):
        return False
    branch = pr.get("branch")
    return isinstance(branch, str) and _BRANCH.fullmatch(branch) is not None
