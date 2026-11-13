"""課題のまとまり（#3081）でスプリントを組む部品: レーンのまとまりの読み出しと代表の設計への課題の受け渡し。
レビューのステップの差し込みと取り外しは `review_units`（#3163）が持つ。"""

from __future__ import annotations

from pathlib import Path

from supervise_lib.absorb import bundle_leads, include_in_design


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
