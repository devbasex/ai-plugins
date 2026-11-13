"""レビューの単位（`branch_flow.reviews`。#3163）でプランへレビューのステップを差し込む・外す。

プランのテンプレートは、目録のスナップショット（`a.flow`）と課題のまとまりの記録の有無（`a.bundles`）から PR の種類の行を
解き（`review_row`）、ここの関数でステップを置く。ステップの中身は既存の部品（`templates._inspect_steps`・`review_step`・
`paths.BEHAVIOR_STEPS`）から作り、並びは `branch_flow.CHECKS` の順（リファクタリング → cross-review → 振る舞いの確認）にする。
宣言のファイルは読まない（I6）。"""

from __future__ import annotations

import branch_flow

from supervise_lib.decl import sprint_flow
from supervise_lib import templates

# 掛けるレビュー → 検査のプランのステップの id の並び
STEP_IDS = {
    "cross-refactoring": ("assess", "refactor"),
    "cross-review": ("review",),
    "behavior": ("behavior-targets", "behavior", "behavior-verify"),
}
# 設計のプランのレビューのまとまり（`sprint_waves._review_steps`）が置く id
DESIGN_REVIEW_IDS = ("review", "review-settled", "resolve-sync", "resolve", "push-resolve", "sync-review")
DESIGN_AFTER_REVIEW = "requirements-recheck"

_JUMPS = ("next", "on_fail", "skip_to", "gate_next")


def _redirect(steps: list[dict], old: str, new: str) -> None:
    """ステップの行き先 `old` を `new` へ付け替える。"""
    for st in steps:
        for k in _JUMPS:
            if st.get(k) == old:
                st[k] = new
        if isinstance(st.get("on_exit"), dict):
            st["on_exit"] = {c: new if v == old else v for c, v in st["on_exit"].items()}


def review_row(a, kind: str, bundled: bool | None = None) -> branch_flow.ReviewRow:
    """引数のスナップショットとまとまりの記録の有無から種類の行を解く。`bundled` を渡せばそれを使う。"""
    if bundled is None:
        bundled = getattr(a, "bundles", None) is not None
    return sprint_flow(a).flow.reviews.row(kind, bundled)


def chosen_checks(a, row: branch_flow.ReviewRow) -> tuple[str, ...]:
    """行の掛けるレビュー。リファクタリングを止める条件に当たれば cross-refactoring を外す（I7）。"""
    off = templates.refactor_suspended(a) or getattr(a, "mode", None) in templates.NO_REFACTOR_MODES
    return tuple(c for c in branch_flow.CHECKS if row.runs(c) and not (off and c == "cross-refactoring"))


def drop_unchosen(plan: dict, checks: tuple[str, ...]) -> dict:
    """選ばないレビューのステップを外し、外したステップへの遷移をそのステップの `next` へ付け替える。"""
    steps = plan["steps"]
    for check in branch_flow.CHECKS:
        if check in checks:
            continue
        for sid in STEP_IDS[check]:
            st = next((s for s in steps if s["id"] == sid), None)
            if st is not None:
                steps.remove(st)
                _redirect(steps, sid, st["next"])
    return plan


def insert_reviews(a, plan: dict, checks: tuple[str, ...]) -> dict:
    """実装のプランの ready の前に、手元の送信 → 選んだレビューのステップ → 手元の早送りを置く。収束しなければ止まる。
    レビューの直しは別の作業場所から PR のブランチへ送られるため、レビューの後に手元を早送りしてから ready が送る。"""
    if not checks:
        return plan
    steps = plan["steps"]
    at = next((i for i, st in enumerate(steps) if st["id"] == "ready"), None)
    if at is None:
        raise ValueError("実装のプランに ready のステップが無い")
    chain = [dict(st) for st in templates._inspect_steps(a, "{pr}", templates.refactor_scope(a, "{pr}"))]
    chain.append({"id": "review-pull", "type": "run", "timeout": 600, "cmd": "git pull -q --ff-only", "on_fail": "stop", "next": "ready"})
    _redirect(chain, "base-sync", "review-pull")
    _redirect(chain, "abort", "stop")
    chain = drop_unchosen({"steps": chain}, checks)["steps"]
    _redirect(steps, "ready", "review-push")
    push = {"id": "review-push", "type": "run", "timeout": 600, "cmd": "git push -q", "on_fail": "stop", "next": chain[0]["id"]}
    steps[at:at] = [push, *chain]
    return plan


def apply_impl(a, plan: dict, bundled: bool | None = None) -> dict:
    """実装 PR の行を解いてレビューのステップを置く。`none` ならプランを変えない。"""
    row = review_row(a, "impl", bundled)
    return plan if row.diff == "none" else insert_reviews(a, plan, chosen_checks(a, row))


def apply_check(a, plan: dict, bundled: bool | None = None) -> dict:
    """スプリントの PR の行を解き、検査のプランから選ばないレビューのステップを外す。"""
    row = review_row(a, "sprint", bundled)
    return drop_unchosen(plan, row.checks if row.diff != "none" else ())


def apply_design(a, plan: dict) -> dict:
    """設計 PR の行が `none` なら設計のレビューのまとまりを外し、その遷移を要求の再確認へ付け替える。"""
    if review_row(a, "design").diff != "none":
        return plan
    steps = plan["steps"]
    if not any(st["id"] == DESIGN_AFTER_REVIEW for st in steps):
        raise ValueError(f"設計のプランに {DESIGN_AFTER_REVIEW} のステップが無い")
    for sid in DESIGN_REVIEW_IDS:
        steps[:] = [st for st in steps if st["id"] != sid]
        _redirect(steps, sid, DESIGN_AFTER_REVIEW)
    for st in steps:
        if st.get("inputs"):
            st["inputs"] = [i for i in st["inputs"] if i not in DESIGN_REVIEW_IDS]
    return plan


def spec_review_steps(a, after: str) -> list[dict]:
    """確定仕様化の PR の行が `pr` なら、文書の cross-review と手元の早送り。`none` なら空。"""
    if review_row(a, "spec").diff == "none":
        return []
    review = {"id": "spec-review", "type": "drive", "drive": "cross-review", "kind": "ドキュメントレビュー", "stage": "確定仕様化"}
    return [
        {**review, "timeout": 3600, "args": "{pr}", "on_fail": "stop", "next": "spec-review-pull"},
        {"id": "spec-review-pull", "type": "run", "timeout": 600, "cmd": "git pull -q --ff-only", "on_fail": "stop", "next": after},
    ]
