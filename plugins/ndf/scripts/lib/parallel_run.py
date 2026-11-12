"""並列の進行（#2436）: 耐久ワークフロー `parallel-<計画名>` と耐久ステップ（起動・観測・停止・リリースを組む・要約）。

耐久ワークフローの中の分岐は耐久ステップの結果とメッセージだけで決まる。時刻・外のコマンド・ファイルを読むのは
すべて耐久ステップである（打ち直しで同じ道を通るため。#1142 の I18）。外へ見せるのはイベントと出力だけである。

- メッセージ `inbox`: `{kind: request | gate1 | gate2 | ask | unmatched | answer | stop | left_stopped, ...}`（レーン・一時レーン・オーガナイザーの答え・`stop`・`park --clear`）
- メッセージ `resume`: `{n, phase: confirm, take}` / `{n, phase: carried, carried}` / `{n, phase: settle, result, version, reason}`
  （`confirm`・`carried`・`settle`）
- リリースの組み方は依頼を受けるたびに耐久ステップ `parallel.route` が宣言から読む。組まないなら `released`、組めないなら
  `failed` と理由を依頼へ答え、計画を止めるかは問い（`kind: build`）への人の答えで決まる（#2616）
- イベント `answer-hold`: `{seq, action: "answer_hold", n, line, hold}`（答え待ちの停止の最新の変化。#2707）
- イベント `pause`: `{seq, action}`（`running` / `release` / `confirmed` / `carried` / `held`。一時レーンが流すものを決める
  （`parallel.py helper` が読む。`parallel.py wait` は出し直しを省いた確定の確かめ直しが通った知らせ（`carried`）だけを読む。#2936・#3020）、`questions`: `{seq, items}`
- 一時レーンの起動と止まったレーンの再起動は耐久ワークフローが打つ（`parallel_launch`。#2936）
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
import parallel_ledger as plg
import parallel_plan as pp
import parallel_release as pr
import parallel_route as prt
from parallel_files import RELAY
from parallel_end import LaneEnding
from parallel_helper import SUPERVISE, helper_spawn, helper_task  # noqa: F401  helper_task は parallel.py とテストが読む
from parallel_launch import LaunchRouting
from parallel_observe import _ask_helper, _asking_lanes, _plan_view, apply_observed_states
from parallel_question import cause_of
from parallel_reply import AnswerRouting, step_prior_waits, step_waiting_lanes
from parallel_repo import dev_version, git_head, mark_organizer, poll_seconds, relay, relay_proc, spawn_refusals, stall_seconds


# ---- 耐久ステップ ----


@durable.step(name="parallel.init")
def step_init(main: str, name: str) -> dict:
    """計画を読み、検査し、オーガナイザーの印を書く。"""
    plan, items, _ = pp.check_data(pp.load_plan(Path(main), name))
    if plan is None:
        return {"ok": False, "items": items}
    mark_organizer(Path(main), name)
    return {"ok": True, "lanes": [ln.model_dump(mode="json") for ln in plan.lanes]}


def _spawn_lane(main: str, plan: pp.ParallelPlan, n: str) -> dict:
    """レーン `n` を起動する。成功（同じ名前の 2 本目の断りも成功とする）は `started`、断りは `refused`（I7）。"""
    ln = plan.lane(n)
    code, res = relay(ln.repo_dir(Path(main)), "spawn", "--name", n, "--json", "--", pp.first_input(ln))  # cwd はレーンのリポジトリ
    why = ((res.get("items") or [{}])[0] or {}).get("result")
    if code == 0 or why == "name-taken":
        return {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return {"refused": why or res.get("summary") or f"exit {code}"}


@durable.step(name="parallel.spawn")
def step_spawn(main: str, name: str, lanes: list[str]) -> dict:
    """`lanes` を起動する（印 `parallel-admit` の無い記録の道。マシンの本数を見ない）。"""
    plan = pp.load_valid_plan(Path(main), name)
    return {n: _spawn_lane(main, plan, n) for n in lanes}


@durable.step(name="parallel.admit")
def step_admit(main: str, name: str, lanes: list[str]) -> dict:
    """`lanes` を、マシンのプランの本数を起動の排他の中で数えてから起動する。上限を超えるレーンは `waiting`（#2999 の I7〜I11）。"""
    import parallel_machine

    plan = pp.load_valid_plan(Path(main), name)
    want = [(n, plan.lane(n).plans) for n in lanes]
    return parallel_machine.admit_lanes(Path(main), name, want, lambda n: _spawn_lane(main, plan, n))


@durable.step(name="parallel.progress")
def step_progress(main: str, name: str, progress: dict) -> None:
    """`plan.md` を描き直す（起動の待ちの組が変わったとき。#2999 の I14）。"""
    render_progress(main, name, progress)


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


@durable.step(name="parallel.carry")
def step_carry(main: str, first: str, head: str) -> dict:
    """承認の後に加わった範囲が文書だけの範囲かを判定する（#3020）。分岐を記録した値だけで決めるため耐久ステップにする。
    Pull Request の一覧は取らない（伝える一覧は確かめ直しの知らせが提示 2 の記録から作り、gh の失敗で出し直しへ倒さないため）。"""
    return pr.carry_check(Path(main), first, head, prs_of=lambda *_: [])


@durable.step(name="parallel.build")
def step_build(main: str, name: str, seq: int, k: int, requests: Optional[list[dict]] = None, carried: bool = False) -> dict:
    """まとめたリリースの提示のプランと本番のプランを書き、受けている依頼を `releases/requests.json` へ書く
    （`parallel.py present` が耐久ワークフローの外からレーンの内訳に読む）。組めない理由は `error` で返し、例外を上げない（#2616 の決定 3）。問いは理由の一致でまとめるため、理由に連番と提示の番号を入れない。"""
    try:
        pr.write_requests(Path(main), name, list(requests or []))
        if seq > pr.MAX_HELPER_SEQ:
            return {"error": f"リリースの連番が一時レーンの名前の上限 {pr.MAX_HELPER_SEQ} を超える"}
        version = dev_version(Path(main))
        if not version:
            return {"error": prt.NO_VERSION}
        d = pr.build_plans(Path(main), name, seq, k, version, **({"carried": True} if carried else {}))
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
    """役割の印を消し、止める指示のファイルと再開の印を消し、`plan.md` を描画し直し、台帳を閉じる（I11）。"""
    mark_organizer(Path(main), name, clear=True)
    plg.close_ledger(Path(main), name)
    ph.clear_plan(Path(main), name)
    render_progress(main, name, progress)


@durable.step(name="parallel.restore")
def step_restore(main: str, name: str) -> dict:
    """作り直しの材料（`parallel_ledger.restore_run`）。一覧に居るレーンを添える（チェックポイントの無い計画の I10）。"""
    items = relay_proc(Path(main), "ls", "--json")[1].get("items") or []
    got = plg.restore_run(Path(main), name, [it["name"] for it in items if isinstance(it, dict) and it.get("name")])
    return {**got, "limits": ph.limit_values(Path(main)), "hold_file": {"hold": ph.read_hold(Path(main), name)}}  # 停止を戻す材料


@durable.step(name="parallel.ledger-read")
def step_ledger_read(main: str, name: str, offset: int, msg, topic: str) -> dict:
    return plg.read_run(Path(main), name, offset, msg, topic)


@durable.step(name="parallel.checkpoint")
def step_checkpoint(main: str, name: str, state: dict) -> None:
    plg.save_checkpoint(Path(main), name, state)


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


class _Run(LaneEnding, plg.RunState, prt.ReleaseRouting, AnswerRouting, ph.AnswerHold, LaunchRouting):
    """耐久ワークフローの中の値。打ち直すと記録から同じ値に戻る。レーンの終わりと版の要るまとめは `LaneEnding`（#3058）。"""

    write_hold, resume_lanes = staticmethod(step_answer_hold), staticmethod(step_resume_lanes)
    read_ledger, write_checkpoint = staticmethod(step_ledger_read), staticmethod(step_checkpoint)

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
        self.launching = True  # 起動と再起動を耐久ワークフローが打つか。古い記録の打ち直しでは False（#2936）
        self.admitting = True  # 依存のそろったレーンの起動をマシンのプランの本数に掛けるか。古い記録の打ち直しでは False（#2999）
        self.carrying = True  # 承認の後に加わった範囲を判定してよいか。古い記録の打ち直しでは False（#3020）
        self.launches: dict[str, dict] = {}  # 起動の目的 → 起動の記録（#2936 の I2・I3）
        self.helper_seen: dict[str, int] = {}  # 一時レーン → 今の起動の後に続けて止まって観えた回数（I4）
        self.woken: set[str] = set()  # 設計の答えを当てるために再起動し、まだ動いて観えていないレーン
        self.wake_tries: dict[str, int] = {}  # レーン → 設計の答えが当たるまでに再起動した回数
        self.init_answer_hold()
        self.init_lane_end()
        self.init_run_state(False)

    def bump(self) -> int:
        self.seq += 1
        return self.seq

    def pause(self, action: str, **fields) -> None:
        """イベント `pause` は上書きされるため、流れているリリースの最後の起動（release / confirmed）を他のイベントにも
        `release` として添え、一時レーンが流すもの（`helper_task`）を失わない。"""
        seq = self.bump()
        fields = self.legacy_spawn(fields)
        if action in ("release", "confirmed"):
            self.launch = {"seq": seq, "action": action, **fields}
        elif self.current is not None and self.launch:
            fields = {**fields, "release": self.launch}
        durable.set_event("pause", {"seq": seq, "action": action, **fields, **self.legacy_wake()})

    def publish(self, key: str, value) -> None:
        """答え・次のリリースへ回した知らせ・範囲のイベントを立て、作り直しの後に読み手が読めるよう控える（決定 6）。"""
        durable.set_event(key, value)
        self.published[key] = value

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
                self.note_request(msg)
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
        elif kind in plg.ENDS:  # レーンの終わりと版の要るまとめ（#3058）
            self.take_end(msg)

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
        durable.set_event("questions", {"seq": self.bump(), "items": self.questions, **self.legacy_mark()})

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
            self.publish(f"answer-{each}", {k: msg.get(k) for k in ("result", "note", "choice", "stamp", "merge", "by", "at")})
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
        リリースを組めない問いの「止める」は計画を止め、「直してから組み直す」は組み直しの依頼を入れ直す。保留したループの残りを流す
        一時レーンを起こせない問いの「止める」も計画を止める（保留の中身はループの記録に残る）。"""
        if self.apply_end(q, choice):  # レーンの終わりの問いと版の要るまとめの一時レーンの問い（#3058）
            return
        if q.get("kind") == "build":
            self.apply_build(q, choice)
            return
        if self.relaunch(q, choice) or prt.STOP_PLAN not in choice:
            return
        if q.get("kind") == "stall" and self.launching and q.get("lane") in self.lanes:
            step_stop_lanes(self.main, [q["lane"]])  # 古い記録の打ち直しへは足さない（旧版はこの答えで何もしなかった）
        elif q.get("kind") == "spawn" and str(q.get("purpose") or "").startswith("held-"):
            self.stop = True  # 保留したループの残りを流す一時レーンを起こせない: 保留の中身はループの記録に残したまま計画を止める
        elif q.get("kind") == "spawn" and q.get("lane") in self.lanes:
            lane = q["lane"]
            self.lanes[lane].update(started=True, state="stopped", given_up=True)
            # reply が持ち主を再開待ちへ加えた後でも、止めたレーンを起こす 1 行と再開待ちから外す（計画を終えられるように）
            self.wake = [n for n in self.wake if n != lane]
            self.revive = [n for n in self.revive if n != lane]
            self.revive_at.pop(lane, None)
        elif q.get("kind") in ("helper", "spawn") and (rel := self.current) is not None and q.get("n") == rel["n"]:
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
        self.publish(f"answer-{rid}", {"result": result, "version": version, "release": n, "reason": reason, "at": None})
        self.after_answer(rid, result, version, n, reason)

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
        rel = {"n": n, "state": "presented", "version": built["version"], "dirs": [built["dir"]], "presented": [],
               "requests": [r["id"] for r in self.pending]}  # fmt: skip
        self.releases.append(rel)
        self.current = rel
        self.emit_release(rel, 1, represent=False)

    def emit_release(self, rel: dict, k: int, represent: bool) -> None:
        d = Path(rel["dirs"][k - 1])
        spec = helper_spawn(self.name, rel["n"], k)
        purpose = f"rel-{rel['n']}-{k}-present"
        self.pause("release", n=rel["n"], k=k, represent=represent, pipeline=pr.pipeline_cmd(SUPERVISE, d), purpose=purpose, **spec)
        self.launch_helper()

    def resume(self, msg: dict) -> None:
        rel = self.current
        if rel is None or msg.get("n") != rel["n"]:
            return
        if any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in self.questions):  # リリース承認ゲートの答えが届いた
            self.questions = [q for q in self.questions if not (q.get("kind") == "gate2" and q.get("n") == rel["n"])]
            self.publish_questions()
        if msg.get("phase") == "confirm":
            self.confirm(rel, msg.get("take"))
        elif msg.get("phase") == "carried":
            self.carried_ok(rel, msg)
        elif msg.get("phase") == "settle":
            self.settle(rel, msg)

    def carried_ok(self, rel: dict, msg: dict) -> None:
        """一時レーンの確かめ直し（`parallel.py carried`）が通ったら、提示 2 の範囲の Pull Request を添えてオーガナイザーへ知らせる。
        答えの時点では知らせない（確かめ直しが承認ゲートへ倒れると、伝えた範囲と提示 2 の範囲が食い違うため。#3020）。
        引き継ぎを外した後（提示 2 への答えの後）に遅れて届いたものは読み飛ばす。"""
        if not rel.get("carried") or not isinstance(msg.get("carried"), dict):
            return
        rel["carried"] = {**rel["carried"], **msg["carried"]}
        self.pause("carried", n=rel["n"], k=2, carried=rel["carried"])

    def confirm(self, rel: dict, take: Optional[str]) -> None:
        """承認の答えを受けたら 1 度だけ比べる。出し直しの提示への答えでは比べない（I14）。進んでいて、加わった範囲が
        文書だけなら出し直しを省き、引き継ぐ形の提示 2 で確定する（#3020 の I1・I3）。提示 2 への答えでは引き継ぎを外す。"""
        if len(rel["dirs"]) == 1 and not self.approved_only:  # 新しい記録は提示 1 で確定し、先頭と比べない（#3070 の I5）
            first = step_presented(rel["dirs"][0])
            head = step_head(self.main)
            if pr.moved(first.get("sha") or "", head):
                check = step_carry(self.main, first.get("sha") or "", head) if self.carrying else {"carry": False}
                built = step_build(self.main, self.name, rel["n"], 2, self.pending, carried=bool(check.get("carry")))
                if built.get("error"):  # 範囲は決まっているので、範囲の依頼だけへ答えて締める（I4）
                    answered = self.settle(rel, {"result": "failed", "reason": built["error"]})
                    self.ask_rebuild(rel["n"], built["error"], answered)
                    return
                rel["dirs"].append(built["dir"])
                rel["versions"] = [rel.get("version"), built.get("version")]  # 提示ごとの版。選ばれた提示の版をリリースの答えへ渡す
                if not check.get("carry"):
                    self.emit_release(rel, 2, represent=True)
                    return
                rel["carried"] = {"from": first.get("sha"), "head": head, "prs": check.get("added") or []}
                self.confirm_k(rel, 2, {}, carried=rel["carried"])  # 提示 2 の範囲は一時レーンの提示のプランが作る
                return
            k = 1
        else:
            k = 2 if take == "new" else 1
            rel.pop("carried", None)
        self.confirm_k(rel, k, step_presented(rel["dirs"][k - 1]))

    def confirm_k(self, rel: dict, k: int, got: dict, **extra) -> None:
        versions = rel.get("versions") or [rel.get("version")]
        rel.update(state="confirmed", k=k, sha=got.get("sha"), prs=got.get("prs") or [], version=versions[min(k, len(versions)) - 1])
        plan, spec = str(Path(rel["dirs"][k - 1]) / "release.json"), helper_spawn(self.name, rel["n"], k)
        # 引き継ぐ確定（提示のプランを流す）と、その後に人が承認した確定（確定の後の 3 手）は別の起動にする
        purpose = f"rel-{rel['n']}-{k}-" + ("carried" if extra.get("carried") else "confirmed")
        self.pause("confirmed", n=rel["n"], k=k, plan=plan, purpose=purpose, **extra, **spec)
        self.launch_helper()

    def settle(self, rel: dict, msg: dict) -> list[dict]:
        """リリースを締め、範囲で答えられる依頼へ答える。答えた依頼を返す。"""
        result = msg.get("result")
        if not rel.get("prs"):
            got = step_presented(rel["dirs"][-1])
            rel["prs"], rel["sha"] = got.get("prs") or [], rel.get("sha") or got.get("sha")
        added = []  # 出し直しを省いたときの確かめ直しの範囲の Pull Request（#3020 の I8）
        if rel.get("carried") and len(rel["dirs"]) > 1:
            before = set(step_presented(rel["dirs"][0]).get("prs") or [])
            added = [n for n in rel["prs"] if n not in before]
        done = {n for r in self.releases if r is not rel and r.get("state") == "released" for n in r.get("prs") or []}
        hit = set(pr.answerable(self.pending, rel["prs"], done))  # 前のリリースで出た PR と合わせて全部が入る依頼に答える
        answered = [r for r in self.pending if r["id"] in hit]
        if result == "released":
            rel.update(state="released", version=msg.get("version") or rel.get("version"))
            got = {"sha": rel.get("sha"), "version": rel["version"], "prs": rel["prs"], "added": added}
            self.publish(f"range-{rel['n']}", {**got, "lanes": sorted({r["lane"] for r in answered})})
            for r in answered:
                self.answer(r["id"], "released", rel["version"], rel["n"])
        else:
            rel.update(state="rejected" if result == "rejected" else "failed", reason=msg.get("reason"))
            for r in answered:
                self.answer(r["id"], rel["state"], None, rel["n"], msg.get("reason"))
        self.pending = [r for r in self.pending if r["id"] not in hit]
        for r in self.pending:
            self.publish(f"defer-{r['id']}", {"seq": rel["n"], "reason": "範囲に入らないため次のリリースへ回した", "at": None})
        self.current = None
        self.wake_answered(answered)
        return answered

    def wake_answered(self, answered: list[dict]) -> None:
        """止まった宛先を再起動の対象へ入れる（異常系 6）。全レーンが止まれば異常系 11 に任せる。組み直しの依頼の答えを読むレーンはいない。"""
        stopped = self.stopped()
        stopped -= {n for n, st in self.lanes.items() if st.get("ended")}  # 終わったレーンは起こさない（#3058）
        wake = sorted({r["lane"] for r in answered if r.get("lane") in stopped and not r.get("again")})
        if wake and len(stopped) < len(self.order):
            self.wake = sorted({*self.wake, *wake})


@durable.workflow(name="parallel")
def parallel_workflow(main: str, name: str) -> dict:
    ledgered = durable.patched("parallel-ledger")  # 途中の記録は今までの道を通す（決定 7）
    init = step_init(main, name)
    if not init.get("ok"):
        return {"status": "stopped", "lanes": {}, "releases": [], "pending": [], "errors": init.get("items")}
    run = _Run(main, name, init["lanes"])
    run.init_run_state(ledgered)
    run.routed = durable.patched("parallel-answers")
    run.launching, run.sprint_hold_wake = durable.patched("parallel-launch"), durable.patched("parallel-sprint-hold-wake")  # #2936・#3000
    run.admitting = durable.patched("parallel-admit")  # 起動の判定をマシンの本数に掛けるか。古い記録は今までの道（#2999 の決定 6）
    run.carrying = durable.patched("parallel-docs-carry")  # 印の無い古い記録は判定せずに出し直す（#3020 の I7）
    run.ending, run.approved_only = durable.patched("parallel-lane-end"), durable.patched("parallel-approved-sha")  # #3058・#3070
    restored = step_restore(main, name) if ledgered else None
    run.adopt(restored)  # 台帳から戻し、running と戻した問いを立てる
    run.restore_hold((restored or {}).get("limits"), (restored or {}).get("hold_file"))  # 最初のレーン起動・リリースの前に戻す
    run.launch_helper()  # 作り直し: 流れているリリースの一時レーンを、起動の記録が無ければ起動する（I3）
    if run.routed:
        run.receive_prior(step_prior_waits(main, name))
    wait = poll_seconds()
    while True:
        for after_stop, step in _TICK:  # 止める依頼の後は after_stop の手順だけを打つ。手順が値を返したら、それで終える
            if (after_stop or not run.stop) and (out := step(run, main, name, wait)) is not None:
                return out
        run.save()


def _tick_release(run: _Run, main: str, name: str, wait: float) -> None:
    run.launch_verify()  # 版の要るまとめを先に流す。流している間は次のリリースを組まない（#3058 の決定 5・I7）
    if run.current is None and run.pending and run.verifying is None:
        run.answer_from_ranges()
        if run.pending and not run.hold.plan:  # 計画全体の答え待ちの停止の間は新しいリリースを組まない（I4）
            run.open_release()


def _tick_finish(run: _Run, main: str, name: str, wait: float) -> Optional[dict]:
    if _run_finished(run) and (out := _finish_or_hold(run, main, name)) is not None:
        return run.save() or out  # 閉じた計画は作り直さないため、最後の答えをチェックポイントに残して終える
    return None


def _tick_govern(run: _Run, main: str, name: str, wait: float) -> None:
    run.govern_hold(_observe_run(run, main))
    run.launch_wakes()


# 周回の 1 回の手順を打つ順に並べる。(止める依頼の後も打つか, 手順)。--force はリリースの settle を待たず、一時レーンも止める。
# 一時レーンがリリースを流している間（提示のまま MVV 判定で本番へ進む場合も含む）の stop は、settle が届くまで待ってから終える
# （設計の失敗の 12 番。作りかけを残さない）。版の要るまとめを流している間も verified が届くまで待つ（#3058）。
# 待つ間も inbox を読み、--force と問いを受ける
_TICK = (
    (True, lambda run, main, name, wait: _handle_stop(run, main) if run.stop and (_idle_helpers(run) or run.force) else None),
    (False, lambda run, main, name, wait: _spawn_ready(run, main)),
    (False, _tick_release),
    (False, _tick_finish),
    (True, lambda run, main, name, wait: run.receive(durable.recv("inbox", wait if run.current is None else min(wait, 30)), "inbox")),
    (True, lambda run, main, name, wait: run.receive(durable.recv("resume", min(wait, 30)), "resume") if run.current is not None else None),
    (False, _tick_govern),
)


def _finish_or_hold(run: _Run, main: str, name: str) -> Optional[dict]:
    """全レーンが止まった後: 終えられれば要約を返す。答えを当てていない設計の承認か、保留したループの 1 回があれば None。"""
    gave_up = {n for n, st in run.lanes.items() if st.get("given_up")}  # 再起動の失敗の問いへ「止める」と答えたレーンは起こさない
    designs = sorted({*step_design_held(main, name, sorted(run.answered)), *(set(run.revive) & run.stopped())} - run.left_stopped - gave_up)
    run.wake_tries = {n: c for n, c in run.wake_tries.items() if n in designs}  # 答えが当たったレーンは再起動の回数を数え直す
    if designs:  # 持ち主が答え（設計の承認・1 スプリントのレーンの問い）を当てるまで終えない。動いて観えるまで 1 回ずつ再起動する（6f）
        run.wake = sorted({*run.wake, *run.wake_or_ask(set(designs) - run.woken)})
        return None
    held = step_held_loops(main, name, sorted(run.answered))
    if not held:
        step_finish(main, name, run.progress())
        return run.summary("finished")
    # 止まったレーンの保留したループの 1 回のまとめを一時レーンで流してから終える（異常系 11）。一時レーンは 1 本ずつ起こす
    todo = [x for x in held if x not in run.held_spawned]
    if todo and not set(run.held_spawned) & set(held):
        run.held_spawned.append(todo[0])
        seq = len(run.releases) + len(run.held_spawned)
        spec = helper_spawn(name, seq, 0, pending=todo[0], cwd=prt.step_loop_repo(main, name, todo[0]))  # 別リポジトリはそこで（#2733）
        run.pause("held", loops=[todo[0]], purpose=f"held-{todo[0]}", **spec)
        run.launch_once(f"held-{todo[0]}", pr.helper_name(name, seq), spec["argv"], spec["cwd"])
    return None


def _started(run: _Run) -> list[str]:
    return [n for n, st in run.lanes.items() if st.get("started")]


def _idle_helpers(run: _Run) -> bool:
    """流れているリリースも、流している版の要るまとめも無い（止める依頼をすぐ締めてよい）。"""
    return run.current is None and run.verifying is None


def _handle_stop(run: _Run, main: str) -> dict:
    """止める依頼を受けた後: 起動済みのレーンを止め、記録を閉じて要約を返す。"""
    helper = [pr.helper_name(run.name, run.current["n"])] if run.force and run.current is not None else []
    if run.force and run.verifying is not None:  # 版の要るまとめを流している一時レーンも止める（#3058）
        helper += [v["helper"] for v in run.verifies if v["id"] == run.verifying and v.get("helper")]
    left = step_stop_lanes(main, _started(run) + helper)
    step_finish(main, run.name, run.progress())
    return {**run.summary("stopped"), "not_stopped": left}


def _spawn_ready(run: _Run, main: str) -> None:
    """起動の条件がそろったレーンを起動し、結果をレーンの状態へ反映する。

    保留中のレーン（答えていない問いを持つ・答えをまだ当てていない `revive`・記録が `waiting_only` で答えを待つループ）は、
    ラッパーが止まっていても依存の判定では止まったと数えない（I8）。問いを保留して自分のラッパーを止めたレーンの後に、
    依存先のレーンが起動して答えの後の再開と同時に動かないためである。再開の印のあるレーンも同じに扱い、計画全体の答え待ちの
    停止の間は起動しない（#2707 の I4・I8）。"""
    if run.hold.plan:
        return
    view, started = _plan_view(run), set(_started(run))
    stopped = run.counted(run.stopped()) - _asking_lanes(run) - set(run.revive) - set(run.marks)  # 終わりの記録で数える（#3058 の I2）
    ready = pp.ready_lanes(view, started, stopped)
    if ready and any(a in stopped for ln in ready for a in ln.after):  # 依存で起こすときだけループの記録を読む
        ready = pp.ready_lanes(view, started, stopped - set(step_waiting_lanes(main, run.name)))
    if not ready:
        return
    import parallel_machine

    before = parallel_machine.waiting_view(run.lanes)
    got = (step_admit if run.admitting else step_spawn)(main, run.name, [ln.name for ln in ready])
    for n, why in parallel_machine.apply_launches(run.lanes, got):
        run.count_refusal(n, why)
    if run.admitting and parallel_machine.waiting_view(run.lanes) != before:
        step_progress(main, run.name, run.progress())


def _run_finished(run: _Run) -> bool:
    """リリースの途中でも残りでもなく、答えていない問いも再開の印も無く、全レーンが止まっている（I11）。前のループの待ちの問いは終わりを止めない（#2510 の I12）。"""
    idle = run.current is None and not run.pending and not run.blocking() and not run.marks and not run.verifies
    return run.ls_ok and idle and len(run.counted(run.stopped())) == len(run.order)


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
    apply_observed_states(run, rows)
    run.observe_ends(rows)  # 終わったレーンを止め、終わりの記録の無い停止と版の要るまとめを見張る（#3058）
    run.release_woken(rows)  # 動いて観えたら、または動いて観える前に今の再起動より後で止まったら、設計の答えの再起動を解く
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
        legacy = run.legacy_commands({"起こし直す": f"python3 {RELAY} restart {n}", "止める": f"python3 {RELAY} stop {n}"})
        run.ask(
            "stall", f"{n}-s{int(now)}", n, f"レーン {n} が {mins} 分動いていない", ["起こし直す", "止める", "待つ"], **legacy
        )  # fmt: skip  答えは耐久ワークフローが当てる（apply_own。#2936 の決定 6）。古い記録はオーガナイザーが commands の 1 行を打つ


# ---- 外の入口 ----


def open_parallel_record(main: Path, name: str, lock_timeout: Optional[float] = 0, retire_if=None) -> durable.Launched:
    return durable.launch(
        pl.KIND, pl.record_identity(main, name), keep=pl.workflow_id(name), lock_timeout=lock_timeout, retire_if=retire_if
    )


def run_plan(main: Path, name: str) -> tuple[dict, bool]:
    """耐久ワークフローを始めるか続け、終わるまで待つ。(出力, 再開か) を返す。失われた計画は `launch` の排他の中で記録を退避し、
    同じ ID で新しく始めて台帳から状態を戻す（耐久ワークフローの作り直し。決定 4）。閉じた計画は作り直さない（I11）。"""
    wid = pl.workflow_id(name)
    if plg.plan_state(main, name, pl.run_status(main, name)) == "closed" and pl.run_status(main, name) != "SUCCESS":
        return {"status": "closed", "lanes": {}, "releases": []}, False
    opened = open_parallel_record(main, name, retire_if=lambda: plg.plan_state(main, name, pl.run_status(main, name)) in ("lost", "legacy"))
    st = durable.status(wid)
    resumed = st is not None
    if st is None:
        plg.open_run(main, name, opened.retired)
        durable.start(durable.WorkflowRef(wid, 1, "start"), parallel_workflow, str(main), name)
    return durable.output_of(wid), resumed


def environment_report(main: Path, plan: pp.ParallelPlan) -> tuple[dict, list[dict]]:
    """開始のときの実行環境の報告と結果の項目。レーンがまだ動いていないので計画の数を渡す。"""
    import run_environment

    report = run_environment.survey(main, lanes=len(plan.lanes))
    return report, run_environment.notes(report)
