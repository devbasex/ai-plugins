"""手順の開始時刻と監視の上限を記録する（#2457 決定 5）。

監視の上限は予算から導かない。提案・計画・判定の手順は `limits.json` の `monitor.phase.<手順>` の秒（空を返して
監視に任せる）、コーディングの手順は安全網の残りで切る。残りが 0 以下なら起動させずに `HALT=1` を返す。
"""

from __future__ import annotations

import argparse

import statefile

from .. import die, safety_net, step_extend
from ..paths import git_out, load_state, work_dir

PHASE_NAMES = ("propose", "plan", "add-tests", "implement", "fix", "final-fix", "judge", "flaky-fix")


def cmd_start_phase(args: argparse.Namespace) -> None:
    """手順の開始を進行側の時計で記録し、監視の上限を返す。"""
    path, state = load_state(args.id)
    phase = args.phase
    if phase not in PHASE_NAMES:
        die(f"未知の手順です: {phase}（{' / '.join(PHASE_NAMES)}）")
    record = state.setdefault("phases", {}).setdefault(phase, {})
    now = statefile.now()
    if phase in ("fix", "final-fix", "judge", "flaky-fix") or not record.get("started_at"):
        record.setdefault("started_at", now)
        record["launch_started_at"] = now
        record["base_sha"] = git_out(work_dir(state), ["rev-parse", "HEAD"])
    timeout = ""
    record.pop("cli_timeout", None)
    if phase in safety_net.CODING_PHASES:
        safety_net.refresh_limit(state)
        left = safety_net.remaining_seconds(state)
        if left <= 0:
            statefile.save(path, state)
            statefile.emit(PHASE_TIMEOUT="", HALT=1, HALT_REASON=safety_net.halt_reason(state))
            return
        import limits as limit_decl

        # 監視の上限は安全網の残り。CLI の上限はそれに余裕を足す
        record["timeout"] = left
        record["cli_timeout"] = left + int(limit_decl.cli_margin())
        timeout = str(left)
        step_extend.ahead(record["cli_timeout"], f"{phase} の CLI の上限（安全網の残り）{left} 秒")
    if phase not in ("final-fix", "judge", "flaky-fix"):
        state["phase"] = phase
    statefile.save(path, state)
    statefile.emit(PHASE_TIMEOUT=timeout)


def cmd_coding_time(args: argparse.Namespace) -> None:
    """コーディングの CLI の起動 1 回の秒（監視の開始から終わり）を安全網へ足す。出力は `HALT=0|1`。"""
    from ..items import find_item

    path, state = load_state(args.id)
    items = []
    if args.phase == "fix":
        items = [find_item(state, i, required=False) for i in (state.get("fix") or {}).get("items") or []]
        items = [i for i in items if i is not None]
    safety_net.add(state, args.phase, float(args.seconds), items)
    statefile.save(path, state)
    stop = safety_net.halted(state)
    statefile.emit(HALT=1 if stop else 0, **({"HALT_REASON": safety_net.halt_reason(state)} if stop else {}))
