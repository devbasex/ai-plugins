"""工程の終わりの判断（純粋な関数。N8a・#2293・#1893・#2067）。

- スプリントを閉じる条件 2: リリース後テストの記録があれば、フラグを問わず見る。不合格の行を持つ課題は閉じない
  （フラグの付け忘れで不合格の課題を閉じない）
- 後片付けの工程の記録: 課題の本文の進行の節で、工程「後片付け」の行が未チェックならチェックを付ける。記録済みなら
  打ち直さない
- gws の認証: クライアントの設定だけがありトークンが無い状態（`credential_source` が `none` か `client_secret.json`）は
  未認証。トークンを環境変数で渡した状態は `auth_method: none` を返しても認証済み
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

from ndf.progress.run import ISSUE_SECTION, SECTION_END

# 後片付けの工程の名前（進行の節の行）
CLEANUP_STAGE = "後片付け"


def blocking_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """課題を閉じない、リリース後テストの行（記録が無ければ空）。"""
    if verification is None:
        return []
    # 結果が不合格（failed）の行。not_run（打てなかった）は閉じるかを人が決めるので数えない
    return [r for r in verification if r.get("issue") == issue and r.get("result") == "failed"]


def closable(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], require: bool) -> tuple[bool, str]:
    """課題を閉じてよいかと理由。`require` は記録が要る（記録が無ければ閉じない）。記録があれば条件 2 を必ず見る。"""
    rows = None if verification is None else list(verification)
    if rows is None:
        return (False, "リリース後テストの記録が無い") if require else (True, "リリース後テストの記録が無い（条件 2 を見ない）")
    bad = blocking_rows(issue, rows)
    if bad:
        return False, f"リリース後テストに不合格の行が {len(bad)} 件ある"
    return True, "リリース後テストに不合格の行が無い"


def _stage_line(stage: str) -> re.Pattern[str]:
    return re.compile(rf"^- \[( |x)\] {re.escape(stage)}(?:\s.*)?$", re.MULTILINE)


def mark_stage(body: str, stage: str, stamp: str) -> Optional[str]:
    """進行の節の工程の行にチェックを付けた本文。記録済み・節か行が無ければ None（書き換えない）。"""
    start = body.find(ISSUE_SECTION)
    if start < 0:
        return None
    end = body.find(SECTION_END, start)
    stop = end if end >= 0 else len(body)
    m = _stage_line(stage).search(body, start, stop)
    if m is None or m.group(1) == "x":
        return None
    return body[: m.start()] + f"- [x] {stage} — {stamp}" + body[m.end() :]


def gws_authenticated(status: Mapping[str, Any], token_in_env: bool) -> bool:
    """`gws auth status` の結果から認証済みかを決める。"""
    if token_in_env:
        return True
    # クライアントの設定だけでトークンの無い認証の出所（gws auth status の credential_source の値）
    return status.get("credential_source") not in ("none", "client_secret.json")
