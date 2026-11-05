"""並列の進行（#2436）: 耐久ワークフロー `parallel-<計画名>` と耐久ステップ（起動・観測・停止・配布を組む・要約）。

耐久ワークフローの中の分岐は耐久ステップの結果とメッセージだけで決まる。時刻・外のコマンド・ファイルを読むのは
すべて耐久ステップである（打ち直しで同じ道を通るため。#1142 の I18）。外へ見せるのはイベントと出力だけである。

- メッセージ `inbox`: `{kind: request | gate1 | gate2 | ask | answer | stop, ...}`（レーン・一時レーン・オーガナイザーの答え・`stop`）
- メッセージ `resume`: `{n, phase: confirm, take}` / `{n, phase: settle, result, version, reason}`（`confirm`・`settle`）
- イベント `pause`: `{seq, action}`（`running` / `notice` / `release` / `confirmed`。配布の途中の `notice` は
  最後の起動の指示を `release` に添える）、`questions`: `{seq, items}`
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
from sprint_loop_names import LOOP_DIR, PENDING

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
    plan = pp.load_valid_plan(Path(main), name)
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


STALL_MINUTES = 60  # 動かないレーンを問うまでの分（設計の失敗の 3 番の既定）
SPAWN_REFUSALS = 3  # 起動の断りがこの回数続いたら問う（設計の失敗の 17 番）


def stall_seconds() -> float:
    """動かないレーンを問うまでの秒（`NDF_PARALLEL_STALL_MINUTES` で変えられる。既定 60 分）。"""
    try:
        return max(1.0, float(os.environ.get("NDF_PARALLEL_STALL_MINUTES") or STALL_MINUTES)) * 60
    except ValueError:
        return STALL_MINUTES * 60.0


@durable.step(name="parallel.observe")
def step_observe(main: str) -> dict:
    """`relay.py ls --json` の行（名前 → {state, at, role}）と、観た時刻（エポック秒）。"""
    _, res = relay(Path(main), "ls", "--json")
    rows = {
        it["name"]: {"state": it.get("state"), "at": it.get("at"), "role": it.get("role")}
        for it in res.get("items") or []
        if isinstance(it, dict) and it.get("name")
    }
    return {"now": time.time(), "rows": rows}


@durable.step(name="parallel.held-loops")
def step_held_loops(main: str, name: str, answered: list[str]) -> list[str]:
    return held_loops(Path(main), name, answered)


def held_loops(main: Path, name: str, answered: list[str]) -> list[str]:
    """答えを受けたのに残りを流していない保留の周回を持つ、計画のループのレーンのループ名（異常系 11）。"""
    asked = lambda lap: (lap.get("release") or lap.get("question") or {}).get("request") if lap.get("outcome") in PENDING else None  # noqa: E731
    return [ln.input.loop for ln, rec in _loop_records(main, name) if any(asked(lap) in answered for lap in rec.get("laps") or [])]


def _loop_records(main: Path, name: str) -> list[tuple]:
    """計画のループのレーンと、読めたループの記録（loop.json）の組。計画を読めなければ空。"""
    try:
        plan = pp.load_valid_plan(main, name)
    except (OSError, ValueError):
        return []
    out = []
    for ln in (ln for ln in plan.lanes if ln.input.kind == "loop"):
        try:
            out.append((ln, json.loads((Path(main) / LOOP_DIR / ln.input.loop / "loop.json").read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


@durable.step(name="parallel.design-held")
def step_design_held(main: str, name: str, answered: list[str]) -> list[str]:
    return design_held(Path(main), name, answered)


def design_held(main: Path, name: str, answered: list[str]) -> list[str]:
    """設計の承認の依頼に答えたのに、持ち主のループのレーンが答えをまだ当てていないレーンの名前（#2436 の 6f）。

    答え（`answer-<依頼>`）は持ち主が周回の境で当てる。当てる前に計画を終えると、起こしたレーンが以後の配布の
    依頼を送れないため、当てるまで計画を終えない。"""
    held = lambda rec: any(w.get("request") in answered and not w.get("answer") for w in rec.get("waiting") or [])  # noqa: E731
    return [ln.name for ln, rec in _loop_records(main, name) if held(rec)]


@durable.step(name="parallel.waiting-lanes")
def step_waiting_lanes(main: str, name: str) -> list[str]:
    return waiting_lanes(Path(main), name)


def waiting_lanes(main: Path, name: str) -> list[str]:
    """記録が `waiting_only` で止まり、答えをまだ当てていない待ちを持つループのレーンの名前（保留中。依存の判定で止まったと数えない）。"""

    def held(rec: dict) -> bool:
        if rec.get("status") != "stopped" or (rec.get("stop") or {}).get("condition") != "waiting_only":
            return False
        return any(not w.get("answer") for w in rec.get("waiting") or [])

    return [ln.name for ln, rec in _loop_records(main, name) if held(rec)]


@durable.step(name="parallel.build")
def step_build(main: str, name: str, seq: int, k: int, requests: Optional[list[dict]] = None) -> dict:
    """まとめた配布の提示のプランと本番のプランを書き、受けている依頼を `releases/requests.json` へ書く
    （`parallel.py present` が耐久ワークフローの外からレーンの内訳に読む）。"""
    pr.write_requests(Path(main), name, list(requests or []))
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
        plan = pp.load_valid_plan(Path(main), name)
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
        self.launch: Optional[dict] = None
        self.received: dict[str, int] = {}
        self.answered: set[str] = set()
        self.force = False
        self.refusals: dict[str, int] = {}
        self.stall_until: dict[str, float] = {}
        self.helper_asked: Optional[int] = None
        self.held_spawned: list[str] = []
        self.wake: list[str] = []
        self.sprint = {ln["name"] for ln in lanes if (ln.get("input") or {}).get("kind") == "sprint"}
        self.revive: list[str] = []  # 問いへ答えた後、まだ動き出していない止まった 1 スプリントのレーン（6f）
        self.revive_at: dict[str, float] = {}  # revive に入れたときの最後の観測の時刻（エポック秒）。最後に動いた時刻と比べる
        self.seen_at = 0.0  # 最後に観測した時刻（エポック秒）

    def bump(self) -> int:
        self.seq += 1
        return self.seq

    def pause(self, action: str, **fields) -> None:
        """イベント `pause` は上書きされるため、流れている配布の最後の起動の指示（release / confirmed）を
        他の知らせにも `release` として添え、知らせが重なっても起動の指示を失わない。配布の答えを受けた止まっている
        レーンを起こす行（`wake`）も、そのレーンが動き出すまでどの知らせにも添える。"""
        seq = self.bump()
        if action in ("release", "confirmed"):
            self.launch = {"seq": seq, "action": action, **fields}
        elif self.current is not None and self.launch:
            fields = {**fields, "release": self.launch}
        stopped = self.stopped()
        self.wake = [n for n in self.wake if n in stopped]
        wake = {"wake": [f"python3 {RELAY} restart {n}" for n in self.wake]} if self.wake else {}
        durable.set_event("pause", {"seq": seq, "action": action, **fields, **wake})

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
            self.force = self.force or bool(msg.get("force"))
        elif kind == "request":
            if all(r["id"] != msg.get("id") for r in self.pending):
                self.pending.append(msg)
                self.received[msg.get("lane", "")] = self.received.get(msg.get("lane", ""), 0) + 1
                durable.set_event(f"lane-{msg.get('lane')}", {"received": self.received[msg.get("lane", "")]})
        elif kind in ("gate1", "gate2", "ask"):
            if any(q.get("id") == msg.get("id") for q in self.questions) or msg.get("id") in self.answered:
                return  # 同じ依頼の打ち直しは数えない（id は送る側が依頼の中身から決める）
            key = cause_of(msg)
            same = next((q for q in self.questions if key and q.get("cause") == key), None)
            if same is not None:  # 同じ原因の問いは 1 つにまとめ、答えを依頼してきた全レーンへ配る（異常系 5）
                same["also"] = [*same.get("also", []), msg.get("id")]
                same["merged"] = [*same.get("merged", []), {"id": msg.get("id"), "lane": msg.get("lane"), "question": msg.get("question")}]
                self.publish_questions()
                return
            if msg.get("source") == "ndf-ask:human-action":  # 代われない操作: レーンへ入る 1 行を添える（6d）
                msg = {**msg, "attach": f"python3 {RELAY} attach {msg.get('lane')}"}
            self.questions.append({**msg, "cause": key} if key else msg)
            self.publish_questions()
        elif kind == "answer":
            self.reply(msg)

    def publish_questions(self) -> None:
        durable.set_event("questions", {"seq": self.bump(), "items": self.questions})

    def reply(self, msg: dict) -> None:
        """オーガナイザーの答えを `answer-<依頼>` に 1 回だけ立て、問いの一覧から外す。答えの後の処理は持ち主のレーンが行う（6f）。"""
        rid = msg.get("id")
        if rid in self.answered or all(q.get("id") != rid for q in self.questions):
            return
        q = next(q for q in self.questions if q.get("id") == rid)
        for each in [rid, *q.get("also", [])]:
            self.answered.add(each)
            durable.set_event(f"answer-{each}", {k: msg.get(k) for k in ("result", "note", "choice", "stamp", "merge", "by", "at")})
        self.questions = [q for q in self.questions if q.get("id") != rid]
        self.publish_questions()
        owners = {q.get("lane"), *(m.get("lane") for m in q.get("merged", []))} & self.sprint & self.stopped()
        self.revive = sorted({*self.revive, *owners})  # 止まった持ち主を起こして答えを当てるまで計画を終えない
        self.revive_at.update({o: self.seen_at for o in owners})  # 止まったと観た時刻。これより後に動いたら起きたと数える
        if owners - set(self.wake):  # 保留中の持ち主の後に依存先を起こさないため、全レーンが止まるのを待たずに起こす 1 行を添える
            self.wake = sorted({*self.wake, *owners})
            self.pause("notice", lanes=[], attach=[])
        self.apply_own(q, list(msg.get("choice") or []))

    def apply_own(self, q: dict, choice: list[str]) -> None:
        """耐久ワークフローが自分で立てた問いへの答えを当てる。「止める」は、起動できないレーンなら起こし直しをやめて
        止まったものと数え、配布の途中で止まった一時レーンなら配布を失敗として締める（依頼へ failed を答える）。"""
        if "止める" not in choice:
            return
        if q.get("kind") == "spawn" and q.get("lane") in self.lanes:
            self.lanes[q["lane"]].update(started=True, state="stopped", given_up=True)
        elif q.get("kind") == "helper" and (rel := self.current) is not None and q.get("n") == rel["n"]:
            if not rel.get("prs"):  # 提示の前に止まったなら、この配布へ入れた依頼の Pull Request を範囲として締める
                own = {n for r in self.pending if r["id"] in rel.get("requests", []) for n in r.get("prs") or []}
                rel["prs"] = step_presented(rel["dirs"][-1]).get("prs") or sorted(own)
            self.settle(rel, {"result": "failed", "reason": "配布の途中で止まった一時レーンを、人が止めると答えた"})

    def ask(self, kind: str, rid: str, lane: str, question: str, options: list[str], **extra) -> None:
        """耐久ワークフローが自分で立てる問い（動かないレーン・一時レーンの停止・起動の断り）。同じ id は 1 回だけ。"""
        if rid in self.answered or any(q.get("id") == rid for q in self.questions):
            return
        item = {"kind": kind, "id": rid, "lane": lane, "source": "ndf-ask:human-action", "question": question, "options": options}
        self.questions.append({**item, "attach": f"python3 {RELAY} attach {lane}", **extra})
        self.publish_questions()

    def answer(self, rid: str, result: str, version: Optional[str], n: Optional[int], reason: Optional[str] = None) -> None:
        self.answered.add(rid)
        durable.set_event(f"answer-{rid}", {"result": result, "version": version, "release": n, "reason": reason, "at": None})

    # -- 配布 --

    def answer_from_ranges(self) -> None:
        """出した配布の範囲で答えられる依頼に答えて外す（I15）。依頼の全 PR がそれまでに出せた範囲の和に入れば答える。
        和は古い配布から順に足し、依頼の最後の PR が出た配布の版と連番で答える（後の配布の PR を前の配布の版で答えない）。"""
        done: set[int] = set()
        for rel in self.releases:
            if rel.get("state") != "released":
                continue
            done |= set(rel.get("prs") or [])
            hit = set(pr.answerable(self.pending, rel.get("prs") or [], done))
            for r in [r for r in self.pending if r["id"] in hit]:
                self.answer(r["id"], "released", rel.get("version"), rel["n"])
            self.pending = [r for r in self.pending if r["id"] not in hit]

    def open_release(self) -> None:
        """流れている配布が無く依頼があれば、他のレーンを待たずに提示 1 を組む（I9）。"""
        n = len(self.releases) + 1
        built = step_build(self.main, self.name, n, 1, self.pending)
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
        if any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in self.questions):  # 承認ゲート 2 の答えが届いた
            self.questions = [q for q in self.questions if not (q.get("kind") == "gate2" and q.get("n") == rel["n"])]
            self.publish_questions()
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
                built = step_build(self.main, self.name, rel["n"], 2, self.pending)
                rel["dirs"].append(built["dir"])
                rel["versions"] = [rel.get("version"), built.get("version")]  # 提示ごとの版。選ばれた提示の版を配布の答えへ渡す
                self.emit_release(rel, 2, represent=True)
                return
            k = 1
        else:
            k = 2 if take == "new" else 1
        got = step_presented(rel["dirs"][k - 1])
        versions = rel.get("versions") or [rel.get("version")]
        rel.update(state="confirmed", k=k, sha=got.get("sha"), prs=got.get("prs") or [], version=versions[min(k, len(versions)) - 1])
        self.pause(
            "confirmed", n=rel["n"], k=k, plan=str(Path(rel["dirs"][k - 1]) / "release.json"), spawn=helper_spawn(self.name, rel["n"], k)
        )

    def settle(self, rel: dict, msg: dict) -> None:
        result = msg.get("result")
        if not rel.get("prs"):
            got = step_presented(rel["dirs"][-1])
            rel["prs"] = got.get("prs") or []
        done = {n for r in self.releases if r is not rel and r.get("state") == "released" for n in r.get("prs") or []}
        hit = set(pr.answerable(self.pending, rel["prs"], done))  # 前の配布で出た PR と合わせて全部が入る依頼に答える
        answered = [r for r in self.pending if r["id"] in hit]
        if result == "released":
            rel.update(state="released", version=msg.get("version") or rel.get("version"))
            durable.set_event(
                f"range-{rel['n']}",
                {
                    "sha": rel.get("sha"),
                    "version": rel["version"],
                    "prs": rel["prs"],
                    "lanes": sorted({r["lane"] for r in answered}),
                },
            )
            for r in answered:
                self.answer(r["id"], "released", rel["version"], rel["n"])
        else:
            rel.update(state="rejected" if result == "rejected" else "failed", reason=msg.get("reason"))
            for r in answered:
                self.answer(r["id"], rel["state"], None, rel["n"], msg.get("reason"))
        self.pending = [r for r in self.pending if r["id"] not in hit]
        for r in self.pending:
            durable.set_event(f"defer-{r['id']}", {"seq": rel["n"], "reason": "範囲に入らないため次の配布へ回した", "at": None})
        self.current = None
        stopped = self.stopped()  # 止まった宛先を起こす 1 行を知らせへ（異常系 6）。全レーンが止まれば異常系 11 に任せる
        if (wake := sorted({r["lane"] for r in answered if r.get("lane") in stopped})) and len(stopped) < len(self.order):
            self.wake = sorted({*self.wake, *wake})
            self.pause("notice", lanes=[], attach=[])


def helper_spawn(name: str, n: int, k: int, pending: Optional[str] = None) -> str:
    """一時レーンを起こす 1 行（`metrics.spawn`）。pending はループ名で、保留の周回の残りを流す一時レーン（異常系 11）。"""
    what = f"pending {pending}" if pending else f"{n} {k}"
    helper = pr.helper_name(name, n)
    # 終わりに自分のラッパーを止める（判断 fca6fe464619 の (a)）。止めないと対話の claude が入力待ちで残り、続きの一時レーンが断られる
    end = f"[ndf-parallel] helper-done {name} の行を出し、python3 {RELAY} stop {helper} で自分のラッパーを止めたとき"
    first = f"/goal /ndf:development-workflow --parallel-helper {name} {what} -- 終わり: {end}"
    return f"python3 {RELAY} spawn --name {helper} --role helper --plan {name} --json -- '{first}'"


def present_approval(d: Path) -> str:
    """提示のプランの承認資料の絶対パス（ステップ `present` の `--approval` と `cwd` から読む）。読めなければ空。"""
    import re
    import shlex

    try:
        steps = json.loads((Path(d) / "present.json").read_text(encoding="utf-8")).get("steps") or []
    except (OSError, ValueError):
        return ""
    step = next((x for x in steps if isinstance(x, dict) and x.get("id") == "present"), {})
    m = re.search(r"--approval (\S+)", step.get("cmd") or "")
    if not m:
        return ""
    path = Path(shlex.split(m.group(1))[0])
    return shlex.quote(str(path if path.is_absolute() else Path(step.get("cwd") or ".") / path))


def helper_task(main: Path, name: str, n: int, k: int) -> dict:
    """一時レーン（`--parallel-helper <計画名> <連番> <提示>`）が流すもの。最後の起動の指示（イベント `pause` か、
    配布の途中の知らせに添えた `release`）が同じ連番と提示の `confirmed` なら確定の後の 3 手、そうでなければ提示。

    - 提示（`phase: present`）: `pipeline` は提示のプラン → 本番のプランの queue と待ち。終われば提示のプランが承認ゲート 2 で
      止まったかを見て、止まっていれば `gate2`（承認ゲート 2 の依頼を送る）、流れ切れば `settle` を打つ
    - 確定の後（`phase: confirmed`）: `pipeline` は答えの記録 → 提示のプランを `approve` から → 本番のプランの queue と待ち。
      終われば `settle` を打つ。`approve` が承認ゲート（10）で終われば queue を打たずに問いとして送る"""
    ev = durable.event_from(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), "pause", 0) or {}
    launch = ev if ev.get("action") in ("release", "confirmed") else ev.get("release") or {}
    d = pr.release_dir(main, name, n, k)
    py = f"python3 {HERE / 'parallel.py'}"
    out = {"n": n, "k": k, "dir": str(d), "gate2": f"{py} request-gate2 {name} {n} {k} --report {present_approval(d)}"}
    if launch.get("action") == "confirmed" and launch.get("n") == n and launch.get("k") == k:
        done = d / "release-done.json"
        gate = f"python3 {HERE / 'sprint-state.py'} gate {d / 'answer.json'} '関門 2' --what 'まとめた配布 {n} の提示 {k}' --by user --outcome approved"
        steps = [gate, f"{SUPERVISE} run {d / 'present.json'} --from approve", f"{SUPERVISE} queue {d / 'release.json'} --done {done}"]
        return {**out, "phase": "confirmed", "pipeline": " && ".join([*steps, f"{SUPERVISE} wait {done}"]),
                "settle": f"{py} settle {name} {n} --done {done}"}  # fmt: skip
    done = d / "done.json"
    return {**out, "phase": "present", "pipeline": f"{pr.pipeline_cmd(SUPERVISE, d)} && {SUPERVISE} wait {done}",
            "settle": f"{py} settle {name} {n} --done {done}"}  # fmt: skip


@durable.workflow(name="parallel")
def parallel_workflow(main: str, name: str) -> dict:
    init = step_init(main, name)
    if not init.get("ok"):
        return {"status": "stopped", "lanes": {}, "releases": [], "pending": [], "errors": init.get("items")}
    run = _Run(main, name, init["lanes"])
    run.pause("running")
    wait = poll_seconds()
    while True:
        if run.stop and (run.current is None or run.force):  # --force は配布の settle を待たず、一時レーンも止める
            return _handle_stop(run, main)
        # 一時レーンが配布を流している間（提示のまま MVV 判定で本番へ進む場合も含む）の stop は、settle が届くまで
        # 待ってから終える（設計の失敗の 12 番。作りかけを残さない）。待つ間も inbox を読み、--force と問いを受ける
        if not run.stop:
            _spawn_ready(run, main)
        if not run.stop and run.current is None and run.pending:
            run.answer_from_ranges()
            if run.pending:
                run.open_release()
        if not run.stop and _run_finished(run) and (out := _finish_or_hold(run, main, name)) is not None:
            return out
        run.take(durable.recv("inbox", wait if run.current is None else min(wait, 30)))
        if run.current is not None:
            msg = durable.recv("resume", min(wait, 30))
            if isinstance(msg, dict):
                run.resume(msg)
        if not run.stop:
            _observe_run(run, main)


def _finish_or_hold(run: _Run, main: str, name: str) -> Optional[dict]:
    """全レーンが止まった後: 終えられれば要約を返す。答えを当てていない設計の承認か、保留の周回があれば None。"""
    designs = sorted({*step_design_held(main, name, sorted(run.answered)), *(set(run.revive) & run.stopped())})
    if designs:  # 持ち主が答え（設計の承認・1 スプリントのレーンの問い）を当てるまで終えない。起こす 1 行を知らせへ添え続ける（6f）
        if set(designs) - set(run.wake):
            run.wake = sorted({*run.wake, *designs})
            run.pause("notice", lanes=[], attach=[])
        return None
    held = step_held_loops(main, name, sorted(run.answered))
    if not held:
        step_finish(main, name, run.progress())
        return run.summary("finished")
    # 止まったレーンの保留の周回のまとめを一時レーンで流してから終える（異常系 11）。一時レーンは 1 本ずつ起こす
    todo = [x for x in held if x not in run.held_spawned]
    if todo and not set(run.held_spawned) & set(held):
        run.held_spawned.append(todo[0])
        seq = len(run.releases) + len(run.held_spawned)
        run.pause("held", loops=[todo[0]], spawn=helper_spawn(name, seq, 0, pending=todo[0]))
    return None


def _started(run: _Run) -> list[str]:
    return [n for n, st in run.lanes.items() if st.get("started")]


def _handle_stop(run: _Run, main: str) -> dict:
    """止める依頼を受けた後: 起動済みのレーンを止め、記録を閉じて要約を返す。"""
    helper = [pr.helper_name(run.name, run.current["n"])] if run.force and run.current is not None else []
    left = step_stop_lanes(main, _started(run) + helper)
    step_finish(main, run.name, run.progress())
    return {**run.summary("stopped"), "not_stopped": left}


def _asking_lanes(run: _Run) -> set[str]:
    """答えていない問い（まとめた問いの `merged` も含む）を送ったレーンの名前。"""
    return {x for q in run.questions for x in (q.get("lane"), *(m.get("lane") for m in q.get("merged") or [])) if x}


def _spawn_ready(run: _Run, main: str) -> None:
    """起動の条件がそろったレーンを起動し、結果をレーンの状態へ反映する。

    保留中のレーン（答えていない問いを持つ・答えをまだ当てていない `revive`・記録が `waiting_only` で答えを待つループ）は、
    ラッパーが止まっていても依存の判定では止まったと数えない（I8）。問いを保留して自分のラッパーを止めたレーンの後に、
    依存先のレーンが起動して答えの後の再開と同時に動かないためである。"""
    view, started = _plan_view(run), set(_started(run))
    stopped = run.stopped() - _asking_lanes(run) - set(run.revive)
    ready = pp.ready_lanes(view, started, stopped)
    if ready and any(a in stopped for ln in ready for a in ln.after):  # 依存で起こすときだけループの記録を読む
        ready = pp.ready_lanes(view, started, stopped - set(step_waiting_lanes(main, run.name)))
    if ready:
        for n, got in step_spawn(main, run.name, [ln.name for ln in ready]).items():
            run.lanes[n].update(got)
            if got.get("refused"):
                run.refusals[n] = run.refusals.get(n, 0) + 1
                if run.refusals[n] % SPAWN_REFUSALS == 0:  # 3 回続けて断られるたびに理由を添えて問う（異常系 17）
                    run.ask(
                        "spawn",
                        f"{run.name}-spawn-{n}-{run.refusals[n]}",
                        n,
                        f"レーン {n} を {SPAWN_REFUSALS} 回起動できない（{got['refused']}）",
                        ["起こし直す", "止める"],
                    )


def _run_finished(run: _Run) -> bool:
    """配布の途中でも残りでもなく、答えていない問いも無く、全レーンが止まっている（I11）。"""
    return run.current is None and not run.pending and not run.questions and len(run.stopped()) == len(run.order)


def _observe_run(run: _Run, main: str) -> None:
    """レーンの状態を観測して反映し、承認待ちへ移ったレーンを知らせる。"""
    seen = step_observe(main)
    rows = seen.get("rows") or {}
    states = {n: r.get("state") for n, r in rows.items()}
    run.seen_at = float(seen.get("now") or 0)
    _clear_revived(run, rows)
    _ask_stalls(run, rows, run.seen_at)
    _ask_helper(run, rows)
    noticed = []
    for n in run.order:
        st = states.get(n)
        if st is None and run.lanes[n].get("started"):
            st = "stopped"
        if st and st != run.lanes[n].get("state"):
            run.lanes[n]["state"] = st
            if st != "stopped" and n in run.revive:  # 起こしたレーンが動き出したら待ちを外す
                run.revive = [x for x in run.revive if x != n]
                run.revive_at.pop(n, None)
            if st == "awaiting_approval":
                noticed.append(n)
    if noticed:
        run.pause("notice", lanes=noticed, attach=[f"python3 {RELAY} attach {n}" for n in noticed])


def _clear_revived(run: _Run, rows: dict) -> None:
    """起こしたレーンの最後に動いた時刻（`relay.py ls` の `at`）が、答えたときの観測の時刻より後へ進んだら `revive` から外す。
    答えを当ててすぐ止まるレーンは観測の間隔の中で stopped → running → stopped と動き、状態の変化として見えないためである。"""
    import clock

    for n in list(run.revive):
        at = (rows.get(n) or {}).get("at")
        t = clock.parse(at, naive="utc") if at else None
        if t is not None and t.timestamp() > run.revive_at.get(n, float("inf")):
            run.revive = [x for x in run.revive if x != n]
            run.revive_at.pop(n, None)


def _ask_stalls(run: _Run, rows: dict, now: float) -> None:
    """承認待ちでも止まってもいないのに最後に動いた時刻が区切りより前のレーンを、区切りごとに 1 回だけ問う（異常系 3）。"""
    import clock

    limit = stall_seconds()
    for n in run.order:
        r = rows.get(n) or {}
        at = clock.parse(r.get("at"), naive="utc") if r.get("at") else None
        if r.get("state") != "running" or at is None or now - at.timestamp() < limit or now < run.stall_until.get(n, 0):
            continue
        run.stall_until[n] = now + limit
        mins = int((now - at.timestamp()) // 60)
        run.ask(
            "stall", f"{n}-s{int(now)}", n, f"レーン {n} が {mins} 分動いていない", ["起こし直す", "止める", "待つ"],
            commands={"起こし直す": f"python3 {RELAY} restart {n}", "止める": f"python3 {RELAY} stop {n}"},
        )  # fmt: skip


def _ask_helper(run: _Run, rows: dict) -> None:
    """配布の途中で一時レーンが止まり、settle も承認ゲート 2 の依頼も、その一時レーンが送った答えていない問い（ask も含む）も
    無ければ問う（受け入れ条件 6g）。一時レーンは問いを保留すると自分のラッパーを止めるため、問いを送って止まったものは問わない。"""
    rel = run.current
    if rel is None or not run.launch or run.helper_asked == run.launch["seq"]:
        return
    name = pr.helper_name(run.name, rel["n"])
    if (rows.get(name) or {}).get("state") != "stopped" or any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in run.questions):
        return
    if name in _asking_lanes(run):
        return
    run.helper_asked = run.launch["seq"]
    run.ask(
        "helper", f"{name}-h{run.launch['seq']}", name, f"一時レーン {name} が配布 {rel['n']} の途中で止まった（settle も承認ゲート 2 の依頼も無い）",
        ["続きから起こし直す", "止める"], commands={"続きから起こし直す": run.launch.get("spawn")}, n=rel["n"],
    )  # fmt: skip


def cause_of(msg: dict) -> str:
    """同じ原因の問いの鍵（異常系 5）: 区分と選択肢と、空白をまとめてレーン名・パス・番号を伏せた問いの文面。

    まとめるのは人にしかできない操作（`ndf-ask:human-action`。認証切れ・ディスク不足・ネットワークなど環境の問題）だけで、
    取り消せない操作・レッドライン・費用の問いは番号の違いで別の対象になり得るため、まとめない（空の鍵を返す）。
    まとめた問いには各レーンの元の文面（`merged`）を残し、オーガナイザーが問いに並べる。"""
    import re

    if msg.get("kind") != "ask" or msg.get("source") != "ndf-ask:human-action":
        return ""
    text = str(msg.get("question") or "")
    if msg.get("lane"):
        text = text.replace(str(msg["lane"]), "<lane>")
    text = re.sub(r"(/[^\s（）()]+)+", "<path>", text)
    text = re.sub(r"\d+", "<n>", text)
    return f"{msg.get('source')}|{json.dumps(msg.get('options') or [], ensure_ascii=False)}|{' '.join(text.split())}"


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
