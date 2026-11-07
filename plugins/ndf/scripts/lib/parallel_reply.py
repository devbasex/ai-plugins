"""並列の計画の答えの届け方（#2510）: 同じ行の問いの置き換え・置き換えた依頼への答え・前のループの待ち・当たらなかった答え・
止まった持ち主を起こす対象。`parallel_run._Run` が継ぐ。耐久ワークフローの中で動くので、外を読むのは耐久ステップだけである。"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import durable
import gate1_requests as g1
import parallel_plan as pp
import parallel_route as prt


@durable.step(name="parallel.prior-waits")
def step_prior_waits(main: str, name: str) -> dict:
    """計画を始めるときの前のループの待ち（#2510 の決定 11）。"""
    msgs, skipped = g1.prior_requests(main, name, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"requests": msgs, "skipped": skipped}


@durable.step(name="parallel.prior-answer")
def step_prior_answer(main: str, name: str, q: dict, answer: dict, stamp: Optional[str] = None) -> dict:
    """前のループの待ちの問いへの答えを、そのループの `loop answer` で当てる。"""
    return g1.answer_prior(main, pp.plan_dir(Path(main), name), q, answer, stamp)


@durable.step(name="parallel.waiting-lanes")
def step_waiting_lanes(main: str, name: str) -> list[str]:
    return prt.waiting_lanes(Path(main), name)


class AnswerRouting:
    """答えを読んで動く側へ届ける。`_Run` の値（`questions`・`answered`・`revive`・`wake`・`routed` ほか）を使う。"""

    def replace_same(self, msg: dict) -> dict:
        """同じ行の設計の承認の問い（`lane` か `loop` と `pr` が同じで id が違う）を一覧から外し、`replaced` に引き継ぐ（I11）。"""
        if msg.get("kind") != "gate1":
            return msg
        owner = "loop" if msg.get("loop") else "lane"
        old = [q for q in self.questions if q.get("kind") == "gate1" and q.get(owner) == msg.get(owner) and q.get("pr") == msg.get("pr")]
        if not old:
            return msg
        self.questions = [q for q in self.questions if q not in old]
        chain = [x for q in old for x in ({"id": q.get("id"), "stamp": q.get("stamp")}, *(q.get("replaced") or []))]
        return {**msg, "replaced": [*(msg.get("replaced") or []), *chain]}

    def blocking(self) -> list[dict]:
        """計画の終わりを止める問い。前のループの待ちは止めない（I12）。"""
        return [q for q in self.questions if not q.get("loop")]

    def reply_unlisted(self, msg: dict) -> None:
        """一覧に無い依頼への答え。置き換えた依頼なら今の問いの持ち主へ流し、ほかは保留して run.log へ警告を残し、知らせる。"""
        rid = msg.get("id")
        holder = next((x for x in self.questions if any(r.get("id") == rid for r in x.get("replaced") or [])), None)
        if holder is not None:
            self.reply_replaced(holder, msg)
        elif rid and rid not in self.held:
            self.held[rid] = msg
            print(f"parallel: 一覧に無い依頼 {rid} への答えを保留した（届いたら当てる）", file=sys.stderr, flush=True)
            if self.routed:
                self.ask_unmatched(rid, "問いの一覧に無い依頼への答え。その依頼が届いたら当たる")

    def reply_replaced(self, holder: dict, msg: dict) -> None:
        """置き換えた依頼への答え。答えのイベントを立て、今の問いの持ち主へ流す: レーンは起こす（当てるかは周回の境で判定する）、
        前のループは置き換えた問いの印と今の先頭を比べて当てる。"""
        rid = msg.get("id")
        self.answered.add(rid)
        durable.set_event(f"answer-{rid}", {k: msg.get(k) for k in ("result", "note", "choice", "stamp", "merge", "by", "at")})
        if holder.get("loop"):
            self.land_prior(holder, msg, next(r.get("stamp") or "" for r in holder["replaced"] if r.get("id") == rid))
        else:
            self.wake_owners(holder)

    def land_prior(self, q: dict, msg: dict, stamp: Optional[str] = None) -> None:
        """前のループの待ちの問いへの答えを当てる。先頭が動いていれば新しい先頭の問いで置き換え、当てられなければ区分 `unmatched` で知らせる。"""
        fields = {k: msg.get(k) for k in ("result", "note", "choice", "by")}
        got = step_prior_answer(self.main, self.name, q, fields, stamp)
        if got.get("unmatched"):
            self.ask_unmatched(msg.get("id"), got["unmatched"])
        elif got.get("reask"):
            old = [{"id": q.get("id"), "stamp": q.get("stamp")}, *(q.get("replaced") or [])]
            self.receive_question({**got["reask"], "replaced": old})
        elif stamp is not None:  # 置き換えた問いへの答えが当たった: 今の問いは役目を終える
            self.answered.add(q.get("id"))
            self.questions = [x for x in self.questions if x.get("id") != q.get("id")]
            self.publish_questions()

    def ask_unmatched(self, answered: str, reason: str, question: Optional[str] = None) -> None:
        """当たらなかった答えを区分 `unmatched` の問いにする（I9）。答えるまで計画は終わらない。"""
        rid = f"{answered}-unmatched"
        if rid not in self.answered and all(q.get("id") != rid for q in self.questions):
            text = question or f"依頼 {answered} への答えを当てられなかった: {reason}"
            self.questions.append(
                {"kind": "unmatched", "id": rid, "answered": answered, "reason": reason, "question": text, "options": ["確かめた"]}
            )  # noqa: E501
            self.publish_questions()

    def receive_prior(self, got: dict) -> None:
        """計画を始めるときの前のループの待ちを問いの一覧へ入れる。読めなかったループは知らせる。"""
        for msg in got.get("requests") or []:
            self.receive_question(msg)
        for s in got.get("skipped") or []:
            self.ask_unmatched(
                f"{self.name}-prior-{s['loop']}", s["reason"], f"ループ {s['loop']} の記録を読めず、待ちを確かめられない: {s['reason']}"
            )

    def wake_owners(self, q: dict) -> None:
        """問いの止まった持ち主を起こす対象へ入れる: 1 スプリントのレーンと、記録が `waiting_only` で答えのまだの行を持つループのレーン（#2510 の決定 9）。"""
        owners = {q.get("lane"), *(m.get("lane") for m in q.get("merged", []))} & self.stopped()
        waits = set(step_waiting_lanes(self.main, self.name)) if self.routed and owners - self.sprint else set()
        owners &= self.sprint | waits
        self.revive = sorted({*self.revive, *owners})  # 止まった持ち主を起こして答えを当てるまで計画を終えない
        self.revive_at.update({o: self.seen_at for o in owners})  # 止まったと観た時刻。これより後に動いたら起きたと数える
        if owners - set(self.wake):  # 保留中の持ち主の後に依存先を起こさないため、全レーンが止まるのを待たずに起こす 1 行を添える
            self.wake = sorted({*self.wake, *owners})
            self.pause("notice", lanes=[], attach=[])
