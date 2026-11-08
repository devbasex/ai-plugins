"""持ち主の部品（#2342）。持ち主の鍵ごとに 1 つの持ち主の記録を、鍵ごとの排他の中で取り・返し・引き取る。

置き場は `NDF_LEASE_DIR`（無ければ `<コンテナの状態の根>/leases`）の `<owner>--<repo>/<鍵>.json` で、鍵の `/`・`:`・`=` は
`%2F`・`%3A`・`%3D` にしてファイル名にする。排他は同じ名前に `.lock` を付けた錠のファイルで取り、錠は消さない（`locks.py`）。

    out = lease.acquire(decl, key, entry, run, repo)   # acquired / reclaimed / inherited / refused / no_session
    lease.touch(key, holder, repo)                      # 最後に動いた時刻
    lease.release(key, holder, repo)                    # 終わった時刻（持ち主のプロセスが holder のときだけ）
    lease.read(key, repo)                               # 記録か None
    lease.prune(days)                                   # 終わった時刻から days 日を過ぎた記録（.json）を消す

生死の判定（I4）: 同じホストでは記録の pid が動いていて、その開始時刻が 1 秒未満で一致するときだけ生きている。
別のホストでは最後に動いた時刻から寿命の窓の中を生きているとみなす。記録の持ち主のプロセスが自分か自分の祖先なら、
鍵を取り直さずに使う（`inherited`。I6）。生きた持ち主がいても例外を投げず、`refused` を返す。外部コマンドを起こさない。

使う側は `deps.require("locks", "procs")` を先に呼ぶ。実行の核（`run_core.hold`）だけが取る・返すを呼ぶ。
"""

from __future__ import annotations

import configparser
import datetime as _dt
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonio
import locks
import procs
import state_home

VERSION = 1
LEASE_DIR_ENV = "NDF_LEASE_DIR"
KEEP_DAYS = 30  # 終わった記録を消すまでの日数（`durable.KEEP_DAYS` と同じ値）
UNITS = ("run", "session")
FIELDS = ("version", "key", "entry", "run", "started_at", "seen_at", "finished_at", "pid", "pid_started", "host")
LOCK_TIMEOUT = 10.0  # 鍵の排他を待つ上限の秒（読んで書くだけの短い区間なので、超えるのは錠の持ち主が止まったとき）
START_SLACK = 1.0  # 開始時刻の一致を見る幅の秒（I4）
_QUOTE = {"%": "%25", "/": "%2F", ":": "%3A", "=": "%3D"}


class LeaseError(RuntimeError):
    """記録を読めない・書けない（壊れた記録・排他の時間切れ・形の違い）。"""


@dataclass(frozen=True)
class Decl:
    """持ち主の契約（入口の契約の行の `lease` の 1 副命令分）。"""

    key: str
    unit: str
    window_seconds: int
    release: bool = False

    @classmethod
    def of(cls, raw: dict) -> "Decl":
        return cls(str(raw["key"]), str(raw["unit"]), int(raw["window_seconds"]), bool(raw.get("release", False)))

    def key_of(self, inputs: dict[str, Any]) -> str:
        """鍵の形を入力で埋める。足りない入力は KeyError。"""
        return self.key.format_map({k: v for k, v in inputs.items() if v is not None and v != ""})


@dataclass
class Outcome:
    """取った結果。核が結果 JSON の `lease` へ写す。"""

    key: str
    outcome: str
    holder_run: str | None = None
    holder_pid: int | None = None
    holder_seen_at: str | None = None
    holder: tuple[int, float] | None = field(default=None, repr=False)  # 返すときの持ち主のプロセス（自分が持ち主のときだけ）
    repo: str = field(default="", repr=False)

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "outcome": self.outcome}
        for k in ("holder_run", "holder_pid", "holder_seen_at"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        return out


# --- 置き場 ---------------------------------------------------------------------------------


def root() -> Path:
    raw = os.environ.get(LEASE_DIR_ENV)
    return Path(raw) if raw else state_home.container_home() / "leases"


def _origin_url(common: Path) -> str:
    cp = configparser.RawConfigParser(strict=False)
    try:
        cp.read(common / "config", encoding="utf-8")
        return cp.get('remote "origin"', "url", fallback="")
    except (configparser.Error, OSError):
        return ""


def repo_slug(start: Path | str | None = None) -> str:
    """`start`（省けば作業ディレクトリ）のリポジトリの `<owner>--<repo>`。origin が無ければ根の名前、git の外は `_none`。

    git を起動せずに `.git` と config を読む（同じリポジトリの別の clone と worktree で同じ値になる）。"""
    import repo as repo_lib  # 読む時点を遅らせる（鍵を取らない入口は読まない）

    got = state_home.find_git_dir(Path(start) if start is not None else Path.cwd())
    if got is None:
        return "_none"
    return repo_lib.slug(repo_lib.owner_repo_from_url(_origin_url(got[1]))) or f"_local--{got[0].name}"


def file_name(key: str) -> str:
    return "".join(_QUOTE.get(c, c) for c in key) + ".json"


def path_of(key: str, repo: str) -> Path:
    return root() / repo / file_name(key)


# --- 記録 -----------------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _check(record: dict[str, Any]) -> None:
    """I5: 記録が持つのは FIELDS だけ。余分な鍵・欠けた鍵は書かない。"""
    extra, missing = set(record) - set(FIELDS), set(FIELDS) - set(record)
    if extra or missing:
        raise LeaseError(f"持ち主の記録の形が違う（余分 {sorted(extra)}・欠け {sorted(missing)}）")


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LeaseError(f"持ち主の記録を読めない（{path}: {e}）") from None
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int) or not record.get("key"):
        raise LeaseError(f"持ち主の記録の形が違う（{path}）")
    return record


def _store(path: Path, record: dict[str, Any]) -> None:
    _check(record)
    jsonio.write_atomic(path, record)


def read(key: str, repo: str) -> dict[str, Any] | None:
    """鍵の記録。無ければ None、壊れていれば LeaseError。"""
    return _load(path_of(key, repo))


def _same(pid_started: Any, started: float | None) -> bool:
    return isinstance(pid_started, (int, float)) and started is not None and abs(float(pid_started) - started) < START_SLACK


def holder_alive(record: dict[str, Any], window: float, now: float | None = None) -> bool:
    """記録の持ち主が生きているか（I4）。終わった記録は死んでいる。"""
    if record.get("finished_at"):
        return False
    if record.get("host") != socket.gethostname():
        seen = _epoch(record.get("seen_at")) or _epoch(record.get("started_at")) or 0.0
        return (time.time() if now is None else now) - seen < window
    pid = record.get("pid")
    return isinstance(pid, int) and procs.pid_alive(pid) and _same(record.get("pid_started"), procs.start_time(pid))


def _own_line(holder: tuple[int, float]) -> list[tuple[int, float]]:
    """自分の系列（自分の持ち主のプロセス・自分・祖先）の (pid, 開始時刻)。"""
    me = os.getpid()
    line = [holder, *procs.ancestors(me)]
    started = procs.start_time(me)
    if started is not None:
        line.append((me, started))
    return line


def _is_own(record: dict[str, Any], line: list[tuple[int, float]]) -> bool:
    if record.get("host") != socket.gethostname():
        return False
    return any(record.get("pid") == pid and _same(record.get("pid_started"), started) for pid, started in line)


def holder_of(unit: str) -> tuple[int, float] | None:
    """単位ごとの持ち主のプロセス。`run` は自分、`session` はランタイムのセッション（無ければ None）。"""
    if unit == "session":
        return procs.runtime_session()
    me = os.getpid()
    started = procs.start_time(me)
    return (me, started if started is not None else round(time.time(), 2))


def acquire(decl: Decl, key: str, entry: str, run: str, repo: str) -> Outcome:
    """鍵の排他の中で記録を読み、生死を決めて取る（E2〜E5）。壊れた記録・排他の時間切れは LeaseError。"""
    holder = holder_of(decl.unit)
    if holder is None:
        return Outcome(key, "no_session", repo=repo)
    path = path_of(key, repo)
    try:
        with locks.exclusive(path, timeout=LOCK_TIMEOUT):
            return _acquire_locked(decl, key, entry, run, repo, holder, path)
    except locks.LockTimeout as e:
        raise LeaseError(str(e)) from None


def _acquire_locked(decl: Decl, key: str, entry: str, run: str, repo: str, holder: tuple[int, float], path: Path) -> Outcome:
    record = _load(path)
    now = _now_iso()
    if record is not None and not record.get("finished_at"):
        if _is_own(record, _own_line(holder)):
            record["seen_at"] = now
            _store(path, record)
            return Outcome(key, "inherited", holder=holder, repo=repo)
        held = Outcome(key, "refused", record.get("run"), record.get("pid"), record.get("seen_at"), repo=repo)
        if holder_alive(record, decl.window_seconds):
            return held
        outcome = "reclaimed"
    else:
        outcome, held = "acquired", None
    fresh = {
        "version": VERSION,
        "key": key,
        "entry": entry,
        "run": run,
        "started_at": now,
        "seen_at": now,
        "finished_at": None,
        "pid": holder[0],
        "pid_started": holder[1],
        "host": socket.gethostname(),
    }
    _store(path, fresh)
    if held is not None:
        return Outcome(key, outcome, held.holder_run, held.holder_pid, held.holder_seen_at, holder=holder, repo=repo)
    return Outcome(key, outcome, holder=holder, repo=repo)


def _update(key: str, holder: tuple[int, float] | None, repo: str, finished: bool) -> bool:
    if holder is None:
        return False
    path = path_of(key, repo)
    try:
        with locks.exclusive(path, timeout=LOCK_TIMEOUT):
            record = _load(path)
            if record is None or record.get("pid") != holder[0] or not _same(record.get("pid_started"), holder[1]):
                return False
            if record.get("finished_at"):
                return False
            now = _now_iso()
            record["seen_at"] = now
            if finished:
                record["finished_at"] = now
            _store(path, record)
            return True
    except (locks.LockTimeout, LeaseError):
        return False


def touch(key: str, holder: tuple[int, float] | None, repo: str) -> bool:
    """記録の持ち主のプロセスが holder のときだけ、最後に動いた時刻を書く。書いたか。"""
    return _update(key, holder, repo, finished=False)


def release(key: str, holder: tuple[int, float] | None, repo: str) -> bool:
    """記録の持ち主のプロセスが holder のときだけ、終わった時刻を書く（E7）。書いたか。"""
    return _update(key, holder, repo, finished=True)


def prune(days: float = KEEP_DAYS, now: float | None = None) -> list[str]:
    """終わった時刻から days 日を過ぎた記録（`.json`）を消し、消したパスを返す。錠の `.lock` は残す。"""
    base, limit = root(), (time.time() if now is None else now) - days * 86400
    removed: list[str] = []
    if not base.is_dir():
        return removed
    for f in sorted(base.glob("*/*.json")):
        try:
            with locks.exclusive(f, timeout=0):
                record = _load(f)
                done = _epoch((record or {}).get("finished_at"))
                if done is not None and done < limit:
                    f.unlink(missing_ok=True)
                    removed.append(str(f))
        except (locks.LockTimeout, LeaseError, OSError):
            continue
    return removed
