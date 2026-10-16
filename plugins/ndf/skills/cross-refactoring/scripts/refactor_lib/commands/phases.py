"""手順の開始時刻と監視期限を記録する。"""

from __future__ import annotations

import argparse

import statefile

from .. import clock, die, timeline
from ..paths import git_out, load_state, work_dir

PHASE_NAMES = ("propose", "plan", "add-tests", "implement", "fix", "final-fix")


def cmd_start_phase(args: argparse.Namespace) -> None:
    """手順の開始を進行側の時計で記録し、監視の上限を返す。"""
    path, state = load_state(args.id)
    phase = args.phase
    if phase not in PHASE_NAMES:
        die(f"未知の手順です: {phase}（{' / '.join(PHASE_NAMES)}）")
    record = state.setdefault("phases", {}).setdefault(phase, {})
    now = statefile.now()
    if phase in ("fix", "final-fix") or not record.get("started_at"):
        record.setdefault("started_at", now)
        record["launch_started_at"] = now
        record["base_sha"] = git_out(work_dir(state), ["rev-parse", "HEAD"])
    limits_table = timeline.limits_of(state)
    end = clock.parse(limits_table.get(timeline.PHASE_END_KEYS[phase]))
    timeout = ""
    if end is not None:
        margin = int(limits_table["margin_seconds"])
        # 締め切りは最終ゲート修正が確かめの開始の締め切り H − v、ほかの手順が想定最大時間の終わり H（#1932 原則 1・3）。
        # 最終ゲート修正は `final-gate` が起動の前に H − v までに入るかを見ている。
        deadline = timeline.gate_start_end(state) if phase == "final-fix" else timeline.final_end(state)
        if phase == "final-fix" and deadline is not None:
            end = deadline
        now = clock.now()
        seconds = timeline.phase_timeout(end, now, margin, deadline)
        record["timeout"] = seconds
        record["cli_timeout"] = max(1, timeline.clip_to_end(seconds + margin, deadline, now))
        timeout = str(seconds)
    if phase != "final-fix":
        state["phase"] = phase
    statefile.save(path, state)
    statefile.emit(PHASE_TIMEOUT=timeout)
