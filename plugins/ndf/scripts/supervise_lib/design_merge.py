"""pace: normal の承認ゲート 1 の後の設計 Pull Request のマージ（#1426 の決定 4・I1）。

設計 PR ごとに 1 本のプラン `design-merge-<N>` を組む: `approved`（承認の証跡を確かめる）→ `merge-gate` → `merge`。
PR の番号は設計のプランの報告から `{queue_pr:design-<N>}` で読み、conductor は書き込まない。承認の証跡は承認ラベル
`design-approved` か、スプリント状態ファイルの承認ゲート 1 の利用者の承認の記録（`--pr N`）である。
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Callable

import gate_answer
from gate_answer import GATE_1
from step_result import EXIT_GATE, EXIT_UNREADABLE, result
from supervise_lib.paths import SELF
from supervise_lib.plan import sprint_state_path
from supervise_lib.procedures import with_record
from supervise_lib.verify_steps import merge_steps

TOOL = "supervise-design-approved"
LABEL = "design-approved"
RULE = "承認の証跡が無ければ approved が関門で止まる。マージが落ちたら止まり、conductor は queue --resume で続ける。"


def plan_design_merge(a, n: int, repo: str) -> dict:
    """設計 PR 1 本のマージのプラン。名前は design-merge-<N>（merge-design-<N> は `{queue_pr:design-<N>}` の照合に当たる）。"""
    state = f" --state {shlex.quote(sprint_state_path(a))}" if getattr(a, "state", None) else ""
    return with_record(
        {
            "フェーズ": "設計",
            "課題": [n],
            "モード": a.mode,
            "作業場所": repo,
            "リポジトリ": repo,
            "Pull Request": f"{{queue_pr:design-{n}}}",
            "規則": RULE,
            "steps": [
                {
                    "id": "approved",
                    "type": "run",
                    "stage": "設計",
                    "timeout": 300,
                    "cmd": f"python3 {shlex.quote(str(SELF))} design-approved --pr {{pr}}{state}",
                    "gate_next": "end",
                    "next": "merge-gate",
                },
                *merge_steps(a, next="end"),
            ],
        }
    )


def _gh_out(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout if p.returncode == 0 else p.stderr


def cmd_design_approved(pr: int, state: str | None, gh: Callable[[list[str]], tuple[int, str]] = _gh_out) -> tuple[dict, int]:
    """承認ラベルがあれば 0。無く、状態に承認ゲート 1 の `--pr N` の利用者の承認の記録があればラベルを付けて 0。
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
    answer = gate_answer.approved(state, GATE_1, pr=pr)
    if answer is None:
        nxt = f'承認を取り、sprint-state.py gate <状態> "{GATE_1}" --what <要約> --pr {pr} --by user --outcome approved（状態が無ければ gh pr edit {pr} --add-label {LABEL}）の後に queue --resume で続ける'
        return result(
            TOOL, "gate", f"PR {pr} に承認の証跡が無い（承認ラベルも承認ゲート 1 の利用者の承認の記録も無い）", [], metrics, next=nxt
        ), EXIT_GATE
    code, out = gh(["pr", "edit", str(pr), "--add-label", LABEL])
    if code != 0:
        return result(TOOL, "stopped", f"PR {pr} へ承認ラベルを付けられない: {out.strip()[-300:]}", [], metrics), EXIT_UNREADABLE
    return result(
        TOOL,
        "ok",
        f"PR {pr} へ承認ラベルを付けた（承認ゲート 1 の記録 {answer.at}）",
        [{"kind": "label", "name": LABEL, "result": "added"}],
        metrics,
    ), 0
