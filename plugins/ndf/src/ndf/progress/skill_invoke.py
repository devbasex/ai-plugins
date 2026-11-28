"""工程の呼び出しのうち Skill を回す起動（`ndf.progress.invoke` から分けた）。

起動の値の選択・準備・起動と基準の組への切り替え・使用量ログの行・回答から工程の結果への写しを持つ。`ndf.progress.invoke` を
import しない（`invoke` がこのモジュールを呼ぶので、型と結果の組み立ては `plan_runs` が持つ）。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import claude, claude_choice, launch_settings, run_log
from ndf.contracts import facts, internal, limits, result
from ndf.progress.plan_runs import StageResult, StageRun, bad_report, finished
from ndf.values import LastOne

# skill の作業の上限（Skill 1 本を回す起動は実装の工程と同じ桁）
SKILL_LIMIT = "monitor.phase.implement"
SKILL_SYSTEM = "工程の作業を 1 つ行い、最後に結果 JSON（統一 JSON レポート）だけを回答する。"

# Skill を回す `claude -p` を打つ関数（argv・質問・上限の秒・作業ディレクトリ・起動の前置きの選び方 → 回答）。テストが差し替える
SkillCaller = Callable[[Sequence[str], str, float, Optional[Path], claude.LaunchOptions], claude.ClaudeReply]


def skill_prompt(run: StageRun) -> str:
    """Skill を回す質問。入力の値は文脈の形を通した JSON だけを渡す。"""
    prompt = f"/{identity.load().prefix}{run.name}\n\n入力（JSON）: {json.dumps(run.inputs, ensure_ascii=False)}"
    return prompt if run.notes is None else f"{prompt}\n\n変更要求の指摘:\n{run.notes}"


def _skill_cause(failure: Optional[claude.Failure]) -> str:
    """`claude -p` が回答を返せなかった理由 → 停止理由。理由の無い失敗は打ち直せば直りうる。"""
    if failure is None:
        return "transient"
    return str(facts.value(claude.FAILURES, failure, "skill_cause"))


class _PreparedSkill(NamedTuple):
    """Skill の起動の準備。起動の引数と期限は 1 回だけ組み、起動し直しでも使う。"""

    picked: claude_choice.ChoiceRun
    grant: launch_settings.Grant
    limit: float
    deadline: float
    base: list[str]
    notice: Optional[str]


def call_skill(run: StageRun, cwd: Path, call_claude: SkillCaller) -> StageResult:
    """Skill を full で 1 回回す。起動の設定の Skill の名前の指定かモデルと effort の値が壊れているか未承認なら、起動せずに
    `precondition` で止める（#1959・#2944）。

    モデルと effort は `NDF_UNIT_ID` を単位に差し込まれた処理で選び（差し込みが無ければ選ばない）、モデルを理由に拒まれたら
    残りの期限で基準の組で 1 回だけ起動し直す。期限は最初の起動の前から単調時計で持ち、切り替えの記録の前と後に残りを測る。
    起動の引数（導入済みのプラグインの確かめを含む）は 1 回だけ組み、起動し直すときは値の引数だけを替える。起動した子が終わるたびに
    使用量ログへ値と終わり方（`outcome`）の 1 行を渡す（工程の名前は Skill の名前。会話を再開しないので番号は会話ごとに 0 から。
    書けなくても結果を変えない。#1959 の決定 6）。回答の検証は行と工程の結果で 1 回を使い回す（`_Checked`）。"""
    checked = _Checked()
    with contextlib.ExitStack() as held:  # 起動の指定（MCP の定義のコピー）は起動し直しが終わるまで持つ
        try:
            picked = claude_choice.ChoiceRun(_skill_choice(run, cwd))
            grant = held.enter_context(launch_settings.prepared(run.name, cwd))
        except internal.InternalError as exc:
            return StageResult(result.stopped(run.kind, exc.kind, f"Skill {run.name} を起動しない: {exc}"), {})
        prepared = _prepare_skill(cwd, picked, grant)
        try:
            reply = _launch_skill(run, cwd, call_claude, prepared, checked)
        except _SkillNotRelaunched as exc:  # 切り替えの前に承認の対象が変わったか、承認が取り消された
            return StageResult(result.stopped(run.kind, exc.cause.kind, f"Skill {run.name} を基準の組で起動し直さない: {exc.cause}"), {})
    return _skill_result(run, reply, checked, prepared.notice)


def _prepare_skill(cwd: Path, picked: claude_choice.ChoiceRun, grant: launch_settings.Grant) -> _PreparedSkill:
    """起動の期限と引数を組む。"""
    limit = limits.seconds(SKILL_LIMIT)
    deadline = claude_choice.deadline_after(limit)
    schema = contracts.load_json(contracts.schema_path("result"))
    base, notice = claude.claude_launch(claude.ClaudeCall(SKILL_SYSTEM, claude.Permission.FULL, json_schema=schema, grant=grant), cwd=cwd)
    return _PreparedSkill(picked, grant, limit, deadline, base, notice)


class _SkillNotRelaunched(Exception):
    """基準の組への切り替えを断られた。起動した呼び出しの例外と取り違えないように、切り替えの例外だけを包む。"""

    def __init__(self, cause: internal.InternalError) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _launch_skill(run: StageRun, cwd: Path, call_claude: SkillCaller, prepared: _PreparedSkill, checked: _Checked) -> claude.ClaudeReply:
    """起動し、モデルを理由に拒まれたら基準の組へ切り替えて起動し直す。最後の回答を返す。"""
    picked, grant = prepared.picked, prepared.grant

    def launch(timeout: float, bound: Optional[Callable[[], float]] = None) -> claude.ClaudeReply:
        # 組んだ引数に選んだ値の引数を足して 1 回起動する。`bound` は起動し直しの期限の残り
        log = claude.LaunchLog(picked.meta(run.name), lambda r: {"outcome": skill_outcome(r, checked)})
        options = claude.LaunchOptions(verify=grant.verifier, log=log, left=bound)
        with run_log.overlay_env(picked.env_changes()):
            got = call_claude([*prepared.base, *picked.cli_args()], skill_prompt(run), timeout, cwd, options)
        picked.seen(got)
        return got

    reply = launch(prepared.limit)
    try:
        left = picked.fall_back_until(reply, prepared.deadline, grant.verifier)
    except internal.InternalError as exc:
        raise _SkillNotRelaunched(exc) from exc
    if left is None:
        return reply
    # 切り替えられたか、期限を使い切った。残りが無ければ、元の拒否ではなく期限切れを理由にする
    if left <= 0:
        return claude.ClaudeReply.failed("", "基準の組で起動し直す期限が残っていない", failure=claude.Failure.TIMEOUT)
    return launch(left, partial(claude_choice.left_until, prepared.deadline))


def _skill_result(run: StageRun, reply: claude.ClaudeReply, checked: _Checked, notice: Optional[str]) -> StageResult:
    """最後の回答から工程の結果を組み、起動の引数の注意を警告の末尾へ足す。"""
    if not reply.ok:
        kind = _skill_cause(reply.failure)
        got = StageResult(result.stopped(run.kind, kind, f"Skill {run.name} を回せない: {reply.error}"), {})
    else:
        report = checked.report(reply)
        got = finished(run, report) if report is not None else bad_report(run, "回答が結果 JSON の形に合わない")
    return got if notice is None else replace(got, warnings=(*got.warnings, notice))


def _skill_choice(run: StageRun, cwd: Path) -> Any:
    """Skill の起動の値。単位は `NDF_UNIT_ID`（無いか空なら割り付けない）、モードは工程の入力の `mode`。"""
    unit = os.environ.get(identity.env_name("unit-id"))
    mode = run.inputs["mode"] if isinstance(run.inputs.get("mode"), str) else None
    return claude_choice.pick_values("skill", run.name, cwd=cwd, unit=None if unit == "" else unit, mode=mode)


class _Checked:
    """Skill の回答の検証（`result.received`）を、回答 1 つにつき 1 回にする。使用量ログの終わり方と工程の結果が同じ検証を使う。"""

    def __init__(self) -> None:
        self._memo: LastOne[Any, Optional[dict[str, Any]]] = LastOne(lambda answer: result.received(answer))

    def report(self, reply: claude.ClaudeReply) -> Optional[dict[str, Any]]:
        """回答の結果 JSON。形に合わなければ None。同じ回答（`answer` の同じオブジェクト）は検証し直さない。"""
        return self._memo.of(reply.answer)


def skill_outcome(reply: claude.ClaudeReply, checked: Optional[_Checked] = None) -> str:
    """Skill の起動 1 回の終わり方: 結果 JSON の status、形に合わなければ `bad_report`、回答を返せなければ `unknown`。"""
    if not reply.ok:
        return "unknown"
    report = (checked if checked is not None else _Checked()).report(reply)
    return str(report["status"]) if report is not None else "bad_report"
