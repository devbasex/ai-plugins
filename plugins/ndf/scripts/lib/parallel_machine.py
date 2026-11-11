"""マシンの数え方（#2999）: 同じマシンで動いている並列の計画のプランの本数を数え、上限を超える起動を待たせる。

- 数える計画は、計画の外のセッションの判定と同じ `parallel_guard.live_plans`（自分のメインディレクトリと計画の所在の一覧）が
  動いていると返す計画である。状態を読めない計画は動いている計画として数える。宣言（`plan.json`）は JSON として読み、
  `lanes[].name` と `lanes[].plans` だけを取る（版の違う NDF の計画も数える。決定 8）。宣言を読めない計画は 0 本とし、
  `<置き場>:<計画名>` を出力の `unreadable` に載せる（I13）
- 計画の検査と開始（`declared_plans`・`plan_warnings`）は宣言の合計を数え、超えても警告で通す（決定 3）
- 耐久ワークフローの起動の判定（`admit_lanes`）は、動いているレーン（レーンの状態の一覧で `running`・`awaiting_approval` のもの）と、
  承認の答えを待って止まった宣言のレーン（`stopped` で、計画の台帳に答えていない問いを持つもの。決定 9）の合計を数える。
  数えてから起動するまでを起動の排他（`<状態の根>/parallel/admit.lock`）の中で行う（決定 4）。合計 0 のときは上限を見ずに起動する（I8）

上限の値は `relay_limit.plans_cap`（マシンの設定 → `NDF_PARALLEL_MAX_PLANS` → 既定 6）から読む。保存しない。判定のたびに数え直す。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from state_home import container_home

RULE = "machine-plans"
RUNNING = ("running", "awaiting_approval")
LOCK_POLICY = "parallel-machine.admit-lock"


def cap() -> tuple[int, str, Optional[str]]:
    """(プランの本数の上限, 出所, 読めなかった理由か None)。"""
    import relay_limit

    return relay_limit.plans_cap()


def lock_target() -> Path:
    """起動の排他が守る対象（錠のファイルは `<状態の根>/parallel/admit.lock`）。"""
    return container_home() / "parallel" / "admit"


def _key(home: Path, name: str) -> str:
    from parallel_files import resolved_path

    return f"{resolved_path(home)}:{name}"


def _split_key(key: str) -> tuple[Path, str]:
    home, _, name = key.rpartition(":")
    return Path(home), name


def lanes_of(home: Path, name: str) -> list[tuple[str, int]]:
    """計画の宣言のレーン（名前, プランの本数）。`plans` が正の整数でなければ 1 本。読めなければ OSError か ValueError。"""
    import parallel_plan as pp

    data = json.loads((pp.plan_dir(Path(home), name) / "plan.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{name} の plan.json が JSON のオブジェクトでない")
    out = []
    for ln in data.get("lanes") or []:
        if not isinstance(ln, dict) or not ln.get("name"):
            continue
        n = ln.get("plans")
        out.append((str(ln["name"]), n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else 1))
    return out


def live_keys(main: Path, own: Optional[str] = None, status=None) -> tuple[list[str], list[str]]:
    """(動いている計画, 状態を読めない計画)。`own`（自分の計画名）は状態を読まずに動いているものとして返す。"""
    import parallel_guard

    me = _key(Path(main), own) if own else None
    if status is None:
        import parallel_lane as pl

        status = pl.settled_status
    return parallel_guard.live_plans(Path(main), lambda h, n: "PENDING" if _key(h, n) == me else status(h, n))


def declared_plans(main: Path, own: Optional[str], status=None) -> dict:
    """自分を除く動いている計画の宣言の合計。`plans` は `{"plan", "plans", "state"}` の一覧、`unreadable` は宣言を読めない計画。"""
    running, unknown = live_keys(main, own, status)
    me = _key(Path(main), own) if own else None
    plans, unreadable = [], []
    for key, state in [*((k, "running") for k in running), *((k, "unreadable") for k in unknown)]:
        if key == me:
            continue
        try:
            n = sum(p for _, p in lanes_of(*_split_key(key)))
        except (OSError, ValueError):
            unreadable.append(key)
            continue
        plans.append({"plan": key, "plans": n, "state": state})
    return {"plans": plans, "unreadable": unreadable}


def _asking(home: Path, name: str) -> set[str]:
    """計画の台帳に答えていない問いを持つレーン。読めなければ空。"""
    try:
        import parallel_ledger as plg

        return {q.get("lane") for q in plg.pending_questions(home, name) if isinstance(q, dict) and q.get("lane")}
    except Exception:  # noqa: BLE001 — 台帳を読めない計画の止まったレーンは数えない（動いているレーンは数える）
        return set()


def running_total(main: Path, rows: dict, own: Optional[str] = None, status=None) -> dict:
    """動いている計画（自分を含む）の、動いているレーンと承認の答えを待って止まった宣言のレーンの `plans` の合計。

    `rows` はレーンの状態の一覧（名前 → `{"state": ...}`）。同じ名前のレーンは 1 回だけ数える（ラッパーの名前はマシンで 1 つ）。"""
    running, unknown = live_keys(main, own, status)
    total, lanes, unreadable, seen = 0, [], [], set()
    for key in [*running, *unknown]:
        home, name = _split_key(key)
        try:
            declared_lanes = lanes_of(home, name)
        except (OSError, ValueError):
            unreadable.append(key)
            continue
        asking: Optional[set[str]] = None
        for lane, n in declared_lanes:
            state = (rows.get(lane) or {}).get("state")
            if lane in seen or state is None:
                continue
            if state not in RUNNING:
                if state != "stopped":
                    continue
                asking = _asking(home, name) if asking is None else asking
                if lane not in asking:
                    continue
            seen.add(lane)
            total += n
            lanes.append({"plan": key, "lane": lane, "plans": n, "state": state})
    return {"total": total, "lanes": lanes, "unreadable": unreadable}


def plan_warnings(main: Path, name: str, own_plans: int, status=None) -> list[dict]:
    """検査と開始の警告（0 か 1 件）。他の動いている計画の宣言の合計とこの計画の宣言の合計の和が上限を超えるとき 1 件（I5・I6）。"""
    limit = cap()[0]
    got = declared_plans(main, name, status)
    others = sum(p["plans"] for p in got["plans"])
    if not got["plans"] and not got["unreadable"] or own_plans + others <= limit:
        return []
    total = own_plans + others
    names = "、".join(p["plan"] + ("（状態を読めない）" if p["state"] == "unreadable" else "") for p in got["plans"])
    tail = f"。宣言を読めない計画: {'、'.join(got['unreadable'])}" if got["unreadable"] else ""
    message = f"マシンのプランの本数 {total} が上限 {limit} を超える（この計画 {own_plans}・他の計画 {others}: {names or '-'}）{tail}"
    return [{
        "level": "warning",
        "rule": RULE,
        "at": "lanes",
        "message": message,
        "machine_plans": total,
        "own_plans": own_plans,
        "max_plans": limit,
        "plans": [{"plan": p["plan"], "plans": p["plans"]} for p in got["plans"]],
        "unreadable": got["unreadable"],
    }]  # fmt: skip


def _relay_rows(main: Path) -> Optional[dict]:
    """レーンの状態の一覧（名前 → 行）。読めなければ None。"""
    from parallel_repo import relay_proc

    got, res = relay_proc(Path(main), "ls", "--json")
    if got.returncode != 0:
        return None
    return {it["name"]: it for it in res.get("items") or [] if isinstance(it, dict) and it.get("name")}


def admit_lanes(
    main: Path,
    name: str,
    lanes: list[tuple[str, int]],
    spawn: Callable[[str], dict],
    ls: Optional[Callable[[], Optional[dict]]] = None,
    status=None,
    timeout: Optional[float] = None,
) -> dict:
    """依存のそろったレーン（名前, プランの本数）を、起動の排他の中で数えてから宣言の順に起動する（I7〜I11）。

    レーンごとに `spawn(名前)` の結果（`started` / `refused`）か、`{"waiting": {reason, machine_plans, plans, max_plans, at,
    unreadable}}` を返す。理由は `machine-plans`（上限に当たった）・`lock`（起動の排他を取れなかった）・`observe`
    （レーンの状態の一覧を読めなかった）である。"""
    import limits
    import locks

    limit = cap()[0]

    def wait(reason: str, total: Optional[int], n: int, unreadable: list[str]) -> dict:
        w = {"reason": reason, "machine_plans": total, "plans": n, "max_plans": limit, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "unreadable": unreadable}
        return {"waiting": w}

    timeout = limits.seconds(LOCK_POLICY) if timeout is None else timeout
    out: dict[str, dict] = {}
    try:
        with locks.exclusive(lock_target(), timeout=timeout):
            rows = (ls or (lambda: _relay_rows(main)))()
            if rows is None:  # 一覧を読めない: 他に動いている計画が無ければ今までどおり起動する（自分の計画は I2 で上限以下）
                others = declared_plans(main, name, status)
                if others["plans"] or others["unreadable"]:
                    return {n: wait("observe", None, p, others["unreadable"]) for n, p in lanes}
                return {n: spawn(n) for n, _ in lanes}
            got = running_total(main, rows, name, status)
            total = got["total"]
            for n, p in lanes:
                if total and total + p > limit:
                    out[n] = wait(RULE, total, p, got["unreadable"])
                    continue
                out[n] = spawn(n)
                if out[n].get("started"):
                    total += p
    except locks.LockTimeout:
        return {n: wait("lock", None, p, []) for n, p in lanes}
    return out


def apply_launches(lanes: dict, got: dict) -> list[tuple[str, str]]:
    """起動の結果をレーンの進行（名前 → 進行）へ当て、断られたレーン（名前, 理由）を返す。

    起動の待ちは断りに数えない（I12）。同じ待ちが続く間は最初の時刻（`since`）を残す。起動か断りが来たら待ちを消す。"""
    refused = []
    for n, res in got.items():
        if "waiting" in res:
            w = dict(res["waiting"])
            at = w.pop("at", None)
            lanes[n]["waiting"] = {**w, "since": (lanes[n].get("waiting") or {}).get("since") or at}
            continue
        lanes[n].pop("waiting", None)
        lanes[n].update(res)
        if res.get("refused"):
            refused.append((n, res["refused"]))
    return refused


def waiting_view(lanes: dict) -> dict:
    """起動の待ちの組（レーン → 理由と数）。最初の時刻は比べない。"""
    return {n: {k: v for k, v in st["waiting"].items() if k != "since"} for n, st in lanes.items() if st.get("waiting")}
