"""runner-trial の候補に共通の部品: プランの雛形・偽物のステップ・遷移・進捗ログ・資源の枠。

候補（LangGraph・Burr・DBOS）は、ここの `route` を遷移の規則として使い、ステップの実行を
`exec_fake` に任せる。候補ごとに違うのは、状態の持ち方・再開・承認ゲートの止め方・キューである。
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

LIMIT = 20  # supervise.py new impl の "上限"（実行するステップの数の上限）
GATE_CODES = range(10, 20)

# supervise.py new impl が書くステップの並び（prompt と cmd を除く）。ready は承認ゲートを試すため
# gate_next を持つ。pr と merge は GitHub の GraphQL の枠を使う（資源のタグ）
PLAN_STEPS = [
    {"id": "impl", "type": "work", "next": "sync"},
    {"id": "sync", "type": "run", "on_fail": "fix-sync", "next": "test-limited"},
    {"id": "fix-sync", "type": "work", "next": "sync"},
    {"id": "test-limited", "type": "run", "on_fail": "judge", "next": "pr"},
    {"id": "judge", "type": "judge", "choices": ["fix", "pr", "doc-lint", "stop"]},
    {"id": "fix", "type": "work", "next": "test-limited"},
    {"id": "pr", "type": "pr", "next": "test-all", "resources": ["graphql"]},
    {"id": "test-all", "type": "run", "on_fail": "judge", "next": "doc-lint"},
    {"id": "doc-lint", "type": "run", "on_fail": "fix-doc", "next": "ready"},
    {"id": "fix-doc", "type": "work", "next": "doc-lint"},
    {"id": "ready", "type": "run", "next": "merge", "gate_next": "merge"},
    {"id": "merge", "type": "run", "next": "end", "resources": ["graphql"]},
]
STEP = {s["id"]: s for s in PLAN_STEPS}
FIRST = PLAN_STEPS[0]["id"]
TERMINAL = ("end", "stop", "limit")

# シナリオ: ステップ → 訪問ごとの終了コード（judge は選んだ答え）。書かない訪問は 0（judge は pr）
SCENARIOS = {
    "pass": {},
    "branch": {"sync": [1, 0], "test-limited": [1, 0], "judge": ["fix", "doc-lint"], "test-all": [1],
               "doc-lint": [1, 0]},
    "loop": {"test-limited": [1] * 99, "judge": ["fix"] * 99},
    "gate": {"ready": [10]},
    "stop": {"test-limited": [1], "judge": ["stop"]},
    "slow": {},
}
SLEEP = float(os.environ.get("RT_SLEEP", "0.2"))
SLOW = {"slow": ("test-limited", 4.0)}  # kill -9 を打つ間だけ長いステップ


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def route(sid: str, code: int | None, decision: str | None) -> tuple[str, str]:
    """(種類, 行き先) を返す。種類は step / end / stop / gate。supervise.py の遷移の規則と同じ。"""
    st = STEP[sid]
    if st["type"] == "judge":
        if decision == "stop":
            return "stop", sid
        if decision == "gate":
            return "gate", sid
        return "step", decision or "pr"
    if st["type"] == "run" and code in GATE_CODES:
        return "gate", st.get("gate_next") or st["next"]
    if code not in (0, None):
        return ("step", st["on_fail"]) if st.get("on_fail") else ("stop", sid)
    nxt = st.get("next", "end")
    return ("end", "end") if nxt == "end" else ("step", nxt)


def scripted(scenario: str, sid: str, visit: int):
    seq = SCENARIOS[scenario].get(sid, [])
    if visit < len(seq):
        return seq[visit]
    return "pr" if STEP[sid]["type"] == "judge" else 0


class Slots:
    """資源のタグごとの同時数の上限。プロセスをまたぐため、枠の数だけのファイルの flock で持つ。"""

    def __init__(self, root: Path, limits: dict[str, int]):
        self.root, self.limits = root, limits
        root.mkdir(parents=True, exist_ok=True)

    def acquire(self, tags: list[str]) -> list:
        held = []
        for tag in tags:
            n = self.limits.get(tag)
            if not n:
                continue
            while True:
                for i in range(n):
                    f = open(self.root / f"{tag}-{i}.lock", "w")
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held.append(f)
                        break
                    except BlockingIOError:
                        f.close()
                else:
                    time.sleep(0.05)
                    continue
                break
        return held

    @staticmethod
    def release(held: list) -> None:
        for f in held:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()


class Ctx:
    """1 本のプランの実行の文脈: 状態ディレクトリ・シナリオ・進捗ログ。"""

    def __init__(self, state: Path, plan: str, scenario: str, slots: Slots | None = None):
        self.state, self.plan, self.scenario, self.slots = state, plan, scenario, slots
        state.mkdir(parents=True, exist_ok=True)
        self.progress = state / "progress.jsonl"
        self.events = state.parent / "events.jsonl"  # プランをまたいだ開始と終わり（同時の本数を数える）

    def write(self, path: Path, rec: dict) -> None:
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def progress_line(self, kind: str, **rec) -> None:
        self.write(self.progress, {"kind": kind, "at": iso_now(), **rec})

    def event(self, what: str, **rec) -> None:
        self.write(self.events, {"t": time.time(), "pid": os.getpid(), "plan": self.plan, "what": what, **rec})

    def exec_fake(self, sid: str, visit: int, use_slots: bool = True) -> tuple[int | None, str | None]:
        """偽物のステップを子プロセスの sleep と決めた終了コードで流す。(終了コード, judge の答え)。"""
        st = STEP[sid]
        value = scripted(self.scenario, sid, visit)
        code, decision = (None, value) if st["type"] == "judge" else (int(value), None)
        secs = SLOW[self.scenario][1] if self.scenario in SLOW and SLOW[self.scenario][0] == sid else SLEEP
        held = self.slots.acquire(st.get("resources", [])) if (use_slots and self.slots) else []
        t0 = time.time()
        self.event("start", step=sid, visit=visit, resources=st.get("resources", []))
        try:
            subprocess.run(["sh", "-c", f"sleep {secs}; exit {code or 0}"], check=False)
        finally:
            self.event("end", step=sid, visit=visit, resources=st.get("resources", []))
            Slots.release(held)
        kind, target = route(sid, code, decision)
        self.progress_line("step", step=sid, type=st["type"], exit=code, seconds=round(time.time() - t0, 2),
                           cost=0, next=target if kind == "step" else kind,
                           summary=f"visit {visit}" + (f" decision {decision}" if decision else ""))
        return code, decision

    def attention(self, step: str, reason: str, text: str) -> None:
        self.progress_line("attention", step=step, reason=reason, text=text)

    def finish(self, status: str, step: str, count: int, path: list[str]) -> dict:
        """プランの終わり。status は done / stopped / gate / limit。結果を <state>/outcome.json へ書く。"""
        if status == "gate":
            # reason の値は supervise.py の attention の今の契約（#1166 の旧い語のまま読まれる）
            self.attention(step, "関門", f"ステップ {step} が承認を待つ")
        elif status in ("stopped", "limit"):
            self.attention(step, "止まった", f"ステップ {step} で止まった（{status}・{count} ステップ）")
        out = {"plan": self.plan, "status": status, "step": step, "count": count, "path": path}
        (self.state / "outcome.json").write_text(json.dumps(out, ensure_ascii=False))
        return out


def own_listen_ports() -> list[int]:
    """このプロセスが待ち受ける TCP のポート（サーバーを立てていないことを確かめる）。"""
    inodes = set()
    for fd in Path("/proc/self/fd").iterdir():
        try:
            link = os.readlink(fd)
        except OSError:
            continue
        if link.startswith("socket:["):
            inodes.add(link[8:-1])
    ports = []
    for name in ("tcp", "tcp6"):
        p = Path("/proc/net") / name
        if not p.is_file():
            continue
        for line in p.read_text().splitlines()[1:]:
            cols = line.split()
            if cols[3] == "0A" and cols[9] in inodes:
                ports.append(int(cols[1].split(":")[1], 16))
    return ports


def reference_path(scenario: str) -> tuple[str, list[str]]:
    """supervise.py の規則どおりに遷移を辿った (終わりの status, 通ったステップ)。承認ゲートは承認した扱い。"""
    visits: dict[str, int] = {}
    cur, path = FIRST, []
    while True:
        v = visits.get(cur, 0)
        value = scripted(scenario, cur, v)
        code, decision = (None, value) if STEP[cur]["type"] == "judge" else (int(value), None)
        visits[cur] = v + 1
        path.append(cur)
        kind, target = route(cur, code, decision)
        if kind in ("end", "stop"):
            return ("done" if kind == "end" else "stopped"), path
        if len(path) >= LIMIT:
            return "limit", path
        cur = target
