"""1 回の実行を識別子 1 つで追う記録（#1142 の AD3。N10。#2338）。

`open_run` が実行の識別子を振り、利用者の状態の置き場の `runs/<リポジトリ>/<UTC の日付>/<起点の識別子>.jsonl` へ
1 行ずつ書く。子は環境変数 `NDF_RUN_ID`・`NDF_RUN_LOG` を受けて親と同じファイルへ書き、`parent_id` で親を指す。

書き口は標準の `logging` の専用の logger `ndf.run`（`propagate` は偽）に出力先の `Handler`（`Sink`）を 1 つだけ
足す形にする（#2338 の決定 1）。伏せる処理と行の上限は `RunLog` が先に済ませ、出力先は受け取った 1 行を書くだけにする。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
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
# 名前の形に当たらなくても値を秘密として伏せる環境変数。進行表の DB の URL は資格情報を含みうる（B8）
PROGRESS_DB_ENV = "NDF_PROGRESS_DB"
SECRET_ENVS = frozenset({PROGRESS_DB_ENV})
MASK = "[REDACTED]"
# 環境変数に無い秘密も、トークンの形で伏せる（I5）。規則は plugins/ndf/scripts/lib/secret_redact.py の _PATTERNS と同じ
# （ndf-next は ndf のスクリプトを読み込めないため写す）
SECRET_SHAPES = (
    # URL の資格情報（`https://user:pass@host`・`https://<トークン>@host`）
    (re.compile(r"(?<=://)[^\s/@]+@"), MASK + "@"),
    # GitHub のトークンの形
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})"), MASK),
    # `x-access-token:<値>` が URL の外に現れたとき
    (re.compile(r"(?<=x-access-token:)[^\s@]+"), MASK),
    # 認証ヘッダー（`Authorization: Bearer <値>`・`Proxy-Authorization: ...`）は値ごと伏せる
    (re.compile(r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)[^\r\n]+"), r"\1" + MASK),
)
# 全文ファイルへ逃がした値の、行に残す先頭の字数
HEAD_CHARS = 200
# JSON に書いた 1 字の最大のバイト数（制御文字の `\u00XX`）。stderr の続きの行の 1 行に入る字数を決める
JSON_CHAR_BYTES = 6
# 置き場のリポジトリの名前・実行の識別子に使えない値（パスの 1 要素だけを許す）
UNSAFE_LOCATION = re.compile(r"[/\\\x00]")
# 書き口の logger（#2338 の決定 1）。ルートの logger へ流さない
LOGGER_NAME = "ndf.run"
_LOGGER = logging.getLogger(LOGGER_NAME)
_LOGGER.propagate = False


class RunLogError(Exception):
    """記録を開けない（置き場に書けない・知らない出力先・既に開いている・置き場の名前や識別子が使えない）。"""


def _safe_name(name: str, what: str) -> str:
    """パスの 1 要素として使える名前を返す。使えなければ、作る・書く・消すの前に `RunLogError`。"""
    if name in ("", ".", "..") or UNSAFE_LOCATION.search(name):
        raise RunLogError(f"{what}に使えない: {name!r}（パスの 1 要素の名前だけ）")
    return name


def secret_values() -> list[str]:
    """伏せる値（名前が秘密の形の環境変数と `SECRET_ENVS` の値）。長いものから並べ、短い値が長い値の一部を先に伏せないようにする。"""
    found = [v for k, v in os.environ.items() if (SECRET_NAME.search(k) or k in SECRET_ENVS) and len(v) >= SECRET_MIN]
    return sorted(found, key=len, reverse=True)


def _mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, MASK)
    for pattern, repl in SECRET_SHAPES:
        text = pattern.sub(repl, text)
    return text


def redact(value: Any, secrets: Optional[list[str]] = None) -> Any:
    """文字列・配列・辞書の中の、秘密の環境変数の値とトークンの形（`SECRET_SHAPES`）を伏せる。"""
    secrets = secret_values() if secrets is None else secrets
    if isinstance(value, str):
        return _mask(value, secrets)
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    return value


def _string_slots(value: Any) -> list[tuple[Any, Any]]:
    """入れ子の辞書・配列の中の文字列の置き場（入れ物と鍵）を並べる。"""
    if isinstance(value, dict):
        items: Any = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return []
    slots: list[tuple[Any, Any]] = []
    for key, item in items:
        slots.extend([(value, key)] if isinstance(item, str) else _string_slots(item))
    return slots


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


class Sink(logging.Handler):
    """記録の出力先。`record.msg` に入った改行を含まない 1 行を書き切る。

    書けなければ `OSError` を書き口へ返す（`logging` の `handleError` で握りつぶさない）。
    """

    path: Optional[Path] = None


class FileSink(Sink):
    """状態の置き場のファイルへ、排他の中で 1 行を書き切る。"""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        if self.path is None:
            raise OSError("記録のファイルが無い")
        _append_line(self.path, (record.msg + "\n").encode())


class StderrSink(Sink):
    """標準エラー（コンテナのログ）へ 1 行を書く。"""

    def emit(self, record: logging.LogRecord) -> None:
        sys.stderr.write(record.msg + "\n")


@dataclass
class RunLog:
    """開いた実行の記録。`sink` は出力先（`Sink`）。`failed` は書けなくなった理由。"""

    run_id: str
    parent_id: Optional[str]
    entry: str
    sink: Sink
    failed: Optional[str] = None
    _blobs: int = field(default=0, repr=False)

    @property
    def path(self) -> Optional[Path]:
        """file の出力先のファイル（stderr なら None）。"""
        return self.sink.path

    def child_env(self) -> dict[str, str]:
        """子へ渡す環境変数。子は同じファイルへ書き、自分の行の `parent_id` でこの実行を指す。"""
        env = {ENV_RUN_ID: self.run_id}
        if self.path is not None:
            env[ENV_RUN_LOG] = str(self.path)
        return env

    def _line(self, event: str, body: Any) -> bytes:
        line = {"at": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "parent_id": self.parent_id}
        line.update(entry=self.entry, event=event, fields=body)
        return (json.dumps(line, ensure_ascii=False, default=str) + "\n").encode()

    def _emit(self, data: bytes) -> None:
        """1 行を logger `ndf.run` を通して出力先へ渡す。手放した後の記録は書けない（`OSError`）。"""
        if self.sink not in _LOGGER.handlers:
            raise OSError(f"実行の記録 {self.run_id} は手放した後で、出力先が無い")
        msg = data.decode().removesuffix("\n")
        _LOGGER.handle(_LOGGER.makeRecord(LOGGER_NAME, logging.INFO, __file__, 0, msg, None, None))

    def _blob(self, text: str, limit: int) -> dict[str, Any]:
        """文字列を切らずに全文で逃がし、行には参照と先頭だけを残す（I4）。

        file は全文ファイル（`full_text`）へ、stderr は全文を分けた続きの行（event `text_part`。`blob` の番号と
        `part`/`parts` で並べ直せる）として先に出す。
        """
        self._blobs += 1
        ref = {"head": text[:HEAD_CHARS], "chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        if self.path is not None:
            blob = self.path.with_name(f"{self.path.stem}.{self.run_id}.{self._blobs}.txt")
            blob.write_text(text, encoding="utf-8")
            return {**ref, "full_text": str(blob)}
        frame = {"blob": self._blobs, "part": 0, "parts": 0, "text": ""}
        size = max(1, (limit - len(self._line("text_part", frame))) // JSON_CHAR_BYTES)
        parts = [text[i : i + size] for i in range(0, len(text), size)] or [""]
        for n, part in enumerate(parts, 1):
            self._emit(self._line("text_part", {**frame, "part": n, "parts": len(parts), "text": part}))
        return {**ref, "blob": self._blobs, "parts": len(parts)}

    def _fit(self, event: str, body: dict[str, Any]) -> bytes:
        """改行を含む行全体を上限に収める。長い文字列から順に逃がし、なお超えれば `fields` 全体を逃がす（I4）。"""
        limit = int(limits.value("run-log.line", "count"))
        data = self._line(event, body)
        if len(data) <= limit:
            return data
        for holder, key in sorted(_string_slots(body), key=lambda s: len(s[0][s[1]].encode()), reverse=True):
            if len(holder[key]) <= HEAD_CHARS:
                break
            holder[key] = self._blob(holder[key], limit)
            data = self._line(event, body)
            if len(data) <= limit:
                return data
        whole = json.dumps(body, ensure_ascii=False, default=str)
        return self._line(event, {"fields_json": self._blob(whole, limit)})

    def write(self, event: str, **fields: Any) -> None:
        """1 行を書く。書けなければ例外を投げず `failed` に理由を持ち、標準エラーへ理由を 1 回だけ出す。"""
        try:
            self._emit(self._fit(event, redact(fields)))
        except OSError as exc:
            self._fail(f"実行の記録に書けない: {exc}")

    def _fail(self, reason: str) -> None:
        if self.failed is None:
            self.failed = reason
            print(reason, file=sys.stderr)

    def finish(self, obj: dict[str, Any], code: int) -> tuple[dict[str, Any], int]:
        """終わりの行に結果の全体と終了コードを書く。記録に書けなかった実行は結果を stopped にする。

        置き換えた結果は本体の元の結果を `original_result` に持つ（#2338 の決定 3）。操作済みの PR の番号などを
        呼ぶ側が読めるようにし、操作のやり直しによる二重の作成を防ぐ。
        """
        self.write("end", result=obj, exit=code)
        if self.failed is None:
            return obj, code
        stopped = result.stopped(self.entry, "precondition", self.failed)
        stopped["original_result"] = obj
        return stopped, causes.exit_code("precondition")


_CURRENT: list[RunLog] = []


def current() -> Optional[RunLog]:
    """このプロセスで開いた記録。開いていなければ None。"""
    return _CURRENT[0] if _CURRENT else None


def close() -> None:
    """このプロセスの記録を手放す（書いたファイルは残す）。logger `ndf.run` から出力先を外す。"""
    _CURRENT.clear()
    _LOGGER.handlers.clear()


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


@dataclass(frozen=True)
class RunTarget:
    """記録の置き場を決める値。`location` は置き場のリポジトリの名前（None なら作業ディレクトリの名前）。"""

    run_id: str
    parent_id: Optional[str]
    location: Optional[str]
    now: datetime


def _repo_dir(target: RunTarget) -> Path:
    """置き場のリポジトリのディレクトリ。runs の下の 1 階層でなければ、作る・消すの前に `RunLogError`。"""
    name = _safe_name(target.location or Path.cwd().name, f"記録の置き場のリポジトリの名前（{RUNS_DIR} の下の 1 階層）")
    return runs_dir() / name


def _root_path(target: RunTarget) -> Path:
    repo_dir = _repo_dir(target)
    day_dir = repo_dir / target.now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    _prune(repo_dir, target.now.date())
    return day_dir / f"{target.run_id}.jsonl"


def _sink() -> str:
    sink = os.environ.get(ENV_RUN_LOG_SINK, "") or SINKS[0]
    if sink not in SINKS:
        raise RunLogError(f"{ENV_RUN_LOG_SINK} の値 {sink} を知らない（{', '.join(SINKS)}）")
    return sink


def _log_path(target: RunTarget) -> Optional[Path]:
    if _sink() != "file":
        return None
    inherited = os.environ.get(ENV_RUN_LOG, "")
    if target.parent_id and inherited:
        return Path(inherited)
    return _root_path(target)


def _inherited_run_id() -> Optional[str]:
    """親から受けた実行の識別子（環境変数 `NDF_RUN_ID`）。無いか空なら None。"""
    inherited = os.environ.get(ENV_RUN_ID, "")
    return inherited if inherited else None


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
    parent = parent_id or _inherited_run_id()
    rid = _safe_name(run_id or uuid.uuid4().hex, "実行の識別子")
    try:
        path = _log_path(RunTarget(rid, parent, location, now or datetime.now(timezone.utc)))
    except OSError as exc:
        raise RunLogError(f"実行の記録の置き場を作れない: {exc}") from exc
    log = RunLog(run_id=rid, parent_id=parent, entry=entry, sink=StderrSink() if path is None else FileSink(path))
    _LOGGER.handlers[:] = [log.sink]
    log.write("start", pid=os.getpid(), cwd=str(Path.cwd()))
    if log.failed:
        _LOGGER.handlers.clear()
        raise RunLogError(log.failed)
    _CURRENT.append(log)
    return log


def diag(text: str, short: Optional[str] = None, **fields: Any) -> None:
    """診断を標準エラー（`short` があれば `short`）と実行の記録（全文）の両方へ出す。

    標準エラーへ出す文も、記録の行と同じく秘密を伏せてから出す（出力先が stderr のときに
    生の文が伏せた行と並んで流れないように）。
    記録を開いていなくても、親の実行の識別子を受けていれば子の実行として開いてから書く。
    """
    shown = redact(short if short is not None else text)
    sys.stderr.write(shown if shown.endswith("\n") else shown + "\n")
    log = current()
    if log is None and _inherited_run_id():
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


def child_process_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """子のプロセスへ渡す環境。今の環境へ `extra`、開いている記録の `child_env` の順に重ねる（#2338）。"""
    log = current()
    return {**os.environ, **(extra if extra else {}), **(log.child_env() if log else {})}


def record(event: str, **fields: Any) -> None:
    """記録を開いていれば 1 行を書く（開いていなければ何もしない）。"""
    log = current()
    if log is not None:
        log.write(event, **fields)
