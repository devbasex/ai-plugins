"""監視の結果（収束ループ共通層、#662）。

`monitor.py` が担当 1 者の監視を終えるたびに、2 つのファイルへ同じ辞書を残す。

| ファイル | 中身 | 読む側 |
| --- | --- | --- |
| `<stem>-monitor.json` | その担当の**最後の**監視の結果 | 結果なしの理由を引く側（stem から 1 つに決まる） |
| `monitor-outcomes.jsonl` | 監視の結果を 1 行 1 つで**追記だけ**で積む | 実行の要約（`run_metrics.py`） |

**語彙と読み書きをここ 1 か所に置く。** 書く側（`monitor.py`）と読む側（`state.py` /
`refactor_lib` / 要約）が同じ定数と関数を使う。`monitor.py` に置くと、読む側が監視の
実体を読み込むことになる（設計の決定 4）。

キーと値の形は `issues/issue-662-598-537-619-584-583-design-contracts.md` の
「監視の結果ファイル」にある。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import threading
from typing import Any, Optional

try:  # Windows には無い。無ければスレッドの排他だけで書く。
    import fcntl
except ImportError:  # pragma: no cover - POSIX では通らない
    fcntl = None  # type: ignore[assignment]

# 監視が書く理由。**状態（`status`）からの対応だけで決まる。** `usage_limit` と
# `cli_timeout` は P3 で足す（それまでは `early_error` と `missing` に落ちる）。
REASONS = ("ok", "timeout", "stalled", "early_error", "missing", "pidfile_bad")

_STATUS_REASON = {
    "OK": "ok",
    "TIMEOUT": "timeout",
    "STALLED": "stalled",
    "EARLY_ERROR": "early_error",
    "NO_RESULT": "missing",
    "PIDFILE_BAD": "pidfile_bad",
}

# 結果ファイルのキー。**並びも契約の文書の表と揃える**（読む人が突き合わせやすい）。
OUTCOME_KEYS = (
    "agent", "stem", "status", "exit_code", "reason", "detail",
    "launched_at", "started_at", "ended_at", "elapsed", "idle_seconds",
    "progress_tail", "result_exists", "pid",
)

JOURNAL_NAME = "monitor-outcomes.jsonl"

# 同じプロセスの担当ごとのスレッドが同じ記録へ追記する。
_JOURNAL_LOCK = threading.Lock()


def now_iso() -> str:
    """タイムゾーン付きの現在時刻。`state.py` の `_now` と同じ形。"""
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def iso_from_timestamp(ts: float) -> str:
    """UNIX 時刻（ファイルの更新時刻など）を、`now_iso` と同じ形へ変える。"""
    return (
        _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
        .astimezone().isoformat(timespec="seconds")
    )


def reason_for(status: str) -> str:
    """監視の状態から理由を決める。表に無い状態は呼び出し側の誤りとして落とす。"""
    try:
        return _STATUS_REASON[status]
    except KeyError:
        raise ValueError(f"監視の状態として知らない値です: {status!r}") from None


def outcome_path(tmp_dir: os.PathLike[str] | str, stem: str) -> pathlib.Path:
    return pathlib.Path(tmp_dir) / f"{stem}-monitor.json"


def journal_path(tmp_dir: os.PathLike[str] | str) -> pathlib.Path:
    return pathlib.Path(tmp_dir) / JOURNAL_NAME


def write_outcome(tmp_dir: os.PathLike[str] | str, stem: str,
                  outcome: dict[str, Any]) -> None:
    """結果ファイルを原子的に置き換える。読みかけの半端な JSON を残さない。"""
    path = outcome_path(tmp_dir, stem)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_outcome(tmp_dir: os.PathLike[str] | str, stem: str) -> Optional[dict[str, Any]]:
    """結果ファイルを読む。無い・読めないときは `None`。"""
    try:
        data = json.loads(outcome_path(tmp_dir, stem).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def append_journal(tmp_dir: os.PathLike[str] | str, outcome: dict[str, Any]) -> None:
    """記録へ 1 行を追記する。

    **1 行を 1 回の `write` で書き、ファイルの排他を掛ける。** 担当ごとのスレッドと、
    別プロセスの監視（レビューと反証）が同じファイルへ追記しうる。`O_APPEND` だけでは
    書き込みが分割されたときに行が混ざりうるため、排他で塞ぐ。
    """
    line = (json.dumps(outcome, ensure_ascii=False) + "\n").encode("utf-8")
    path = journal_path(tmp_dir)
    with _JOURNAL_LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)  # 閉じれば排他も外れる


def read_journal(tmp_dir: os.PathLike[str] | str) -> list[dict[str, Any]]:
    """記録の全行を読む。読めない行は飛ばす（書きかけの末尾行を含む）。"""
    try:
        text = journal_path(tmp_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
