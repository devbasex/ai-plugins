"""並列の計画のレーンの側（#2436）: 自分が属する計画の判定（I12）・すでに配布済みかの判定（I15）・配布の依頼を送る・
答えを読む（待たずに読むのと待つのの両方）・プランの本数を返す。

耐久の記録は外のプロセスとして `durable.py` の `send_to`・`event_of`・`status_of` で開く（DBOS を起動しない）。
DBOS を読まずに済む判定（計画の有無・本数）は、耐久の記録のファイルが無ければ DBOS を import しない。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Optional

import parallel_plan as pp

KIND = "parallel"
RUNNING = ("PENDING", "ENQUEUED")


def workflow_id(name: str) -> str:
    """耐久ワークフローの ID（計画名から決まる）。"""
    return f"parallel-{name}"


def identity(main: Path, name: str) -> str:
    """耐久の記録の識別（計画の置き場の絶対パス）。"""
    return str(pp.plan_dir(main, name).resolve())


def record_exists(main: Path, name: str) -> bool:
    from durable_keys import record_path

    return record_path(KIND, identity(main, name)).exists()


def run_status(main: Path, name: str) -> Optional[str]:
    """計画の耐久ワークフローの状態（記録が無ければ None）。"""
    if not record_exists(main, name):
        return None
    import durable

    return durable.status_of(KIND, identity(main, name), workflow_id(name))


def plans_in(main: Path) -> list[str]:
    root = Path(main) / pp.PARALLEL_DIR
    return sorted(p.name for p in root.iterdir() if (p / "plan.json").is_file()) if root.is_dir() else []


def lane_of(main: Path, lane: Optional[str] = None, status=run_status) -> Optional[tuple[str, pp.Lane]]:
    """`NDF_RELAY_NAME` の名前が、耐久ワークフローが終わっていない計画のレーンにあれば (計画名, レーン)。無ければ None（I12）。"""
    lane = lane if lane is not None else os.environ.get("NDF_RELAY_NAME")
    if not lane:
        return None
    for name in plans_in(main):
        try:
            plan = pp.ParallelPlan.model_validate(pp.load(main, name), context={"max_plans": 10**6, "max_named": 10**6})
        except (OSError, ValueError):
            continue
        hit = plan.lane(lane)
        if hit is not None and status(main, name) in RUNNING:
            return name, hit
    return None


def lane_plans(main: Path, lane: Optional[str] = None) -> Optional[int]:
    """レーンなら、そのレーンの同時に流すプランの本数。レーンでなければ None（`queue --max` の既定）。"""
    got = lane_of(main, lane)
    return got[1].plans if got else None


def merged_on_main(root: Path, prs: list[int], run=subprocess.run) -> bool:
    """`prs` のマージのコミットがすべて `origin/main` にあるか（I15）。1 本でも分からなければ False。"""
    if not prs:
        return False
    for n in prs:
        got = run(["gh", "pr", "view", str(n), "--json", "mergeCommit", "-q", ".mergeCommit.oid"], cwd=root, capture_output=True, text=True)
        sha = (got.stdout or "").strip()
        if got.returncode != 0 or not sha:
            return False
        anc = run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=root, capture_output=True, text=True)
        if anc.returncode != 0:
            return False
    return True


def released_version(main: Path, name: str, prs: list[int]) -> Optional[str]:
    """`prs` を含む範囲のイベント `range-<連番>` の版（無ければ None）。"""
    import durable

    ident, wid = identity(main, name), workflow_id(name)
    n = 1
    found = None
    while True:
        ev = durable.event_of(KIND, ident, wid, f"range-{n}", 0)
        if not isinstance(ev, dict):
            return found
        if set(prs) & set(ev.get("prs") or []):
            found = ev.get("version")
        n += 1


def request_id(state: Path, lane: str) -> str:
    """依頼の ID。最初の実行で `<レーン名>-r<乱数 12 桁>` を作って `work/request-id` へ書き、打ち直しでは読む。"""
    p = Path(state) / "work" / "request-id"
    if p.is_file() and p.read_text(encoding="utf-8").strip():
        return p.read_text(encoding="utf-8").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    rid = f"{lane}-r{secrets.randbelow(10**12):012d}"
    p.write_text(rid + "\n", encoding="utf-8")
    return rid


def send_request(main: Path, name: str, lane: str, prs: list[int], issues: list[int], state: Path) -> str:
    """配布の依頼を話題 `inbox` へ送る。冪等の鍵は `<ID>:inbox:<依頼>`。依頼の ID を返す。"""
    import durable

    rid = request_id(state, lane)
    wid = workflow_id(name)
    msg = {
        "kind": "request",
        "id": rid,
        "lane": lane,
        "prs": list(prs),
        "issues": list(issues),
        "sprint_state": str(state),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    durable.send_to(KIND, identity(main, name), wid, msg, "inbox", f"{wid}:inbox:{rid}")
    return rid


def read_answer(main: Path, name: str, rid: str, timeout: float = 0) -> tuple[Optional[dict], Optional[dict]]:
    """(答え `answer-<依頼>`, 知らせ `defer-<依頼>`)。`timeout` 秒まで答えを待つ（0 は待たない）。知らせでは返らない（I10）。"""
    import durable

    ident, wid = identity(main, name), workflow_id(name)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        ans = durable.event_of(KIND, ident, wid, f"answer-{rid}", 0)
        if isinstance(ans, dict):
            return ans, None
        if time.monotonic() >= deadline:
            dfr = durable.event_of(KIND, ident, wid, f"defer-{rid}", 0)
            return None, dfr if isinstance(dfr, dict) else None
        time.sleep(min(5.0, max(0.2, deadline - time.monotonic())))


def write_out(out: Path, data: dict) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
