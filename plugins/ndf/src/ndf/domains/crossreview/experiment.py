"""cross-review の実験の記録と、起動した AI の子の使用量ログの行（#2944）。

状態ファイルの `experiment_units` は、実行の ID（`id`。1 回だけ作り、打ち直しでも同じ）・シートの割り付け ID（`seats`）・1 ラウンド目に
挙がった指摘の id（`first`）を持つ。シートの単位は `<実行の ID>:<シート>` である。値を選ぶ処理の差し込みが無い起動は、状態ファイルに
何も足さず、行も書かない。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf import identity
from ndf.adapters import agents, claude, claude_choice
from ndf.contracts import facts
from ndf.core.converge import participants
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import experiment_values
from ndf.domains.records import usage

# 状態ファイルのキー
KEY = "experiment_units"
# 効果指標を書く終わり方（結果を読めないシートがあって終わった場合・振動・誤りは書かない）
RECORDED = (fin.CONVERGED, fin.MAX_ROUNDS)


def start_run(data: dict[str, Any]) -> bool:
    """値を選ぶ処理があるのに状態ファイルに `experiment_units` が無ければ、実行の ID を作って足し、真を返す（呼び手が保存する）。"""
    if claude_choice.launch_chooser is None or KEY in data:
        return False
    data[KEY] = {"id": uuid.uuid4().hex, "seats": {}}
    return True


def keep_first(data: dict[str, Any], entered: Sequence[str]) -> None:
    """1 ラウンド目に相手役が挙げた指摘の id を残す。"""
    if KEY in data:
        data[KEY]["first"] = list(entered)


def charge_to() -> Optional[str]:
    """このレビューと修正の費用を負う割り付け ID（環境変数。無いか空なら None）。"""
    value = os.environ.get(identity.env_name("charge-to"))
    return None if value == "" else value


class Held(Protocol):
    """状態ファイルの中身を持つ 1 回の起動の値（`drive._Run`）。"""

    data: dict[str, Any]


def choose_seat(run: Held, seat: str, cwd: Path) -> claude_choice.ChoiceRun:
    """シートの単位の値を選ぶ。値を渡せないランタイム（表の `choice_args` が無い kiro）と、差し込みの無い起動は値なし。"""
    runtime = participants.runtime_of(seat)
    if claude_choice.launch_chooser is None or "choice_args" not in facts.row(participants.RUNTIMES, runtime):
        return claude_choice.ChoiceRun(None)
    start_run(run.data)
    unit = f"{run.data[KEY]['id']}:{seat}"
    return claude_choice.ChoiceRun(claude_choice.pick_values("seat", "review", cwd=cwd, runtime=runtime, unit=unit))


def keep_seat(run: Held, seat: str, chosen: claude_choice.ChoiceRun) -> None:
    """シートの割り付け ID を残す。割り付けの無い値（停止・手動指定・基準の組への切り替え）なら外す。"""
    if chosen.choice is None:
        return
    if chosen.assign_id is not None:
        run.data[KEY]["seats"][seat] = chosen.assign_id
    else:
        run.data[KEY]["seats"].pop(seat, None)


def meta_of(name: str, chosen: claude_choice.ChoiceRun) -> dict[str, Any]:
    """使用量ログの行の工程の欄と、選んだ値の欄。"""
    return {**claude.launch_meta(name, chosen.repo), **chosen.meta()}


def claude_row(log: claude.LaunchLog, output: str, status: agents.AgentStatus) -> claude.ClaudeReply:
    """監視した `claude -p` の子 1 つの行を書き、出力から読んだ回答を返す（起動しなかった子の行は書かない）。"""
    reply = claude.parse_reply(output)._replace(launched=status.seconds is not None, seconds=status.seconds)
    log.meta["outcome"] = str(status.state)
    log.report(reply)
    return reply


def seat_row(spec: agents.Launch, status: agents.AgentStatus, chosen: claude_choice.ChoiceRun) -> Optional[claude.ClaudeReply]:
    """シートの起動 1 回の行を書く。返す回答は基準の組へ切り替えるかの判断に使う（値を選ばなかった起動は None）。"""
    if chosen.choice is None:
        return None
    if spec.agent == participants.FIXER:
        reply = claude_row(claude.LaunchLog(meta_of("review", chosen)), agents.tail(spec.paths.out), status)
        chosen.seen(reply)
        return reply
    refused = agents.codex_refused(spec.paths.out)
    if status.seconds is not None:
        meta = {**meta_of("review", chosen), "seconds": status.seconds, "outcome": str(status.state)}
        if refused:
            meta["model_refused"] = True
        usage.record_codex(agents.codex_usage(spec.paths.out), meta, chosen.choice)
    failure = claude.Failure.MODEL_REFUSED if refused else None
    return claude.ClaudeReply.failed("", "codex の起動の結果", failure=failure)


def record(data: dict[str, Any], rows: Sequence[dict[str, Any]], seats: Sequence[str], final: str) -> None:
    """収束かラウンドの上限で終わったレビューの効果指標を書く: シートの単位と、費用を負う割り付け（評価した設計か実装の単位）。

    書けなくても警告だけで、レビューの結果は変えない。同じ割り付け ID の 2 回目の指標は書き込み処理が足さない。
    """
    if final not in RECORDED or KEY not in data:
        return
    held = data[KEY]
    count, shares = experiment_values(rows, held.get("first"), seats)
    for seat in seats:
        if seat in held["seats"]:
            claude_choice.report_outcome(held["seats"][seat], shares[seat], {"seats": len(seats)})
    charged = charge_to()
    if charged is not None:
        claude_choice.report_outcome(charged, None if count is None else 1 / (1 + count), {"major": count})
