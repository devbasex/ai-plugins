"""課題のまとまり（#3081）でスプリントを組む部品: レーンのまとまりの読み出し・代表の設計への課題の受け渡し・
実装のプランの cross-review の差し込みと検査のプランからの取り外し。"""

from __future__ import annotations

from pathlib import Path

from supervise_lib.absorb import bundle_leads, include_in_design
from supervise_lib.templates import review_step


def bundle_refusal(a):
    """並列の計画のこのレーンの課題のまとまりを `a.bundles` へ読む（#3081）。まとまりの記録が無ければ None を置く。
    まとまりはこのスプリントの課題（`a.issue`）に絞り、設計の課題（`a.design`）を代表の課題へ寄せる。
    レーンの計画が検証に通らなければ、プランを書かずに止める結果を返す（まとまりの記録の無いスプリントとして黙って動かない）。"""
    import decl
    import parallel_bundles as pb
    from step_result import result

    try:
        got = pb.bundles_of(decl.main_dir(a.worktree) or Path(a.worktree))
    except pb.BrokenPlanError as e:
        return result("supervise-new", "stopped", f"{e}。計画を書かない", [], {"plan": e.plan}, next="plan.json を直して打ち直す")
    issues = list(a.issue)
    # スプリントの課題を 1 つも持たないまとまりは Bundle を作る前に外す（課題の 1 件以上の検証で落ちる）
    kept = [pb.Bundle(name=b.name, issues=m) for b in got or [] if (m := [n for n in b.issues if n in issues])]
    a.bundles = pb.bundle_list(issues, kept) if got is not None else None
    lead = bundle_leads(a)
    a.design = list(dict.fromkeys(lead.get(n, n) for n in a.design))
    return None


def apply_bundle_design(a, waves: list[dict]) -> None:
    """代表の課題の設計のプランへ、まとまりのすべての課題を `課題` と要求・設計の指示として渡す（#3081）。
    まとまりの設計文書は代表の番号で作り、ほかの課題の受け入れ条件を取り込む。"""
    plans = {k: p for w in waves for k, p in (w.get("plans") or {}).items()}
    for b in getattr(a, "bundles", None) or []:
        h = b.lead()
        design = plans.get(f"design-{h}")
        for m in b.issues[1:] if design is not None else ():
            include_in_design(design, h, m, f"まとまりの課題 #{m} の受け入れ内容も #{h} の設計に含める（#{m} の本文も読む）。")


_JUMPS = ("next", "on_fail", "skip_to", "gate_next")


def _redirect(steps: list[dict], old: str, new: str) -> None:
    """ステップの行き先 `old` を `new` へ付け替える。"""
    for st in steps:
        for k in _JUMPS:
            if st.get(k) == old:
                st[k] = new
        if isinstance(st.get("on_exit"), dict):
            st["on_exit"] = {c: new if v == old else v for c, v in st["on_exit"].items()}


def with_review(plan: dict) -> dict:
    """実装のプランの ready（Draft を外す）の前に cross-review を置く。収束しなければ止まり、マージへ進めない。"""
    steps = plan["steps"]
    _redirect(steps, "ready", "review")
    at = next(i for i, st in enumerate(steps) if st["id"] == "ready")
    steps.insert(at, review_step("{pr}", "ready", "stop"))
    return plan


def without_review(plan: dict) -> dict:
    """検査のプランから cross-review を外す（まとまりの Pull Request で済んでいる。#3081 の I8c）。"""
    steps = plan["steps"]
    review = next((st for st in steps if st["id"] == "review"), None)
    if review is not None:
        steps.remove(review)
        _redirect(steps, "review", review["next"])
    return plan
