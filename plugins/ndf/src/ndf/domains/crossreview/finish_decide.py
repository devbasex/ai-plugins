"""収束ループの終わり方から結果を決める（#2370・#2444。純粋な関数）。

- 指摘は 1 件ずつ「直した」「見送った（理由）」「未解決」のどれかで終わる。組み直しが残した「足すべき内容」
  （設計のプランの `--findings`）も指摘の候補として同じ 3 つで扱い、どれにもならなかった件は欠け（gaps）に載せる
- 未解決・扱わなかった足すべき内容・理由の無い見送りが 1 件でもあれば `incomplete`（gaps つき）で止まる。
  プランは `incomplete` のルートへ進み、gaps は承認資料の「未確認の点」になる
- 上限（`max_rounds`）・振動（`oscillating`）・途中の誤り（`errored`）で終わっても、欠けが 0 件なら ok で次の工程へ進み、そう決めた理由を
  decisions に残す（conductor が手で review を飛ばさない）。1 ラウンドも終わらずに誤りで終わったら `work_stopped`
- 収束の妨げにならない重さ（`minor`・`nit`。`criteria_decide.blocking`）の未解決の指摘は欠けにしない（#3941）
- 必ず人の承認が要る操作に当たる指摘（`redline`）は、どの欠けにもせず、件数を要約に載せる。ほかの、プロジェクト MVV の項目に
  反する指摘（`mvv`）と、NDF が機械で確かめて作った指摘（`by_ndf`。MVV の根拠の記述の誤り）の未解決は、欠けの種類を分ける。
  最後の head で根拠の記述を確かめられなかったとき・PR に届いていないコミットに誤りがあるときも同じ種類の欠けにし、この種類が 1 件でもあれば、次の工程へ進まない
  `mvv_unresolved` で止まる（#4076）
- 一部の相手役が起動し直しても結果を残さなかった（`incomplete_reviewers`。#3941）ら、欠けによらず `work_stopped` で止まる
  （読めなかった相手役の分を収束に数えない）
- 承認ゲートの判定に要る MVV の記録の行（`redline`・`deferred`）を書けなかったら、どの終わり方より優先して `mvv_record_failed`
  で止まる（記録が無いと承認ゲートが PR を省けるため。#3959）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.contracts.exit_codes import OK, STOPPED
from ndf.domains.crossreview import criteria_decide, store
from ndf.domains.records import mvv_decide

# 指摘の終わり方
FIXED = mvv_decide.REVIEW_FIXED
SKIPPED = mvv_decide.REVIEW_DEFERRED
UNRESOLVED = mvv_decide.REVIEW_UNRESOLVED
OUTCOMES = (FIXED, SKIPPED, UNRESOLVED)
# 決めた（直した・見送った）終わり方
DECIDED = (FIXED, SKIPPED)
# 収束ループの終わり方
CONVERGED = "converged"
MAX_ROUNDS = "max_rounds"
# 前々ラウンドの指摘が前ラウンドで消えて戻った（`ndf.core.converge.oscillation`）
OSCILLATING = "oscillating"
ERROR = "errored"
# 一部の相手役が起動し直しても結果を残さなかった（#3941）
INCOMPLETE_REVIEWERS = "incomplete_reviewers"
FINALS = (CONVERGED, MAX_ROUNDS, OSCILLATING, ERROR, INCOMPLETE_REVIEWERS)
# 欠けの種類
GAP_UNRESOLVED = "unresolved_finding"
GAP_UNHANDLED = "unhandled_finding"
GAP_NO_REASON = "skip_without_reason"
GAP_MVV = "mvv_unresolved"
# 承認ゲートの判定に要る MVV の記録の行を書けなかった（#3959）
RECORD_FAILED = "mvv_record_failed"
# 必ず人の承認が要る操作に当たる指摘かどうかの欄（ジャーナルと要約の指摘）
REDLINE = mvv_decide.REVIEW_REDLINE
# NDF が機械で確かめて作った指摘かどうかの欄（要約の指摘）
BY_NDF = "by_ndf"
# 根拠の記述を確かめられたかの欄（ラウンドの記録と要約）と、確かめられなかった欠けの名前
MVV_CHECKED = "mvv_checked"
CITATION_CHECK = "citation_check"
# PR に届いていないコミットで見つけた根拠の記述の誤りの欄（要約では件数、状態ファイルでは中身）と、その欠けの名前
UNSENT_CITATIONS = "unsent_citations"


class Finding(NamedTuple):
    """レビューの指摘 1 件。足すべき内容を扱った件は、その `id` を持つ。`severity` は重要度（分からなければ None）、
    `mvv` は反する MVV の項目（無ければ None）、`redline` は必ず人の承認が要る操作に当たるか、`by_ndf` は NDF が機械で確かめて
    作った指摘か。"""

    id: str
    text: str
    outcome: str
    reason: Optional[str]
    severity: Optional[str] = None
    mvv: Optional[str] = None
    redline: bool = False
    by_ndf: bool = False


def blocking(f: Finding) -> bool:
    """収束の妨げになる指摘か（重要度の分からない指摘は妨げになる側）。"""
    return criteria_decide.blocking(criteria_decide.severity_of(f.severity))


class Handed(NamedTuple):
    """組み直しが残した足すべき内容 1 件。"""

    id: str
    text: str


class Review(NamedTuple):
    """収束ループの終わり方・終えたラウンドの数・指摘。`mvv_checked` は、終わり方を決めた head で PR の足した根拠の記述を
    確かめられたか（承認済みの MVV が無ければ真）。`unsent_citations` は、修正を送れずに手元に残ったコミットで見つけた誤りの件数
    （中身は PR に無いので指摘にしない）。"""

    final: str
    rounds: int
    findings: tuple[Finding, ...]
    mvv_checked: bool = True
    unsent_citations: int = 0


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


def _gap_text(f: Finding) -> str:
    """欠けに載せる指摘の文。根拠の記述の誤りの本文は、文書のパスと根拠欄の中身を含むので載せない。"""
    if f.by_ndf:
        return f"NDF が見つけた根拠の記述の誤り（中身は PR の記録のコメントの指摘 {f.id} にある）"
    return f.text or f"指摘 {f.id} の本文が無い"


def _finding_gaps(findings: Sequence[Finding], *, mvv_checked: bool = True, unsent_citations: int = 0) -> list[dict[str, str]]:
    rest = [f for f in findings if not f.redline]
    gaps = [
        _gap(GAP_MVV if f.mvv or f.by_ndf else GAP_UNRESOLVED, f.id, _gap_text(f)) for f in rest if f.outcome == UNRESOLVED and blocking(f)
    ]
    gaps += [_gap(GAP_NO_REASON, f.id, f"見送った理由が無い: {_gap_text(f)}") for f in rest if f.outcome == SKIPPED and not f.reason]
    if not mvv_checked:
        reason = "PR が足した根拠の記述が承認済みの MVV に合うかを、最後のコミットで確かめられなかった（原因は注意にある）"
        gaps.append(_gap(GAP_MVV, CITATION_CHECK, reason))
    if unsent_citations:
        reason = f"PR に届いていない手元のコミットに、根拠の記述の誤りが {unsent_citations} 件ある（中身は状態ファイルにある。直すか送って打ち直す）"
        gaps.append(_gap(GAP_MVV, UNSENT_CITATIONS, reason))
    return gaps


def _handed_decision(h: Handed, found: Finding) -> dict[str, str]:
    said = "直した" if found.outcome == FIXED else f"見送った（理由: {found.reason}）"
    return {"decision": f"足すべき内容 {h.id} を{said}", "basis": h.text or h.id}


def _settled(found: Finding) -> bool:
    """直した、または理由を添えて見送った（終わり方は `_bad_input` が 3 種に限っている）。"""
    return found.outcome == FIXED or (found.outcome == SKIPPED and bool(found.reason))


def _handed(handed: Sequence[Handed], findings: Sequence[Finding]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """足すべき内容ごとの判断と、扱わなかった件の欠け。未解決の件の欠けは `_finding_gaps` が持つ。"""
    by_id = {f.id: f for f in findings}
    decisions, gaps = [], []
    for h in handed:
        found = by_id.get(h.id)
        if found is None:
            gaps.append(_gap(GAP_UNHANDLED, h.id, f"レビューで直したとも見送ったとも決めていない: {h.text or h.id}"))
        elif _settled(found):
            decisions.append(_handed_decision(h, found))
    return decisions, gaps


def _counts(findings: Sequence[Finding]) -> str:
    waiting = sum(1 for f in findings if f.outcome == UNRESOLVED and f.redline)
    rest = [f for f in findings if f.outcome != UNRESOLVED or not f.redline]
    n = {o: sum(1 for f in rest if f.outcome == o and (o != UNRESOLVED or blocking(f))) for o in OUTCOMES}
    light = sum(1 for f in rest if f.outcome == UNRESOLVED and not blocking(f))
    tail = (f"・軽い指摘 {light}" if light else "") + (f"・人の承認を待つ {waiting}" if waiting else "")
    return f"指摘 {len(findings)} 件（直した {n[FIXED]}・見送った {n[SKIPPED]}・未解決 {n[UNRESOLVED]}{tail}）"


def _mvv_left(gaps: Sequence[Mapping[str, str]], findings: Sequence[Finding]) -> list[str]:
    """MVV に関わる欠けの名前（反する項目があれば添える）。"""
    items = {f.id: f.mvv for f in findings}
    names = [g["name"] for g in gaps if g["kind"] == GAP_MVV]
    return [f"{name}（{items[name]}）" if items.get(name) else name for name in names]


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


def finish(review: Review, handed: Sequence[Handed] = (), record_failed: Optional[Mapping[str, Any]] = None) -> Verdict:
    """終わり方と指摘・足すべき内容から結果を決める。`record_failed` は承認ゲートの判定に要る MVV の記録の行を書けなかったときの
    書けなかった行（`rows`）と注意（`cause`）で、あれば普段の判定の後に `mvv_record_failed` へ変える（欠けと判断は残す）。"""
    verdict = _finish(review, handed)
    if not record_failed:
        return verdict
    rows = record_failed.get("rows")
    n = len(rows) if isinstance(rows, list) else 0
    head = f"承認ゲートの判定に要る MVV の記録の行 {n} 件を書けない（保存先を直して打ち直す）"
    summary = f"{head}。本来の終わり方は {verdict.kind or verdict.status}: {verdict.summary}"
    cause = record_failed.get("cause")
    warnings = (*verdict.warnings, *([cause] if isinstance(cause, str) else []))
    return Verdict(STOPPED, RECORD_FAILED, summary, verdict.gaps, verdict.decisions, warnings)


def _finish(review: Review, handed: Sequence[Handed]) -> Verdict:
    bad = _bad_input(review)
    if bad is not None:
        return Verdict(STOPPED, "invalid", bad, (), (), ())
    if review.final == ERROR and review.rounds == 0:
        return Verdict(STOPPED, "work_stopped", "収束ループが 1 ラウンドも終えずに誤りで終わった", (), (), ())
    decisions, gaps = _handed(handed, review.findings)
    gaps = _finding_gaps(review.findings, mvv_checked=review.mvv_checked, unsent_citations=review.unsent_citations) + gaps
    counts = _counts(review.findings)
    if review.final == INCOMPLETE_REVIEWERS:
        summary = f"{review.rounds} ラウンドで {review.final}（相手役が起動し直しても差分を読めなかった。理由は warnings）。{counts}"
        return Verdict(STOPPED, "work_stopped", summary, tuple(gaps), tuple(decisions), ())
    left = _mvv_left(gaps, review.findings)
    if left:
        summary = f"{review.rounds} ラウンドで {review.final}。MVV に関わる未解決が残る: {'・'.join(left)}。{counts}・欠け {len(gaps)} 件"
        return Verdict(STOPPED, "mvv_unresolved", summary, tuple(gaps), tuple(decisions), ())
    if gaps:
        summary = f"{review.rounds} ラウンドで {review.final}。{counts}・欠け {len(gaps)} 件"
        return Verdict(STOPPED, "incomplete", summary, tuple(gaps), tuple(decisions), ())
    warnings: tuple[str, ...] = ()
    if review.final != CONVERGED:
        decisions.append(_limit_decision(review))
        warnings = (f"収束ループは {review.final} で終わった（欠けは 0 件）",)
    return Verdict(OK, None, f"{review.rounds} ラウンドで {review.final}。{counts}", (), tuple(decisions), warnings)


def _id_of(item: dict[str, Any], prefix: str, i: int) -> str:
    """辞書の `id`。無ければ順の番号から作る（`<prefix>-<1 からの番号>`）。"""
    return str(item["id"]) if "id" in item else f"{prefix}-{i + 1}"


def _text_of(item: dict[str, Any]) -> str:
    """足すべき内容の本文（text・summary・name の順に、空でない最初の値）。"""
    return next((str(item[k]) for k in ("text", "summary", "name") if item.get(k)), "")


def as_findings(raw: Sequence[Any]) -> tuple[Finding, ...]:
    """指摘の辞書（`id`・`text`・`outcome`・`reason`）の並びを読む。形が違えば `ValueError`。"""
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("outcome"), str):
            raise ValueError(f"指摘 {i} の形が違う（outcome の文字列を持つ辞書）")
        reason = item.get("reason")
        name = _id_of(item, "finding", i)
        text = str(item["text"]) if "text" in item else ""
        severity = criteria_decide.severity_of(item.get("severity"))
        why = reason if reason else None  # noqa: NDF501 ファイル: 理由の無い指摘の結果は理由を持たない
        mvv = item.get("mvv")
        item_mvv = mvv if isinstance(mvv, str) else None
        out.append(Finding(name, text, item["outcome"], why, severity, item_mvv, bool(item.get(REDLINE)), bool(item.get(BY_NDF))))
    return tuple(out)


def as_review(data: Mapping[str, Any]) -> Review:
    """要約（`summary_of` の形）を読む。指摘の形が違えば `ValueError`、ラウンドの数が整数でなければ 0。根拠の記述の確かめの成否が
    無い要約（駆動を通さずに作ったもの）は、確かめる対象が無かったとみなす。"""
    findings = store.list_of(data, store.FINDINGS)
    rounds = data.get(store.ROUNDS)
    unsent = data.get(UNSENT_CITATIONS)
    count = unsent if type(unsent) is int else 0
    done = rounds if type(rounds) is int else 0
    return Review(str(data.get("final")), done, as_findings(findings), data.get(MVV_CHECKED) is not False, count)


def summary_of(review: Review, pr: int) -> dict[str, Any]:
    """要約の形（終わり方・ラウンドの数・指摘・根拠の記述の確かめの成否・PR に届いていない誤りの件数・元の PR 番号）。`as_review` が読む。"""
    return {
        "final": review.final,
        store.ROUNDS: review.rounds,
        store.FINDINGS: [dict(f._asdict()) for f in review.findings],
        MVV_CHECKED: review.mvv_checked,
        UNSENT_CITATIONS: review.unsent_citations,
        "pr": pr,
    }


def as_handed(items: Sequence[Any]) -> tuple[Handed, ...]:
    """組み直しの結果 JSON の items（辞書か文字列）を足すべき内容にする。`id` が無ければ順の番号から作る。"""
    out = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            out.append(Handed(f"rebuild-{i + 1}", item))
        elif isinstance(item, dict):
            out.append(Handed(_id_of(item, "rebuild", i), _text_of(item)))
        else:
            raise ValueError(f"足すべき内容 {i} の形が違う（文字列か辞書）")
    return tuple(out)
