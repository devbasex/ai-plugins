"""cross-review の席の起動と、結果の無い席を claude で埋める処理（`drive.py` から分けた。#3955）。

席ごとのリクエストで相手役を並べて起動し、指摘と結果の無い席の直し方と理由を返す（`launch_reviewers`）。始める前に学習の設定で
断られた claude 以外の席を claude の読むだけの起動で埋め（`fill_refused`）、埋めた席の注意を作る（`filled_warning`）。

席の起動は、席の単位で選んだモデルと effort を足し、起動ごとに使用量ログの行を書く（`experiment`。#2944）。選んだ組がモデルを
理由に拒まれたら、基準の組で 1 回だけ起動し直す。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf import identity
from ndf.adapters import agents, claude, claude_choice
from ndf.contracts import facts
from ndf.core.converge import participants
from ndf.domains.crossreview import experiment, scope_decide, store
from ndf.domains.crossreview.fixer import Launcher
from ndf.domains.crossreview.scope_decide import Filled
from ndf.domains.crossreview.settings import DriveStopped, RunConfig

# 席の claude の system の文
SEAT_SYSTEM = "cross-review の相手役（読むだけ）"
# claude の席が渡した差分のファイルの読み取りを拒否された理由の頭（結果の無い席の理由と過程に載せる）
DENIED_READ = "permission_denials"
# その席の直し方（ログインでなく、読み取りの権限を確かめさせる。PR 4461 の r3-2）
DENIED_FIX = "claude が理由に示した差分のファイルを読める設定か（`--restricted`・`--add-dir` の読み取りの範囲と、利用者の permissions の deny）を確かめる"


class SeatRun(Protocol):
    """席の起動と埋めが読み書きする、1 回の起動の値（`drive._Run`）。"""

    cfg: RunConfig
    data: dict[str, Any]
    warnings: list[str]
    seats: list[str]
    filled: list[Filled]
    input_dir: Optional[Path]
    repo_key: Optional[str]
    seat_assigns: dict[str, str]

    def round_stem(self, round_no: int, suffix: str) -> Path: ...

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None: ...


class _SeatResult(NamedTuple):
    """席 1 回の結果。`findings` は指摘（結果が無ければ None）、`denied` は読み取りを拒否された差分のファイル（claude の席だけ）。"""

    findings: Optional[list[dict[str, Any]]]
    denied: tuple[Path, ...] = ()


def _seat_result(
    status: agents.AgentStatus, spec: agents.Launch, required: Sequence[Path], reply: Optional[claude.ClaudeReply] = None
) -> _SeatResult:
    """完了した席の出力の指摘。claude は外側の JSON を 1 回だけ読み、`result` の指摘と `permission_denials` を取り出す（PR 4461 の
    r2-4）。渡した差分のファイルの読み取りを拒否されていれば、指摘が空でも結果が無い（r1-1）。完了でない・出力が無い・指摘の配列が
    無ければ結果が無い。使用量の行を書くために解析済みの claude の回答（`reply`）があれば、出力を読み直さない。"""
    if status.state != agents.AgentState.DONE:
        return _SeatResult(None)
    out = spec.paths.out
    try:
        if spec.agent != participants.FIXER:
            return _SeatResult(participants.findings_of(participants.answer_text(spec.agent, out)))
        answer = participants.claude_answer(out) if reply is None else participants.ClaudeAnswer(reply.raw, reply.denials)
    except OSError:  # 出力の無い相手役は回答しなかった扱い（呼び手が注意に載せる）
        return _SeatResult(None)
    denied = participants.denied_reads(answer.denials, required, spec.cwd)
    return _SeatResult(None, denied) if denied else _SeatResult(participants.findings_of(answer.text))


def _unread_hint(seat: str, cause: str, err: Path, local_cause: Optional[str] = None) -> tuple[str, str]:
    """読めなかった席 1 つの理由と直し方の 1 行を、PR のコメント用と手元用の組で返す。直し方は席のランタイムで引き、
    文は 1 度だけ組み立てる。標準エラーのログ `err` は、PR のコメントに載る注意にはファイル名、手元の precondition の
    詳細には絶対パスで示す（PR 4461 の r3-1）。`local_cause` は手元用の理由（実行ファイルの絶対パスを含む CLI の
    バージョン検査の文面など。無ければ `cause`。PR 4461 の r1-1）。"""
    runtime = participants.runtime_of(seat)
    if cause.startswith(DENIED_READ):
        fix = DENIED_FIX
    elif cause.startswith(agents.SANDBOX_FAILED):
        fix = (
            "サンドボックスが名前空間を作れない。ユーザー名前空間を許すか、このマシンが外側で隔離されていることを確かめてから、"
            f"起動の設定の permissions.unsandboxed に {runtime} を書いて /{identity.load().prefix}launch-config で承認する"
            "（承認があるときだけ、サンドボックスを使えない環境で安全装置を外して起動する）"
        )
    else:
        probe = facts.value(participants.RUNTIMES, runtime, "auth_probe")
        fix = f"ログインを `{' '.join([agents.executable(runtime), *probe])}` で確かめる"
    local = cause if local_cause is None else local_cause
    return f"- {seat}: {cause}。{fix}（標準エラー: {err.name}）", f"- {seat}: {local}。{fix}（標準エラー: {err}）"


class Unread(NamedTuple):
    """結果の無い席 1 つの直し方と理由（埋めた席の `reason`）。`hint` は PR のコメントに載る注意の行で、標準エラーのログを
    ファイル名だけで示す。`local` は手元の precondition の詳細の行で、ログを絶対パスで示す（PR 4461 の r3-1）。"""

    hint: str
    cause: str
    local: str


def _seat_launch(run: SeatRun, seat: str, prompt: str, stem: Path, choice: Optional[Any]) -> agents.Launch:
    """席の 1 回の起動。claude の席は読める場所をこの実行の資料のディレクトリに限った読むだけの起動（#3955 の決定 2・3）。
    `choice` の値を足し、子の環境から費用を負う先（`NDF_CHARGE_TO`）を外す（レビューは手戻りでない。#2944）。"""
    readable = () if run.input_dir is None else (run.input_dir,)
    spec = participants.reading_launch(seat, participants.ClaudeReader(SEAT_SYSTEM, readable), run.cfg.worktree, prompt, stem)
    spec = participants.with_choice(spec, choice)
    return agents.adding_env(spec, {experiment.charge_env(): None})


def _run_seat(
    spec_of: Callable[[Optional[Any]], agents.Launch], chosen: claude_choice.ChoiceRun, launch: Launcher
) -> tuple[agents.Launch, agents.AgentStatus, Optional[claude.ClaudeReply]]:
    """席を起動して行を書き、最後の起動・状態・解析した回答を返す。

    モデルを理由に拒まれたら、監視の上限の残りの時間で基準の組の起動へ 1 回だけ替える。
    """
    spec = spec_of(chosen.choice)
    deadline = claude_choice.deadline_after(spec.timeout)
    status = launch(spec)
    reply = experiment.seat_row(spec, status, chosen)
    left = None if reply is None else chosen.fall_back_until(reply, deadline)
    if left is not None and left > 0:
        spec = replace(spec_of(chosen.choice), timeout=left)
        status = launch(spec)
        reply = experiment.seat_row(spec, status, chosen)
    return spec, status, reply


class _Cause(NamedTuple):
    """結果の無い席の過程に載せる種類（`kind`）、注意に載せる理由（`shown`）、手元の precondition の詳細の理由（`local`）。"""

    kind: str
    shown: str
    local: Optional[str] = None


def _unread_cause(sandbox: Optional[str], got: _SeatResult, status: agents.AgentStatus) -> _Cause:
    """結果の無い席の理由。サンドボックスの失敗・差分のファイルの読み取りの拒否・完了でない状態の順に決める。"""
    if sandbox is not None:
        return _Cause("sandbox", sandbox)
    if got.denied:
        # 注意は PR のコメントに載るので、差分のファイルの名前だけを書く。絶対パスは手元の結果 JSON の過程にだけ残す（PR 4461 の r2-1）
        cause = f"{DENIED_READ}: " + " / ".join(p.name for p in got.denied)
        return _Cause(f"{DENIED_READ}: " + " / ".join(str(p) for p in got.denied), cause)
    # CLI のバージョン検査で拒否された席は、注意へ CLI 名・バージョン・下限だけを載せ、実行ファイルの絶対パスは手元の詳細に残す（PR 4461 の r1-1）
    return _Cause(str(status.state), f"{status.state}: {status.shown}", f"{status.state}: {status.detail}")


def launch_reviewers(
    run: SeatRun, round_no: int, prompts: Mapping[str, str], required: Sequence[Path], launch: Launcher, suffix: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Unread]]:
    """席ごとのリクエストで並べて起動し、指摘と、結果の無い席ごとの直し方と理由を返す。結果の無い席は注意と過程に載せる。
    `required` はリクエストで渡した差分のファイル（claude の席がその読み取りを拒否されたら結果の無い席と数える）。
    出力の名前・指摘の担当・注意・過程には席の名前を、起動にはランタイムの名前を使う（#3955 の決定 3）。"""
    seats = list(prompts)
    # 値の誤り（precondition）でどの席も起動しないよう、起動の前にすべての席の値を選ぶ
    chosen = [experiment.choose_seat(run, s, run.cfg.worktree) for s in seats]
    builders = [partial(_seat_launch, run, s, prompts[s], run.round_stem(round_no, f"-{s}{suffix}")) for s in seats]
    with ThreadPoolExecutor(max_workers=max(1, len(seats))) as pool:
        ran = list(pool.map(_run_seat, builders, chosen, [launch] * len(seats)))
    for seat, got_values in zip(seats, chosen):
        experiment.keep_seat(run, seat, got_values)
    raised: list[dict[str, Any]] = []
    unread: dict[str, Unread] = {}
    again = "（起動し直した）" if suffix else ""
    for seat, (spec, status, reply) in zip(seats, ran):
        sandbox = agents.sandbox_failed(spec.paths)
        got = _SeatResult(None) if sandbox is not None else _seat_result(status, spec, required, reply)
        found = got.findings
        if found is None:
            why = _unread_cause(sandbox, got, status)
            warning = f"round {round_no} の相手役 {seat} が差分を読めなかった{again}（{why.shown}）"
            run.note(round_no, seat, f"回答無し{again}（{why.kind}）", warning)
            hint, local = _unread_hint(seat, why.shown, spec.paths.err, why.local)
            unread[seat] = Unread(hint, why.shown, local)
            continue
        run.note(round_no, seat, f"指摘 {len(found)} 件{again}")
        raised += [{**f, "by": seat} for f in found]
    return raised, unread


def filled_warning(f: Filled, when: str) -> str:
    """席を埋めた注意の文。`when` は `round <n>`・`起動の前`・`前回の round <n>`。"""
    who = f"相手役 {f.absent} の席" if f.absent is not None else f"席 {f.seat}"
    return f"{when}に{who}を claude の読むだけの起動（{f.seat}）で埋めた（理由: {f.reason}）"


def seat_filled(run: SeatRun, f: Filled, when: str) -> None:
    run.filled.append(f)
    run.warnings.append(filled_warning(f, when))


def fill_seat(run: SeatRun, absent: str, reason: str, when: str) -> str:
    """席 `absent` を空いている claude の席で置き換え、埋めた席と注意を記録して、新しい席の名前を返す。"""
    seat = participants.free_seat(run.seats)
    run.seats[run.seats.index(absent)] = seat
    seat_filled(run, Filled(seat, absent, reason), when)
    return seat


def fill_refused(run: SeatRun) -> None:
    """始める前の確かめ。学習の設定で断られた claude 以外の席を claude で埋める。修正担当か、1 者だけ名指した相手役が断られたら、
    どの担当も起動せずに precondition で止める（#3907・#3955）。"""
    cfg = run.cfg
    refused = participants.refused_runtimes([*(participants.runtime_of(s) for s in run.seats), cfg.fixer])
    if not refused:
        return
    if cfg.fixer in refused or not cfg.may_fill:
        # レビューを流す前に止める。断られた担当を相手役が読めなかったと数えない（#3907）
        raise DriveStopped("precondition", "担当を起動できない: " + " / ".join(refused.values()))
    for absent in list(run.seats):
        why = refused.get(participants.runtime_of(absent))
        if why is not None:
            fill_seat(run, absent, f"unready: {why}", "起動の前")


def reused_warnings(run: SeatRun) -> None:
    """前回の結果を返すときに、最後のラウンドの埋めた席の注意を作り直す（#3955）。"""
    last = store.last_round(run.data)
    for f in scope_decide.filled_of(last):
        run.warnings.append(filled_warning(f, f"前回の round {last.get('round')}"))
