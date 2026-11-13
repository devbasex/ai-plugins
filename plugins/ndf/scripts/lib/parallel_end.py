"""1 スプリントのレーンの終わり（#3058・#3060）: 終わりの記録・止める・止められなかったレーンの問い・終わりの記録の無い停止の問い・
`released` の後の版の要るまとめ（リリース後テスト → 課題を閉じる）の待ちと一時レーン。

- 終わりの記録はレーンのまとめのプランの最後のステップ（`parallel.py end`）が台帳へ書く行 `lane_end` で、耐久ワークフローは
  その行だけを読んで止める（決定 1）。止めるのは終わりの記録の時刻から `HALT_DELAY` 秒の後で、止める 1 行（`relay.py stop`）の
  終了コードと続く一覧で止まったかを決め、結果を台帳の行 `lane_halt` に残す（決定 2・I3）
- 止められなければ区分 `halt` の問いで止める 1 行を返し、止まって観えるまで立て直す（決定 3・I4）
- 依存の判定と計画の終わりで 1 スプリントのレーンを止まったと数えるのは、終わりの記録があり止まって観えたときだけ（I2）。
  終わりの記録も答え待ちも無いまま停滞の区切りより長く止まったレーンは区分 `unended` で問う（決定 10）
- 差し戻しと失敗の答えは、終わりの記録を受け付けた後なら区分 `after_end` の問いにする（決定 6・I9）
- 版の要るまとめの待ちは `released` の答えから作り、流れているリリースが無いときに一時レーンで 1 本ずつ流す（決定 4・5・I6・I7）

`LaneEnding` は `_Run`（`parallel_run`）が継ぐ。耐久ワークフローの中で呼ぶ部分は、耐久ステップ（`step_halt`）のほかは渡された値だけで
決める。印 `parallel-lane-end` の無い古い記録（`ending` が偽）は今までの道を通す（決定 10）。下の `cmd_end`・`cmd_verified` は
`parallel.py` の副命令 `end`・`verified` で、耐久ワークフローの外で打つ。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import clock
import durable
import parallel_ledger as plg
import parallel_release as pr
from parallel_files import RELAY
from parallel_helper import AGAIN_RE, verify_spawn, verify_task
from parallel_lane import ENDING_EVENT
from parallel_launch import RESTART, _moved_since
from parallel_observe import _asking_lanes
from parallel_repo import relay, relay_proc, stall_seconds

HALT_DELAY = 60  # 終わりの記録から止める 1 行を打つまでの秒。まとめのプランの queue が結果を書き終えるのを待つ（決定 2）
END_KINDS = ("halt", "unended", "after_end")  # 耐久ワークフローがレーンの終わりについて立てる問い（持ち主を起こさない）
HALTED, COUNT_ENDED, REVIVE, WAIT = "止めた", "終わったと数える", "起こし直す", "待つ"
FIX, SKIP, RESUME, STOP = "レーンを足して直す", "見送る", "続きから起こし直す", "止める"
LIVE = ("running", "awaiting_approval")
PARALLEL_PY = Path(__file__).resolve().parents[1] / "parallel.py"


def _utc_at(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@durable.step(name="parallel.halt")
def step_halt(main: str, name: str, lane: str) -> dict:
    """終わったレーンへ `relay.py stop <レーン>` を打ち、続く一覧で止まったかを確かめ、台帳へ `lane_halt` の行を足す。
    止める 1 行が 0 か 3（既に止まっている）で、一覧でそのレーンが止まっているか一覧に無ければ `stopped`、ほかは `failed`。"""
    code = relay(Path(main), "stop", lane, "--json")[0]
    got, res = relay_proc(Path(main), "ls", "--json")
    row = next((it for it in res.get("items") or [] if isinstance(it, dict) and it.get("name") == lane), None)
    state = (row or {}).get("state") or "absent" if got.returncode == 0 else "unknown"
    ok = code in (0, 3) and got.returncode == 0 and state not in LIVE
    out = {"lane": lane, "result": "stopped" if ok else "failed", "code": code, "state": state, "at": _utc_at()}
    try:
        plg.append_ledger_row(Path(main), name, "lane_halt", f"lane_halt:{lane}", {"kind": "lane_halt", "id": lane, **out}, by="organizer")
    except OSError as exc:  # 止めた結果は耐久ステップの戻り値とチェックポイントにも残る（I3）
        print(f"parallel: 止めた結果を台帳へ書けない（{lane}）: {exc}", file=sys.stderr, flush=True)
    return out


class LaneEnding:
    """`_Run` が継ぐ。`_Run` は `main`・`name`・`lanes`・`sprint`・`questions`・`pending`・`current`・`published`・`launches`・`seen_at`・
    `stall_until`・`left_stopped`・`revive`・`marks`・`wake`・`woken`・`hold`・`ask`・`publish_questions`・`launch_once` を持つ。"""

    ending = False  # 印 `parallel-lane-end` の後の記録だけ True（決定 10）

    def announce_ending(self) -> None:
        """印 `parallel-lane-end` の後の記録だけ、イベント `lane-end` を立てる（版の要るまとめを流せる計画の印）。"""
        if self.ending:
            durable.set_event(ENDING_EVENT, True)

    def init_lane_end(self) -> None:
        self.verifies: list[dict] = []  # 版の要るまとめの待ち（先に入ったものから流す。流している待ちも外さずに持つ）
        self.verifying: Optional[str] = None  # 流している待ちの依頼の id
        self.verify_plans: dict[str, dict] = {}  # 依頼の id → {lane, plan}（版の要るまとめのプランを持つ依頼だけ）

    # -- 受け取り --

    def note_request(self, msg: dict) -> None:
        if msg.get("verify_plan"):
            self.verify_plans[str(msg.get("id"))] = {"lane": msg.get("lane"), "plan": msg["verify_plan"]}

    def take_end(self, msg: dict) -> None:
        """台帳の行 `lane_end`・`lane_halt`・`verify`・`verified` を当てる。古い記録では当てない。"""
        if not self.ending:
            return
        kind, lane = msg.get("kind"), msg.get("lane")
        if kind == "lane_end":
            self.take_lane_end(msg)
        elif kind == "lane_halt" and lane in self.lanes and not self.lanes[lane].get("halt"):
            self.lanes[lane]["halt"] = {k: msg.get(k) for k in ("result", "code", "state", "at")}
        elif kind == "verify":
            self.queue_verify(str(msg.get("id")), lane, msg.get("plan"), msg.get("version"), None)
        elif kind == "verified":
            self.take_verified(msg)

    def take_lane_end(self, msg: dict) -> None:
        """終わりの記録は 1 スプリントのレーンの最初の 1 件だけが効く（I1）。記録の `request` の答えが差し戻しか失敗で
        確定していれば、区分 `after_end` の問いを立てる（I9。`end` が確かめた後に確定した答えを失わない）。"""
        lane = msg.get("lane")
        if lane not in self.sprint or self.lanes[lane].get("ended"):
            why = "1 スプリントのレーンでない" if lane not in self.sprint else "2 件目"
            print(f"parallel: レーン {lane} の終わりの記録を読み捨てた（{why}）", file=sys.stderr, flush=True)
            return
        rid = msg.get("request")
        self.lanes[lane]["ended"] = {"at": msg.get("at"), "by": msg.get("by") or lane, "request": rid}
        ans = self.published.get(f"answer-{rid}") if rid else None
        if isinstance(ans, dict) and ans.get("result") in ("rejected", "failed"):
            self.ask_after_end(lane, rid, ans)

    def after_answer(self, rid: str, result: str, version: Optional[str], n: Optional[int], reason: Optional[str]) -> None:
        """依頼へ答えた後: `released` なら版の要るまとめの待ちを作り、差し戻しか失敗で、その依頼の終わりの記録があれば問う。"""
        if not self.ending:
            return
        origin = AGAIN_RE.sub("", rid)
        got = self.verify_plans.get(rid) or self.verify_plans.get(origin)
        if result == "released" and got:
            self.queue_verify(rid, got["lane"], got["plan"], version, n)
        if result in ("rejected", "failed"):
            for lane, st in self.lanes.items():
                if (st.get("ended") or {}).get("request") in (rid, origin):
                    self.ask_after_end(lane, rid, {"result": result, "release": n, "reason": reason})

    def ask_after_end(self, lane: str, rid: str, ans: dict) -> None:
        text = f"終わったレーン {lane} のリリースの依頼 {rid} が {ans.get('result')} で答えられた（{ans.get('reason') or '理由なし'}）"
        add = f"python3 {PARALLEL_PY} lane {self.name} add --from <直すレーンの JSON>"
        extra = {"release": ans.get("release"), "result": ans.get("result"), "reason": ans.get("reason"), "commands": {FIX: add}}
        self.ask("after_end", f"{rid}-after-end", lane, text + "。直すにはレーンを足す", [FIX, SKIP], **extra)

    # -- 版の要るまとめ --

    def queue_verify(self, rid: str, lane: Optional[str], plan: Optional[str], version: Optional[str], n: Optional[int]) -> None:
        if plan and all(v["id"] != rid for v in self.verifies):
            self.verifies.append({"id": rid, "lane": lane, "plan": plan, "version": version, "release": n})

    def take_verified(self, msg: dict) -> None:
        rid = msg.get("id")
        item = next((v for v in self.verifies if v["id"] == rid), None)
        if item is not None and item.get("lane") in self.lanes:
            self.lanes[item["lane"]]["verified"] = msg.get("result")
        self.verifies = [v for v in self.verifies if v["id"] != rid]
        self.verifying = None if self.verifying == rid else self.verifying

    def open_verifies(self) -> list[dict]:
        """計画の終わりを止める待ち。終わりの記録を得られないまま止まったと数えるレーン（人が止めたままにしたか、起動を諦めた）の
        待ちは流れないので数えない（流している待ちは数える）。レーンが動き出して終わりの記録が来れば、外していない待ちが流れる。"""
        stalled = {n for n, st in self.lanes.items() if not st.get("ended") and (n in self.left_stopped or st.get("given_up"))}
        return [v for v in self.verifies if v["id"] == self.verifying or v.get("lane") not in stalled]

    def idle_helpers(self) -> bool:
        """流れているリリースも、流している版の要るまとめも無い（止める依頼をすぐ締めてよい）。"""
        return self.current is None and self.verifying is None

    def verify_helpers(self) -> list[str]:
        """流している版の要るまとめの一時レーン（計画の停止で止める）。"""
        return [v["helper"] for v in self.verifies if v["id"] == self.verifying and v.get("helper")]

    def launch_verify(self) -> None:
        """流れているリリースも流している待ちも無く、計画全体の停止の間でなければ、待ちのうちレーンの終わりの記録（版の要らない
        まとめの完了）があるものの先頭を一時レーンで流す（I7）。まとめより先に課題を閉じない。"""
        if not self.ending or self.verifying is not None or self.current is not None or self.hold.plan:
            return
        item = next((v for v in self.verifies if (self.lanes.get(v.get("lane")) or {}).get("ended")), None)
        if item is None:
            return
        k = 1 + sum(1 for p in self.launches if p.startswith("verify-"))
        helper = f"{pr.helper_name(self.name, k)}v"
        spec = verify_spawn(self.name, item["id"], helper)
        item.update(helper=helper, purpose=f"verify-{item['id']}", launch=spec)
        self.verifying = item["id"]
        self.launch_once(item["purpose"], helper, spec["argv"], spec.get("cwd"), verify=item["id"])  # 起動の問いの「止める」で待ちを外す

    def _watch_verify(self, rows: dict) -> None:
        """版の要るまとめの一時レーンが `verified` を送らずに止まったら、立ち上がりの後に 2 回続けて観たときに問う。"""
        item = next((v for v in self.verifies if v["id"] == self.verifying), None)
        rec = self.launches.get((item or {}).get("purpose") or "") or {}
        row = rows.get((item or {}).get("helper")) or {}
        if item is None or rec.get("result") != "up" or row.get("state") != "stopped" or item["helper"] in _asking_lanes(self):
            return
        since = clock.parse(rec.get("at"), naive="utc")
        if since is None or not _moved_since(row, since.timestamp()):
            return
        item["seen"] = int(item.get("seen") or 0) + 1
        if item["seen"] < 2:
            return
        item["seen"], item["asked"] = 0, int(item.get("asked") or 0) + 1
        text = f"一時レーン {item['helper']} が依頼 {item['id']} の版の要るまとめの途中で止まった（verified が無い）"
        rid = f"{item['helper']}-verify-{item['asked']}"
        self.ask("helper", rid, item["helper"], text, [RESUME, STOP], purpose=item["purpose"], launch=item["launch"], verify=item["id"])

    # -- 観測 --

    def counted(self, stopped: set) -> set:
        """依存の判定と計画の終わりで止まったと数えるレーン（I2）。1 スプリントのレーンは終わりの記録があるか、人が止めたままに
        したか、起動を諦めたものだけ。古い記録は止まって観えたレーンをすべて数える。"""
        if not self.ending:
            return stopped
        return {
            n for n in stopped
            if n not in self.sprint or n in self.left_stopped or self.lanes[n].get("ended") or self.lanes[n].get("given_up")
        }  # fmt: skip

    def observe_ends(self, rows: dict) -> None:
        """観測ごと: 終わったレーンを止め、止められなかったレーンを問い、終わりの記録の無い停止を問い、版の要るまとめを見張る。"""
        if not self.ending:
            return
        for n in sorted(self.sprint & set(self.lanes)):
            if self.lanes[n].get("ended"):
                self._halt_or_ask(n, self.lanes[n])
            else:
                self._ask_unended(n, self.lanes[n], rows.get(n) or {})
        self._watch_verify(rows)

    def _try_halt(self, n: str, st: dict) -> bool:
        """止める記録の無い終わったレーンのラッパーを止めにいく。この観測で問いの判断へ進んでよければ True。"""
        ended = st["ended"]
        if ended.get("by") == "organizer":  # 終わったと数えると答えたレーンはラッパーが止まっている。止める 1 行は打たない
            st["halt"] = {"result": "stopped", "code": None, "state": "stopped", "at": ended.get("at")}
            return False
        at = clock.parse(ended.get("at"), naive="utc")
        if at is not None and self.seen_at - at.timestamp() < HALT_DELAY:
            return False
        st["halt"] = step_halt(self.main, self.name, n)
        if st["halt"].get("result") == "stopped":
            st["state"] = "stopped"
        return True

    def _halt_or_ask(self, n: str, st: dict) -> None:
        """止まったと観えれば止められなかった問いを片付け、止まらなければ問いを立てる。"""
        if not st.get("halt") and not self._try_halt(n, st):
            return
        open_q = [q for q in self.questions if q.get("kind") == "halt" and q.get("lane") == n]
        if st.get("state") == "stopped":
            if open_q:  # 止まって観えた: 止められなかった問いは役目を終える
                self.questions = [q for q in self.questions if q not in open_q]
                self.publish_questions()
            return
        if st["halt"].get("result") == "stopped" and st.get("state") not in LIVE or open_q:
            return
        st["halt_asked"] = int(st.get("halt_asked") or 0) + 1  # 答えた後も動いて観えれば新しい id で立て直す（I4）
        text = f"終わったレーン {n} のラッパーを止められない（終了コード {st['halt'].get('code')}・観えた状態 {st.get('state')}）"
        self.ask("halt", f"{n}-halt-{st['halt_asked']}", n, text, [HALTED], commands={HALTED: f"python3 {RELAY} stop {n}"})

    def _ask_unended(self, n: str, st: dict, row: dict) -> None:
        """終わりの記録も答え待ちも再開の印も起こし直しの待ちも無いまま、停滞の区切りより長く止まって観えたら、区切りごとに 1 回問う。"""
        if not st.get("started") or st.get("state") != "stopped" or st.get("given_up") or n in self.left_stopped:
            return
        waiting = {*_asking_lanes(self), *self.revive, *self.marks, *self.wake, *self.woken, *(r.get("lane") for r in self.pending)}
        if n in waiting:
            st.pop("stopped_seen", None)
            return
        at = clock.parse(row.get("at"), naive="utc")
        since = at.timestamp() if at is not None else float(st.setdefault("stopped_seen", self.seen_at))
        key, now, limit = f"unended:{n}", self.seen_at, stall_seconds()
        if now - since < limit or now < self.stall_until.get(key, 0):
            return
        self.stall_until[key] = now + limit
        text = f"レーン {n} が終わりの記録も答え待ちも無いまま {int((now - since) // 60)} 分止まっている"
        self.ask("unended", f"{n}-unended-{int(now)}", n, text, [COUNT_ENDED, REVIVE, WAIT])

    # -- 答え --

    def wake_owners(self, q: dict) -> None:
        """レーンの終わりの問いは持ち主を起こさない（終わったレーンを起こし直さない。決定 6）。"""
        if q.get("kind") not in END_KINDS:
            super().wake_owners(q)  # type: ignore[misc]

    def apply_end(self, q: dict, choice: list[str]) -> bool:
        """レーンの終わりの問いと版の要るまとめの一時レーンの問いへの答えを当てる。当て終えたら True（`apply_own` を続けない）。"""
        lane = q.get("lane")
        if q.get("verify"):
            item = next((v for v in self.verifies if v["id"] == q["verify"]), None)
            if STOP in choice:
                self.take_verified({"id": q["verify"], "result": "止めた"})
                return True
            if item is not None and set(RESTART) & set(choice):  # relaunch が `<目的>-a<問い>` で起こし直す。見張りを新しい起動へ向ける
                item["purpose"] = f"{q.get('purpose')}-a{q.get('id')}"
            return False
        if q.get("kind") not in END_KINDS:
            return False
        if q.get("kind") == "unended" and lane in self.lanes and not self.lanes[lane].get("ended"):
            if COUNT_ENDED in choice:
                self.lanes[lane]["ended"] = {"at": _utc_at(self.seen_at), "by": "organizer", "request": None}
            elif REVIVE in choice:
                self.wake = sorted({*self.wake, lane})
        return True


# ---- 耐久ワークフローの外: parallel.py の副命令 end・verified と依頼の知らせ ----


def add_end_parsers(sub) -> None:
    """`parallel.py` の副命令 end・verified の引数を足す。"""
    en = sub.add_parser("end", help="1 スプリントのレーンの終わりの記録を台帳へ書く（版の要らないまとめの最後のステップ。#3058）")
    en.add_argument("name")
    en.add_argument("--lane", required=True)
    en.add_argument("--request", help="リリースの依頼の記録（work/request.txt）。答えが差し戻しか失敗なら書かずに 10 で返す")
    vd = sub.add_parser("verified", help="版の要るまとめの一時レーンが流すものを返す。--done で結果を送る（#3058）")
    vd.add_argument("name")
    vd.add_argument("id")
    vd.add_argument("--done")


def _main() -> Path:
    import os

    import decl

    return decl.main_dir(os.getcwd()) or Path(os.getcwd())


def _home_running(main: Path, name: str, lane: Optional[str]) -> Path:
    """計画の置き場（別のリポジトリのレーンでは自分のリポジトリと違う。#2733）。計画が動いていなければ 1 で終える。"""
    import parallel_lane as pl
    from parallel_lane_cli import emit_reply

    got = pl.lane_of(main, lane) if lane else None
    home = pl.home_of(got, main) if got and got[0] == name else main
    if not plg.accepts(plg.plan_state(home, name, pl.run_status(home, name))):
        emit_reply("stopped", f"並列の計画 {name} が動いていない", code=1)
    return home


def cmd_end(a) -> None:
    import parallel_external as pe
    import parallel_lane as pl
    from parallel_lane_cli import emit_reply

    line = f"[ndf-parallel] lane-end {a.name} {a.lane}"
    if pe.organizer_dir() is not None or pe.is_external(a.name):
        emit_reply("ok", f"{line}（外のオーガナイザーのレーンは終わりの記録を書かない）", lane=a.lane, written=False)
    home = _home_running(_main(), a.name, a.lane)
    rid = None
    if a.request:
        try:
            rid = json.loads(Path(a.request).read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError, AttributeError) as exc:
            emit_reply("stopped", f"依頼の記録を読めない: {exc}", code=2)
        ans = pl.read_answer(home, a.name, rid)[0] if rid else None
        if isinstance(ans, dict) and ans.get("result") in ("rejected", "failed"):
            summary = f"リリースの依頼 {rid} の答えが {ans['result']}。終わりの記録を書かずに差し戻しの直しへ戻る"
            emit_reply("gate", summary, code=10, next_step="今のリリース承認ゲートの差し戻しと同じに扱う", result=ans["result"],
                       reason=ans.get("reason"), release=ans.get("release"))  # fmt: skip
    msg = {"kind": "lane_end", "id": a.lane, "lane": a.lane, "by": a.lane, "at": pe.utc_stamp(), **({"request": rid} if rid else {})}
    try:
        pl.deliver_message(home, a.name, msg, "inbox", f"lane-end:{a.lane}")
    except OSError as exc:
        emit_reply("stopped", f"計画の台帳へ書けない: {exc}", code=2)
    print(line, file=sys.stderr, flush=True)
    emit_reply("ok", line, lane=a.lane, request=rid, written=True)


def cmd_verified(a) -> None:
    import parallel_external as pe
    import parallel_lane as pl
    from parallel_lane_cli import emit_reply

    main = _home_running(_main(), a.name, None)
    if not a.done:
        task = verify_task(main, a.name, a.id)
        if task is None:
            emit_reply("stopped", f"依頼 {a.id} の版の要るまとめのプランが台帳に無い", code=1)
        emit_reply("ok", f"一時レーンの仕事: 依頼 {a.id} の版の要るまとめ（リリース後テスト → 課題を閉じる）", **task)
    try:
        done = json.loads(Path(a.done).read_text(encoding="utf-8"))
        results = [str(i.get("result")) for i in done.get("items") or [] if isinstance(i, dict)]
    except (OSError, ValueError, AttributeError) as exc:
        emit_reply("stopped", f"done を読めない: {exc}", code=2)
    res = "完了" if results and all(r == "完了" for r in results) else "失敗"
    try:
        pl.deliver_message(main, a.name, {"kind": "verified", "id": a.id, "result": res, "at": pe.utc_stamp()}, "inbox", f"verified:{a.id}")
    except OSError as exc:
        emit_reply("stopped", f"計画の台帳へ書けない: {exc}", code=2)
    emit_reply("ok", f"依頼 {a.id} の版の要るまとめの結果 {res} を送った", id=a.id, result=res)


def send_verify(main: Path, name: str, lane: str, state: Path, plan: str, version: Optional[str]) -> None:
    """自分の Pull Request が既に本番にあり依頼を送らないとき、版の要るまとめの待ちを知らせる（台帳の行 `verify`）。"""
    import parallel_external as pe
    import parallel_lane as pl

    rid = pl.lane_request_id(state, lane)
    msg = {"kind": "verify", "id": rid, "lane": lane, "plan": plan, "version": version, "at": pe.utc_stamp()}
    pl.deliver_message(main, name, msg, "inbox", f"verify:{rid}")
