"""ステップの打ち直しの同じ原因の上限（#2343 の決定 8・不変条件 I11・I12）。

プランの記録（`RunState.record`）がステップの失敗ごとに原因の鍵を作って数え、判定役（judge）が失敗したステップを
もう一度流すと返したときに、プランの実行（`Engine._next_after_judge`）が回数を見て止める。判定役の入力は変えない。

- 原因の鍵の理由の文は、ステップの出力の最後の結果 JSON の `summary`。無ければ出力の最後の空でない行。種類は `exit=<終了コード>`
- 回数は `RunState.cause_counts`（`{ステップの id: {原因の鍵: 回数}}`）。耐久ステップの出力の写し（`snapshot`）が運ぶ
- GitHub の上限で落ちたステップは数えない。回復の時刻までの待ち（`Engine.gh_limit_wait`）が受け持つ（要求の前提 6）
- 上限は上限の宣言の方針 `supervise.step-rerun` の `same_cause`
"""

from __future__ import annotations

import cause_key
import gh_quota
import limits
from supervise_lib.steps import last_json

POLICY = "supervise.step-rerun"


def _failed(result: dict) -> bool:
    return result.get("exit") not in (0, None)


def key_of(result: dict) -> str | None:
    """失敗したステップの結果（`exit` と `text`）の原因の鍵。"""
    text = result.get("text", "") or ""
    summary = str((last_json(text) or {}).get("summary") or "")
    if not summary:
        summary = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return cause_key.make(f"exit={result.get('exit')}", summary)


def count(cause_counts: dict, sid: str, result: dict) -> None:
    """落ちたステップの原因の鍵を数える。GitHub の上限の失敗は数えない。"""
    if not _failed(result) or gh_quota.is_rate_limited(result.get("text", "") or ""):
        return
    per = cause_counts.setdefault(sid, {})
    key = key_of(result) or ""
    per[key] = per.get(key, 0) + 1


def stop_reason(state, sid: str) -> str | None:
    """判定役が `sid` の打ち直しを返したとき、同じ原因の上限に達していれば止める理由（進捗の記録へも 1 行書く）。"""
    last = state.results.get(sid) or {}
    if not _failed(last) or gh_quota.is_rate_limited(last.get("text", "") or ""):
        return None
    key = key_of(last)
    n = (state.cause_counts.get(sid) or {}).get(key or "", 0)
    cap = limits.value(POLICY, "same_cause")
    if n < cap:
        return None
    state.progress_write({"kind": "same-cause", "step": sid, "cause": cause_key.brief(key), "count": n, "policy": POLICY})
    return f"ステップ {sid} が同じ原因（{cause_key.brief(key)}）で {n} 回失敗した（方針 {POLICY}）。打ち直さずに止める"
