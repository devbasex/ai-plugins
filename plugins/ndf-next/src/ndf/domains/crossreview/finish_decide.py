"""収束ループの終わり方から結果を決める（#2370・#2444。純粋な関数）。

- 指摘は 1 件ずつ「直した」「見送った（理由）」「未解決」のどれかで終わる。組み直しが残した「足すべき内容」
  （設計のプランの `--findings`）も指摘の候補として同じ 3 つで扱い、どれにもならなかった件は欠け（gaps）に載せる
- 未解決・扱わなかった足すべき内容・理由の無い見送りが 1 件でもあれば `incomplete`（gaps つき）で止まる。
  プランは `incomplete` の行き先へ進み、gaps は承認資料の「未確認の点」になる
- 上限（`max_rounds`）・振動（`oscillating`）・途中の誤り（`errored`）で終わっても、欠けが 0 件なら ok で次の工程へ進み、そう決めた理由を
  decisions に残す（conductor が手で review を飛ばさない）。1 ラウンドも終わらずに誤りで終わったら `work_stopped`
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.contracts.exit_codes import OK, STOPPED
from ndf.domains.crossreview import store

# 指摘の終わり方
FIXED = "fixed"
SKIPPED = "deferred"
UNRESOLVED = "unresolved"
OUTCOMES = (FIXED, SKIPPED, UNRESOLVED)
# 決めた（直した・見送った）終わり方
DECIDED = (FIXED, SKIPPED)
# 収束ループの終わり方
CONVERGED = "converged"
MAX_ROUNDS = "max_rounds"
# 前々ラウンドの指摘が前ラウンドで消えて戻った（`ndf.core.converge.oscillation`）
OSCILLATING = "oscillating"
ERROR = "errored"
FINALS = (CONVERGED, MAX_ROUNDS, OSCILLATING, ERROR)
# 欠けの種類
GAP_UNRESOLVED = "unresolved_finding"
GAP_UNHANDLED = "unhandled_finding"
GAP_NO_REASON = "skip_without_reason"


class Finding(NamedTuple):
    """レビューの指摘 1 件。足すべき内容を扱った件は、その `id` を持つ。"""

    id: str
    text: str
    outcome: str
    reason: Optional[str]


class Handed(NamedTuple):
    """組み直しが残した足すべき内容 1 件。"""

    id: str
    text: str


class Review(NamedTuple):
    """収束ループの終わり方・終えたラウンドの数・指摘。"""

    final: str
    rounds: int
    findings: tuple[Finding, ...]


class Verdict(NamedTuple):
    """結果の status と種類・要約・欠け・判断・注意。"""

    status: str
    kind: Optional[str]
    summary: str
    gaps: tuple[dict[str, str], ...]
    decisions: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]


def _gap(kind: str, name: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "name": name, "reason": reason}


def _finding_gaps(findings: Sequence[Finding]) -> list[dict[str, str]]:
    gaps = [_gap(GAP_UNRESOLVED, f.id, f.text or f"指摘 {f.id} の本文が無い") for f in findings if f.outcome == UNRESOLVED]
    gaps += [_gap(GAP_NO_REASON, f.id, f"見送った理由が無い: {f.text}") for f in findings if f.outcome == SKIPPED and not f.reason]
    return gaps


def _handed_decision(h: Handed, found: Finding) -> dict[str, str]:
    said = "直した" if found.outcome == FIXED else f"見送った（理由: {found.reason}）"
    return {"decision": f"足すべき内容 {h.id} を{said}", "basis": h.text or h.id}


def _handed(handed: Sequence[Handed], findings: Sequence[Finding]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """足すべき内容ごとの判断と、扱わなかった件の欠け。未解決の件の欠けは `_finding_gaps` が持つ。"""
    by_id = {f.id: f for f in findings}
    decisions, gaps = [], []
    for h in handed:
        found = by_id.get(h.id)
        if found is None:
            gaps.append(_gap(GAP_UNHANDLED, h.id, f"レビューで直したとも見送ったとも決めていない: {h.text or h.id}"))
        elif found.outcome != UNRESOLVED and (found.outcome == FIXED or found.reason):
            decisions.append(_handed_decision(h, found))
    return decisions, gaps


def _counts(findings: Sequence[Finding]) -> str:
    n = {o: sum(1 for f in findings if f.outcome == o) for o in OUTCOMES}
    return f"指摘 {len(findings)} 件（直した {n[FIXED]}・見送った {n[SKIPPED]}・未解決 {n[UNRESOLVED]}）"


def _limit_decision(review: Review) -> dict[str, str]:
    return {
        "decision": f"収束ループは {review.final} で終わったが、欠けが 0 件なので次の工程へ進む",
        "basis": f"{review.rounds} ラウンド・{_counts(review.findings)}。見送りはすべて理由を持ち、足すべき内容はすべて扱った",
    }


def _bad_input(review: Review) -> Optional[str]:
    if review.final not in FINALS:
        return f"収束ループの終わり方 {review.final!r} を知らない（{'・'.join(FINALS)} のどれか）"
    unknown = sorted({f.outcome for f in review.findings} - set(OUTCOMES))
    if unknown:
        return f"指摘の終わり方 {'・'.join(unknown)} を知らない（{'・'.join(OUTCOMES)} のどれか）"
    return None


def finish(review: Review, handed: Sequence[Handed] = ()) -> Verdict:
    """終わり方と指摘・足すべき内容から結果を決める。"""
    bad = _bad_input(review)
    if bad is not None:
        return Verdict(STOPPED, "invalid", bad, (), (), ())
    if review.final == ERROR and review.rounds == 0:
        return Verdict(STOPPED, "work_stopped", "収束ループが 1 ラウンドも終えずに誤りで終わった", (), (), ())
    decisions, gaps = _handed(handed, review.findings)
    gaps = _finding_gaps(review.findings) + gaps
    counts = _counts(review.findings)
    if gaps:
        summary = f"{review.rounds} ラウンドで {review.final}。{counts}・欠け {len(gaps)} 件"
        return Verdict(STOPPED, "incomplete", summary, tuple(gaps), tuple(decisions), ())
    warnings: tuple[str, ...] = ()
    if review.final != CONVERGED:
        decisions.append(_limit_decision(review))
        warnings = (f"収束ループは {review.final} で終わった（欠けは 0 件）",)
    return Verdict(OK, None, f"{review.rounds} ラウンドで {review.final}。{counts}", (), tuple(decisions), warnings)


def as_findings(raw: Sequence[Any]) -> tuple[Finding, ...]:
    """指摘の辞書（`id`・`text`・`outcome`・`reason`）の並びを読む。形が違えば `ValueError`。"""
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("outcome"), str):
            raise ValueError(f"指摘 {i} の形が違う（outcome の文字列を持つ辞書）")
        reason = item.get("reason")
        name = str(item["id"]) if "id" in item else f"finding-{i + 1}"
        text = str(item["text"]) if "text" in item else ""
        out.append(Finding(name, text, item["outcome"], reason if reason else None))
    return tuple(out)


def as_review(data: Mapping[str, Any]) -> Review:
    """要約（`summary_of` の形）を読む。指摘の形が違えば `ValueError`、ラウンドの数が整数でなければ 0。"""
    findings = data.get(store.FINDINGS) if isinstance(data.get(store.FINDINGS), list) else []
    rounds = data.get(store.ROUNDS)
    return Review(str(data.get("final")), rounds if type(rounds) is int else 0, as_findings(findings))


def summary_of(review: Review, pr: int) -> dict[str, Any]:
    """要約の形（終わり方・ラウンドの数・指摘・元の PR 番号）。`as_review` が読む。"""
    return {"final": review.final, store.ROUNDS: review.rounds, store.FINDINGS: [dict(f._asdict()) for f in review.findings], "pr": pr}


def as_handed(items: Sequence[Any]) -> tuple[Handed, ...]:
    """組み直しの結果 JSON の items（辞書か文字列）を足すべき内容にする。`id` が無ければ順の番号から作る。"""
    out = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            out.append(Handed(f"rebuild-{i + 1}", item))
        elif isinstance(item, dict):
            text = next((str(item[k]) for k in ("text", "summary", "name") if item.get(k)), "")
            out.append(Handed(str(item["id"]) if "id" in item else f"rebuild-{i + 1}", text))
        else:
            raise ValueError(f"足すべき内容 {i} の形が違う（文字列か辞書）")
    return tuple(out)
