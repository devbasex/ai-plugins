"""並列の進行（#2436）: 耐久ワークフロー `parallel-<計画名>` と耐久ステップ（起動・観測・停止・配布を組む・要約）。

耐久ワークフローの中の分岐は耐久ステップの結果とメッセージだけで決まる。時刻・外のコマンド・ファイルを読むのは
すべて耐久ステップである（打ち直しで同じ道を通るため。#1142 の I18）。外へ見せるのはイベントと出力だけである。

- メッセージ `inbox`: `{kind: request | gate1 | gate2 | ask | stop, ...}`（レーン・一時レーン・`stop`）
- メッセージ `resume`: `{n, phase: confirm, take}` / `{n, phase: settle, result, version, reason}`（`confirm`・`settle`）
- イベント `pause`: `{seq, action}`（`running` / `notice` / `release` / `confirmed`）、`questions`: `{seq, items}`
- イベント `answer-<依頼>`（最終の答え。1 回だけ）・`defer-<依頼>`（次の配布へ回した知らせ）・`range-<連番>`・`lane-<名>`

統括役の CLI（`relay.py spawn`・`ls`・`stop`・`role`）は子プロセスで打ち、統一 JSON レポートだけを読む。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import durable
import parallel_lane as pl
import parallel_plan as pp
import parallel_release as pr

HERE = Path(__file__).resolve().parent.parent
RELAY = HERE / "relay.py"
SUPERVISE = f"python3 {HERE / 'supervise.py'}"


def poll_seconds() -> float:
    """`inbox` と `resume` を待つ秒（既定 60。テストは `NDF_PARALLEL_POLL` で縮める）。"""
    try:
        return max(0.1, float(os.environ.get("NDF_PARALLEL_POLL") or 60))
    except ValueError:
        return 60.0


def relay_argv() -> list[str]:
    """統括役の CLI の打ち方。`NDF_PARALLEL_RELAY` があればその 1 行（テストの差し替え）。"""
    alt = os.environ.get("NDF_PARALLEL_RELAY")
    return alt.split() if alt else [sys.executable, str(RELAY)]


def relay(main: Path, *args: str) -> tuple[int, dict]:
    """統括役の CLI を打ち、(終了コード, 統一 JSON レポート) を返す。読めなければ空の dict。"""
    got = subprocess.run([*relay_argv(), *args], cwd=main, capture_output=True, text=True)
    lines = [ln for ln in (got.stdout or "").splitlines() if ln.strip().startswith("{")]
    try:
        return got.returncode, json.loads(lines[-1]) if lines else {}
    except ValueError:
        return got.returncode, {}


def base_tracking_ref(main: Path) -> str:
    """ベースブランチ（`.ndf/worktree.json` の `base_branch` → 既定ブランチ）の `origin/<名>`。決まらなければ空。"""
    import repo

    b = repo.base_branch(main)
    return f"origin/{b}" if b else ""


def release_plugin(main: Path) -> str:
    """配るプラグイン（`.ndf/supervise.json` の `release.plugin`）。無ければ空。"""
    from supervise_lib.decl import read_decl

    return str((read_decl([Path(main)], "supervise.json").get("release") or {}).get("plugin") or "")


def git_head(main: Path) -> str:
    """ベースブランチの先頭のコミット。ベースブランチが決まらなければ空。"""
    ref = base_tracking_ref(main)
    if not ref:
        return ""
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=main, capture_output=True)
    got = subprocess.run(["git", "rev-parse", ref], cwd=main, capture_output=True, text=True)
    return (got.stdout or "").strip()


def dev_version(main: Path) -> str:
    """ベースブランチの配るプラグインの `plugin.json` の開発版の版数から接尾辞を外した版（出す版）。決まらなければ空。"""
    import re

    from release_version import plugin_json_rel

    ref, plugin = base_tracking_ref(main), release_plugin(main)
    rel = plugin_json_rel(main, ref, plugin) if ref and plugin else None
    if not rel:
        return ""
    got = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=main, capture_output=True, text=True)
    try:
        return re.sub(r"-.*$", "", json.loads(got.stdout).get("version") or "") if got.returncode == 0 else ""
    except ValueError:
        return ""


# ---- 耐久ステップ ----


@durable.step(name="parallel.init")
def step_init(main: str, name: str) -> dict:
    """計画を読み、検査し、オーガナイザーの印を書く。"""
    plan, items, _ = pp.check_data(pp.load_plan(Path(main), name))
    if plan is None:
        return {"ok": False, "items": items}
    relay(Path(main), "role", "organizer", "--plan", name)
    return {"ok": True, "lanes": [ln.model_dump(mode="json") for ln in plan.lanes]}


@durable.step(name="parallel.spawn")
def step_spawn(main: str, name: str, lanes: list[str]) -> dict:
    """`lanes` を起動する。成功（同じ名前の 2 本目の断りも成功とする）は `started`、断りは `refused`（I7）。"""
    plan = pp.ParallelPlan.model_validate(pp.load_plan(Path(main), name), context={"max_plans": 10**6, "max_named": 10**6})
    out = {}
    for n in lanes:
        ln = plan.lane(n)
        code, res = relay(Path(main), "spawn", "--name", n, "--json", "--", pp.first_input(ln))
        why = ((res.get("items") or [{}])[0] or {}).get("result")
        if code == 0 or why == "name-taken":
            out[n] = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        else:
            out[n] = {"refused": why or res.get("summary") or f"exit {code}"}
    return out


@durable.step(name="parallel.observe")
def step_observe(main: str) -> dict:
    """`relay.py ls --json` のレーンの状態（名前 → running / awaiting_approval / stopped）。"""
    _, res = relay(Path(main), "ls", "--json")
    return {it["name"]: it.get("state") for it in res.get("items") or [] if isinstance(it, dict) and it.get("name")}


@durable.step(name="parallel.build")
def step_build(main: str, name: str, seq: int, k: int) -> dict:
    """まとめた配布の提示のプランと本番のプランを書く。"""
    if seq > pr.MAX_HELPER_SEQ:
        return {"error": f"配布の連番 {seq} が一時レーンの名前の上限 {pr.MAX_HELPER_SEQ} を超える"}
    version = dev_version(Path(main))
    if not version:
        return {"error": "出す版を決められない（ベースブランチと .ndf/supervise.json の release.plugin の plugin.json の版を読めない）"}
    d = pr.build_plans(Path(main), name, seq, k, version)
    return {"dir": str(d), "version": version}


@durable.step(name="parallel.presented")
def step_presented(d: str) -> dict:
    p = Path(d) / "presented.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


@durable.step(name="parallel.head")
def step_head(main: str) -> str:
    return git_head(Path(main))


@durable.step(name="parallel.stop-lanes")
def step_stop_lanes(main: str, names: list[str]) -> list[str]:
    """各レーンへ `relay.py stop <名前>` を打ち、止まらなかった名前を返す。"""
    return [n for n in names if relay(Path(main), "stop", n, "--json")[0] != 0]


@durable.step(name="parallel.finish")
def step_finish(main: str, name: str, progress: dict) -> None:
    """役割の印を消し、`plan.md` を描画し直す。"""
    relay(Path(main), "role", "--clear")
    render_progress(main, name, progress)


@durable.step(name="parallel.render")
def step_render(main: str, name: str, progress: dict) -> None:
    render_progress(main, name, progress)


def render_progress(main: str, name: str, progress: dict) -> None:
    try:
        plan = pp.ParallelPlan.model_validate(pp.load_plan(Path(main), name), context={"max_plans": 10**6, "max_named": 10**6})
    except (OSError, ValueError):
        return
    (pp.plan_dir(Path(main), name) / "plan.md").write_text(pp.render_md(plan, progress), encoding="utf-8")


# ---- 耐久ワークフロー ----


class _Run:
    """耐久ワークフローの中の値。打ち直すと記録から同じ値に戻る。"""

    def __init__(self, main: str, name: str, lanes: list[dict]):
        self.main, self.name = main, name
        self.order = [ln["name"] for ln in lanes]
        self.after = {ln["name"]: list(ln.get("after") or []) for ln in lanes}
        self.lanes: dict[str, dict] = {n: {} for n in self.order}
        self.pending: list[dict] = []
        self.questions: list[dict] = []
        self.releases: list[dict] = []
        self.current: Optional[dict] = None
        self.stop = False
        self.seq = 0
        self.received: dict[str, int] = {}

    def bump(self) -> int:
        self.seq += 1
        return self.seq

    def pause(self, action: str, **fields) -> None:
        durable.set_event("pause", {"seq": self.bump(), "action": action, **fields})

    def progress(self) -> dict:
        return {"lanes": self.lanes, "releases": [{k: v for k, v in r.items() if k != "presented"} for r in self.releases]}

    def summary(self, status: str) -> dict:
        return {"status": status, "lanes": self.lanes, "releases": self.progress()["releases"], "pending": [r["id"] for r in self.pending]}

    def stopped(self) -> set[str]:
        return {n for n, st in self.lanes.items() if st.get("started") and st.get("state") == "stopped"}

    # -- 受け取り --

    def take(self, msg) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("kind")
        if kind == "stop":
            self.stop = True
        elif kind == "request":
            if all(r["id"] != msg.get("id") for r in self.pending):
                self.pending.append(msg)
                self.received[msg.get("lane", "")] = self.received.get(msg.get("lane", ""), 0) + 1
                durable.set_event(f"lane-{msg.get('lane')}", {"received": self.received[msg.get("lane", "")]})
        elif kind in ("gate1", "gate2", "ask"):
            self.questions.append(msg)
            durable.set_event("questions", {"seq": self.bump(), "items": self.questions})

    def answer(self, rid: str, result: str, version: Optional[str], n: Optional[int], reason: Optional[str] = None) -> None:
        durable.set_event(f"answer-{rid}", {"result": result, "version": version, "release": n, "reason": reason, "at": None})

    # -- 配布 --

    def answer_from_ranges(self) -> None:
        """出した配布の範囲で答えられる依頼に答えて外す（I15）。"""
        for rel in self.releases:
            if rel.get("state") != "released":
                continue
            hit = set(pr.answerable(self.pending, rel.get("prs") or []))
            for r in [r for r in self.pending if r["id"] in hit]:
                self.answer(r["id"], "released", rel.get("version"), rel["n"])
            self.pending = [r for r in self.pending if r["id"] not in hit]

    def open_release(self) -> None:
        """流れている配布が無く依頼があれば、他のレーンを待たずに提示 1 を組む（I9）。"""
        n = len(self.releases) + 1
        built = step_build(self.main, self.name, n, 1)
        if built.get("error"):
            self.stop = True
            self.releases.append({"n": n, "state": "failed", "reason": built["error"]})
            return
        rel = {
            "n": n,
            "state": "presented",
            "version": built["version"],
            "dirs": [built["dir"]],
            "presented": [],
            "requests": [r["id"] for r in self.pending],
        }
        self.releases.append(rel)
        self.current = rel
        self.emit_release(rel, 1, represent=False)

    def emit_release(self, rel: dict, k: int, represent: bool) -> None:
        d = Path(rel["dirs"][k - 1])
        self.pause(
            "release",
            n=rel["n"],
            k=k,
            represent=represent,
            pipeline=pr.pipeline_cmd(SUPERVISE, d),
            spawn=helper_spawn(self.name, rel["n"], k),
        )

    def resume(self, msg: dict) -> None:
        rel = self.current
        if rel is None or msg.get("n") != rel["n"]:
            return
        if msg.get("phase") == "confirm":
            self.confirm(rel, msg.get("take"))
        elif msg.get("phase") == "settle":
            self.settle(rel, msg)

    def confirm(self, rel: dict, take: Optional[str]) -> None:
        """承認の答えを受けたら 1 度だけ比べる。出し直しの提示への答えでは比べない（I14）。"""
        if len(rel["dirs"]) == 1:
            first = step_presented(rel["dirs"][0])
            head = step_head(self.main)
            if pr.moved(first.get("sha") or "", head):
                built = step_build(self.main, self.name, rel["n"], 2)
                rel["dirs"].append(built["dir"])
                self.emit_release(rel, 2, represent=True)
                return
            k = 1
        else:
            k = 2 if take == "new" else 1
        got = step_presented(rel["dirs"][k - 1])
        rel.update(state="confirmed", k=k, sha=got.get("sha"), prs=got.get("prs") or [])
        self.pause(
            "confirmed", n=rel["n"], k=k, plan=str(Path(rel["dirs"][k - 1]) / "release.json"), spawn=helper_spawn(self.name, rel["n"], k)
        )

    def settle(self, rel: dict, msg: dict) -> None:
        result = msg.get("result")
        if not rel.get("prs"):
            got = step_presented(rel["dirs"][-1])
            rel["prs"] = got.get("prs") or []
        hit = set(pr.answerable(self.pending, rel["prs"]))
        if result == "released":
            rel.update(state="released", version=msg.get("version") or rel.get("version"))
            durable.set_event(
                f"range-{rel['n']}",
                {
                    "sha": rel.get("sha"),
                    "version": rel["version"],
                    "prs": rel["prs"],
                    "lanes": sorted({r["lane"] for r in self.pending if r["id"] in hit}),
                },
            )
            for r in [r for r in self.pending if r["id"] in hit]:
                self.answer(r["id"], "released", rel["version"], rel["n"])
        else:
            rel.update(state="rejected" if result == "rejected" else "failed", reason=msg.get("reason"))
            for r in [r for r in self.pending if r["id"] in hit]:
                self.answer(r["id"], rel["state"], None, rel["n"], msg.get("reason"))
        self.pending = [r for r in self.pending if r["id"] not in hit]
        for r in self.pending:
            durable.set_event(f"defer-{r['id']}", {"seq": rel["n"], "reason": "範囲に入らないため次の配布へ回した", "at": None})
        self.current = None


def helper_spawn(name: str, n: int, k: int) -> str:
    """一時レーンを起こす 1 行（`metrics.spawn`）。"""
    first = (
        f"/goal /ndf:development-workflow --parallel-helper {name} {n} {k} -- 終わり: [ndf-parallel] helper-done {name} の行を出したとき"
    )
    return f"python3 {RELAY} spawn --name {pr.helper_name(name, n)} --role helper --plan {name} --json -- '{first}'"


@durable.workflow(name="parallel")
def parallel_workflow(main: str, name: str) -> dict:
    init = step_init(main, name)
    if not init.get("ok"):
        return {"status": "stopped", "lanes": {}, "releases": [], "pending": [], "errors": init.get("items")}
    run = _Run(main, name, init["lanes"])
    run.pause("running")
    wait = poll_seconds()
    while True:
        if run.stop:
            left = step_stop_lanes(main, [n for n, st in run.lanes.items() if st.get("started")])
            step_finish(main, name, run.progress())
            return {**run.summary("stopped"), "not_stopped": left}
        ready = pp.ready_lanes(_plan_view(run), {n for n, st in run.lanes.items() if st.get("started")}, run.stopped())
        if ready:
            for n, got in step_spawn(main, name, [ln.name for ln in ready]).items():
                run.lanes[n].update(got)
        if run.current is None and run.pending:
            run.answer_from_ranges()
            if run.pending:
                run.open_release()
        if run.current is None and not run.pending and len(run.stopped()) == len(run.order):
            step_finish(main, name, run.progress())
            return run.summary("finished")
        run.take(durable.recv("inbox", wait if run.current is None else min(wait, 30)))
        if run.current is not None:
            msg = durable.recv("resume", min(wait, 30))
            if isinstance(msg, dict):
                run.resume(msg)
        states = step_observe(main)
        noticed = []
        for n in run.order:
            st = states.get(n)
            if st is None and run.lanes[n].get("started"):
                st = "stopped"
            if st and st != run.lanes[n].get("state"):
                run.lanes[n]["state"] = st
                if st == "awaiting_approval":
                    noticed.append(n)
        if noticed:
            run.pause("notice", lanes=noticed, attach=[f"python3 {RELAY} attach {n}" for n in noticed])


def _plan_view(run: _Run) -> pp.ParallelPlan:
    """起動の判定に使う計画の形（耐久ワークフローの入力から作る。ファイルを読まない）。"""
    lanes = [
        {"name": n, "input": {"kind": "loop", "loop": n, "args": ["--milestones", n]}, "plans": 1, "touches": [], "after": run.after[n]}
        for n in run.order
    ]
    return pp.ParallelPlan.model_construct(version=1, name=run.name, rules=[], lanes=[pp.Lane.model_construct(**ln) for ln in lanes])


# ---- 外の入口 ----


def open_parallel_record(main: Path, name: str, lock_timeout: Optional[float] = 0) -> None:
    durable.launch(pl.KIND, pl.record_identity(main, name), keep=pl.workflow_id(name), lock_timeout=lock_timeout)


def run_plan(main: Path, name: str) -> tuple[dict, bool]:
    """耐久ワークフローを始めるか続け、終わるまで待つ。(出力, 再開か) を返す。"""
    wid = pl.workflow_id(name)
    open_parallel_record(main, name)
    st = durable.status(wid)
    resumed = st is not None
    if st is None:
        durable.start(durable.WorkflowRef(wid, 1, "start"), parallel_workflow, str(main), name)
    return durable.output_of(wid), resumed
