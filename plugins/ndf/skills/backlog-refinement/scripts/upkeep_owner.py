"""upkeep_owner.py: 見直しの持ち主の宣言（#2315 の決定 6・不変条件 I1・I2）。

    upkeep.py owner claim --what research|review|overhaul [--loop L] [--pid N] [--keepalive SECONDS]
    upkeep.py owner keepalive --run R --pid N --seconds S
    upkeep.py owner touch --run R [--item ID]
    upkeep.py owner release --run R | --pid N --what research|review|overhaul
    upkeep.py owner status

宣言はメインディレクトリの `.ndf/backlog/owner.json` に置き、錠（`owner.json.lock`）の中でだけ作る・書き換える。
有効な宣言は、終わった時刻が無く、最後に動いた時刻から寿命の窓（`depths.owner.ttl`）の内で、同じホストなら pid が
生きているものだけである（別のホストは窓だけで決める）。無効な宣言は次の `claim` が引き取り、`taken_from` に前の持ち主を書く。
部品は `run_owner`（記録・窓・pid の生死）と `locks`（錠）で、同じ役割の部品を作らない。

終了コード: claim は 0 = 持ち主になった / 10 = 他の有効な持ち主がいる（`not_owner`）/ 3 = 錠を 5 秒で取れない。
touch・release は 0 / 10 = 宣言が別の実行のもの（引き取られた）。`release --pid --what` は、宣言の持ち主の pid・ホスト・
`what` が渡した値と同じときだけ手放す（queue の work ステップの後の run ステップが、run を知らずに自分の宣言を手放すため）。
既に手放されていれば 0、別の持ち主のものなら 10。

`claim --keepalive S` は、持ち主になったら背景の見張り（`owner keepalive`）を切り離して起こす。見張りは `owner.beat` ごとに
`touch` し、宣言が手放される・引き取られる・持ち主の pid が止まる・S 秒が過ぎる、のどれかで止まる。queue の work ステップ
（見直し・大整理は最大 5400 秒）のように、作業の中で `touch` を打つ手順を持たない持ち主が寿命（`owner.ttl`）を過ぎないためである。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import locks
import limits
import run_owner
from step_result import EXIT_GATE, EXIT_PRECONDITION, emit, git_root, result
import upkeep_depth as D

TOOL = "backlog-refinement"
WHATS = ("research", "review", "overhaul")
LOCK_WAIT = limits.seconds("backlog-refinement.owner-lock")


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


def release_owned(root, pid: int, what: str) -> str | None:
    """宣言の pid・ホスト・`what` が渡した値と同じなら手放す（既に手放されていれば何もしない）。違えば理由を返す。"""
    path = owner_file(root)
    with locks.exclusive(path, timeout=LOCK_WAIT):
        cur, err = run_owner.read_owner(path)
        mine = cur is not None and cur.get("pid") == int(pid) and cur.get("host") == socket.gethostname() and cur.get("what") == what
        if err or not mine:
            return err or f"宣言は別の持ち主 {(cur or {}).get('run')} のもの"
        if cur.get("finished_at"):
            return None
        cur["seen_at"] = cur["finished_at"] = run_owner.utc_iso(time.time())
        cur["item"] = None
        run_owner.write_owner(path, cur)
    return None


def keepalive(root, run: str, pid: int, seconds: float, sleep=time.sleep, now=time.time) -> str:
    """`owner.beat` ごとに宣言の最後に動いた時刻を書き続ける。止まった理由を返す。

    止まるのは、宣言が `run` のものでなくなった・手放された（`released`）、持ち主の pid が止まった（`owner_gone`）、
    `seconds` が過ぎた（`expired`）ときである。期限を持つのは、作業が `release` を打たずに終わっても宣言を持ち続けないためで、
    期限の後は寿命の窓で無効になる。"""
    beat = D.settings(root)["owner"]["beat"]
    path = owner_file(root)
    until = now() + seconds
    while (left := until - now()) > 0:
        sleep(min(beat, left))
        if not run_owner.owner_pid_alive({"pid": int(pid), "host": socket.gethostname()}):
            return "owner_gone"
        cur, _ = run_owner.read_owner(path)
        if cur is None or cur.get("run") != run or cur.get("finished_at"):
            return "released"
        try:
            if touch(root, run):
                return "released"
        except locks.LockTimeout:
            continue  # 次の拍で書く。1 回書けなくても寿命は拍より十分長い
    return "expired"


def spawn_keepalive(root, run: str, pid: int, seconds: float) -> int:
    """`owner keepalive` を切り離して起こし、その pid を返す。標準の入出力を閉じ、別のセッションに置く
    （起こしたステップの出力の読み取りを待たせず、ステップのプロセスグループの停止に巻き込まれない）。"""
    upkeep = Path(__file__).resolve().with_name("upkeep.py")
    cmd = [sys.executable, str(upkeep), "owner", "keepalive", "--run", run, "--pid", str(int(pid)), "--seconds", str(seconds)]
    p = subprocess.Popen(
        [*cmd, "--root", str(root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return p.pid


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
    metrics = {"run": run, "taken_from": taken, "what": a.what}
    if a.keepalive:
        metrics["keepalive_pid"] = spawn_keepalive(root, run, int(a.pid or os.getppid()), a.keepalive)
    emit(result(TOOL, "ok", f"見直しの持ち主になった: {run}{who}", [], metrics))


def cmd_keepalive(a):
    why = keepalive(git_root(a.root), a.run, a.pid, a.seconds)
    emit(result(TOOL, "ok", f"最後に動いた時刻の更新を止めた: {why}", [], {"run": a.run, "reason": why}))


def _cmd_mark(a, fn, done: str):
    root = git_root(a.root)
    if fn is release_review and a.run is None:
        if a.pid is None or a.what is None:
            emit(result(TOOL, "stopped", "release は --run か、--pid と --what の組を渡す"), EXIT_PRECONDITION)
        why = release_owned(root, a.pid, a.what)
    else:
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
    c.add_argument(
        "--keepalive", type=float, default=None, help="持ち主になったら、この秒数まで owner.beat ごとに touch する見張りを背景で起こす"
    )
    c.set_defaults(func=cmd_claim)
    k = op.add_parser("keepalive", parents=[common])
    k.add_argument("--run", required=True)
    k.add_argument("--pid", type=int, required=True, help="持ち主のプロセス。止まったら見張りも止まる")
    k.add_argument("--seconds", type=float, required=True)
    k.set_defaults(func=cmd_keepalive)
    t = op.add_parser("touch", parents=[common])
    t.add_argument("--run", required=True)
    t.add_argument("--item", default=None)
    t.set_defaults(func=lambda a: _cmd_mark(a, touch, "最後に動いた時刻を書いた"))
    r = op.add_parser("release", parents=[common])
    r.add_argument("--run", default=None)
    r.add_argument("--pid", type=int, default=None, help="--run の代わりに、持ち主の pid と --what で自分の宣言を指す")
    r.add_argument("--what", choices=WHATS, default=None)
    r.set_defaults(func=lambda a: _cmd_mark(a, release_review, "持ち主を解放した"))
    s = op.add_parser("status", parents=[common])
    s.set_defaults(func=cmd_status)
