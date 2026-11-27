"""worker の起動のモデルと effort（`core/worker.py` の部品。#2944）。

- 値は会話の最初に、差し込まれた値を選ぶ処理（`claude_choice.pick_values`）で選ぶ。差し込みが無ければ値を選ばずに起動する
- 単位は `--unit` か `NDF_UNIT_ID`、引き上げの理由は `NDF_RETRY_CAUSE`、費用を負う割り付け ID は手戻りの作業（`CHARGED_KINDS`）
  だけが `NDF_CHARGE_TO` から受ける。空の値は無いとみなす
- 子の環境の `CLAUDE_CODE_EFFORT_LEVEL` は会話の最初の起動の扱いに固定する（引き継いだなら最初の値、ほかは外す）。中断の間に
  利用者が変数を変えても、再開は最初の起動と同じ値で動く（設計書の「同じ会話を続ける起動」）
- 子ごとの終わり方（`outcome`）は結果ファイルの status、無ければ `no_result`、起動の失敗と読めない結果ファイルは `unknown`
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf import identity
from ndf.adapters import claude, claude_choice
from ndf.contracts import worker_paths
from ndf.contracts.exit_codes import STATUSES

# 費用を手戻りとして負う割り付け ID（`NDF_CHARGE_TO`）を受ける作業種別。ほかは子の環境から外す
CHARGED_KINDS = frozenset({"fix", "ci-fix"})


def _nonempty(name: str, environ: Mapping[str, str]) -> Optional[str]:
    """環境変数の値。無いか空なら None。"""
    value = environ.get(name)
    return None if value is None or value == "" else value


def choice_inputs(
    kind: str, unit: Optional[str], mode: Optional[str], environ: Optional[Mapping[str, str]] = None
) -> dict[str, Optional[str]]:
    """値を選ぶ手がかり（`Handoff` の `unit`・`mode`・`retry`・`charge_to`）。引数の単位が環境変数より先に効く。"""
    env = os.environ if environ is None else environ
    charge = _nonempty(identity.env_name("charge-to"), env) if kind in CHARGED_KINDS else None
    held, retry = _nonempty(identity.env_name("unit-id"), env), _nonempty(identity.env_name("retry-cause"), env)
    return {"unit": held if unit is None or unit == "" else unit, "mode": mode, "retry": retry, "charge_to": charge}


class Request(Protocol):
    """値を選ぶ手がかりを持つ worker の作業（`worker.Handoff`）。"""

    kind: str
    cwd: Path
    unit: Optional[str]
    mode: Optional[str]
    retry: Optional[str]
    charge_to: Optional[str]


def choose_values(h: Request, key_of: Optional[Callable[[Path], str]] = None) -> claude_choice.ChoiceRun:
    """会話の最初に値を選ぶ。値と設定の誤りは選ぶ処理の `InternalError`（precondition）をそのまま送る（起動しない）。"""
    request = {"unit": h.unit, "mode": h.mode, "retry": h.retry, "charge_to": h.charge_to}
    return claude_choice.ChoiceRun(claude_choice.pick_values("worker", h.kind, cwd=h.cwd, key_of=key_of, **request), charge_to=h.charge_to)


def conversation_env(kind: str, wd: worker_paths.WorkDir, run: claude_choice.ChoiceRun) -> dict[str, Optional[str]]:
    """会話の起動の間だけ子へ重ねる環境（`run_log.overlay_env`。None の名前は外す）。

    作業種別（`identity.WORKER_KIND_ENV`）は hook の guard が worker の中かを見分ける（#3112）。開いた結果の保存先の識別子とパス
    （`identity.WORKER_RESULT_DIR_ENV`）は、`ndf contracts emit --out` が同名の別の実ディレクトリへの差し替えを子の側で拒むために渡す。
    effort の変数は値を選んだ起動だけ最初の扱いに固定し、`NDF_CHARGE_TO` は `run.charge_to` が無ければ外す。
    """
    dev, ino = worker_paths.dir_id(wd)
    env: dict[str, Optional[str]] = {
        identity.WORKER_KIND_ENV: kind,
        identity.WORKER_RESULT_DIR_ENV: f"{dev}:{ino}:{wd.path}",
        identity.env_name("charge-to"): run.charge_to,
    }
    if run.choice is not None:
        env.update({identity.env_name("claude-effort-level"): None, **run.env_changes()})
    return env


def first_launch(
    kind: str, session: str, base: list[str], wd: worker_paths.WorkDir, run: claude_choice.ChoiceRun, report: Callable[[], Any]
) -> tuple[list[str], dict[str, Optional[str]], claude.LaunchLog]:
    """会話の最初の起動の引数・子の環境・行の書き手。再開は同じ環境と書き手を使い、引数は会話の指定だけを替える。"""
    return base + run.cli_args() + ["--session-id", session], conversation_env(kind, wd, run), launch_log(kind, session, run, report)


def launch_log(kind: str, session: str, run: claude_choice.ChoiceRun, report: Callable[[], Any]) -> claude.LaunchLog:
    """会話 1 つの行の書き手。`report` は結果ファイルを読んで解く（無ければ None、読めなければ `EntryError`）。"""
    meta = {**claude.launch_meta(kind), **run.meta(), "session_id": session}
    return claude.LaunchLog(meta, lambda reply: {"outcome": end_of(reply, report)})


def end_of(reply: claude.ClaudeReply, report: Callable[[], Any]) -> str:
    """子 1 つの終わり方。"""
    if reply.failure is not None:
        return "unknown"
    try:
        got = report()
    except worker_paths.EntryError:  # noqa: NDF401 ファイル: 読めない結果ファイルは委譲の側が bad_report で止める。行は終わり方を不明にする
        return "unknown"
    status = got["status"] if isinstance(got, dict) and "status" in got else None
    return status if status in STATUSES else "no_result"


def next_try(run: claude_choice.ChoiceRun, log: claude.LaunchLog) -> None:
    """同じ会話を再開する前に、リクエストの中の番号（`request_try`）を 1 つ進め、行の書き手の欄にも反映する。"""
    run.request_try += 1
    log.meta["request_try"] = run.request_try


def launch_metrics(last: Mapping[str, Any], run: claude_choice.ChoiceRun, reply: claude.ClaudeReply) -> dict[str, Any]:
    """結果の `metrics.launch`: 今の値と出所・割り付け ID と、分かれば最後に実際のモデルを返した子のモデル。"""
    if run.choice is None:
        return {}
    found = claude.main_model(reply.model_usage) if reply.model_usage is not None else None
    model = found[0] if found is not None else last["model"] if "model" in last else None
    return {**run.choice.row_fields(), **({"model": model} if model is not None else {})}
