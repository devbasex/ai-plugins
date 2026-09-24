"""cross-refactoring の実行の要約が持つフェーズの所要（#662 / #933）。

**フェーズの所要は状態ファイルの `phases` から読む**（#933 の決定 8。進行側の時計）。
あわせて監視の記録から、フェーズごとの CLI の起動回数と合計秒を足す。CLI の時間と
フェーズの所要の差が、進行側が回したテストと取り込みの時間である。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Optional

# stem からフェーズを読む規則（`paths.stem_for` と揃える）。
_STEM = re.compile(
    r"^(?:codex|agy|claude|kiro)-(?:"
    r"(?P<phase>propose|plan|add-tests|implement|fix|judge-test-changes)-rf\d+"
    r"|(?P<final>final-fix)"
    r")$"
)


def _phase_of(launch: dict[str, Any]) -> Optional[str]:
    m = _STEM.match(str(launch.get("stem") or ""))
    if m is None:
        return None
    return m.group("phase") or "final-fix"


def phases(state: dict[str, Any], launches: list[dict[str, Any]]) -> dict[str, Any]:
    """フェーズごとの所要（進行側の時計）と、CLI の起動回数・合計秒（監視の記録）。

    **起動や記録のあったフェーズだけを置く。** 無いフェーズを 0 で置くと、測っていない
    フェーズと 0 秒のフェーズを区別できない。
    """
    out: dict[str, Any] = {}
    for name, record in (state.get("phases") or {}).items():
        if isinstance(record, dict) and record.get("seconds") is not None:
            out.setdefault(name, {})["seconds"] = record["seconds"]
    for launch in launches:
        name = _phase_of(launch)
        if name is None:
            continue
        bucket = out.setdefault(name, {})
        elapsed = launch.get("elapsed")
        bucket["launches"] = bucket.get("launches", 0) + 1
        if isinstance(elapsed, (int, float)):
            bucket["cli_seconds"] = round(bucket.get("cli_seconds", 0.0) + float(elapsed), 1)
    return out


def summary_extra(path: pathlib.Path, state: dict[str, Any],
                  launches: list[dict[str, Any]]) -> dict[str, Any]:
    """`run_metrics.after_save` へ渡す、cross-refactoring だけが持つ鍵。"""
    return {
        "phases": phases(state, launches),
        "budget_minutes": state.get("budget_minutes"),
        "implementer": state.get("implementer"),
    }
