"""設計 Pull Request のマージの前の承認の証跡の確かめ（`supervise.py design-approved`。#1426 の決定 4・I1）。

承認の証跡は承認ラベル `design-approved` か、スプリント状態ファイルの設計承認ゲートの利用者の承認の記録（`--pr N`）である。
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable

import gate_answer
from gate_answer import DESIGN_GATE
from step_result import EXIT_GATE, EXIT_UNREADABLE, result
import limits

TOOL = "supervise-design-approved"
LABEL = "design-approved"


def _gh_out(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=limits.seconds("supervise.design-merge-gh"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout if p.returncode == 0 else p.stderr


def cmd_design_approved(pr: int, state: str | None, gh: Callable[[list[str]], tuple[int, str]] = _gh_out) -> tuple[dict, int]:
    """承認ラベルがあれば 0。無く、状態に設計承認ゲートの `--pr N` の利用者の承認の記録があればラベルを付けて 0。
    証跡が無い・PR が 0 なら 10（承認ゲート）。gh が読めない・書けないなら 2。"""
    metrics = {"pr": pr, "state": state or ""}
    if not pr:
        return result(
            TOOL, "gate", "設計 Pull Request の番号が無い（設計のプランの報告に無い）", [], metrics, next="設計のプランの報告を読む"
        ), EXIT_GATE
    code, out = gh(["pr", "view", str(pr), "--json", "labels", "--jq", "[.labels[].name]"])
    if code != 0:
        return result(TOOL, "stopped", f"PR {pr} のラベルを読めない: {out.strip()[-300:]}", [], metrics), EXIT_UNREADABLE
    try:
        names = json.loads(out)
    except ValueError:
        names = None
    if not isinstance(names, list):
        return result(TOOL, "stopped", f"PR {pr} のラベルを読めない: {out.strip()[-300:]}", [], metrics), EXIT_UNREADABLE
    if LABEL in names:  # 完全一致だけ（design-approved-pending などの別ラベルを承認に数えない）
        return result(TOOL, "ok", f"PR {pr} に承認ラベルがある", [{"kind": "label", "name": LABEL, "result": "kept"}], metrics), 0
    answer = gate_answer.approved(state, DESIGN_GATE, pr=pr)
    if answer is None:
        nxt = f'承認を取り、sprint-state.py gate <状態> "{DESIGN_GATE}" --what <要約> --pr {pr} --by user --outcome approved（状態が無ければ gh pr edit {pr} --add-label {LABEL}）の後に、止まった工程を続ける（進行表なら progress.py next）'
        return result(
            TOOL, "gate", f"PR {pr} に承認の証跡が無い（承認ラベルも設計承認ゲートの利用者の承認の記録も無い）", [], metrics, next=nxt
        ), EXIT_GATE
    code, out = gh(["pr", "edit", str(pr), "--add-label", LABEL])
    if code != 0:
        return result(TOOL, "stopped", f"PR {pr} へ承認ラベルを付けられない: {out.strip()[-300:]}", [], metrics), EXIT_UNREADABLE
    return result(
        TOOL,
        "ok",
        f"PR {pr} へ承認ラベルを付けた（設計承認ゲートの記録 {answer.at}）",
        [{"kind": "label", "name": LABEL, "result": "added"}],
        metrics,
    ), 0
