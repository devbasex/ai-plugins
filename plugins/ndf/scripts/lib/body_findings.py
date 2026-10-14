"""総評だけの指摘（インラインのスレッドを持たず、レビュー本文にだけ書かれた指摘）の判定（#1980）。

cross-review の状態ファイルの `review_findings[]` のうち `posted_to` が `body` のものを指す。
スレッドが無いため、決着は GitHub の上ではなく状態ファイルの `body_settlements[]` が持つ。
修正の雛形（fix の `fix-steps.py`）・収束の判定（`judge`）・最終スイープ（`verify-sweep`）・
取り込み（`merge-fix`）が、この 1 か所の判定を使う。

`read_state` を除き、状態ファイルの dict を受けて返す関数だけを持つ。終了コードも出力も
持たず、止めるか・何を出すかは呼ぶ側が決める。
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from finding_match import match_kind, normalized_body

SEVERITIES = ("critical", "major", "minor", "nit")
SOURCES = ("fix", "sweep")


def read_state(path) -> dict[str, Any] | None:
    """状態ファイルを dict で返す。無い・読めない・dict でないときは None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _findings(st: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in st.get("review_findings") or [] if isinstance(f, dict)]


def _settlements(st: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in st.get("body_settlements") or [] if isinstance(s, dict)]


def is_body(finding: dict[str, Any]) -> bool:
    """総評だけの指摘か。`posted_to` を持たない記録は `inline` と読む（旧い状態ファイル）。"""
    return (finding.get("posted_to") or "inline") == "body"


def _line(finding: dict[str, Any]) -> int | None:
    try:
        return int(finding.get("line"))
    except (TypeError, ValueError):
        return None


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """2 件の指摘が同じ箇所か。照合は新しい指摘の数え方と同じ `finding_match.match_kind`。

    行を持たない指摘は位置で比べられないため、同じファイルで本文が一致するときだけ同じとみる。
    """
    path_a, path_b = str(a.get("path") or ""), str(b.get("path") or "")
    body_a, body_b = normalized_body(a.get("body")), normalized_body(b.get("body"))
    line_a, line_b = _line(a), _line(b)
    if line_a is None or line_b is None:
        return path_a == path_b and bool(body_a) and body_a == body_b
    return match_kind((path_a, line_a, body_a), [(path_b, line_b, body_b)]) is not None


def _round(finding: dict[str, Any]) -> int:
    try:
        return int(finding.get("round"))
    except (TypeError, ValueError):
        return 0


def pending(st: dict[str, Any]) -> list[dict[str, Any]]:
    """決着していない総評だけの指摘を、`review_findings[]` の並びの順で返す。

    すべてのラウンドを見る。外すのは、決着の記録を持つもの・証拠集約が統合した（`merged_into`）
    もの・前のラウンドで `fixed` 以外に決着した総評だけの指摘の再提出である。`fixed` に決着した
    指摘と同じ箇所の再提出は外さない（直したはずの指摘が残っている）。
    """
    findings = _findings(st)
    by_id = {f.get("finding_id"): f for f in findings}
    settled = {s.get("finding_id"): s for s in _settlements(st)}
    let_go = [by_id[i] for i, s in settled.items() if s.get("decision") != "fixed" and i in by_id]
    out = []
    for f in findings:
        if not is_body(f) or f.get("merged_into") or f.get("finding_id") in settled:
            continue
        if any(_round(prev) < _round(f) and _same(f, prev) for prev in let_go):
            continue
        out.append(f)
    return out


def _head_line(s: object, n: int = 100) -> str:
    text = s.strip() if isinstance(s, str) else ""
    return text.splitlines()[0][:n] if text else ""


def template_entry(finding: dict[str, Any]) -> dict[str, Any]:
    """修正の振り分けの雛形へ入れる要素。`severity` は担当の値を入れておく（付け直してよい）。"""
    severity = finding.get("severity")
    return {
        "thread_id": None,
        "finding_id": finding.get("finding_id"),
        "comment_id": None,
        "path": finding.get("path"),
        "line": finding.get("line"),
        "severity": severity if severity in SEVERITIES else "",
        "category": "",
        "summary": _head_line(finding.get("body")),
        "decision": "",
        "reason": "",
        "mvv_basis": [],
    }


def missing(decisions: Iterable[Any], pending_findings: Iterable[dict[str, Any]]) -> list[str]:
    """`pending` の指摘のうち、振り分け（`decisions[]`）に同じ `finding_id` の要素が無いものの `finding_id`。"""
    present = {e.get("finding_id") for e in decisions if isinstance(e, dict) and e.get("finding_id")}
    return [str(f.get("finding_id")) for f in pending_findings if f.get("finding_id") not in present]


def entries(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """戻り値ファイル（またはスイープの結果ファイル）の要素を、置き場所と組にして並べる。"""
    out: list[tuple[str, dict[str, Any]]] = []
    for place in ("resolved_threads", "deferred", "rejected"):
        out += [(place, e) for e in result.get(place) or [] if isinstance(e, dict)]
    return out


def _decision(place: str, e: dict[str, Any]) -> str:
    if place == "resolved_threads":
        return "fixed"
    if place == "rejected":
        return "rejected"
    if e.get("waived"):
        return "waived"
    if e.get("issue"):
        return "separate_pr"
    return "deferred"


def settle(
    st: dict[str, Any],
    items: Iterable[tuple[str, dict[str, Any]]],
    round_no: int,
    pr: int,
    source: str,
) -> int:
    """総評だけの指摘の決着を `body_settlements[]` へ追記し、足した件数を返す。

    `items` は `entries` が返す（置き場所, 要素）の並び。`finding_id` を持ち、その `finding_id` が
    `review_findings[]` にあり、決着の記録がまだ無い要素だけを記録する。記録は書き換えない。
    知らない `finding_id` は記録せず、件数を標準エラーへ出す。
    """
    known = {f.get("finding_id") for f in _findings(st)}
    records = st.setdefault("body_settlements", [])
    done = {s.get("finding_id") for s in records if isinstance(s, dict)}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    added = unknown = 0
    for place, e in items:
        fid = e.get("finding_id")
        if not fid:
            continue
        if fid not in known:
            unknown += 1
            continue
        if fid in done:
            continue
        decision = _decision(place, e)
        reason = e.get("reason_for_rejection") if place == "rejected" else e.get("reason_for_deferral")
        records.append(
            {
                "finding_id": fid,
                "decision": decision,
                "reason": "" if decision == "fixed" else str(reason or ""),
                "issue": str(e.get("issue") or "") if decision == "separate_pr" else "",
                "round": round_no,
                "pr": pr,
                "source": source,
                "recorded_at": now,
            }
        )
        done.add(fid)
        added += 1
    if unknown:
        print(f"⚠ 総評だけの指摘の決着: 状態ファイルに無い finding_id が {unknown} 件あり、記録しなかった", file=sys.stderr)
    return added
