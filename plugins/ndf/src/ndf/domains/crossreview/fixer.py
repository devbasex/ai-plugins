"""修正担当の起動と、その結果の読み込みと送り（`drive.py` から分けた。#4076）。

指摘ごとの「直した」「見送った（理由）」を結果ファイルからジャーナルへ読み込み、修正のコミットを PR のブランチへ送る。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import agents, claude, claude_choice, git
from ndf.contracts import limits
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, experiment, push, resume_decide, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import FindingJournal
from ndf.domains.crossreview.settings import RunConfig
from ndf.domains.records import mvv as mvv_records
from ndf.testing import declared, scoped

# 修正担当に許す Tool
FIXER_TOOLS = "Read,Edit,Write,Bash,Grep,Glob"
# 修正担当の出力の名前の後ろ（`round<n>-fix`）
FIX_SUFFIX = "-fix"
# 会話が見つからず新しい会話で起動し直した修正担当の出力の名前の後ろ（`round<n>-fix-new`。#4013）
FRESH_SUFFIX = FIX_SUFFIX + "-new"
# 修正担当の会話を続けない理由のうち、駆動が決めるもの（#4013）
MISSING = "前の会話が見つからない"
UNSAVED = "会話を保存しない設定"
# 修正担当が会話を続けるときの文脈の窓の手前の余白（#4013）
RESUME_MARGIN = "cross-review.fixer.resume-margin"

# 担当の起動（テストは偽物に差し替える）
Launcher = Callable[[agents.Launch], agents.AgentStatus]


class FixRun(Protocol):
    """修正担当の起動が読み書きする、1 回の起動の値（`drive._Run`）。"""

    cfg: RunConfig
    g: git.Git
    ref: store.StateRef
    journal: FindingJournal
    warnings: list[str]
    snapshot: Optional[mvv_records.Snapshot]
    mvv: Optional[str]
    sent: str
    fixer_last: Optional[resume_decide.Previous]
    fixer_growth: int
    documents: bool

    def round_stem(self, round_no: int, suffix: str) -> Path: ...

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None: ...


def _where(f: dict[str, Any]) -> str:
    """修正担当へ渡す指摘の場所と重要度の括弧（無ければ空）。"""
    place = f"{f['path']}:{f['line']}" if f.get("path") and f.get("line") else f.get("path") or f.get("section")
    parts = [p for p in (criteria_decide.severity_of(f.get("severity")), place) if p]
    return f"（{'・'.join(parts)}）" if parts else ""


def _fix_prompt(run: FixRun, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed], result_path: Path) -> str:
    lines = [
        f"PR #{run.ref.current_pr} の worktree {run.cfg.worktree} で、次を 1 件ずつ直すか、理由を添えて見送る。直したらコミットする（push しない）。"
    ]
    lines += [f"- {f['id']}: {f['text']}" + _where(f) + criteria_decide.fixer_impact(f) for f in open_]
    lines += [f"- {h.id}: 足すべき内容 {h.text}" for h in handed]
    shape = (
        '{"pr": '
        + str(run.ref.origin_pr)
        + ', "commit": "<最後のコミット>", "findings": [{"id": "<上の id>", "outcome": "fixed|deferred", "reason": "<見送った理由>", '
        + '"defer": "<deferred のときだけ needs_user|filed|not_applicable>"}]}'
    )
    lines += criteria_decide.fixer_block(run.cfg.settings.focus, run.snapshot)
    lines += criteria_decide.document_block(criteria_decide.FIXER, run.documents)
    # 修正担当は hook の無い起動なので、テストを CI に寄せる決まりは指示で伝える（#4030）
    if scoped.ci_only(declared.test_section(run.cfg.worktree)):
        lines.append(criteria_decide.fixer_ci_only())
    lines.append(f"終えたら {result_path} へ JSON {shape} を書く。すべての id を載せる。")
    return "\n".join(lines)


class _Held(NamedTuple):
    """修正担当の 1 つの会話で変えないもの: 最初に選んだ値と、使用量ログの行の書き手（`session_seq` を数える。#2944）。"""

    chosen: claude_choice.ChoiceRun
    log: claude.LaunchLog


def _held(run: FixRun, session: Optional[claude.Session]) -> _Held:
    """新しい会話の値を選ぶ。工程が無いので実験せず、起動の引数と環境変数の指定だけが効く。費用は `NDF_CHARGE_TO` の割り付けが負う。"""
    charged = experiment.charge_to()
    picked = claude_choice.pick_values("seat", "fix", cwd=run.cfg.worktree, runtime=run.cfg.fixer, charge_to=charged)
    chosen = claude_choice.ChoiceRun(picked, charge_to=charged)
    meta = experiment.meta_of("fix", chosen)
    return _Held(chosen, claude.LaunchLog(meta if session is None else {**meta, "session_id": session.id}))


def _resumed(held: _Held) -> _Held:
    """続ける会話は値を選び直さず、リクエストの中の番号だけを進める。"""
    held.chosen.request_try += 1
    held.log.meta["request_try"] = held.chosen.request_try
    return held


def _fixer_launch(
    run: FixRun, round_no: int, prompt: str, session: Optional[claude.Session], suffix: str = FIX_SUFFIX, held: Optional[_Held] = None
) -> agents.Launch:
    """修正担当の 1 回の起動。`session` は会話の指定（None なら会話を残さない。#4013）、出力の名前は `round<n><suffix>`、値は会話の
    最初に選んだもの（`held`）。"""
    # 結果はファイルで受けるので標準出力を逐次にし、作業中の修正担当を無進捗と誤って止めない（#3945）
    call = claude.ClaudeCall(
        system="cross-review の修正担当", permission=claude.Permission.TOOLS, tools=FIXER_TOOLS, stream=True, session=session
    )
    paths = agents.AgentPaths.at(run.round_stem(round_no, suffix))
    spec = agents.Launch(
        run.cfg.fixer, (*claude.claude_args(call), prompt), run.cfg.worktree, paths, limits.seconds(participants.REVIEW_TIMEOUT)
    )
    if held is None or held.chosen.choice is None:
        return spec
    return replace(participants.with_choice(spec, held.chosen.choice), env=held.chosen.conversation_env())


def _run_fixer_child(spec: agents.Launch, held: _Held, launch: Launcher) -> agents.AgentStatus:
    """修正担当を起動し、値を選んだ起動なら使用量ログの行を書く（出力は逐次なので、結果の行を回答として読む）。"""
    status = launch(spec)
    if held.chosen.choice is not None:
        experiment.claude_row(held.log, json.dumps(claude.result_line(agents.tail(spec.paths.out))), status)
    return status


def _fix_record(
    journal: FindingJournal, ref: store.StateRef, path: Path, handed: Sequence[fin.Handed], round_no: int, mvv: Optional[str] = None
) -> dict[str, Any]:
    """修正の結果ファイルを読んでジャーナルへ読み込み、ラウンドの修正の記録（commit・reported・fixed）を返す。`mvv` は
    修正担当を起動したときの MVV の識別子（前の起動が残した結果では、どの MVV での結果か決められないので None）。番号が合わなければ `store.FixResultMismatch`。"""
    found = store.read_fix_result(path, ref)
    reported = journal.apply_fix(store.list_of(found, store.FINDINGS), handed, round_no, mvv=mvv)
    fixed = [str(f.get("id")) for f in reported if f["outcome"] == fin.FIXED]
    return {"commit": found.get("commit"), "reported": len(reported), "fixed": fixed}


def take_fix(run: FixRun, path: Path, round_no: int, mvv: Optional[str] = None) -> dict[str, Any]:
    """修正の結果をジャーナルへ読み込む。結果ファイルは呼び手が状態ファイルへ書いてから `consume` で移す。"""
    return _fix_record(run.journal, run.ref, path, run.cfg.handed, round_no, mvv)


def consume(path: Path, round_no: int, label: str = "round") -> None:
    """マージした（またはマージできなかった）結果ファイルを `<label><ラウンド>` の名前へ移す（次の起動が残りと読み違えない）。
    状態ファイルへ書いた後に呼ぶ。"""
    os.replace(path, path.with_name(f"{path.stem}-{label}{round_no}.json"))


def merge_fix(state_dir: Path, pr: int, path: Path, handed: Sequence[fin.Handed] = ()) -> int:
    """駆動の外で残った修正の結果ファイルを、最後のラウンドへ `run` と同じ形でマージする（`merge-fix`。#2770）。
    ジャーナルへ反映して状態ファイルへ書いてから結果ファイルを移し、マージしたラウンドの番号を返す。`handed` は `run` へ
    渡した足すべき内容（`--findings`）で、その id の決定もジャーナルの行にする。
    番号が合わなければ `store.FixResultMismatch`（何も書かない）。"""
    ref = store.state_pr(state_dir, pr)
    data = store.read_state(ref)
    rounds = data.setdefault(store.ROUNDS, [])
    if not rounds:
        rounds.append({"round": 1})
    round_no = rounds[-1].get("round") or len(rounds)
    journal = FindingJournal(store.list_of(data, store.FINDINGS))
    rounds[-1]["fix"] = _fix_record(journal, ref, path, handed, round_no)
    data[store.FINDINGS] = list(journal.rows.values())
    store.write_object(ref.path, data)
    consume(path, round_no)
    return round_no


def run_fixer(
    run: FixRun, round_no: int, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed], launch: Launcher
) -> Optional[dict[str, Any]]:
    """修正担当を起動して結果を読み込む。結果ファイルが無ければ注意に載せて None。
    起動の前に残っている結果ファイルは退避する（前の起動の結果を今のラウンドの結果と読み違えない）。修正担当が
    DONE で終わらなくても（タイムアウトなど）結果ファイルを書き終えていればマージする（直した・見送ったを失わない）。"""
    result_path = store.fix_result_path(run.cfg.state_dir, run.ref)
    if result_path.is_file():
        consume(result_path, round_no, "stale-round")
    prompt = _fix_prompt(run, open_, handed, result_path)
    status, how = _launch_fixer(run, round_no, prompt, result_path, launch)
    if not result_path.is_file():
        warning = f"round {round_no} の修正担当が結果を書かなかった（{status.state}: {status.shown}）"
        run.note(round_no, run.cfg.fixer, f"結果無し（{status.state}）{how}", warning)
        return None
    try:
        fix = take_fix(run, result_path, round_no, run.mvv)
    except (store.FixResultMismatch, store.StateBroken) as exc:
        # 合わない・書きかけ（DONE でない修正担当）のファイルを残すと、次の起動が同じファイルを読み込もうとして
        # 止まり続けるので、退避する
        consume(result_path, round_no, "mismatch-round")
        run.warnings.append(f"round {round_no} の修正の結果を読み込めない: {exc}")
        return None
    if run.fixer_last is not None:
        run.fixer_last = run.fixer_last._replace(taken=True)
    run.note(round_no, run.cfg.fixer, f"指摘 {fix['reported']} 件を扱った{how}")
    return fix


def _how(reason: Optional[str]) -> str:
    """過程の行の後ろに付ける会話の扱い（続けたか、新しい会話の理由）。"""
    return "（会話を続けた）" if reason is None else f"（新しい会話: {reason}）"


def _launch_fixer(run: FixRun, round_no: int, prompt: str, result_path: Path, launch: Launcher) -> tuple[agents.AgentStatus, str]:
    """会話の指定を決めて修正担当を起動し、続けた会話が見つからなければ 1 度だけ新しい会話で起動し直す（#4013）。
    最後に起動した方の記録を `run.fixer_last`・`run.fixer_growth` に置き、状態と過程の行の後ろを返す。"""
    if not run.cfg.settings.fixer_sessions:
        held = _held(run, None)
        return _run_fixer_child(_fixer_launch(run, round_no, prompt, None, held=held), held, launch), _how(UNSAVED)
    choice = resume_decide.choose(run.fixer_last, run.fixer_growth, int(limits.value(RESUME_MARGIN, "count")))
    last = run.fixer_last if choice.resume else None
    session = _new_session() if last is None else claude.Session(last.session_id, True)
    held = _held(run, session) if last is None or last.carry is None else _resumed(last.carry)
    spec = _fixer_launch(run, round_no, prompt, session, held=held)
    status, reason = _run_fixer_child(spec, held, launch), choice.reason
    if (
        session.resume
        and not result_path.is_file()
        and claude.conversation_missing(agents.tail(spec.paths.out), agents.tail(spec.paths.err))
    ):
        session, reason = _new_session(), MISSING
        held = _held(run, session)
        spec = _fixer_launch(run, round_no, prompt, session, FRESH_SUFFIX, held)
        status = _run_fixer_child(spec, held, launch)
    carried = last.context if last is not None and session.resume else None
    _keep_fixer(run, session, status, spec.paths.out, carried)
    if held.chosen.choice is not None:
        run.fixer_last = run.fixer_last._replace(carry=held)
    return status, _how(reason)


def _new_session() -> claude.Session:
    return claude.Session(str(uuid.uuid4()), False)


def _keep_fixer(run: FixRun, session: claude.Session, status: agents.AgentStatus, out: Path, carried: Optional[int]) -> None:
    """最後に起動した修正担当の記録（結果をマージできたかは呼び手が後で真にする）と、1 ラウンドの増分の最大を置く。"""
    result = claude.result_line(agents.tail(out))
    squeezed = claude.compacted(out)
    context, window = claude.context_tokens(result), claude.context_window(result)
    finished = status.state == agents.AgentState.DONE
    run.fixer_last = resume_decide.Previous(session.id, finished, False, squeezed, context, window)
    run.fixer_growth = max(run.fixer_growth, resume_decide.grown(context, carried, squeezed, window))


def send_fix(run: FixRun, round_no: int, reviewed_head: str, fix: dict[str, Any], check_commit: bool = True) -> bool:
    """修正のコミットを送る。コミットが無ければ送らない。送れなければ、このラウンドで直したとした指摘を未解決へ戻し
    （PR に届いていない）、注意に載せて False。"""
    if run.g.rev_parse("HEAD") == reviewed_head:
        return True
    commit = fix.get("commit") if check_commit and isinstance(fix.get("commit"), str) else None
    sent = push.push_fix(run.g, run.cfg.branch, commit)
    if sent.ok:
        fix["pushed"] = sent.head
        run.sent = str(sent.head)
        return True
    run.journal.reopen(store.list_of(fix, "fixed"))
    tail = f"（衝突: {', '.join(sent.conflicts)}）" if sent.conflicts else ""
    run.note(round_no, "push", f"送れない（{sent.reason}）", f"round {round_no} の修正を送れなかった: {sent.reason}{tail}")
    return False
