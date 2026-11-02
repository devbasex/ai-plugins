"""統括役（#2346）: 同じコンテナの名前つきのラッパーを登録し、tmux で起動し、同時に動かす数を判定する。

統括役は `hub.lock` の排他を持つ前景のラッパーが兼ねる（決定 1）。仕事は前景のラッパーの thread で行い、
端末へは書かない（決定 12）。頼む側（端末の 2 本目の `run`・`spawn`・`restart`・Stop hook の起動のブロック）は
受付箱 `hub/requests/<id>.json` へ依頼を置き、統括役が 0.5 秒ごとに `rename` で受け取って処理し、結果を
`hub/results/<id>.json` へ書く（決定 4）。登録簿 `registry.json` を書くのは統括役だけである（I4）。

状態の読み取り（`scan`・`live`）は頼む側の一覧（`hub_client`）と共有する。読むのは作業ディレクトリの今のファイル
（`relay.lock`・`question`・`log.jsonl`・`name`）だけで、各ラッパーの状態機械には触れない（I5・決定 8）。
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from typing import NamedTuple

from . import claude as cl
from . import tmux
from .common import (
    LOCK_FILE,
    LOG_FILE,
    NAME_FILE,
    QUESTION_FILE,
    _lock,
    _unlock,
    env_num,
    fallback_cwd,
    launcher_path,
    load_json,
    parse_iso,
    read_text,
    relay_running,
    remove,
    stamp,
    state_root,
    write_json_atomic,
)
from .record import _start_rows

HUB_DIR = "hub"
HUB_LOCK = "hub.lock"
HUB_JSON = "hub.json"
REGISTRY = "registry.json"
REQUESTS = "requests"
RESULTS = "results"
HUB_LOG = "hub-log.jsonl"
# 名前の形（I7・決定 13）。tmux はセッション名の `.` を `_` へ置き換え、`:` を対象の区切りに使う
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
POLL = 0.5
RESULT_TTL = 600  # 結果のファイルを残す秒数（頼む側が読まずに終わった分を消す）

# 断りの理由（結果の items[].result と hub-log の reason）
BAD_NAME = "bad-name"
NAME_TAKEN = "name-taken"
LIMIT = "limit"
NOT_FOUND = "not-found"
NO_TMUX = "no-tmux"
TMUX_FAILED = "tmux-failed"


def hub_dir() -> str:
    return os.path.join(state_root(), HUB_DIR)


def hub_path(*names: str) -> str:
    return os.path.join(hub_dir(), *names)


def ensure_dirs() -> None:
    """受付箱と結果の置き場を所有者だけのディレクトリ（0700）で用意する（I8）。"""
    for d in (hub_dir(), hub_path(REQUESTS), hub_path(RESULTS)):
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)


def max_named() -> int:
    """同時に動かすラッパーの数の上限（`NDF_RELAY_MAX_NAMED`。既定 3。決定 5）。"""
    return max(1, int(env_num("NDF_RELAY_MAX_NAMED", 3)))


def wait_seconds() -> float:
    """頼む側が結果を待つ秒数（`NDF_RELAY_HUB_WAIT`。既定 10。決定 14）。"""
    return env_num("NDF_RELAY_HUB_WAIT", 10)


def valid_name(name) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name))


def hub_log(event: str, name: str | None = None, reason: str | None = None) -> None:
    """統括役の記録へ 1 行足す（非機能の運用・保守性）。最初の入力は書かない（I10）。"""
    import locks  # 外部パッケージ（filelock）を使う。読み込みのときには import しない

    try:
        os.makedirs(hub_dir(), mode=0o700, exist_ok=True)
        row = {"at": stamp(), "event": event, "name": name, "reason": reason}
        locks.append_locked(hub_path(HUB_LOG), json.dumps(row, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — 記録の失敗で起動・停止を止めない
        pass


def hub_running() -> bool:
    """`hub.lock` が持たれていれば統括役が居る。"""
    return relay_running(hub_dir()) if os.path.exists(hub_path(HUB_LOCK)) else False


def hub_relay_dir() -> str | None:
    """統括役を兼ねるラッパーの作業ディレクトリ。統括役が居なければ None。"""
    if not hub_running():
        return None
    h = load_json(hub_path(HUB_JSON))
    return h.get("dir") if isinstance(h, dict) and isinstance(h.get("dir"), str) else None


def read_registry() -> dict[str, dict]:
    reg = load_json(hub_path(REGISTRY))
    entries = reg.get("entries") if isinstance(reg, dict) else None
    return {k: v for k, v in entries.items() if isinstance(v, dict)} if isinstance(entries, dict) else {}


# ---------------------------------------------------------------- 状態の読み取り（頼む側と共有）


class Wrapper(NamedTuple):
    """1 つの作業ディレクトリの今の姿。"""

    dir: str
    name: str | None
    running: bool
    question: bool
    start: dict | None  # 最後の start の行
    last_at: str | None  # log.jsonl の最後の行の時刻

    @property
    def started(self) -> float:
        return (parse_iso(self.start.get("at")) if self.start else None) or 0.0


def read_name(d: str) -> str | None:
    s = (read_text(os.path.join(d, NAME_FILE)) or "").strip()
    return s or None


def _last_at(d: str) -> str | None:
    try:
        with open(os.path.join(d, LOG_FILE), "rb") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("at"), str):
            return row["at"]
    return None


def scan() -> list[Wrapper]:
    """状態ディレクトリの作業ディレクトリをすべて読む。統括役の置き場（`hub/`）は作業ディレクトリと読まない。"""
    root = state_root()
    out = []
    for entry in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, entry)
        if entry == HUB_DIR or not os.path.isdir(d):
            continue
        if not (os.path.exists(os.path.join(d, LOCK_FILE)) or os.path.exists(os.path.join(d, LOG_FILE))):
            continue
        out.append(
            Wrapper(
                d,
                read_name(d),
                relay_running(d),
                os.path.exists(os.path.join(d, QUESTION_FILE)),
                next(_start_rows(d), None),
                _last_at(d),
            )
        )
    return out


def pick(ws: list[Wrapper]) -> Wrapper | None:
    """同じ名前の作業ディレクトリから 1 つを選ぶ。動いているもの、無ければ最後の start が最も新しいもの。"""
    running = [w for w in ws if w.running]
    if running:
        return running[0]
    return max(ws, key=lambda w: w.started) if ws else None


def by_name(ws: list[Wrapper]) -> dict[str, Wrapper]:
    groups: dict[str, list[Wrapper]] = {}
    for w in ws:
        if w.name:
            groups.setdefault(w.name, []).append(w)
    return {n: pick(g) for n, g in groups.items()}


class Live(NamedTuple):
    """動いているラッパーの数え方（I3）。`names` は名前の無いものを `-` で含む。"""

    count: int
    names: list[str]
    taken: set[str]  # 名前つきで動いている・起動した直後の名前


def live(ws: list[Wrapper], registry: dict[str, dict], sessions: dict[str, dict]) -> Live:
    """`relay.lock` が取られている作業ディレクトリと、登録簿の行のうち tmux のセッションが在るのに同じ名前の
    作業ディレクトリがまだ `relay.lock` を取っていないもの（起動した直後）の和。重複は名前で除く。"""
    running = [w for w in ws if w.running]
    taken = {w.name for w in running if w.name}
    pending = sorted(n for n, row in registry.items() if n not in taken and row.get("session", n) in sessions)
    names = [w.name or "-" for w in running] + pending
    # tmux のセッションの名前も重ねない（tmux が `duplicate session` で断る）
    return Live(len(running) + len(pending), names, taken | set(sessions))


def restart_input(name: str, row: dict, w: Wrapper | None) -> tuple[str, list[str]]:
    """やり直しの (cwd, 引数)。区間 1 なら登録簿の引数、2 以降は最後の start の行の入力（決定 10）。
    cwd は最後の start の行のもの（区間の切り替えで worktree へ移っている）。"""
    args = [a for a in row.get("args") or [] if isinstance(a, str)]
    start = w.start if w else None
    cwd = (start or {}).get("cwd") or row.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = fallback_cwd(cwd)
    if start and isinstance(start.get("section"), int) and start["section"] >= 2 and start.get("command"):
        args = [*cl.carried_args(args), start["command"]]
    return cwd, args


# ---------------------------------------------------------------- 統括役


def new_id() -> str:
    return f"{time.time_ns():020d}-{os.getpid()}-{random.randint(0, 999999):06d}"


def private(path: str) -> bool:
    """所有者だけが読み書きできる（I8）。"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_uid == os.getuid() and not st.st_mode & 0o077


class Refusal(NamedTuple):
    reason: str
    message: str
    code: int


class Hub:
    """統括役の役目。`claim` で `hub.lock` を取り、`start` で受付箱を読む thread を始める。"""

    def __init__(self, held):
        self.held = held
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    @classmethod
    def claim(cls) -> Hub | None:
        """`hub.lock` を待たずに取る。取れなければ None（統括役が居る。AC22）。"""
        try:
            ensure_dirs()
            held = _lock(hub_path(HUB_LOCK), 0)
        except OSError:
            return None
        return cls(held) if held is not None else None

    def start(self, relay_dir: str, name: str | None) -> None:
        write_json_atomic(hub_path(HUB_JSON), {"pid": os.getpid(), "dir": relay_dir, "claimed_at": stamp()})
        hub_log("claimed", name)
        self.thread = threading.Thread(target=self.run, name="ndf-relay-hub", daemon=True)
        self.thread.start()

    def release(self) -> None:
        self.stopped.set()
        _unlock(self.held)
        self.held = None

    def run(self) -> None:
        try:
            self.cleanup()
            while True:
                self.serve_once()
                if self.stopped.wait(POLL):
                    return
        except Exception as e:  # noqa: BLE001 — 統括役の例外で前景のラッパーを巻き込まない（決定 12）
            hub_log("failed", None, type(e).__name__)
            self.release()

    def cleanup(self) -> None:
        """古い結果のファイルを消す（統括役が替わった直後）。"""
        now = time.time()
        for entry in os.listdir(hub_path(RESULTS)):
            p = hub_path(RESULTS, entry)
            try:
                if now - os.stat(p).st_mtime > RESULT_TTL:
                    remove(p)
            except OSError:
                continue

    def serve_once(self) -> None:
        """受付箱の依頼を古い順に処理する（統括役の居ない間に置かれた依頼も含む）。"""
        box = hub_path(REQUESTS)
        if not private(box):
            hub_log("refused", None, "受付箱の権限が所有者だけでない")
            return
        for entry in sorted(os.listdir(box)):
            if not entry.endswith(".json"):
                continue
            self.handle(entry[: -len(".json")])

    def handle(self, rid: str) -> None:
        path = hub_path(REQUESTS, f"{rid}.json")
        if not private(path):
            hub_log("refused", None, f"依頼 {rid} の権限が所有者だけでない")
            remove(path)
            return
        taken = hub_path(REQUESTS, f"{rid}.taken")
        try:
            os.rename(path, taken)  # 受け取った印。頼む側はこの後は依頼を消せない（決定 14）
        except OSError:
            return
        req = load_json(taken)
        remove(taken)
        res = self.process(req if isinstance(req, dict) else {})
        write_json_atomic(hub_path(RESULTS, f"{rid}.json"), res)

    def process(self, req: dict) -> dict:
        kind = req.get("kind")
        name = req.get("name")
        ws = scan()
        registry = read_registry()
        sessions = tmux.sessions()
        now = live(ws, registry, sessions)
        if kind == "restart":
            row = registry.get(name) if isinstance(name, str) else None
            if row is None:
                return self.refuse(kind, name, Refusal(NOT_FOUND, f"その名前は無い: {name}", 3))
            cwd, args = restart_input(name, row, by_name(ws).get(name))
        elif kind == "spawn":
            cwd = req.get("cwd") if isinstance(req.get("cwd"), str) and os.path.isdir(req["cwd"]) else os.path.expanduser("~")
            args = [a for a in req.get("args") or [] if isinstance(a, str)]
            if name is None:
                name = self.auto_name(now.taken)
        else:
            return self.refuse(kind, name, Refusal("bad-request", f"知らない依頼の種類: {kind}", 2))
        refusal = self.judge(name, now)
        if refusal:
            return self.refuse(kind, name, refusal)
        return self.launch(kind, name, cwd, args, str(req.get("by") or ""), registry)

    @staticmethod
    def auto_name(taken: set[str]) -> str:
        """端末の 2 本目の名前。`t2`・`t3`… のうち動いていない最小の番号（決定 13）。"""
        n = 2
        while f"t{n}" in taken:
            n += 1
        return f"t{n}"

    @staticmethod
    def judge(name, now: Live) -> Refusal | None:
        """名前の形（I7）・重複（I2）・上限（I3）。入口に依らず、ここだけで判定する。"""
        if not valid_name(name):
            return Refusal(BAD_NAME, f"名前の形が違う（{NAME_RE.pattern}）: {name}", 3)
        if name in now.taken:
            return Refusal(NAME_TAKEN, f"名前が重なる: {name} は動いている", 3)
        cap = max_named()
        if now.count >= cap:
            return Refusal(LIMIT, f"同時に動かすラッパーの上限 {cap} に達した（動いている: {', '.join(now.names)}）", 3)
        if tmux.binary() is None:
            return Refusal(NO_TMUX, "tmux が PATH に無い", 3)
        return None

    def launch(self, kind: str, name: str, cwd: str, args: list[str], by: str, registry: dict) -> dict:
        argv = [sys.executable, launcher_path(), "run", *args]
        why = tmux.new_session(name, cwd, argv, {"NDF_RELAY_NAME": name, "NDF_RELAY_SPAWNED": "1"})
        if why:
            return self.refuse(kind, name, Refusal(TMUX_FAILED, why, 1))
        registry[name] = {"session": name, "cwd": cwd, "args": args, "created_at": stamp(), "by": by}
        write_json_atomic(hub_path(REGISTRY), {"version": 1, "entries": registry})
        hub_log("started" if kind == "spawn" else "restarted", name, by or None)
        return outcome(kind, "ok", f"{name} を起動した（relay.py attach {name} で結び付ける）", name, "started", 0)

    @staticmethod
    def refuse(kind, name, r: Refusal) -> dict:
        hub_log("refused", name if isinstance(name, str) else None, r.reason)
        return outcome(kind, "stopped", r.message, name, r.reason, r.code)


def outcome(kind, status: str, summary: str, name, result: str, code: int) -> dict:
    """依頼の結果（step_result の形。`metrics.exit_code` は頼む側が終える終了コード）。"""
    tool = f"relay-{kind}" if kind in ("spawn", "restart") else "relay-hub"
    item = {"name": name if isinstance(name, str) else None, "result": result}
    return {"tool": tool, "status": status, "summary": summary, "items": [item], "metrics": {"exit_code": code}}
