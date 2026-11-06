"""並列の計画のリリースの組み方（#2616）と、ループのレーンの記録の読み出し。`parallel_run` と `parallel.py check` が使う。

リリースの組み方の判定は `delivery.release_way` に任せ、ここは `.ndf/` の宣言と組むときの出す版を読むだけである。
"""

from __future__ import annotations

from pathlib import Path

import delivery
from parallel_repo import dev_version, loop_records
from sprint_loop_names import PENDING

REBUILD, STOP_PLAN = "直してから組み直す", "止める"  # リリースを組めないときの問いの選択肢
NO_VERSION = "出す版を決められない（ベースブランチと .ndf/supervise.json の release.plugin の plugin.json の版を読めない）"


def way_of(main: Path) -> delivery.ReleaseWay:
    """メインディレクトリの宣言から決まるリリースの組み方。"""
    from supervise_lib.release_templates import RELEASE_FORMS

    return delivery.release_way(delivery.load_delivery(main), tuple(RELEASE_FORMS))


def check_items(main: Path) -> list[dict]:
    """起動の前の検査の警告。リリースを組めないなら 1 件。起動は断らない（レーンがリリースの依頼を送らない計画もある）。"""
    got = way_of(main)
    if got.way == delivery.REFUSE:
        return [{"level": "warning", "rule": "release", "at": "delivery", "message": got.reason}]
    if got.way == delivery.BUILD and not dev_version(main):
        return [{"level": "warning", "rule": "release", "at": "release.plugin", "message": NO_VERSION}]
    return []


def with_release_warnings(main: Path, checked: tuple) -> tuple:
    """計画の検査の結果 (計画, items, metrics) へリリースの警告を足す。計画に誤りがあれば、そのまま返す。"""
    plan, items, metrics = checked
    if plan is None:
        return checked
    warned = check_items(main)
    return plan, [*items, *warned], {**metrics, "warnings": metrics["warnings"] + len(warned)}


def held_loops(main: Path, name: str, answered: list[str]) -> list[str]:
    """答えを受けたのに残りを流していない保留の周回を持つ、計画のループのレーンのループ名（異常系 11）。"""
    asked = lambda lap: (lap.get("release") or lap.get("question") or {}).get("request") if lap.get("outcome") in PENDING else None  # noqa: E731
    return [ln.input.loop for ln, rec in loop_records(main, name) if any(asked(lap) in answered for lap in rec.get("laps") or [])]


def design_held(main: Path, name: str, answered: list[str]) -> list[str]:
    """設計の承認の依頼に答えたのに、持ち主のループのレーンが答えをまだ当てていないレーンの名前（#2436 の 6f）。

    答え（`answer-<依頼>`）は持ち主が周回の境で当てる。当てる前に計画を終えると、起こしたレーンが以後のリリースの
    依頼を送れないため、当てるまで計画を終えない。"""
    held = lambda rec: any(w.get("request") in answered and not w.get("answer") for w in rec.get("waiting") or [])  # noqa: E731
    return [ln.name for ln, rec in loop_records(main, name) if held(rec)]


def waiting_lanes(main: Path, name: str) -> list[str]:
    """記録が `waiting_only` で止まり、答えをまだ当てていない待ちを持つループのレーンの名前（保留中。依存の判定で止まったと数えない）。"""

    def held(rec: dict) -> bool:
        if rec.get("status") != "stopped" or (rec.get("stop") or {}).get("condition") != "waiting_only":
            return False
        return any(not w.get("answer") for w in rec.get("waiting") or [])

    return [ln.name for ln, rec in loop_records(main, name) if held(rec)]


class ReleaseRouting:
    """組まない・組めないリリースの締めと、組めないときの問い。`parallel_run._Run` が継ぐ（#2616）。"""

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
