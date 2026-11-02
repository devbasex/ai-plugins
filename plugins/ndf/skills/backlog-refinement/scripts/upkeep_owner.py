"""upkeep_owner.py: 見直しの持ち主の宣言（#2315 の決定 6・不変条件 I1・I2）。

    upkeep.py owner claim --what research|review|overhaul [--loop L] [--pid N]
    upkeep.py owner touch --run R [--item ID]
    upkeep.py owner release --run R
    upkeep.py owner status

宣言はメインディレクトリの `.ndf/backlog/owner.json` に置き、錠（`owner.json.lock`）の中でだけ作る・書き換える。
有効な宣言は、終わった時刻が無く、最後に動いた時刻から寿命の窓（`depths.owner.ttl`）の内で、同じホストなら pid が
生きているものだけである（別のホストは窓だけで決める）。無効な宣言は次の `claim` が引き取り、`taken_from` に前の持ち主を書く。
部品は `run_owner`（記録・窓・pid の生死）と `locks`（錠）で、同じ役割の部品を作らない。

終了コード: claim は 0 = 持ち主になった / 10 = 他の有効な持ち主がいる（`not_owner`）/ 3 = 錠を 5 秒で取れない。
touch・release は 0 / 10 = 宣言が別の実行のもの（引き取られた）。
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from pathlib import Path

import locks
import run_owner
from step_result import EXIT_GATE, EXIT_PRECONDITION, emit, git_root, result
import upkeep_depth as D

TOOL = "backlog-refinement"
WHATS = ("research", "review", "overhaul")
LOCK_WAIT = 5


def owner_file(root) -> Path:
    return D.backlog_dir(root) / "owner.json"


def valid(path: Path, record: dict | None, ttl: float, now: float | None = None) -> bool:
    """宣言が有効か（I2）。記録が無ければ無効。"""
    return record is not None and run_owner.alive(path, record, time.time() if now is None else now, window=ttl)


def claim_review(root, what: str, loop: str | None = None, pid: int | None = None) -> tuple[str | None, dict | None, dict | None]:
    """(実行の識別子, 引き取り元, 今の有効な持ち主)。持ち主になれなければ識別子は None。錠を取れなければ LockTimeout。"""
    path = owner_file(root)
    ttl = D.settings(root)["owner"]["ttl"]
    with locks.exclusive(path, timeout=LOCK_WAIT):
        cur, _ = run_owner.read_owner(path)
        if valid(path, cur, ttl):
            return None, None, cur
        taken = None
        if cur is not None and not cur.get("finished_at"):
            taken = {"run": cur.get("run"), "loop": cur.get("loop"), "seen_at": cur.get("seen_at")}
        now = run_owner.utc_iso(time.time())
        run = str(uuid.uuid4())
        run_owner.write_owner(
            path,
            {
                "version": 1,
                "kind": "review-owner",
                "run": run,
                "pid": int(pid or os.getppid()),
                "host": socket.gethostname(),
                "loop": loop,
                "what": what,
                "item": None,
                "started_at": now,
                "seen_at": now,
                "finished_at": None,
                "taken_from": taken,
            },
        )
        return run, taken, None


def _mark(root, run: str, finish: bool, item: str | None = None) -> str | None:
    """宣言が `run` のものなら最後に動いた時刻（と今の項目）か終わった時刻を書く。違えば理由を返す。"""
    path = owner_file(root)
    with locks.exclusive(path, timeout=LOCK_WAIT):
        cur, err = run_owner.read_owner(path)
        if err or cur is None or cur.get("run") != run:
            return err or f"宣言は別の実行 {(cur or {}).get('run')} のもの（引き取られた）"
        cur["seen_at"] = run_owner.utc_iso(time.time())
        if finish:
            cur["finished_at"] = cur["seen_at"]
            cur["item"] = None
        elif item is not None:
            cur["item"] = item
        run_owner.write_owner(path, cur)
    return None


def touch(root, run: str, item: str | None = None) -> str | None:
    return _mark(root, run, False, item)


def release_review(root, run: str) -> str | None:
    return _mark(root, run, True)


def review_owner_status(root) -> dict:
    """持ち主の有無・持ち主・生死・最後に動いた時刻（`loop status` が載せる）。"""
    path = owner_file(root)
    cur, err = run_owner.read_owner(path)
    ttl = D.settings(root)["owner"]["ttl"]
    return {
        "path": str(path),
        "owner": cur,
        "alive": valid(path, cur, ttl),
        "seen_at": (cur or {}).get("seen_at"),
        "error": err,
    }


def cmd_claim(a):
    root = git_root(a.root)
    try:
        run, taken, cur = claim_review(root, a.what, a.loop, a.pid)
    except locks.LockTimeout as e:
        emit(result(TOOL, "stopped", f"持ち主の宣言の錠を取れない: {e}"), EXIT_PRECONDITION)
    if run is None:
        emit(
            result(
                TOOL,
                "gate",
                f"見直しの持ち主は別の実行 {cur.get('run')}（最後に動いた時刻 {cur.get('seen_at')}）。持ち主にならず自分の作業を続ける",
                [],
                {"reason": "not_owner", "owner": cur},
                next="持ち主にならない。見直し・調査を始めずに自分の作業を続ける",
            ),
            EXIT_GATE,
        )
    who = f"（{taken['run']} から引き取った）" if taken else ""
    emit(result(TOOL, "ok", f"見直しの持ち主になった: {run}{who}", [], {"run": run, "taken_from": taken, "what": a.what}))


def _cmd_mark(a, fn, done: str):
    root = git_root(a.root)
    why = fn(root, a.run, a.item) if fn is touch else fn(root, a.run)
    if why:
        emit(result(TOOL, "gate", why, [], {"reason": "not_owner"}, next="持ち主でなくなった。見直し・調査の続きをせずに終える"), EXIT_GATE)
    emit(result(TOOL, "ok", done, [], {"run": a.run}))


def cmd_status(a):
    st = review_owner_status(git_root(a.root))
    o = st["owner"] or {}
    summary = (
        f"持ち主 {o.get('run')}（{'有効' if st['alive'] else '無効'}・最後に動いた時刻 {o.get('seen_at')}）" if o else "持ち主はいない"
    )
    emit(result(TOOL, "ok", summary, [], st))


def add_owner_parsers(sub, common) -> None:
    op = sub.add_parser("owner").add_subparsers(dest="owner_cmd", required=True)
    c = op.add_parser("claim", parents=[common])
    c.add_argument("--what", choices=WHATS, required=True)
    c.add_argument("--loop", default=None)
    c.add_argument("--pid", type=int, default=None, help="持ち主のプロセス（省くと呼び出し元。queue のステップなら supervise.py）")
    c.set_defaults(func=cmd_claim)
    t = op.add_parser("touch", parents=[common])
    t.add_argument("--run", required=True)
    t.add_argument("--item", default=None)
    t.set_defaults(func=lambda a: _cmd_mark(a, touch, "最後に動いた時刻を書いた"))
    r = op.add_parser("release", parents=[common])
    r.add_argument("--run", required=True)
    r.set_defaults(func=lambda a: _cmd_mark(a, release_review, "持ち主を解放した"))
    s = op.add_parser("status", parents=[common])
    s.set_defaults(func=cmd_status)
