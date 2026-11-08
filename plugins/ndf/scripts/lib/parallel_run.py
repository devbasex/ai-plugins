"""並列の進行（#2436）: 耐久ワークフロー `parallel-<計画名>` と耐久ステップ（起動・観測・停止・リリースを組む・要約）。

耐久ワークフローの中の分岐は耐久ステップの結果とメッセージだけで決まる。時刻・外のコマンド・ファイルを読むのは
すべて耐久ステップである（打ち直しで同じ道を通るため。#1142 の I18）。外へ見せるのはイベントと出力だけである。

- メッセージ `inbox`: `{kind: request | gate1 | gate2 | ask | unmatched | answer | stop | left_stopped, ...}`（レーン・一時レーン・オーガナイザーの答え・`stop`・`park --clear`）
- メッセージ `resume`: `{n, phase: confirm, take}` / `{n, phase: settle, result, version, reason}`（`confirm`・`settle`）
- リリースの組み方は依頼を受けるたびに耐久ステップ `parallel.route` が宣言から読む。組まないなら `released`、組めないなら
  `failed` と理由を依頼へ答え、計画を止めるかは問い（`kind: build`）への人の答えで決まる（#2616）
- イベント `answer-hold`: `{seq, action: "answer_hold", n, line, hold}`（答え待ちの停止の最新の変化。#2707）
- イベント `pause`: `{seq, action}`（`running` / `notice` / `release` / `confirmed`。リリースの途中の `notice` は
  最後の起動の指示を `release` に添える）、`questions`: `{seq, items}`
- イベント `answer-<依頼>`（最終の答え。1 回だけ）・`defer-<依頼>`（次のリリースへ回した知らせ）・`range-<連番>`・`lane-<名>`

リレーハブの CLI（`relay.py spawn`・`ls`・`stop`・`role`）は子プロセスで打ち、統一 JSON レポートだけを読む。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import delivery
import durable
import parallel_hold as ph
import parallel_lane as pl
import parallel_plan as pp
import parallel_release as pr
import parallel_route as prt
from parallel_files import RELAY
from parallel_helper import SUPERVISE, helper_spawn, helper_task  # noqa: F401  helper_task は parallel.py とテストが読む
from parallel_observe import apply_observed_states
from parallel_question import cause_of
from parallel_reply import AnswerRouting, step_prior_waits, step_waiting_lanes
from parallel_repo import dev_version, git_head, poll_seconds, relay, relay_proc, spawn_refusals, stall_seconds


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
        code, res = relay(ln.repo_dir(Path(main)), "spawn", "--name", n, "--json", "--", pp.first_input(ln))  # cwd はレーンのリポジトリ
        why = ((res.get("items") or [{}])[0] or {}).get("result")
        if code == 0 or why == "name-taken":
            out[n] = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        else:
            out[n] = {"refused": why or res.get("summary") or f"exit {code}"}
    return out


@durable.step(name="parallel.observe")
def step_observe(main: str, name: str) -> dict:
    """`relay.py ls --json` の行（名前 → {state, at, role}）・観た時刻（エポック秒）・終了コード・標準エラーの末尾 1 行。
    `can_ask` は一覧の失敗を問える版の記録の印で、無い記録の再生では問わない（問いの DBOS の呼び出しが記録の順を崩す。決定 5）。
    `limits`・`marks` も同じで、再開の印は一覧の後に読む（止まって見えたレーンの印を読み逃さない。#2707 の I7・I9）。"""
    got, res = relay_proc(Path(main), "ls", "--json")
    rows = {
        it["name"]: {"state": it.get("state"), "at": it.get("at"), "role": it.get("role")}
        for it in res.get("items") or []
        if isinstance(it, dict) and it.get("name")
    }
    err = (got.stderr or "").strip().splitlines()[-1:]
    seen = {"now": time.time(), "rows": rows, "code": got.returncode, "err": err[0] if err else "", "can_ask": True}
    return {**seen, "limits": ph.limit_values(Path(main)), "marks": ph.read_marks(Path(main), name)}


@durable.step(name="parallel.held-loops")
def step_held_loops(main: str, name: str, answered: list[str]) -> list[str]:
    return prt.held_loops(Path(main), name, answered)


@durable.step(name="parallel.design-held")
def step_design_held(main: str, name: str, answered: list[str]) -> list[str]:
    return prt.design_held(Path(main), name, answered)


@durable.step(name="parallel.route")
def step_route(main: str) -> dict:
    """宣言からリリースの組み方を読む。耐久ワークフローの分岐を記録した値だけで決めるため、耐久ステップにする（#2616 の決定 2）。"""
    got = prt.way_of(Path(main))
    return {"way": got.way, "reason": got.reason}


@durable.step(name="parallel.build")
def step_build(main: str, name: str, seq: int, k: int, requests: Optional[list[dict]] = None) -> dict:
    """まとめたリリースの提示のプランと本番のプランを書き、受けている依頼を `releases/requests.json` へ書く
    （`parallel.py present` が耐久ワークフローの外からレーンの内訳に読む）。組めない理由は `error` で返し、例外を上げない（#2616 の決定 3）。問いは理由の一致でまとめるため、理由に連番と提示の番号を入れない。"""
    try:
        pr.write_requests(Path(main), name, list(requests or []))
        if seq > pr.MAX_HELPER_SEQ:
            return {"error": f"リリースの連番が一時レーンの名前の上限 {pr.MAX_HELPER_SEQ} を超える"}
        version = dev_version(Path(main))
        if not version:
            return {"error": prt.NO_VERSION}
        d = pr.build_plans(Path(main), name, seq, k, version)
    except Exception as exc:  # noqa: BLE001  理由を依頼への答えとリリースの記録へ渡す
        return {"error": f"リリースを組み立てられない（{type(exc).__name__}: {exc}）"}
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
    """役割の印を消し、止める指示のファイルと再開の印を消し、`plan.md` を描画し直す。"""
    relay(Path(main), "role", "--clear")
    ph.clear_plan(Path(main), name)
    render_progress(main, name, progress)


@durable.step(name="parallel.answer-hold")
def step_answer_hold(main: str, name: str, data: dict, changes: list[dict], progress: dict) -> dict:
    """止める指示のファイル・履歴・`plan.md` を書く。書けなければ `ok: false` を返し、次の 1 周で書き直す。"""
    try:
        got = ph.write_state(Path(main), name, data, changes)
        render_progress(main, name, progress)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **got}


@durable.step(name="parallel.resume-lanes")
def step_resume_lanes(main: str, name: str, lanes: list[str], drop: list[str]) -> dict:
    return ph.resume_marks(Path(main), name, lanes, drop, relay)


def render_progress(main: str, name: str, progress: dict) -> None:
    try:
        plan = pp.load_valid_plan(Path(main), name)
    except (OSError, ValueError):
        return
    (pp.plan_dir(Path(main), name) / "plan.md").write_text(pp.render_md(plan, progress), encoding="utf-8")


# ---- 耐久ワークフロー ----


class _Run(prt.ReleaseRouting, AnswerRouting, ph.AnswerHold):
    """耐久ワークフローの中の値。打ち直すと記録から同じ値に戻る。"""

    write_hold, resume_lanes = staticmethod(step_answer_hold), staticmethod(step_resume_lanes)

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
        self.ls_ok = True  # 最後の観測で一覧が成功したか。失敗した回の後は全レーンが止まったと読まない（#2509 の I2）
        self.ls_failed_since: Optional[float] = None  # 一覧の失敗が始まった時刻（エポック秒）。成功に戻ると None
        self.ls_ask_until = 0.0  # 一覧の失敗を次に問うてよい時刻（エポック秒）
        self.held: dict[str, dict] = {}  # 一覧に無い依頼への答え。その依頼の問いが届いたら当てる（#2617）
        self.routed = True  # 答えの届け先の耐久ステップを打ってよいか。古い記録の打ち直しでは False（#2510）
        self.init_answer_hold()

    def bump(self) -> int:
        self.seq += 1
        return self.seq

    def pause(self, action: str, **fields) -> None:
        """イベント `pause` は上書きされるため、流れているリリースの最後の起動の指示（release / confirmed）を
        他の知らせにも `release` として添え、知らせが重なっても起動の指示を失わない。リリースの答えを受けた止まっている
        レーンを起こす行（`wake`）も、そのレーンが動き出すまでどの知らせにも添える。"""
        seq = self.bump()
        if action in ("release", "confirmed"):
            self.launch = {"seq": seq, "action": action, **fields}
        elif self.current is not None and self.launch:
            fields = {**fields, "release": self.launch}
        stopped = self.stopped()
        self.wake = [n for n in self.wake if n in stopped]
        wake = {"wake": [f"python3 {RELAY} restart {n}" for n in lanes]} if (lanes := self.wake_lanes()) else {}
        durable.set_event("pause", {"seq": seq, "action": action, **fields, **wake})

    def emit_hold(self, event: dict) -> None:
        durable.set_event("answer-hold", event)

    def progress(self) -> dict:
        return {"lanes": self.lanes, "releases": [{k: v for k, v in r.items() if k != "presented"} for r in self.releases]}

    def summary(self, status: str) -> dict:
        return {
            "status": status,
            "lanes": self.lanes,
            "releases": self.progress()["releases"],
            "pending": [r["id"] for r in self.pending],
            "holds": self.holds,
            "prior_waits": [q for q in self.questions if q.get("loop")],
        }

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
        elif kind in ("gate1", "gate2", "ask", "unmatched"):
            self.receive_question(msg)
        elif kind == "answer":
            self.reply(msg)
        elif kind == "left_stopped":
            self.keep_stopped(msg.get("lane"))
        elif kind == "lanes_changed":  # parallel.py lane add / set の後（#2733 の決定 9）
            self.take_lanes_changed()

    def receive_question(self, msg: dict) -> None:
        """レーンからの問い（gate1 / gate2 / ask）を一覧へ受け付ける。打ち直しは数えず、同じ原因の問いは 1 つにまとめる。"""
        if any(q.get("id") == msg.get("id") for q in self.questions) or msg.get("id") in self.answered:
            return  # 同じ依頼の打ち直しは数えない（id は送る側が依頼の中身から決める）
        key = cause_of(msg)
        same = next((q for q in self.questions if key and q.get("cause") == key), None)
        if same is not None:  # 同じ原因の問いは 1 つにまとめ、答えを依頼してきた全レーンへ配る（異常系 5）
            same["also"] = [*same.get("also", []), msg.get("id")]
            same["merged"] = [*same.get("merged", []), {"id": msg.get("id"), "lane": msg.get("lane"), "question": msg.get("question")}]
            self.publish_questions()
            self.apply_held(msg.get("id"), same.get("id"))
            return
        if msg.get("source") == "ndf-ask:human-action":  # 代われない操作: レーンへ入る 1 行を添える（6d）
            msg = {**msg, "attach": f"python3 {RELAY} attach {msg.get('lane')}"}
        msg = self.replace_same(msg)
        self.questions.append({**msg, "cause": key} if key else msg)
        self.publish_questions()
        self.apply_held(msg.get("id"))

    def apply_held(self, rid, target=None) -> None:
        """保留した依頼 `rid` への答えを、届いた問い（まとめた先があれば `target`）へ当てる。保留の知らせは役目を終える。"""
        if (msg := self.held.pop(rid, None)) is not None:
            self.questions = [q for q in self.questions if q.get("id") != f"{rid}-unmatched"]
            self.reply({**msg, "id": target or rid})

    def publish_questions(self) -> None:
        durable.set_event("questions", {"seq": self.bump(), "items": self.questions})

    def reply(self, msg: dict) -> None:
        """オーガナイザーの答えを `answer-<依頼>` に 1 回だけ立て、問いの一覧から外す。答えの後の処理は持ち主のレーンが行う（6f）。
        置き換えた依頼への答えは今の問いの持ち主へ流す。どちらでもない依頼への答えは捨てずに保留し、run.log へ警告を残し、
        区分 `unmatched` の問いでオーガナイザーへ知らせる（#2617・#2510 の I9）。"""
        rid = msg.get("id")
        if rid in self.answered:
            return
        q = next((q for q in self.questions if q.get("id") == rid), None)
        if q is None:
            self.reply_unlisted(msg)
            return
        for each in [rid, *q.get("also", [])]:
            self.answered.add(each)
            durable.set_event(f"answer-{each}", {k: msg.get(k) for k in ("result", "note", "choice", "stamp", "merge", "by", "at")})
        self.note_answered(q)
        self.questions = [q for q in self.questions if q.get("id") != rid]
        self.publish_questions()
        if q.get("loop"):
            self.land_prior(q, msg)
            return
        self.wake_owners(q)
        self.apply_own(q, list(msg.get("choice") or []))

    def apply_own(self, q: dict, choice: list[str]) -> None:
        """耐久ワークフローが自分で立てた問いへの答えを当てる。「止める」は、起動できないレーンなら起こし直しをやめて
        止まったものと数え、リリースの途中で止まった一時レーンならリリースを失敗として締める（依頼へ failed を答える）。
        リリースを組めない問いの「止める」は計画を止め、「直してから組み直す」は組み直しの依頼を入れ直す。"""
        if q.get("kind") == "build":
            self.apply_build(q, choice)
            return
        if prt.STOP_PLAN not in choice:
            return
        if q.get("kind") == "spawn" and q.get("lane") in self.lanes:
            lane = q["lane"]
            self.lanes[lane].update(started=True, state="stopped", given_up=True)
            # reply が持ち主を再開待ちへ加えた後でも、止めたレーンを起こす 1 行と再開待ちから外す（計画を終えられるように）
            self.wake = [n for n in self.wake if n != lane]
            self.revive = [n for n in self.revive if n != lane]
            self.revive_at.pop(lane, None)
        elif q.get("kind") == "helper" and (rel := self.current) is not None and q.get("n") == rel["n"]:
            if not rel.get("prs"):  # 提示の前に止まったなら、このリリースへ入れた依頼の Pull Request を範囲として締める
                own = {n for r in self.pending if r["id"] in rel.get("requests", []) for n in r.get("prs") or []}
                rel["prs"] = step_presented(rel["dirs"][-1]).get("prs") or sorted(own)
            self.settle(rel, {"result": "failed", "reason": "リリースの途中で止まった一時レーンを、人が止めると答えた"})

    def count_refusal(self, n: str, why: str) -> None:
        """起動か再開の断りを数え、既定 3 回続けて断られるたびに理由を添えて問う（異常系 17）。"""
        self.refusals[n] = self.refusals.get(n, 0) + 1
        limit = spawn_refusals()
        if self.refusals[n] % limit == 0:
            text = f"レーン {n} を {limit} 回起動できない（{why}）"
            self.ask("spawn", f"{self.name}-spawn-{n}-{self.refusals[n]}", n, text, ["起こし直す", "止める"])

    def ask(self, kind: str, rid: str, lane: Optional[str], question: str, options: list[str], **extra) -> None:
        """耐久ワークフローが自分で立てる問い（動かないレーン・一時レーンの停止・起動の断り・一覧の失敗）。同じ id は 1 回だけ。
        レーンに紐づかない問い（`lane` が None）は `lane` と `attach` を持たない。"""
        if rid in self.answered or any(q.get("id") == rid for q in self.questions):
            return
        item = {"kind": kind, "id": rid, "source": "ndf-ask:human-action", "question": question, "options": options}
        where = {"lane": lane, "attach": f"python3 {RELAY} attach {lane}"} if lane else {}
        self.questions.append({**item, **where, **extra})
        self.publish_questions()
        self.apply_held(rid)

    def answer(self, rid: str, result: str, version: Optional[str], n: Optional[int], reason: Optional[str] = None) -> None:
        self.answered.add(rid)
        durable.set_event(f"answer-{rid}", {"result": result, "version": version, "release": n, "reason": reason, "at": None})

    # -- リリース --

    def answer_from_ranges(self) -> None:
        """出したリリースの範囲で答えられる依頼に答えて外す（I15）。依頼の全 PR がそれまでに出せた範囲の和に入れば答える。
        和は古いリリースから順に足し、依頼の最後の PR が出たリリースの版と連番で答える（後のリリースの PR を前のリリースの版で答えない）。"""
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
        """流れているリリースが無く依頼があれば、リリースの組み方を読む。組むなら他のレーンを待たずに提示 1 を組む（I9）。
        組まない・組めないなら、受けている依頼のすべてへその場で答える。古い手順の記録の打ち直しは `open_recorded` を通す。"""
        if not durable.patched("parallel-route"):
            self.open_recorded()
            return
        n = len(self.releases) + 1
        got = step_route(self.main)
        if got["way"] == delivery.SKIP:
            self.skip_release(n, got["reason"])
            return
        if got["way"] == delivery.REFUSE:
            self.refuse_release(n, got["reason"])
            return
        built = step_build(self.main, self.name, n, 1, self.pending)
        if built.get("error"):
            self.refuse_release(n, built["error"])
            return
        self.begin_release(n, built)

    def open_recorded(self) -> None:
        """`parallel.route` の無い記録の打ち直し。記録と同じ道を通すため、古い手順のまま組む（#2616 の決定 2）。"""
        n = len(self.releases) + 1
        built = step_build(self.main, self.name, n, 1, self.pending)
        if built.get("error"):
            self.stop = True
            self.releases.append({"n": n, "state": "failed", "reason": built["error"]})
            return
        self.begin_release(n, built)

    def begin_release(self, n: int, built: dict) -> None:
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
        if any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in self.questions):  # リリース承認ゲートの答えが届いた
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
                if built.get("error"):  # 範囲は決まっているので、範囲の依頼だけへ答えて締める（I4）
                    answered = self.settle(rel, {"result": "failed", "reason": built["error"]})
                    self.ask_rebuild(rel["n"], built["error"], answered)
                    return
                rel["dirs"].append(built["dir"])
                rel["versions"] = [rel.get("version"), built.get("version")]  # 提示ごとの版。選ばれた提示の版をリリースの答えへ渡す
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

    def settle(self, rel: dict, msg: dict) -> list[dict]:
        """リリースを締め、範囲で答えられる依頼へ答える。答えた依頼を返す。"""
        result = msg.get("result")
        if not rel.get("prs"):
            got = step_presented(rel["dirs"][-1])
            rel["prs"] = got.get("prs") or []
        done = {n for r in self.releases if r is not rel and r.get("state") == "released" for n in r.get("prs") or []}
        hit = set(pr.answerable(self.pending, rel["prs"], done))  # 前のリリースで出た PR と合わせて全部が入る依頼に答える
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
            durable.set_event(f"defer-{r['id']}", {"seq": rel["n"], "reason": "範囲に入らないため次のリリースへ回した", "at": None})
        self.current = None
        self.wake_answered(answered)
        return answered

    def wake_answered(self, answered: list[dict]) -> None:
        """止まった宛先を起こす 1 行を知らせへ（異常系 6）。全レーンが止まれば異常系 11 に任せる。組み直しの依頼の答えを読むレーンはいない。"""
        stopped = self.stopped()
        wake = sorted({r["lane"] for r in answered if r.get("lane") in stopped and not r.get("again")})
        if wake and len(stopped) < len(self.order):
            self.wake = sorted({*self.wake, *wake})
            self.pause("notice", lanes=[], attach=[])


@durable.workflow(name="parallel")
def parallel_workflow(main: str, name: str) -> dict:
    init = step_init(main, name)
    if not init.get("ok"):
        return {"status": "stopped", "lanes": {}, "releases": [], "pending": [], "errors": init.get("items")}
    run = _Run(main, name, init["lanes"])
    run.routed = durable.patched("parallel-answers")
    run.pause("running")
    if run.routed:
        run.receive_prior(step_prior_waits(main, name))
    wait = poll_seconds()
    while True:
        if run.stop and (run.current is None or run.force):  # --force はリリースの settle を待たず、一時レーンも止める
            return _handle_stop(run, main)
        # 一時レーンがリリースを流している間（提示のまま MVV 判定で本番へ進む場合も含む）の stop は、settle が届くまで
        # 待ってから終える（設計の失敗の 12 番。作りかけを残さない）。待つ間も inbox を読み、--force と問いを受ける
        if not run.stop:
            _spawn_ready(run, main)
        if not run.stop and run.current is None and run.pending:
            run.answer_from_ranges()
            if run.pending and not run.hold.plan:  # 計画全体の答え待ちの停止の間は新しいリリースを組まない（I4）
                run.open_release()
        if not run.stop and _run_finished(run) and (out := _finish_or_hold(run, main, name)) is not None:
            return out
        run.take(durable.recv("inbox", wait if run.current is None else min(wait, 30)))
        if run.current is not None:
            msg = durable.recv("resume", min(wait, 30))
            if isinstance(msg, dict):
                run.resume(msg)
        if not run.stop:
            run.govern_hold(_observe_run(run, main))


def _finish_or_hold(run: _Run, main: str, name: str) -> Optional[dict]:
    """全レーンが止まった後: 終えられれば要約を返す。答えを当てていない設計の承認か、保留の周回があれば None。"""
    designs = sorted({*step_design_held(main, name, sorted(run.answered)), *(set(run.revive) & run.stopped())} - run.left_stopped)
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
    依存先のレーンが起動して答えの後の再開と同時に動かないためである。再開の印のあるレーンも同じに扱い、計画全体の答え待ちの
    停止の間は起動しない（#2707 の I4・I8）。"""
    if run.hold.plan:
        return
    view, started = _plan_view(run), set(_started(run))
    stopped = run.stopped() - _asking_lanes(run) - set(run.revive) - set(run.marks)
    ready = pp.ready_lanes(view, started, stopped)
    if ready and any(a in stopped for ln in ready for a in ln.after):  # 依存で起こすときだけループの記録を読む
        ready = pp.ready_lanes(view, started, stopped - set(step_waiting_lanes(main, run.name)))
    if ready:
        for n, got in step_spawn(main, run.name, [ln.name for ln in ready]).items():
            run.lanes[n].update(got)
            if got.get("refused"):
                run.count_refusal(n, got["refused"])


def _run_finished(run: _Run) -> bool:
    """リリースの途中でも残りでもなく、答えていない問いも再開の印も無く、全レーンが止まっている（I11）。前のループの待ちの問いは終わりを止めない（#2510 の I12）。"""
    idle = run.current is None and not run.pending and not run.blocking() and not run.marks
    return run.ls_ok and idle and len(run.stopped()) == len(run.order)


def _observe_run(run: _Run, main: str) -> dict:
    """レーンの状態を観測して反映し、承認待ちへ移ったレーンを知らせる。一覧が失敗した回は、一覧の失敗の追跡のほかは何も変えない（#2509 の I1）。"""
    seen = step_observe(main, run.name)
    run.ls_ok = int(seen.get("code") or 0) == 0  # code の無い古い記録は成功として読む（決定 5）
    _track_ls_failure(run, seen)
    if not run.ls_ok:
        return seen
    rows = seen.get("rows") or {}
    run.seen_at = float(seen.get("now") or 0)
    run.clear_revived(rows)
    _ask_stalls(run, rows, run.seen_at)
    _ask_helper(run, rows)
    noticed = apply_observed_states(run, rows)
    if noticed:
        run.pause("notice", lanes=noticed, attach=[f"python3 {RELAY} attach {n}" for n in noticed])
    return seen


def _track_ls_failure(run: _Run, seen: dict) -> None:
    """一覧の失敗が停滞の区切りより長く続いたら、区切りごとに 1 回問う（#2509 の I3）。成功に戻ったら追跡を解き、答えていない
    一覧の失敗の問いを外す（I4。残すと答えを待って計画が終われない）。"""
    if run.ls_ok:
        run.ls_failed_since, run.ls_ask_until = None, 0.0
        if any(q.get("kind") == "observe" for q in run.questions):
            run.questions = [q for q in run.questions if q.get("kind") != "observe"]
            run.publish_questions()
        return
    now, limit = float(seen.get("now") or 0), stall_seconds()
    if run.ls_failed_since is None:
        run.ls_failed_since = now
    if now - run.ls_failed_since < limit or now < run.ls_ask_until or not seen.get("can_ask"):
        return
    run.ls_ask_until = now + limit
    mins = int((now - run.ls_failed_since) // 60)
    rebuild, rid = "ラッパーの環境を作り直す", f"{run.name}-observe-{int(now)}"
    text = f"レーンの状態の一覧（relay.py ls）が {mins} 分続けて失敗している（終了コード {seen.get('code')}: {seen.get('err') or ''}）"
    run.ask("observe", rid, None, text, [rebuild, "待つ"], commands={rebuild: f"python3 {RELAY} status"})


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
    """リリースの途中で一時レーンが止まり、settle もリリース承認ゲートの依頼も、その一時レーンが送った答えていない問い（ask も含む）も
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
        "helper", f"{name}-h{run.launch['seq']}", name, f"一時レーン {name} がリリース {rel['n']} の途中で止まった（settle もリリース承認ゲートの依頼も無い）",
        ["続きから起こし直す", "止める"], commands={"続きから起こし直す": run.launch.get("spawn")}, n=rel["n"],
    )  # fmt: skip


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


def environment_report(main: Path, plan: pp.ParallelPlan) -> tuple[dict, list[dict]]:
    """開始のときの実行環境の報告と結果の項目。レーンがまだ動いていないので計画の数を渡す。"""
    import run_environment

    report = run_environment.survey(main, lanes=len(plan.lanes))
    return report, run_environment.notes(report)
