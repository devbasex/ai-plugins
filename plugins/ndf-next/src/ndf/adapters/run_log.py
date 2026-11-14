"""1 回の実行を識別子 1 つで追う記録（#1142 の AD3。N10。#2338）。

`open_run` が実行の識別子を振り、利用者の状態の置き場の `runs/<リポジトリ>/<UTC の日付>/<起点の識別子>.jsonl` へ
1 行ずつ書く。子は環境変数 `NDF_RUN_ID`・`NDF_RUN_LOG` を受けて親と同じファイルへ書き、`parent_id` で親を指す。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import causes, limits, result

ENV_RUN_ID = "NDF_RUN_ID"
ENV_RUN_LOG = "NDF_RUN_LOG"
ENV_RUN_LOG_SINK = "NDF_RUN_LOG_SINK"
# 記録の出力先。file は状態の置き場のファイル、stderr は標準エラー（コンテナのログへ流す）
SINKS = ("file", "stderr")
RUNS_DIR = "runs"
# 名前がこの形の環境変数の値は、記録へ書く前に伏せる
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY", re.IGNORECASE)
SECRET_MIN = 8
MASK = "[REDACTED]"
# 全文ファイルへ逃がした値の、行に残す先頭の字数
HEAD_CHARS = 200


class RunLogError(Exception):
    """記録を開けない（置き場に書けない・知らない出力先・既に開いている）。"""


def _secret_values() -> list[str]:
    found = [v for k, v in os.environ.items() if SECRET_NAME.search(k) and len(v) >= SECRET_MIN]
    return sorted(found, key=len, reverse=True)


def redact(value: Any, secrets: Optional[list[str]] = None) -> Any:
    """文字列・配列・辞書の中の秘密の環境変数の値を伏せる。"""
    secrets = _secret_values() if secrets is None else secrets
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, MASK)
        return value
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    return value


def _append_line(path: Path, data: bytes) -> None:
    """排他の中で、書きかけの行を閉じてから 1 行を書き切る（並行する子と同じファイルへ書く）。"""
    with open(path, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0, os.SEEK_END)
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
        fh.write(data)
        fh.flush()


@dataclass
class RunLog:
    """開いた実行の記録。`path` は file の出力先のファイル（stderr なら None）。`failed` は書けなくなった理由。"""

    run_id: str
    parent_id: Optional[str]
    entry: str
    path: Optional[Path]
    failed: Optional[str] = None
    _blobs: int = field(default=0, repr=False)

    def child_env(self) -> dict[str, str]:
        """子へ渡す環境変数。子は同じファイルへ書き、自分の行の `parent_id` でこの実行を指す。"""
        env = {ENV_RUN_ID: self.run_id}
        if self.path is not None:
            env[ENV_RUN_LOG] = str(self.path)
        return env

    def _blob(self, text: str) -> dict[str, Any]:
        """行の上限を超える文字列を全文ファイルへ逃がし、行には置き場と先頭だけを残す。"""
        if self.path is None:
            return {"head": text[:HEAD_CHARS], "bytes": len(text.encode())}
        self._blobs += 1
        blob = self.path.with_name(f"{self.path.stem}.{self.run_id}.{self._blobs}.txt")
        blob.write_text(text, encoding="utf-8")
        return {"full_text": str(blob), "head": text[:HEAD_CHARS]}

    def _fit(self, fields: dict[str, Any]) -> dict[str, Any]:
        limit = int(limits.value("run-log.line", "count"))
        return {k: self._blob(v) if isinstance(v, str) and len(v.encode()) > limit else v for k, v in fields.items()}

    def write(self, event: str, **fields: Any) -> None:
        """1 行を書く。書けなければ例外を投げず `failed` に理由を持ち、標準エラーへ理由を 1 回だけ出す。"""
        try:
            body = self._fit(redact(fields))
            line = {"at": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "parent_id": self.parent_id}
            line.update(entry=self.entry, event=event, fields=body)
            data = (json.dumps(line, ensure_ascii=False, default=str) + "\n").encode()
            if self.path is None:
                sys.stderr.write(data.decode())
            else:
                _append_line(self.path, data)
        except OSError as exc:
            self._fail(f"実行の記録に書けない: {exc}")

    def _fail(self, reason: str) -> None:
        if self.failed is None:
            self.failed = reason
            print(reason, file=sys.stderr)

    def finish(self, obj: dict[str, Any], code: int) -> tuple[dict[str, Any], int]:
        """終わりの行に結果の全体と終了コードを書く。記録に書けなかった実行は結果を stopped にする。"""
        self.write("end", result=obj, exit=code)
        if self.failed is None:
            return obj, code
        stopped = result.stopped(self.entry, "precondition", self.failed)
        return stopped, causes.exit_code("precondition")


_CURRENT: list[RunLog] = []


def current() -> Optional[RunLog]:
    """このプロセスで開いた記録。開いていなければ None。"""
    return _CURRENT[0] if _CURRENT else None


def close() -> None:
    """このプロセスの記録を手放す（書いたファイルは残す）。"""
    _CURRENT.clear()


def runs_dir() -> Path:
    """記録の置き場の根（利用者の状態の置き場の下）。"""
    return identity.load().state_dir / RUNS_DIR


def _prune(repo_dir: Path, today: date) -> None:
    """保持の日数を超えた日付のディレクトリを消す。消せなければ診断を書いて続ける。"""
    keep = timedelta(seconds=limits.seconds("run-log.keep"))
    for day in sorted(repo_dir.iterdir()):
        try:
            old = date.fromisoformat(day.name) < today - keep
        except ValueError:  # noqa: S112 ファイル: 日付の名前でないディレクトリは記録の置き場のものでないので触らない
            continue
        if old:
            try:
                shutil.rmtree(day)
            except OSError as exc:
                diag(f"古い実行の記録を消せない（続ける）: {day}: {exc}")


def _root_path(run_id: str, location: Optional[str], now: datetime) -> Path:
    repo_dir = runs_dir() / (location or Path.cwd().name)
    day_dir = repo_dir / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    _prune(repo_dir, now.date())
    return day_dir / f"{run_id}.jsonl"


def _sink() -> str:
    sink = os.environ.get(ENV_RUN_LOG_SINK, "") or SINKS[0]
    if sink not in SINKS:
        raise RunLogError(f"{ENV_RUN_LOG_SINK} の値 {sink} を知らない（{', '.join(SINKS)}）")
    return sink


def _log_path(run_id: str, parent_id: Optional[str], location: Optional[str], now: datetime) -> Optional[Path]:
    if _sink() != "file":
        return None
    inherited = os.environ.get(ENV_RUN_LOG, "")
    if parent_id and inherited:
        return Path(inherited)
    return _root_path(run_id, location, now)


def open_run(
    entry: str,
    run_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    location: Optional[str] = None,
    now: Optional[datetime] = None,
) -> RunLog:
    """実行の記録を開き、start の行を書く。親の識別子は省けば環境変数 `NDF_RUN_ID` から受ける。

    `location` は置き場のリポジトリの名前（省けば作業ディレクトリの名前）。起点の実行は保持の日数を超えた
    日付のディレクトリを消す。開けなければ `RunLogError`（呼び出し元は結果を stopped にする）。
    """
    if _CURRENT:
        raise RunLogError(f"このプロセスは実行の記録を既に開いている: {_CURRENT[0].run_id}")
    inherited = os.environ.get(ENV_RUN_ID, "")
    parent = parent_id if parent_id else (inherited if inherited else None)
    rid = run_id or uuid.uuid4().hex
    try:
        path = _log_path(rid, parent, location, now or datetime.now(timezone.utc))
    except OSError as exc:
        raise RunLogError(f"実行の記録の置き場を作れない: {exc}") from exc
    log = RunLog(run_id=rid, parent_id=parent, entry=entry, path=path)
    log.write("start", pid=os.getpid(), cwd=str(Path.cwd()))
    if log.failed:
        raise RunLogError(log.failed)
    _CURRENT.append(log)
    return log


def diag(text: str, short: Optional[str] = None, **fields: Any) -> None:
    """診断を標準エラー（`short` があれば `short`）と実行の記録（全文）の両方へ出す。

    記録を開いていなくても、親の実行の識別子を受けていれば子の実行として開いてから書く。
    """
    shown = short if short is not None else text
    sys.stderr.write(shown if shown.endswith("\n") else shown + "\n")
    log = current()
    if log is None and os.environ.get(ENV_RUN_ID, ""):
        try:
            log = open_run("diag")
        except RunLogError as exc:
            print(f"実行の記録を開けない: {exc}", file=sys.stderr)
            return
    if log is not None:
        log.write("diag", text=text, display=short, **fields)


def clip(text: str, limit: int, what: str) -> str:
    """人へ見せる文を `limit` 字に切る。切るときは、切る前に全文を記録へ残す。"""
    if limit < 1:
        raise ValueError(f"切る字数は 1 以上: {limit}")
    if len(text) <= limit:
        return text
    log = current()
    if log is not None:
        log.write("clip", what=what, full=text, shown=text[:limit])
    return text[:limit]


def record(event: str, **fields: Any) -> None:
    """記録を開いていれば 1 行を書く（開いていなければ何もしない）。"""
    log = current()
    if log is not None:
        log.write(event, **fields)
