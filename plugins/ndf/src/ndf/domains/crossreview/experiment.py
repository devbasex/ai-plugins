"""cross-review の実験の記録と、起動した AI の子の使用量ログの行（#2944）。

状態ファイルの `experiment_units` は、実行の ID（`id`。1 回だけ作り、打ち直しでも同じ）・シートの割り付け ID（`seats`）・評価する設計か
実装の割り付け ID（`charged`）と、その最初のレビューのラウンドで重大だった指摘の id（`first`）を持つ。読めない相手役がいる間は
`partial` を持つ。シートの単位は `<実行の ID>:<シート>` である。値を選ぶ処理の差し込みが無い起動は、状態ファイルに何も足さず、行も書かない。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf import identity
from ndf.adapters import agents, claude, claude_choice, launch_approval, launch_settings
from ndf.contracts import facts
from ndf.core.converge import participants
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import experiment_values, undecided_serious
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


def charge_env() -> str:
    """費用を手戻りとして負う割り付け ID を持つ環境変数の名前。"""
    return identity.env_name("charge-to")


def charge_to() -> Optional[str]:
    """このレビューと修正の費用を負う割り付け ID（環境変数。無いか空なら None）。"""
    value = os.environ.get(charge_env())
    return None if value == "" else value


def keep_first(data: dict[str, Any], round_no: int, serious: Sequence[str], complete: bool) -> None:
    """評価する単位の最初のラウンドで重大だった指摘の id を、その割り付け ID と対で残す。

    残し始めるのは 1 ラウンド目と、費用を負う割り付けが残したものから変わった後の最初のラウンドである（同じ状態の保存先で別の実装の
    変更を続けてレビューした場合に、前の実装の指摘で評価しない）。読めない相手役がいた（`complete` が偽）ラウンドでは確定せず、
    全員が読んだ最初のラウンドまでの指摘を足し合わせる。割り付けを指定しない打ち直しは、残した割り付けを変えない。
    """
    if KEY not in data:
        return
    held, charged = data[KEY], charge_to()
    if round_no == 1 or (charged is not None and ("charged" not in held or held["charged"] != charged)):
        held["first"], held["charged"] = list(serious), charged
    elif "partial" in held:
        held["first"] = list(dict.fromkeys([*held["first"], *serious]))
    else:
        return
    if complete:
        held.pop("partial", None)
    else:
        held["partial"] = True


class Held(Protocol):
    """状態ファイルの中身と、解決したリポジトリのキーを持つ 1 回の起動の値（`drive._Run`）。"""

    data: dict[str, Any]
    repo_key: Optional[str]


def choose_seat(run: Held, seat: str, cwd: Path) -> claude_choice.ChoiceRun:
    """シートの単位の値を選ぶ。値を渡せないランタイム（表の `choice_args` が無い kiro）と、差し込みの無い起動は値なし。"""
    runtime = participants.runtime_of(seat)
    if claude_choice.launch_chooser is None or "choice_args" not in facts.row(participants.RUNTIMES, runtime):
        return claude_choice.ChoiceRun(None)
    start_run(run.data)
    unit = f"{run.data[KEY]['id']}:{seat}"
    if run.repo_key is None:  # git の起動は 1 回の駆動で 1 回だけにする（シートと打ち直しで使い回す）
        run.repo_key = launch_approval.repo_key(launch_settings.launch_root(cwd))
    picked = claude_choice.pick_values("seat", "review", cwd=cwd, runtime=runtime, unit=unit, repo_key=run.repo_key)
    return claude_choice.ChoiceRun(picked)


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
        # 出力は 1 つの JSON なので全体を読む（末尾だけでは、大きい回答の使用量と費用が欠ける）。回答と読み取りの拒否の
        # 判定もこの解析結果を使う
        reply = claude_row(claude.LaunchLog(meta_of("review", chosen)), agents.whole_output(spec.paths.out), status)
        chosen.seen(reply)
        return reply
    events = agents.codex_events(spec.paths.out)
    refused = agents.codex_refused(events)
    if status.seconds is not None:
        meta = {**meta_of("review", chosen), "seconds": status.seconds, "outcome": str(status.state)}
        if refused:
            meta["model_refused"] = True
        usage.record_codex(agents.codex_usage(events), meta, chosen.choice)
    failure = claude.Failure.MODEL_REFUSED if refused else None
    return claude.ClaudeReply.failed("", "codex の起動の結果", failure=failure)


def record(data: dict[str, Any], rows: Sequence[dict[str, Any]], seats: Sequence[str], final: str) -> None:
    """収束かラウンドの上限で終わったレビューの効果指標を書く: シートの単位と、費用を負う割り付け（評価した設計か実装の単位）。

    書けなくても警告だけで、レビューの結果は変えない。同じ割り付け ID の 2 回目の指標は書き込み処理が足さないので、ラウンドの
    上限では値の決まらない指標（null）を書かず、再開して裁定した後の値を書けるようにする。シートの割合は、上限で裁定していない
    重大な指摘がある間は決まらない。
    """
    if final not in RECORDED or KEY not in data:
        return
    held = data[KEY]
    count, shares = experiment_values(rows, held.get("first"), seats)
    pending = final == fin.MAX_ROUNDS and undecided_serious(rows)
    for seat in seats:
        if seat in held["seats"] and not pending:
            _report(held["seats"][seat], shares[seat], {"seats": len(seats)}, final)
    charged = charge_to()
    if charged is not None:
        _report(charged, None if count is None else 1 / (1 + count), {"major": count}, final)


def _report(assign_id: str, quality: Optional[float], detail: dict[str, Any], final: str) -> None:
    """効果指標を 1 件書く。ラウンドの上限の null は書かない（`record`）。行が無い単位は null の行と同じく計算に数えない。"""
    if quality is not None or final == fin.CONVERGED:
        claude_choice.report_outcome(assign_id, quality, detail)
