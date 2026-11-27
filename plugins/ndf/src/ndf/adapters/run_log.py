"""1 回の実行を識別子 1 つで追う記録（#1142 の AD3。N10。#2338）。

`open_run` が実行の識別子を振り、利用者の状態の保存先の `runs/<リポジトリ>/<UTC の日付>/<起点の識別子>.jsonl` へ
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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from ndf import identity
from ndf.contracts import causes, facts, limits, result

ENV_RUN_ID = "NDF_RUN_ID"
ENV_RUN_LOG = "NDF_RUN_LOG"
ENV_RUN_LOG_SINK = "NDF_RUN_LOG_SINK"
# 記録の出力先。file は状態の保存先のファイル、stderr は標準エラー（コンテナのログへ流す）
SINKS = ("file", "stderr")
RUNS_DIR = "runs"
# 名前がこの形の環境変数の値は、記録へ書く前に伏せる
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY", re.IGNORECASE)
SECRET_MIN = 8
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
# 秘密に数えない固定の SSH の利用者名（`ssh://git@github.com/...` の `git@`。群 user の終わりが `@` の後ろ）
FIXED_SSH_USER = re.compile(r"(?<![A-Za-z0-9+.-])ssh://(?P<user>git@)github\.com(?![A-Za-z0-9.-])")
# 全文ファイルへ逃がした値の、行に残す先頭の字数
HEAD_CHARS = 200
# JSON に書いた 1 字の最大のバイト数（制御文字の `\u00XX`）。stderr の続きの行の 1 行に入る字数を決める
JSON_CHAR_BYTES = 6
# 保存先のリポジトリの名前・実行の識別子に使えない値（パスの 1 要素だけを許す）
UNSAFE_LOCATION = re.compile(r"[/\\\x00]")
# 書き口の logger（#2338 の決定 1）。ルートの logger へ流さない
LOGGER_NAME = "ndf.run"
_LOGGER = logging.getLogger(LOGGER_NAME)
_LOGGER.propagate = False


class RunLogError(Exception):
    """記録を開けない（保存先に書けない・知らない出力先・既に開いている・保存先の名前や識別子が使えない）。"""


def _safe_name(name: str, what: str) -> str:
    """パスの 1 要素として使える名前を返す。使えなければ、作る・書く・消すの前に `RunLogError`。"""
    if name in ("", ".", "..") or UNSAFE_LOCATION.search(name):
        raise RunLogError(f"{what}に使えない: {name!r}（パスの 1 要素の名前だけ）")
    return name


def secret_values() -> list[str]:
    """伏せる値（名前が秘密の形の環境変数と、環境変数の表で `secret` の値）。長いものから並べ、短い値が長い値の一部を先に伏せないようにする。

    表の `secret` は名前の形に当たらない秘密（ステートマシンの DB の URL は資格情報を含みうる。B8）を拾う。
    """
    marked = set(facts.ids("env", secret=True))
    found = [v for k, v in os.environ.items() if (SECRET_NAME.search(k) or k in marked) and len(v) >= SECRET_MIN]
    return sorted(found, key=len, reverse=True)


def _occurrences(text: str, value: str) -> list[tuple[int, int]]:
    """`value` の一致範囲をすべて返す。重なる一致も拾い（次は一致の開始の 1 字後から探す）、和は `union_spans` がまとめる。"""
    found, at = [], text.find(value)
    while at >= 0:
        found.append((at, at + len(value)))
        at = text.find(value, at + 1)
    return found


def _shape_span(m: re.Match[str], repl: str) -> tuple[int, int]:
    """形の一致のうち伏せる範囲（置き換えが残す前置きの群と、URL の資格情報の後ろの `@` を除く）。"""
    start = m.end(1) if m.re.groups else m.start()
    return start, m.end() - 1 if repl.endswith("@") else m.end()


def union_marked_spans(spans: list[tuple[int, int, bool]]) -> list[tuple[int, int, bool]]:
    """伏せる範囲（始まり・終わり・秘密か）の重なりを和にまとめ、始まりの順に並べる。まとめた範囲は、どれかが秘密なら真。"""
    out: list[tuple[int, int, bool]] = []
    for start, end, mark in sorted(spans):
        if out and start < out[-1][1]:
            last = out[-1]
            out[-1] = (last[0], max(last[1], end), last[2] or mark)
        else:
            out.append((start, end, mark))
    return out


def union_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """重なる範囲を和にまとめ、始まりの順に並べる。"""
    return [(start, end) for start, end, _ in union_marked_spans([(start, end, False) for start, end in spans])]


def secret_spans(text: str, secrets: Optional[list[str]] = None) -> list[tuple[int, int]]:
    """原文の上の秘密の一致範囲（秘密の環境変数の値と `SECRET_SHAPES`）。重なりは和にまとめる（#3959）。

    固定の SSH の利用者名（スキームがちょうど `ssh`・利用者名がちょうど `git`・ホストがちょうど `github.com`）は秘密に数えない。
    """
    secrets = secret_values() if secrets is None else secrets
    fixed = {m.end("user") for m in FIXED_SSH_USER.finditer(text)}
    spans = [span for value in secrets for span in _occurrences(text, value)]
    for pattern, repl in SECRET_SHAPES:
        spans += [_shape_span(m, repl) for m in pattern.finditer(text) if not (repl.endswith("@") and m.end() in fixed)]
    return union_spans(spans)


def replace_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    """重ならない範囲（始まり・終わり・置き換える文。始まりの順）を順に置き換えてつなぐ。"""
    out, at = [], 0
    for start, end, repl in spans:
        out += [text[at:start], repl]
        at = end
    return "".join(out) + text[at:]


def _mask(text: str, secrets: list[str]) -> str:
    return replace_spans(text, [(start, end, MASK) for start, end in secret_spans(text, secrets)])


def redact(value: Any, secrets: Optional[list[str]] = None) -> Any:
    """値の中の、秘密の環境変数の値とトークンの形（`SECRET_SHAPES`）を伏せる。

    辞書はキーも伏せる。配列・タプル・集合は配列にして中を伏せる。バイト列は UTF-8 の文字列に読んで伏せる。
    JSON の値にならないもの（パス・例外など）は、記録へ書くときの文字列（`str`）にしてから伏せる。
    数・真偽値・None だけをそのまま返す（秘密を持ちうる値をパススルーしない。#3394）。
    """
    secrets = secret_values() if secrets is None else secrets
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {_mask(str(k), secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return _mask(bytes(value).decode("utf-8", errors="replace"), secrets)
    return _mask(value if isinstance(value, str) else str(value), secrets)


def _string_slots(value: Any) -> list[tuple[Any, Any]]:
    """入れ子の辞書・配列の中の文字列の保存先（入れ物とキー）を並べる。"""
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
    """状態の保存先のファイルへ、排他の中で 1 行を書き切る。"""

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
        stored = self._store_blob_file(text, self.path) if self.path is not None else self._emit_blob_parts(text, limit)
        return {**ref, **stored}

    def _store_blob_file(self, text: str, path: Path) -> dict[str, Any]:
        """全文を `path` の隣の全文ファイルへ書き、参照（`full_text`）を返す。"""
        blob = path.with_name(f"{path.stem}.{self.run_id}.{self._blobs}.txt")
        blob.write_text(text, encoding="utf-8")
        return {"full_text": str(blob)}

    def _emit_blob_parts(self, text: str, limit: int) -> dict[str, Any]:
        """全文を行の上限に収まる続きの行（event `text_part`）に分けて出し、参照（`blob`・`parts`）を返す。"""
        frame = {"blob": self._blobs, "part": 0, "parts": 0, "text": ""}
        size = max(1, (limit - len(self._line("text_part", frame))) // JSON_CHAR_BYTES)
        parts = [text[i : i + size] for i in range(0, len(text), size)] or [""]
        for n, part in enumerate(parts, 1):
            self._emit(self._line("text_part", {**frame, "part": n, "parts": len(parts), "text": part}))
        return {"blob": self._blobs, "parts": len(parts)}

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
    """記録の保存先の根（利用者の状態の保存先の下）。

    状態の保存先は `identity.prepare_state_dir` を通して用意する。形のバージョンのファイルの無いまま記録を書くと、
    次の用意が旧い保存先と判定して、書いている途中の記録とスロットのロックごと退避するため（#3394）。
    """
    return identity.prepare_state_dir(identity.load()).path / RUNS_DIR


def _prune(repo_dir: Path, today: date) -> None:
    """保持の日数を超えた日付のディレクトリを消す。消せなければ診断を書いて続ける。"""
    keep = timedelta(seconds=limits.seconds("run-log.keep"))
    for day in sorted(repo_dir.iterdir()):
        try:
            old = date.fromisoformat(day.name) < today - keep
        except ValueError:  # noqa: S112 ファイル: 日付の名前でないディレクトリは記録の保存先のものでないので触らない
            continue
        if old:
            try:
                shutil.rmtree(day)
            except OSError as exc:
                diag(f"古い実行の記録を消せない（続ける）: {day}: {exc}")


@dataclass(frozen=True)
class RunTarget:
    """記録の保存先を決める値。`location` は保存先のリポジトリの名前（None なら作業ディレクトリの名前）。"""

    run_id: str
    parent_id: Optional[str]
    location: Optional[str]
    now: datetime


def _repo_dir(target: RunTarget) -> Path:
    """保存先のリポジトリのディレクトリ。runs の下の 1 階層でなければ、作る・消すの前に `RunLogError`。"""
    name = _safe_name(target.location or Path.cwd().name, f"記録の保存先のリポジトリの名前（{RUNS_DIR} の下の 1 階層）")
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
    return inherited if inherited else None  # noqa: NDF501 利用者の入力: 空の NDF_RUN_ID は未設定と同じ


def open_run(
    entry: str,
    run_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    location: Optional[str] = None,
    now: Optional[datetime] = None,
) -> RunLog:
    """実行の記録を開き、start の行を書く。親の識別子は省けば環境変数 `NDF_RUN_ID` から受ける。

    `location` は保存先のリポジトリの名前（省けば作業ディレクトリの名前）。起点の実行は保持の日数を超えた
    日付のディレクトリを消す。開けなければ `RunLogError`（呼び出し元は結果を stopped にする）。
    """
    if _CURRENT:
        raise RunLogError(f"このプロセスは実行の記録を既に開いている: {_CURRENT[0].run_id}")
    parent = parent_id or _inherited_run_id()
    rid = _safe_name(run_id or uuid.uuid4().hex, "実行の識別子")
    try:
        path = _log_path(RunTarget(rid, parent, location, now or datetime.now(timezone.utc)))
    except OSError as exc:
        raise RunLogError(f"実行の記録の保存先を作れない: {exc}") from exc
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


def child_process_env(extra: Optional[Mapping[str, Optional[str]]] = None) -> dict[str, str]:
    """子のプロセスへ渡す環境。今の環境へ `extra`、開いている記録の `child_env` の順に重ねる（#2338）。

    `extra` の値が None の名前は子の環境から外す（#2944。親から継いだ値を子へ渡さない）。
    """
    env = dict(os.environ)
    if extra is not None:
        _overlay_into(env, extra)
    log = current()
    if log:
        env.update(log.child_env())
    return env


@contextmanager
def overlay_env(changes: Mapping[str, Optional[str]]) -> Iterator[None]:
    """このプロセスの環境を、中の処理の間だけ `changes` で上書きし（None の名前は外す）、例外でも元へ戻す（#2944）。"""
    before = {name: os.environ.get(name) for name in changes}
    try:
        _overlay_into(os.environ, changes)
        yield
    finally:
        _overlay_into(os.environ, before)


def _overlay_into(env: MutableMapping[str, str], changes: Mapping[str, Optional[str]]) -> None:
    """`env` へ `changes` を重ねる。値が None の名前は外す。"""
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value


def record(event: str, **fields: Any) -> None:
    """記録を開いていれば 1 行を書く（開いていなければ何もしない）。"""
    log = current()
    if log is not None:
        log.write(event, **fields)


# ---------------------------------------------------------------- `claude -p` の費用のジャーナル（statusline の合算）

COST_JOURNAL_NAME = "print-costs.jsonl"


def cost_journal_path(environ: Mapping[str, str]) -> Path:
    """費用のジャーナルのパス。statusline の `ctx_sessions` の 1 つ上（`NDF_CONTEXT_DIR` → 旧い状態の根の `context`）。"""
    explicit = environ.get("NDF_CONTEXT_DIR")
    base = Path(explicit) if explicit else identity.legacy_state_root(environ) / "context"
    return base / COST_JOURNAL_NAME


def reported_cost(stdout: str) -> Optional[float]:
    """`claude -p` の結果の JSON（`json` は 1 行、`stream-json` は最後の行）の `total_cost_usd`。無い・数でなければ None。"""
    lines = stdout.strip().split("\n")
    try:
        payload = json.loads(lines[-1]) if lines[-1] else None
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 終わる前の出力は JSON でない
        return None
    spent = payload.get("total_cost_usd") if isinstance(payload, dict) else None
    return float(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool) else None


def record_cost(stdout: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """費用が読めればジャーナルへ 1 起動 1 行を足し、真を返す。読めない・書けないときは足さずに偽（起動の結果は変えない）。"""
    cost = reported_cost(stdout)
    if cost is None:
        return False
    env = os.environ if environ is None else environ
    row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "usd": cost, "relay": env.get("NDF_RELAY_NAME")}
    path = cost_journal_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:  # noqa: NDF401 ファイル: 書けなければ足さない。statusline の表示のための記録で、起動の結果は変えない
        return False
    return True


def record_cost_file(out: Path) -> bool:
    """claude の担当の出力のファイルから費用をジャーナルへ足す。読めなければ足さずに偽。"""
    try:
        text = out.read_text(encoding="utf-8", errors="replace")
    except OSError:  # noqa: NDF401 ファイル: 読めない出力は費用を足さない
        return False
    return record_cost(text)
