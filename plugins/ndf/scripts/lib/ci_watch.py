"""CI の結果の監視（`ci-watch.py peek`）の判定と記録（#3056）。標準ライブラリだけで動く純粋な部分。

PR の head のテストのチェックを 1 回だけ読んだ結果から結論（`passed` / `failed` / `pending` / `unknown`）を決め、
プランの状態ディレクトリの `work/ci-watch.jsonl` へ追記する。改修の回数は記録のうち `verdict: failed` の行の
`head` の異なる数で、同じ head を何度覗いても 1 回と数える（過去の行は書き換えない）。
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Iterable, Optional

import jsonio

LOG_NAME = "ci-watch.jsonl"
PASSED = "passed"
FAILED = "failed"
PENDING = "pending"
UNKNOWN = "unknown"

# 終了コード（プランの行き先は `supervise_lib/verify_steps.ci_watch_steps` が決める）
EXIT_GO = 0  # 進めてよい（通った・未完了・チェックがまだ無い・取得できなかった）
EXIT_FIX = 1  # テストのチェックが落ちた。改修の回数は上限の内
EXIT_LIMIT = 2  # 落ちたが改修の回数が上限に当たった
EXIT_NOT_CI = 3  # --after-merge で、落ちたテストのチェックが無い（マージが CI 以外の理由で落ちた）


def log_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "work" / LOG_NAME


def matches(names: list[str], label: str, name: str) -> bool:
    """チェックがテストのチェックか。`names` が空なら head のすべて。照合は名前の完全一致（ラベルか、ジョブの名前）。"""
    return not names or label in names or name in names


def missing(names: list[str], seen: Iterable[tuple[str, str]]) -> list[str]:
    """テストのチェックのうち、head に 1 件も現れないものの名前。"""
    seen = list(seen)
    return [n for n in names if not any(n in (label, name) for label, name in seen)]


def verdict(failed: int, pending: int, passed: int, absent: int) -> str:
    """結論。落ちたものがあれば failed、未完了・現れないものがあれば pending、通ったものだけなら passed。何も無ければ pending。"""
    if failed:
        return FAILED
    if pending or absent or not passed:
        return PENDING
    return PASSED


def failed_heads(rows: list[dict]) -> list[str]:
    """記録のうち落ちた head（重ねない。記録の順）。"""
    heads: list[str] = []
    for row in rows:
        head = row.get("head")
        if row.get("verdict") == FAILED and head and head not in heads:
            heads.append(str(head))
    return heads


def decide(result: str, head: Optional[str], rows: list[dict], limit: int, after_merge: bool) -> tuple[int, int]:
    """(終了コード, 改修の回数)。`rows` は今回の行を足す前の記録。改修の回数は今回の head を含む落ちた head の数。

    今回より前の落ちた head（今回の head を除く）が上限に達していれば、改修を起こさず 2 を返す（I5）。
    """
    before = [h for h in failed_heads(rows) if h != head]
    fixes = len(before) + (1 if result == FAILED and head else 0)
    if result == FAILED:
        return (EXIT_LIMIT if len(before) >= limit else EXIT_FIX), fixes
    return (EXIT_NOT_CI if after_merge else EXIT_GO), fixes


def read_log(state_dir: str | Path) -> list[dict]:
    rows = jsonio.read_jsonl(log_path(state_dir), missing=[], skip_broken=True)
    return [r for r in rows if isinstance(r, dict)]


def append_log(state_dir: str | Path, head: Optional[str], result: str, failed: list[str], after_merge: bool) -> dict:
    row = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "head": head,
        "verdict": result,
        "failed": list(failed),
        "after_merge": after_merge,
    }
    path = log_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonio.append_jsonl(path, row)
    return row
