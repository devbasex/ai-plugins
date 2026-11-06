"""並列の計画の読み出し: 配布の組み方（#2616）と、ループのレーンの記録。`parallel_run` の耐久ステップと `parallel.py check` が使う。

配布の組み方の判定は `delivery.release_way` に任せ、ここは `.ndf/` の宣言と組むときの出す版を読むだけである。
"""

from __future__ import annotations

import json
from pathlib import Path

import delivery
import parallel_plan as pp
from parallel_repo import dev_version
from sprint_loop_names import LOOP_DIR, PENDING

NO_VERSION = "出す版を決められない（ベースブランチと .ndf/supervise.json の release.plugin の plugin.json の版を読めない）"


def way_of(main: Path) -> delivery.ReleaseWay:
    """メインディレクトリの宣言から決まる配布の組み方。"""
    from supervise_lib.release_templates import RELEASE_FORMS

    return delivery.release_way(delivery.load_delivery(main), tuple(RELEASE_FORMS))


def check_items(main: Path) -> list[dict]:
    """起動の前の検査の警告。配布を組めないなら 1 件。起動は断らない（レーンが配布の依頼を送らない計画もある）。"""
    got = way_of(main)
    if got.way == delivery.REFUSE:
        return [{"level": "warning", "rule": "release", "at": "delivery", "message": got.reason}]
    if got.way == delivery.BUILD and not dev_version(main):
        return [{"level": "warning", "rule": "release", "at": "release.plugin", "message": NO_VERSION}]
    return []


def with_release_warnings(main: Path, checked: tuple) -> tuple:
    """計画の検査の結果 (計画, items, metrics) へ配布の警告を足す。計画に誤りがあれば、そのまま返す。"""
    plan, items, metrics = checked
    if plan is None:
        return checked
    warned = check_items(main)
    return plan, [*items, *warned], {**metrics, "warnings": metrics["warnings"] + len(warned)}


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


def design_held(main: Path, name: str, answered: list[str]) -> list[str]:
    """設計の承認の依頼に答えたのに、持ち主のループのレーンが答えをまだ当てていないレーンの名前（#2436 の 6f）。

    答え（`answer-<依頼>`）は持ち主が周回の境で当てる。当てる前に計画を終えると、起こしたレーンが以後の配布の
    依頼を送れないため、当てるまで計画を終えない。"""
    held = lambda rec: any(w.get("request") in answered and not w.get("answer") for w in rec.get("waiting") or [])  # noqa: E731
    return [ln.name for ln, rec in _loop_records(main, name) if held(rec)]


def waiting_lanes(main: Path, name: str) -> list[str]:
    """記録が `waiting_only` で止まり、答えをまだ当てていない待ちを持つループのレーンの名前（保留中。依存の判定で止まったと数えない）。"""

    def held(rec: dict) -> bool:
        if rec.get("status") != "stopped" or (rec.get("stop") or {}).get("condition") != "waiting_only":
            return False
        return any(not w.get("answer") for w in rec.get("waiting") or [])

    return [ln.name for ln, rec in _loop_records(main, name) if held(rec)]
