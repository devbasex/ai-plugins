"""修正担当の起動と、その結果の読み込みと送り（`drive.py` から分けた。#4076）。

指摘ごとの「直した」「見送った（理由）」を結果ファイルからジャーナルへ読み込み、修正のコミットを PR のブランチへ送る。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import agents, claude, git
from ndf.contracts import limits
from ndf.core.converge import participants, worktree
from ndf.domains.crossreview import criteria_decide, push, resume_decide, store
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
# 未送信のコミットがあるので修正を始めない・送らない注意（PR へ載るので、コミットの中身を入れない）
HELD_BACK = "手元の未送信のコミットを修正担当が作ったと確かめられないので、修正を始めず、送りもしない（PR へ送るか外して打ち直す）"
# 修正担当が報告したコミットを HEAD と比べるときの、短縮した SHA の最小の長さ
SHORT_SHA = 7

# 担当の起動（テストは偽物に差し替える）
Launcher = Callable[[agents.Launch], agents.AgentStatus]


class FixRun(Protocol):
    """修正担当の起動が読み書きする、1 回の起動の値（`drive._Run`）。"""

    cfg: RunConfig
    g: git.Git
    ref: store.StateRef
    data: dict[str, Any]
    journal: FindingJournal
    warnings: list[str]
    snapshot: Optional[mvv_records.Snapshot]
    mvv: Optional[str]
    sent: str
    fixer_last: Optional[resume_decide.Previous]
    fixer_growth: int

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
        + ', "commit": "<最後のコミット>", "findings": [{"id": "<上の id>", "outcome": "fixed|deferred", "reason": "<見送った理由>"}]}'
    )
    lines += criteria_decide.fixer_block(run.cfg.settings.focus, run.snapshot)
    # 修正担当は hook の無い起動なので、テストを CI に寄せる決まりは指示で伝える（#4030）
    if scoped.ci_only(declared.test_section(run.cfg.worktree)):
        lines.append(criteria_decide.fixer_ci_only())
    lines.append(f"終えたら {result_path} へ JSON {shape} を書く。すべての id を載せる。")
    return "\n".join(lines)


def _fixer_launch(run: FixRun, round_no: int, prompt: str, session: Optional[claude.Session], suffix: str = FIX_SUFFIX) -> agents.Launch:
    """修正担当の 1 回の起動。`session` は会話の指定（None なら会話を残さない。#4013）、出力の名前は `round<n><suffix>`。"""
    # 結果はファイルで受けるので標準出力を逐次にし、作業中の修正担当を無進捗と誤って止めない（#3945）
    call = claude.ClaudeCall(
        system="cross-review の修正担当", permission=claude.Permission.TOOLS, tools=FIXER_TOOLS, stream=True, session=session
    )
    paths = agents.AgentPaths.at(run.round_stem(round_no, suffix))
    return agents.Launch(
        run.cfg.fixer, (*claude.claude_args(call), prompt), run.cfg.worktree, paths, limits.seconds(participants.REVIEW_TIMEOUT)
    )


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
        warning = f"round {round_no} の修正担当が結果を書かなかった（{status.state}: {status.detail}）"
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
        return launch(_fixer_launch(run, round_no, prompt, None)), _how(UNSAVED)
    choice = resume_decide.choose(run.fixer_last, run.fixer_growth, int(limits.value(RESUME_MARGIN, "count")))
    last = run.fixer_last if choice.resume else None
    session = _new_session() if last is None else claude.Session(last.session_id, True)
    spec = _fixer_launch(run, round_no, prompt, session)
    status, reason = launch(spec), choice.reason
    if (
        session.resume
        and not result_path.is_file()
        and claude.conversation_missing(agents.tail(spec.paths.out), agents.tail(spec.paths.err))
    ):
        session, reason = _new_session(), MISSING
        spec = _fixer_launch(run, round_no, prompt, session, FRESH_SUFFIX)
        status = launch(spec)
    carried = last.context if last is not None and session.resume else None
    _keep_fixer(run, session, status, spec.paths.out, carried)
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


def keep_unsent(run: FixRun, reviewed_head: str, head: str) -> None:
    """修正担当が `reviewed_head` から `head` までに作ったコミットの範囲を状態に置く（保存は呼び手）。`reviewed_head` に、修正担当が
    作ったと確かめられない未送信のコミットがあれば置かない。"""
    if head == reviewed_head:
        return
    held = run.data.pop(store.UNSENT_FIX, None)
    base = reviewed_head if reviewed_head == run.sent else held["base"] if _is_head(held, reviewed_head) else None
    if base is not None:
        _keep_range(run, base, head)


def keep_replayed(run: FixRun, synced: worktree.SyncResult) -> bool:
    """前の起動の修正担当が残したコミットの範囲を、同期で載せ直した後の範囲へ移す（変わったら真。保存は呼び手）。その後に
    別のコミットが足されたか、範囲の起点が PR のブランチから外れたなら捨てる。"""
    held = run.data.pop(store.UNSENT_FIX, None)
    if _is_head(held, synced.before):
        _move(run, held["base"], synced)
    return bool(held != run.data.get(store.UNSENT_FIX))


def keep_reported(run: FixRun, synced: worktree.SyncResult) -> None:
    """駆動が落ちて範囲を残せなかった修正（結果ファイルからマージしたもの）の範囲を、同期で載せ直した後の範囲として置く。
    起点はラウンドの head で、報告されたコミットが同期の前の HEAD と同じときだけ置く（その後に別のコミットが足されていない）。"""
    last = store.last_round(run.data)
    fix, base = last.get("fix"), last.get("head")
    commit = fix.get("commit") if isinstance(fix, dict) else None
    if isinstance(base, str) and isinstance(commit, str) and len(commit) >= SHORT_SHA and synced.before.startswith(commit):
        _move(run, base, synced)


def _move(run: FixRun, base: str, synced: worktree.SyncResult) -> None:
    """`base` からの修正担当のコミットの範囲を、載せ直した後の範囲として置く。`base` が PR のブランチに無ければ、載せ直しに
    修正担当の作っていないコミットが混ざるので置かない。"""
    if synced.replayed and _in_remote(run, base, synced.remote):
        _keep_range(run, synced.remote, synced.after)


def _keep_range(run: FixRun, base: str, head: str) -> None:
    run.data[store.UNSENT_FIX] = {"base": base, "head": head}


def _is_head(held: Any, head: str) -> bool:
    """`held` が、`head` までのコミットの範囲か。"""
    return isinstance(held, dict) and held.get("head") == head and isinstance(held.get("base"), str)


def _in_remote(run: FixRun, base: str, remote: str) -> bool:
    try:
        return base == remote or run.g.is_ancestor(base, remote)
    except git.GitError:  # noqa: NDF401 外部コマンド: 起点のコミットが手元に無ければ、範囲を確かめられない
        return False


def sendable(run: FixRun, head: str) -> bool:
    """`head` までに未送信のコミットが無いか、すべて修正担当の作ったものと確かめられるか。"""
    return head == run.sent or _is_head(run.data.get(store.UNSENT_FIX), head)


def hold_back(run: FixRun, round_no: int) -> None:
    """修正担当が作ったと確かめられない未送信のコミットを、利用者が手元に留めたものとして残し、注意に載せる。"""
    run.note(round_no, "push", "送らない（修正担当のコミットと確かめられない）", HELD_BACK)


def send_fix(run: FixRun, round_no: int, head: str, fix: dict[str, Any], check_commit: bool = True) -> bool:
    """作業場所の `head` までの修正のコミットを送る（`head` が PR に届いていれば送らない）。送る範囲のすべてを修正担当が作ったと
    確かめられなければ送らない（利用者が手元に留めたコミットを公開しない）。確かめた `head` だけを送る。送らない・送れないときは、このラウンドで直したとした指摘を
    未解決へ戻し（PR に届いていない）、注意に載せて False。"""
    if head == run.sent:
        fix["pushed"] = head
        return True
    if not sendable(run, head):
        run.journal.reopen(store.list_of(fix, "fixed"))
        hold_back(run, round_no)
        return False
    commit = fix.get("commit") if check_commit and isinstance(fix.get("commit"), str) else None
    try:
        sent = push.push_fix(run.g, run.cfg.branch, commit, push.Verified(head, run.sent))
    except push.ResendFailed as exc:
        _keep_range(run, exc.moved.base, exc.moved.head)
        raise
    if sent.ok:
        fix["pushed"] = sent.head
        run.sent = str(sent.head)
        run.data.pop(store.UNSENT_FIX, None)
        return True
    if sent.rebased and sent.origin_head is not None and sent.head is not None:
        # 載せ直した後に拒まれた。HEAD が変わったので、次の起動が確かめる範囲も移す
        _keep_range(run, sent.origin_head, sent.head)
    run.journal.reopen(store.list_of(fix, "fixed"))
    tail = f"（衝突: {', '.join(sent.conflicts)}）" if sent.conflicts else ""
    run.note(round_no, "push", f"送れない（{sent.reason}）", f"round {round_no} の修正を送れなかった: {sent.reason}{tail}")
    return False
