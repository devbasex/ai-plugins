"""工程「リファクタリング」の差し込み `refactor` の値（`ndf.progress.invoke` から分けた。#3908 の Task 8）。

実装計画の「リファクタリングの時機」のスプリントの終わりの行を候補のファイルへ書いて `--candidates` を足す（#3879 構成要素 5）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ndf.core import route
from ndf.core.plan import inputs_decide as plan_inputs


REFACTOR_CANDIDATES = "refactor-candidates.md"
PLAN_FILE = "issues/issue-{number}-plan.md"
# implementation-plan の「リファクタリングの時機」の表（時機 | 対象 | 理由）の見出しと、スプリントの終わりの行の時機
TIMING_HEADING = "リファクタリングの時機"
SPRINT_END = "スプリントの終わり"
NO_TARGET = "しない"


def _cells(line: str) -> list[str]:
    """Markdown の表の行の列。エスケープしたパイプ（`\\|`）は列を区切らず、列の中では `|` に戻す。"""
    return plan_inputs.split_row(line)


def _sprint_end_rows(text: str) -> list[tuple[str, str]]:
    """実装計画の「リファクタリングの時機」の表（時機 | 対象 | 理由）の、スプリントの終わりの行の (対象, 理由)。

    見出しが無ければ空。対象が「しない」の行は含めない。
    """
    lines = text.splitlines()
    heading = next((i for i, line in enumerate(lines) if line.startswith("#") and line.lstrip("#").strip() == TIMING_HEADING), None)
    if heading is None:
        return []
    found = []
    for line in lines[heading + 1 :]:
        if line.startswith("#"):
            break
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) == 3 and cells[0].startswith(SPRINT_END) and cells[1] != NO_TARGET:
            found.append((cells[1], cells[2]))
    return found


def _candidates(worktree: str, issues: Sequence[Any]) -> list[str]:
    """課題の実装計画のスプリントの終わりの行を、課題の番号を添えた候補の行にしたもの。"""
    lines: list[str] = []
    for number in issues:
        plan = Path(worktree) / PLAN_FILE.format(number=number)
        if plan.is_file():
            lines += [
                f"- #{number}: {target}" + (f"（理由: {why}）" if why else "")
                for target, why in _sprint_end_rows(plan.read_text(encoding="utf-8"))
            ]
    return lines


def fill(
    t: route.Template, values: Mapping[str, route.Value], issues: Sequence[Any], state_dir: Path
) -> dict[str, route.Value]:
    """cross-refactoring なら、課題の実装計画のスプリントの終わりの候補を `state_dir` へ書き、`refactor` の後ろに `--candidates` を足す。

    実装計画か表の無い課題は飛ばす。候補が 1 行も無ければ何も書かず、`refactor` は今のまま。
    """
    launch, worktree = values.get("refactor"), values.get("worktree")
    if t.name != "cross-refactoring" or not isinstance(launch, list) or not isinstance(worktree, str):
        return {}
    lines = _candidates(worktree, issues)
    if not lines:
        return {}
    out = state_dir / REFACTOR_CANDIDATES
    out.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return {"refactor": [*launch, "--candidates", str(out)]}
