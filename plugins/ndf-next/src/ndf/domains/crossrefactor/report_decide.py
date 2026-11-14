"""完了報告とリファクタリング計画のコメントの本文。純粋な関数だけを置く。

- 計画のコメントは PR へ載せる項目の表（番号・対象・状態・理由）と予算の行で、結果の出口（完了・止まった）で書く。
  前に書いた本文と同じなら書かない（`unchanged`）
- 完了報告は手元の作業場所の logs へ置く文書で、項目の表に加えて見送りの理由別の件数・工程別の所要・予算と安全網・
  成立の判定・フレーキーの直し・最終ゲートの記録を持つ
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from ndf import identity
from ndf.domains.crossrefactor.items_decide import Status

MARKER = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-refactoring-plan -->"
STATUS_TEXT = {Status.OPEN: "未着手", Status.ADOPTED: "採った", Status.REVERTED: "戻した", Status.DEFERRED: "見送った"}


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _field(row: Mapping[str, Any], key: str) -> Any:
    """状態の行の鍵の値。無ければ空の文字列（表に空欄として出す）。"""
    return row[key] if key in row and row[key] is not None else ""


def _rows(state: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = state[key] if key in state else None
    return list(value) if isinstance(value, list) else []


def _item_rows(items: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = ["| 番号 | 対象 | 要約 | 状態 | 理由 |", "| --- | --- | --- | --- | --- |"]
    for i in items:
        status = str(i["status"])
        shown = STATUS_TEXT[status] if status in STATUS_TEXT else status
        rows.append(
            f"| {i['id']} | `{_cell(i['path'])}` の `{_cell(i['symbol'])}` | {_cell(i['title'])} | {shown} | {_cell(_field(i, 'reason'))} |"
        )
    return rows


def _budget_line(state: Mapping[str, Any]) -> str:
    budget = state["budget"] if "budget" in state else None
    net = state["safety_net"] if "safety_net" in state else None
    if not isinstance(budget, Mapping):
        return "予算: 計画の担当が見積りを返さなかったため、予算で選んでいない"
    line = f"予算: {budget['minutes']} 分"
    if budget["extended_to"] is not None:
        line += f"（{budget['extended_to']} 分へ延ばした。{budget['reason']}）"
    if isinstance(net, Mapping):
        line += f"・安全網 {float(net['limit_minutes']):.0f} 分のうちコーディングの CLI の累計 {float(net['used_seconds']) / 60:.1f} 分"
    return line


def plan_comment(pr: int, state: Mapping[str, Any], outcome: str) -> str:
    """PR へ載せるリファクタリング計画のコメントの本文。"""
    items = _rows(state, "items")
    lines = [MARKER, f"## リファクタリング計画（PR #{pr}）", "", f"結果: {outcome}", "", _budget_line(state), ""]
    lines += _item_rows(items) if items else ["採る項目は無い。"]
    return "\n".join(lines) + "\n"


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _skipped_lines(state: Mapping[str, Any]) -> list[str]:
    reasons = Counter(str(_field(s, "reason")) for s in _rows(state, "skipped"))
    reasons.update(str(_field(i, "reason")) for i in _rows(state, "items") if i["status"] == Status.DEFERRED)
    return [f"- {why or '理由なし'}: {n} 件" for why, n in sorted(reasons.items())] or ["- 無し"]


def _phase_lines(state: Mapping[str, Any]) -> list[str]:
    seconds = state["phase_seconds"] if "phase_seconds" in state else {}
    return [f"- {phase}: {float(s):.0f} 秒" for phase, s in sorted(seconds.items())] or ["- 無し"]


def _judge_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    for i in items:
        for j in _rows(i, "feasibility"):
            verdict = {True: "成立する", False: "成立しない", None: "判定できない"}[j["feasible"]]
            out.append(f"- {i['id']}: {verdict}（{j['by']}。{_field(j, 'reason')}）")
    return out or ["- 無し"]


def _flaky_lines(state: Mapping[str, Any]) -> list[str]:
    return [
        f"- {f['key']}: {f['result']}" + (f"（{f['commit']}）" if _field(f, "commit") else "") for f in _rows(state, "flaky_fixes")
    ] or ["- 無し"]


def report(pr: int, state: Mapping[str, Any], outcome: str) -> str:
    """完了報告の本文（Markdown）。"""
    items = _rows(state, "items")
    final = state["final"] if "final" in state else None
    lines = [f"# cross-refactoring の完了報告（PR #{pr}）", "", f"結果: {outcome}", "", _budget_line(state), "", "## 項目", ""]
    lines += _item_rows(items) if items else ["採る項目は無い。"]
    lines += ["", "## 見送り（理由別）", "", *_skipped_lines(state), "", "## 工程別の所要", "", *_phase_lines(state)]
    lines += ["", "## 成立の判定", "", *_judge_lines(items), "", "## フレーキーなテストの直し", "", *_flaky_lines(state)]
    lines += ["", "## 最終ゲート", "", f"- {final}" if final else "- 未実施"]
    return "\n".join(lines) + "\n"
