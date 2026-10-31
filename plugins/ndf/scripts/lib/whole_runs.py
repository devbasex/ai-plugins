"""全体テストの実測の記録（#1631）。

手元で走らせた全体検証（静的解析とテスト）が上限の中で終わったら、その壁時計の秒を 1 行ずつ追記する。
予算なしのテストの上限（`test_strategy.unbudgeted_limits`）が、所要の記録に加えて直近の実測を読む。

置き場は `<git の共通ディレクトリ>/ndf/test-run/whole.jsonl`（worktree の間で 1 つ）。git でなければ
`${XDG_STATE_HOME:-~/.local/state}/ndf/test-run/<リポジトリの根の sha256 の先頭 12 文字>/whole.jsonl`。
`NDF_WHOLE_RUNS_DIR` が先に効く。利用者の設定（`.ndf/`）には置かない。読み書きの失敗は例外にしない（書けなければ知らせて続け、読めなければ無し）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

import estimate_records

SCHEMA = 1
RECENT_ROWS = 5  # 直近 = 同じ鍵の最新 5 行のうち最大の秒
SOURCES = ("test-run", "cross-refactoring")


def path(root: Path | str | None = None) -> Path:
    """記録のファイル。`NDF_WHOLE_RUNS_DIR` があればその下（テストの隔離に使う）。"""
    if os.environ.get("NDF_WHOLE_RUNS_DIR"):
        return Path(os.environ["NDF_WHOLE_RUNS_DIR"]) / "whole.jsonl"
    start = Path(root or Path.cwd()).resolve()
    got = estimate_records.find_git_dir(start)
    if got:
        return got[1] / "ndf" / "test-run" / "whole.jsonl"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ndf" / "test-run" / hashlib.sha256(str(start).encode()).hexdigest()[:12] / "whole.jsonl"


def record_whole(root: Path | str | None, key: str, seconds: float, source: str) -> bool:
    """実測を 1 行足す。書けなければ標準エラーへ 1 行知らせて偽を返す（呼び手の結果は変えない）。"""
    if not key or not isinstance(seconds, (int, float)) or seconds <= 0:
        return False
    row = {
        "schema": SCHEMA,
        "key": key,
        "seconds": round(float(seconds), 1),
        "source": source,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        target = path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        print(f"⚠ 全体テストの実測を残せませんでした（{exc}）。結果は変えません", file=sys.stderr, flush=True)
        return False


def recent_whole(root: Path | str | None, key: str) -> Optional[tuple[float, str]]:
    """同じ鍵の最新 `RECENT_ROWS` 行のうち最大の秒と、その行の出所。無い・読めなければ `None`。"""
    if not key:
        return None
    try:
        lines = path(root).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    rows: list[tuple[float, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("schema") != SCHEMA or row.get("key") != key:
            continue
        seconds = row.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            continue
        rows.append((float(seconds), str(row.get("source") or "")))
    if not rows:
        return None
    return max(rows[-RECENT_ROWS:], key=lambda r: r[0])
