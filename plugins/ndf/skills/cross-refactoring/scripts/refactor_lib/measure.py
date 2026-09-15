"""cross-refactoring の実行の要約が持つ工程の所要（#662）。

**監視の記録から組み立てる**（設計の決定 9）。提案・適用・修正・段 2 の判定・最終ゲートの
修正はどれも監視を通るため、記録の stem から工程とラウンドが決まる。ラウンドの種類
（テスト整備 / 構造改善）は状態ファイルの `rounds[].kind` が持つ。取り込み（`commands/`）へ
時刻の記録を足さずに済む。

**進行側が回すテストの所要は直接は測らない。** ラウンドの所要から CLI の時間を引いた残りを
`other_seconds` として出す。

群ごとの試行回数（`apply_attempts`）はここに無い。D-C の P6（#665）が状態ファイルの群から足す。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Optional

import run_metrics

from .rounds import entry_kind

# stem から工程とラウンドを読む規則（契約の文書の「stem から工程を読む規則」）。
# 監視の記録が `phase` を持つとき（P2 以降の記録）は、工程だけはそちらを優先する。
_STEM = re.compile(
    r"^(?:codex|agy|claude|kiro)-(?:"
    r"(?P<propose>propose)-rf\d+-r(?P<propose_round>\d+)"
    r"|(?P<apply>apply|fix)-r(?P<apply_round>\d+)"
    r"|(?P<judge>judge-test-changes)-r(?P<judge_round>\d+)-g\d+"
    r"|(?P<final>final-fix)"
    r")$"
)


# 記録の `phase` として受ける工程（cross-refactoring の監視が渡す名前）。
_PHASES = ("propose", "apply", "fix", "judge-test-changes", "final-fix")


def _phase_of(launch: dict[str, Any]) -> Optional[tuple[str, Optional[int]]]:
    """起動 1 回の工程とラウンド。ラウンドは stem から、工程は記録の `phase` を優先して読む。"""
    m = _STEM.match(str(launch.get("stem") or ""))
    if m is None:
        return None
    parsed: tuple[str, Optional[int]] = ("final-fix", None)
    for name in ("propose", "apply", "judge"):
        if m.group(name):
            parsed = m.group(name), int(m.group(f"{name}_round"))
            break
    recorded = launch.get("phase")
    if recorded in _PHASES:
        return recorded, None if recorded == "final-fix" else parsed[1]
    return parsed


def _before(a: Any, b: Any) -> bool:
    """時刻 `a` が `b` より前か。どちらかが読めなければ偽。"""
    diff = run_metrics.seconds_between(a, b)
    return diff is not None and diff > 0


def _add(bucket: dict[str, Any], launch: dict[str, Any]) -> None:
    """工程 1 つへ起動 1 回を足す。時刻は最も早い開始と最も遅い終了を残す。"""
    elapsed = launch.get("elapsed")
    seconds = float(elapsed) if isinstance(elapsed, (int, float)) else 0.0
    bucket["launches"] = bucket.get("launches", 0) + 1
    bucket["cli_seconds"] = round(bucket.get("cli_seconds", 0.0) + seconds, 1)
    started, ended = launch.get("started_at"), launch.get("ended_at")
    if "first_started_at" not in bucket or _before(started, bucket["first_started_at"]):
        bucket["first_started_at"] = started
    if "last_ended_at" not in bucket or _before(bucket["last_ended_at"], ended):
        bucket["last_ended_at"] = ended


def phases(state: dict[str, Any], launches: list[dict[str, Any]]) -> dict[str, Any]:
    """種類ごとの工程の起動回数・CLI の合計秒・開始と終了、と `other_seconds`。

    **起動のあった工程だけを置く。** 起動の無い工程を 0 で置くと、測っていない工程と
    0 秒の工程を区別できない。
    """
    rounds = [r for r in state.get("rounds") or [] if isinstance(r, dict)]
    kind_of = {r.get("round"): entry_kind(r) for r in rounds}
    out: dict[str, Any] = {}
    for kind in dict.fromkeys(kind_of.values()):
        out[kind] = {}
    cli_by_round: dict[int, float] = {}
    for launch in launches:
        parsed = _phase_of(launch)
        if parsed is None:
            continue
        phase, round_no = parsed
        if phase == "final-fix":
            _add(out.setdefault("final-fix", {}), launch)
            continue
        kind = kind_of.get(round_no)
        if kind is None:
            continue
        _add(out[kind].setdefault(phase, {}), launch)
        elapsed = launch.get("elapsed")
        if isinstance(elapsed, (int, float)):
            cli_by_round[round_no] = cli_by_round.get(round_no, 0.0) + float(elapsed)
    for kind in dict.fromkeys(kind_of.values()):
        out[kind]["other_seconds"] = _other_seconds(state, kind, cli_by_round)
    return out


def _other_seconds(state: dict[str, Any], kind: str, cli_by_round: dict[int, float]) -> Optional[int]:
    """終わりの分かるラウンドの所要から、そのラウンドの CLI の時間を引いた値。

    終わりの分からない（進行中の）ラウンドを混ぜると、CLI の時間だけが入って残りが
    負になる。終わりの分かるラウンドが無ければ `None`。
    """
    total: Optional[int] = None
    cli = 0.0
    for span in run_metrics.round_spans(state):
        if entry_kind(span) != kind:
            continue
        seconds = run_metrics.seconds_between(span.get("started_at"), span.get("ended_at"))
        if seconds is None:
            continue
        total = (total or 0) + seconds
        cli += cli_by_round.get(span.get("round"), 0.0)
    return None if total is None else max(0, int(round(total - cli)))


def summary_extra(path: pathlib.Path, state: dict[str, Any],
                  launches: list[dict[str, Any]]) -> dict[str, Any]:
    """`run_metrics.after_save` へ渡す、cross-refactoring だけが持つ鍵。"""
    return {"phases": phases(state, launches)}
