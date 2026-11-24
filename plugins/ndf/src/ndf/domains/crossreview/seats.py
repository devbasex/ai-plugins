"""cross-review の席の起動と、結果の無い席を claude で埋める処理（`drive.py` から分けた。#3955）。

席ごとのリクエストで相手役を並べて起動し、指摘と結果の無い席の直し方と理由を返す（`launch_reviewers`）。始める前に学習の設定で
断られた claude 以外の席を claude の読むだけの起動で埋め（`fill_refused`）、埋めた席の注意を作る（`filled_warning`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import agents
from ndf.contracts import facts
from ndf.core.converge import participants
from ndf.domains.crossreview import scope_decide, store
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

    def round_stem(self, round_no: int, suffix: str) -> Path: ...

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None: ...


class _SeatResult(NamedTuple):
    """席 1 回の結果。`findings` は指摘（結果が無ければ None）、`denied` は読み取りを拒否された差分のファイル（claude の席だけ）。"""

    findings: Optional[list[dict[str, Any]]]
    denied: tuple[Path, ...] = ()


def _seat_result(status: agents.AgentStatus, spec: agents.Launch, required: Sequence[Path]) -> _SeatResult:
    """完了した席の出力の指摘。claude は外側の JSON を 1 回だけ読み、`result` の指摘と `permission_denials` を取り出す（PR 4461 の
    r2-4）。渡した差分のファイルの読み取りを拒否されていれば、指摘が空でも結果が無い（r1-1）。完了でない・出力が無い・指摘の配列が
    無ければ結果が無い。"""
    if status.state != agents.AgentState.DONE:
        return _SeatResult(None)
    out = spec.paths.out
    try:
        if spec.agent != participants.FIXER:
            return _SeatResult(participants.findings_of(out.read_text(encoding="utf-8", errors="replace")))
        answer = participants.claude_answer(out)
    except OSError:  # 出力の無い相手役は回答しなかった扱い（呼び手が注意に載せる）
        return _SeatResult(None)
    denied = participants.denied_reads(answer.denials, required, spec.cwd)
    return _SeatResult(None, denied) if denied else _SeatResult(participants.findings_of(answer.text))


def _unread_hint(seat: str, cause: str, err: Path) -> str:
    """読めなかった席 1 つの理由と直し方（precondition の詳細の 1 行）。直し方は席のランタイムで引く。"""
    row = facts.row(participants.RUNTIMES, participants.runtime_of(seat))
    if cause.startswith(DENIED_READ):
        fix = DENIED_FIX
    elif cause.startswith(agents.SANDBOX_FAILED):
        fix = (
            "サンドボックスが名前空間を作れない。ユーザー名前空間を許すか、外で隔離された環境で打つ"
            "（`unshare -Ur true` が通らない環境では NDF がサンドボックスを外して起動する）"
        )
    else:
        fix = f"ログインを `{' '.join([row['executable'], *row['auth_probe']])}` で確かめる"
    return f"- {seat}: {cause}。{fix}（標準エラー: {err}）"


class Unread(NamedTuple):
    """結果の無い席 1 つの直し方（precondition の詳細の行）と理由（埋めた席の `reason`）。"""

    hint: str
    cause: str


def _seat_launch(run: SeatRun, seat: str, prompt: str, stem: Path) -> agents.Launch:
    """席の 1 回の起動。claude の席は読める場所をこの実行の資料のディレクトリに限った読むだけの起動（#3955 の決定 2・3）。"""
    if participants.runtime_of(seat) != participants.FIXER:
        return participants.review_launch(seat, run.cfg.worktree, prompt, stem)
    readable = () if run.input_dir is None else (run.input_dir,)
    return participants.read_only_launch(SEAT_SYSTEM, run.cfg.worktree, prompt, stem, readable=readable)


def launch_reviewers(
    run: SeatRun, round_no: int, prompts: Mapping[str, str], required: Sequence[Path], launch: Launcher, suffix: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Unread]]:
    """席ごとのリクエストで並べて起動し、指摘と、結果の無い席ごとの直し方と理由を返す。結果の無い席は注意と過程に載せる。
    `required` はリクエストで渡した差分のファイル（claude の席がその読み取りを拒否されたら結果の無い席と数える）。
    出力の名前・指摘の担当・注意・過程には席の名前を、起動にはランタイムの名前を使う（#3955 の決定 3）。"""
    seats = list(prompts)
    specs = [_seat_launch(run, s, prompts[s], run.round_stem(round_no, f"-{s}{suffix}")) for s in seats]
    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        statuses = list(pool.map(launch, specs))
    raised: list[dict[str, Any]] = []
    unread: dict[str, Unread] = {}
    again = "（起動し直した）" if suffix else ""
    for seat, spec, status in zip(seats, specs, statuses):
        sandbox = agents.sandbox_failed(spec.paths)
        got = _SeatResult(None) if sandbox is not None else _seat_result(status, spec, required)
        found = got.findings
        if found is None:
            kind = "sandbox" if sandbox is not None else DENIED_READ if got.denied else str(status.state)
            if sandbox is not None:
                cause = sandbox
            elif got.denied:
                # 注意は PR のコメントに載るので、差分のファイルの名前だけを書く。絶対パスは手元の結果 JSON の過程にだけ残す（PR 4461 の r2-1）
                cause = f"{DENIED_READ}: " + " / ".join(p.name for p in got.denied)
                kind += ": " + " / ".join(str(p) for p in got.denied)
            else:
                cause = f"{status.state}: {status.detail}"
            warning = f"round {round_no} の相手役 {seat} が差分を読めなかった{again}（{cause}）"
            run.note(round_no, seat, f"回答無し{again}（{kind}）", warning)
            unread[seat] = Unread(_unread_hint(seat, cause, spec.paths.err), cause)
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
    for i, absent in enumerate(list(run.seats)):
        why = refused.get(participants.runtime_of(absent))
        if why is not None:
            run.seats[i] = participants.free_seat(run.seats)
            seat_filled(run, Filled(run.seats[i], absent, f"unready: {why}"), "起動の前")


def reused_warnings(run: SeatRun) -> None:
    """前回の結果を返すときに、最後のラウンドの埋めた席の注意を作り直す（#3955）。"""
    last = store.last_round(run.data)
    for f in scope_decide.filled_of(last):
        run.warnings.append(filled_warning(f, f"前回の round {last.get('round')}"))
