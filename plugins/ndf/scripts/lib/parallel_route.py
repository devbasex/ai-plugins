"""並列の計画のリリースの組み方（#2616）と、ループのレーンの記録の読み出し。`parallel_run` と `parallel.py check` が使う。

リリースの組み方の判定は `delivery.release_way` に任せ、ここは `.ndf/` の宣言と組むときの出す版を読むだけである。
"""

from __future__ import annotations

from pathlib import Path

import delivery
import durable
import parallel_hold
from pace import hours_items
from parallel_repo import dev_version, loop_records
from sprint_loop_names import PENDING

REBUILD, STOP_PLAN = "直してから組み直す", "止める"  # リリースを組めないときの問いの選択肢
NO_VERSION = "出す版を決められない（ベースブランチと .ndf/supervise.json の release.plugin の plugin.json の版を読めない）"


def way_of(main: Path) -> delivery.ReleaseWay:
    """メインディレクトリの宣言から決まるリリースの組み方。"""
    from supervise_lib.release_templates import RELEASE_FORMS

    return delivery.release_way(delivery.load_delivery(main), tuple(RELEASE_FORMS))


def start_warnings(main: Path) -> list[dict]:
    """閾値の警告と業務時間の宣言の警告（`check`・`start` が出す。#2913）。"""
    return [*parallel_hold.limit_items(main), *hours_items(main)]


def check_items(main: Path) -> list[dict]:
    """起動の前の検査の警告。リリースを組めないなら 1 件。起動は断らない（レーンがリリースの依頼を送らない計画もある）。"""
    got = way_of(main)
    if got.way == delivery.REFUSE:
        return [{"level": "warning", "rule": "release", "at": "delivery", "message": got.reason}, *start_warnings(main)]
    if got.way == delivery.BUILD and not dev_version(main):
        return [{"level": "warning", "rule": "release", "at": "release.plugin", "message": NO_VERSION}, *start_warnings(main)]
    return start_warnings(main)


def with_release_warnings(main: Path, checked: tuple) -> tuple:
    """計画の検査の結果 (計画, items, metrics) へリリースの警告を足す。計画に誤りがあれば、そのまま返す。"""
    plan, items, metrics = checked
    if plan is None:
        return checked
    warned = check_items(main)
    return plan, [*items, *warned], {**metrics, "warnings": metrics["warnings"] + len(warned)}


def held_loops(main: Path, name: str, answered: list[str]) -> list[str]:
    """答えを受けたのに残りを流していない保留したループの 1 回を持つ、計画のループのレーンのループ名（異常系 11）。"""
    asked = lambda lap: (lap.get("release") or lap.get("question") or {}).get("request") if lap.get("outcome") in PENDING else None  # noqa: E731
    return [ln.input.loop for ln, rec in loop_records(main, name) if any(asked(lap) in answered for lap in rec.get("laps") or [])]


def loop_repo(main: Path, name: str, loop: str) -> str | None:
    """ループ `loop` を持つ別のリポジトリのレーンのリポジトリ。計画の置き場のレーン・見つからない・計画を読めなければ None（#2733）。"""
    import parallel_plan as pp

    try:
        plan = pp.load_valid_plan(main, name)
    except (OSError, ValueError):
        return None
    hit = next((ln for ln in plan.lanes if ln.input.kind == "loop" and ln.input.loop == loop), None)
    return str(hit.repo_dir(Path(main))) if hit is not None and hit.own_release(Path(main)) else None


@durable.step(name="parallel.loop-repo")
def step_loop_repo(main: str, name: str, loop: str) -> str | None:
    return loop_repo(Path(main), name, loop)


def design_held(main: Path, name: str, answered: list[str]) -> list[str]:
    """設計の承認の依頼に答えたのに、持ち主のループのレーンが答えをまだ当てていないレーンの名前（#2436 の 6f）。

    答え（`answer-<依頼>`）は持ち主がループの 1 回の境で当てる。当てる前に計画を終えると、起こしたレーンが以後のリリースの
    依頼を送れないため、当てるまで計画を終えない。"""
    held = lambda rec: any(w.get("request") in answered and not w.get("answer") for w in rec.get("waiting") or [])  # noqa: E731
    return [ln.name for ln, rec in loop_records(main, name) if held(rec)]


def waiting_lanes(main: Path, name: str) -> list[str]:
    """記録が `waiting_only` か答え待ちの停止（`answer_hold`）で止まり、答えをまだ当てていない待ちを持つループのレーンの名前
    （保留中。依存の判定で止まったと数えない。答えを受けたら再起動の対象へ入れる）。"""

    def held(rec: dict) -> bool:
        if rec.get("status") != "stopped" or (rec.get("stop") or {}).get("condition") not in parallel_hold.WAIT_STOPS:
            return False
        return any(not w.get("answer") for w in rec.get("waiting") or [])

    return [ln.name for ln, rec in loop_records(main, name) if held(rec)]


def waiting_requests(main: Path, name: str) -> list[tuple[str, str]]:
    """ループの記録が答えを待つ依頼の (レーン, 依頼の id)。台帳に無い待ちを `wait` が知らせる（#2647 の条件 14）。"""
    return [
        (ln.name, w["request"])
        for ln, rec in loop_records(main, name)
        for w in rec.get("waiting") or []
        if w.get("request") and not w.get("answer")
    ]


def closed_prs(main: Path, prs: list[int]) -> dict:
    """`prs` のうちマージされずに閉じた Pull Request の番号（`closed`）。GitHub の GraphQL で 100 件ごとにまとめて読む。
    読めなければ `closed` を空にして理由を `error` に残す（取得に失敗した Pull Request は閉じたと見なさない。#3085 の I5）。"""
    if not prs:
        return {"closed": [], "error": ""}
    from release_lib.included import pr_fields

    got, why = pr_fields(prs, cwd=str(main))
    if got is None:
        return {"closed": [], "error": why}
    return {"closed": sorted(n for n, d in got.items() if d and d.get("state") == "CLOSED"), "error": ""}


@durable.step(name="parallel.closed-prs")
def step_closed_prs(main: str, prs: list[int]) -> dict:
    return closed_prs(Path(main), prs)


def split_closed(pending: list[dict], closed) -> tuple[list[dict], list[dict]]:
    """(中身の無い依頼, 残す依頼)（#3085 の I3・I4）。載せた Pull Request がすべて `closed`（マージされずに閉じた）の依頼は中身が無い。
    一部が閉じた依頼は `prs` を残りへ置き換え、除いた番号を `closed` に残す（範囲の判定 `answerable` をそのまま通す）。"""
    shut, empty, kept = set(closed), [], []
    for r in pending:
        gone = [n for n in r.get("prs") or [] if n in shut]
        if not gone:
            kept.append(r)
            continue
        got = {**r, "prs": [n for n in r["prs"] if n not in shut], "closed": sorted({*(r.get("closed") or []), *gone})}
        (kept if got["prs"] else empty).append(got)
    return empty, kept


def closed_reason(r: dict) -> str:
    return f"Pull Request {', '.join(f'#{n}' for n in r['closed'])} はマージされずに閉じた（出すものが無い）"


class ReleaseRouting:
    """組まない・組めないリリースの締めと、組めないときの問い。`parallel_run._Run` が継ぐ（#2616）。"""

    closing = True  # リリースを組む前に中身の無い依頼を外すか。印の無い古い記録の打ち直しでは False（#3085 の I7）

    def drop_closed(self) -> bool:
        """リリースを組む直前に 1 回だけ依頼の Pull Request の状態を読み、中身の無い依頼へ `released`・版なし・閉じた番号の理由で
        答えて外す。一部が閉じた依頼は残りの番号で、出したリリースの範囲の答えを受け直す。組む依頼が残れば True（#3085 の I3〜I6）。"""
        got = step_closed_prs(self.main, sorted({n for r in self.pending for n in r.get("prs") or []}))
        empty, kept = split_closed(self.pending, got.get("closed") or [])
        if kept == self.pending:
            return True
        self.pending = kept
        for r in empty:
            self.answer(r["id"], "released", None, None, closed_reason(r))
        self.wake_answered(empty)
        self.answer_from_ranges()
        return bool(self.pending)

    def take_pending(self) -> tuple[list[dict], dict]:
        """受けている依頼をすべて外し、(依頼, リリースの記録の共通のキー) を返す（#2616 の I4）。"""
        taken, self.pending = self.pending, []
        return taken, {"prs": sorted({n for r in taken for n in r.get("prs") or []}), "requests": [r["id"] for r in taken]}

    def skip_release(self, n: int, reason: str) -> None:
        """マージで届くかリリースしないリポジトリ: 組まずに受けている依頼のすべてへリリース済みと答える。"""
        taken, keys = self.take_pending()
        self.releases.append({"n": n, "state": "released", "version": None, **keys, "reason": reason, "skipped": True})
        for r in taken:
            self.answer(r["id"], "released", None, n, reason)
        self.wake_answered(taken)

    def refuse_release(self, n: int, reason: str) -> None:
        """組めない: リリースを失敗として締め、受けている依頼のすべてへ理由を答え、組み直すか止めるかを問う。計画は止めない（I5）。"""
        taken, keys = self.take_pending()
        self.releases.append({"n": n, "state": "failed", **keys, "reason": reason})
        for r in taken:
            self.answer(r["id"], "failed", None, n, reason)
        self.ask_rebuild(n, reason, taken)
        self.wake_answered(taken)

    def ask_rebuild(self, n: int, reason: str, requests: list[dict]) -> None:
        """リリースを組めない問いを立てる。レーンに結び付けない（後続のレーンの起動を止めない）。同じ理由の問いは 1 件にまとめる（I6）。"""
        same = next((q for q in self.questions if q.get("kind") == "build" and q.get("reason") == reason), None)
        if same is not None:
            same["requests"] = [*same["requests"], *requests]
            self.publish_questions()
            return
        text = f"リリース {n} を組めない: {reason}"
        self.ask("build", f"{self.name}-build-{n}", None, text, [REBUILD, STOP_PLAN], reason=reason, requests=requests)

    def apply_build(self, q: dict, choice: list[str]) -> None:
        """リリースを組めない問いへの答え。組み直しの依頼は元の依頼の答えを変えず、次のリリースの範囲に入れるために受けている依頼へ入れ直す。"""
        if STOP_PLAN in choice:
            self.stop = True
        elif REBUILD in choice:
            seq = len(self.releases) + 1
            for r in q.get("requests") or []:
                origin = r.get("origin") or r["id"]
                self.pending.append({**r, "id": f"{origin}-again{seq}", "origin": origin, "again": True})
