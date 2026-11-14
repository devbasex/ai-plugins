"""`ndf loop` の副命令（開発ループ）。

    ndf loop start <名前> --session <S>                       # 持ち主の鍵を取る（持っていれば延ばす）
    ndf loop select <名前> --session <S> --candidates <JSON> [--first <課題>...] [--size <N>]   # 選んで周を始める
    ndf loop sprint <名前> --session <S> --mode <M> --basis <根拠>   # 周のモードを根拠つきで決める（#2923）
    ndf loop exclude <名前> --session <S> --issue <N> --reason <理由>   # 周の途中で課題を外す（#2788）
    ndf loop created <名前> <課題>...                         # 周の中で起票した課題を記録する（#2132）
    ndf loop gate <名前> --pr <N>                             # 設計承認ゲートを出した時点の先端を記録する
    ndf loop answer <名前> --pr <N> --answer <答え> [--note <文>]   # ゲートの答えを記録する（#2400・#2714）
    ndf loop released <名前> --version <版>                   # 周で出した版を記録する
    ndf loop lap <名前> --session <S> [--failed] [--failure-key <鍵>] [--version <版>...] [--returned] [--unreadable <文>]
    ndf loop stop <名前> --by human|organizer [--reason <文>] [--now]   # 止める要求（#2820）・その場で止める（#2435）
    ndf loop get <名前> [--field <名前>]                      # 読むだけ。次の 1 手を next に返す
    ndf loop close-plan --pace <P> --route <段>... [--mvv] [--record-pr <N>] [--retrospective-due]
    ndf loop review-args --purpose design|code --pr <N> --state-dir <D> --base <B>

書き込む副命令は、記録の排他の下で記録を読み、判断（`control_decide`）を受けて事象を追記する。断りと止まりは
止まった理由の種類と、打てる次の 1 手（`next`）を返す（SF11）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

from filelock import Timeout

from ndf import cli, contracts, identity
from ndf.adapters import git, github
from ndf.adapters.git import GitError
from ndf.contracts import limits, result
from ndf.domains.loop import control_decide as ctl
from ndf.domains.loop import lap_decide, select_decide, sprint_decide, store
from ndf.domains.loop import record_decide as rec
from ndf.domains.release.approval_decide import APPROVED, REJECTED
from ndf.progress import definition, run

TOOL = cli.tool_of(__name__)
# 持ち主の鍵の期限の契約の名前
LEASE_POLICY = "loop.lease"
# 設計承認ゲートで受ける答え（事実の表 answers の行）
GATE_ANSWERS = (APPROVED, run.REVISE, REJECTED)


def _command(step: Optional[ctl.Step], session: Optional[str]) -> Optional[dict[str, Any]]:
    if step is None:
        return None
    args = list(step.args)
    if step.session and session is not None:
        args += ["--session", session]
    return result.command(*args, ask=step.ask)


def _outcome(d: ctl.Decision, session: Optional[str]) -> dict[str, Any]:
    nxt = _command(d.next, session)
    if d.refused:
        assert d.kind is not None
        return result.stopped(TOOL, d.kind, d.summary, next=nxt, metrics=dict(d.metrics))
    return result.result(TOOL, result.OK, d.summary, list(d.items) or None, dict(d.metrics), next=nxt)


def _lap_dict(lap: rec.Lap) -> dict[str, Any]:
    return {
        "lap": lap.number, "issues": list(lap.issues), "remaining": list(lap.remaining),
        "excluded": [{"issue": n, "reason": r} for n, r in lap.excluded], "created": list(lap.created),
        "mode": lap.mode, "basis": lap.basis, "released": list(lap.released), "outcome": lap.outcome, "reason": lap.reason,
    }  # fmt: skip


def view(state: rec.LoopState) -> dict[str, Any]:
    """記録の読み取りの値（get が返す）。"""
    stop = state.stopped if state.stopped is not None else state.requested
    return {
        "status": state.status,
        "stop": {"by": stop.by, "reason": stop.reason} if stop is not None else None,
        "holder": state.holder,
        "laps": [_lap_dict(lap) for lap in state.laps],
        "current": _lap_dict(state.current) if state.current is not None else None,
        "rejections": state.rejections(),
        "created": sorted({n for lap in state.laps for n in lap.created}),
    }


def _hub(cwd: Path) -> github.GitHub:
    return github.GitHub(git.Git(cwd).remote_repo(), github.client())


def head_of(cwd: Path, pr: int) -> str:
    """PR の今の先端（GitHub から 1 回だけ読む）。テストが差し替える。"""
    return _hub(cwd).pull(pr).head_sha


def _candidates(path: Path) -> list[select_decide.Row]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise select_decide.CandidateError(f"候補のファイル {path} を読めない: {exc}") from None
    return select_decide.rows_of(raw)


def _select(state: rec.LoopState, c: ctl.Ctx, a: argparse.Namespace) -> ctl.Decision:
    rows = _candidates(a.candidates)
    chosen = select_decide.select(rows, select_decide.facts_of(state, a.first, a.size))
    return replace(ctl.open_lap(state, c, chosen.chosen), items=tuple(chosen.items()))


def _lap(state: rec.LoopState, c: ctl.Ctx, a: argparse.Namespace) -> ctl.Decision:
    facts = lap_decide.LapFacts(a.failed, a.failure_key, tuple(a.version), a.returned, unreadable=a.unreadable)
    return ctl.close_lap(state, c, facts, limits.seconds(LEASE_POLICY))


def _answer(state: rec.LoopState, c: ctl.Ctx, a: argparse.Namespace) -> ctl.Decision:
    # 先端は 1 回だけ読み、比べる値と記録する値に同じものを使う（#2714）
    return ctl.answer(state, c, a.pr, a.answer, head_of(a.cwd, a.pr), a.note)


# 書き込む副命令の判断の呼び出し（記録を畳んだ状態・文脈・引数 → 判断。選定は選んだ行を判断の items に持つ）
Write = Callable[[rec.LoopState, ctl.Ctx, argparse.Namespace], ctl.Decision]


def _write(a: argparse.Namespace) -> dict[str, Any]:
    loop = store.open_store(a.name, a.cwd)
    session = a.session if "session" in a else None
    with loop.locked():
        state = rec.fold(loop.read())
        decision = a.write(state, ctl.Ctx(a.name, session, time.time()), a)
        if not decision.refused:
            loop.append(decision.events)
    return _outcome(decision, session)


def _get(a: argparse.Namespace) -> dict[str, Any]:
    state = rec.fold(store.open_store(a.name, a.cwd).read())
    values = view(state)
    shown = {a.field: values[a.field]} if a.field is not None else values
    summary = f"ループ {a.name}: {state.status}（周 {len(state.laps)} 回）"
    return result.result(TOOL, result.OK, summary, [shown], {"status": state.status}, next=_command(ctl.next_step(state, a.name), None))


def _close_plan(a: argparse.Namespace) -> dict[str, Any]:
    paces = contracts.load_json(definition.CONTEXT)["properties"]["pace"]["enum"]
    facts = sprint_decide.CloseFacts(a.pace, tuple(a.route), a.mvv, a.record_pr, a.retrospective_due)
    plan = sprint_decide.close_plan(facts, paces)
    metrics = {"record_pr": plan.record_pr, "verify": plan.verify, "pace": plan.pace}
    return result.result(TOOL, result.OK, f"閉じの手順 {len(plan.steps)} 件", [{"steps": list(plan.steps)}], metrics)


def review_args(purpose: str, pr: int, state_dir: str, base: str) -> list[str]:
    """設計のプラン（design）と検査のプラン（code）の差し込み `{{review}}` の値。"""
    entry = identity.FIXED_WORDS["entrypoint"]
    return [entry, "cross-review", "run", "--pr", str(pr), "--state-dir", state_dir, "--base", base, "--purpose", purpose]


def _review_args(a: argparse.Namespace) -> dict[str, Any]:
    args = review_args(a.purpose, a.pr, a.state_dir, a.base)
    return result.result(TOOL, result.OK, f"{a.purpose} のレビューの起動の引数", [{"review": args}])


def _named(sub: Any, cmd: str, help_text: str, write: Optional[Write] = None, session: bool = False) -> argparse.ArgumentParser:
    p = sub.add_parser(cmd, help=help_text)
    p.add_argument("name")
    p.set_defaults(write=write, handler=_get if write is None else _write)
    if session:
        p.add_argument("--session", required=True, help="打ったセッションの識別子")
    return p


def _add_writes(sub: Any) -> None:
    _named(sub, "start", "持ち主の鍵を取る", lambda s, c, a: ctl.start(s, c, limits.seconds(LEASE_POLICY)), True)
    s = _named(sub, "select", "候補から課題を選んで周を始める", _select, True)
    s.add_argument("--candidates", type=Path, required=True, help="候補の行の JSON の配列のファイル")
    s.add_argument("--first", type=int, nargs="*", default=[], help="先頭に選ぶ課題（直して出し直すと答えた課題）")
    s.add_argument("--size", type=int, default=1, help="周に入れる課題の数")
    m = _named(
        sub, "sprint", "周のモードを根拠つきで決める", lambda s, c, a: ctl.decide_mode(s, c, a.mode, a.basis, definition.modes()), True
    )
    m.add_argument("--mode", required=True)
    m.add_argument("--basis", required=True, help="モードの判定の根拠")
    x = _named(sub, "exclude", "周の途中で課題を外す", lambda s, c, a: ctl.exclude(s, c, a.issue, a.reason), True)
    x.add_argument("--issue", type=int, required=True)
    x.add_argument("--reason", required=True)
    made = _named(sub, "created", "周の中で起票した課題を記録する", lambda s, c, a: ctl.created(s, c, a.issues))
    made.add_argument("issues", type=int, nargs="+")
    g = _named(sub, "gate", "設計承認ゲートを出した時点の先端を記録する", lambda s, c, a: ctl.gate(s, c, a.pr, head_of(a.cwd, a.pr)))
    g.add_argument("--pr", type=int, required=True)
    w = _named(sub, "answer", "設計承認ゲートの答えを記録する", _answer)
    w.add_argument("--pr", type=int, required=True)
    w.add_argument("--answer", required=True, choices=GATE_ANSWERS)
    w.add_argument("--note", default="", help="差し戻しの理由など")
    v = _named(sub, "released", "周で出した版を記録する", lambda s, c, a: ctl.released(s, c, a.version))
    v.add_argument("--version", required=True)
    lp = _named(sub, "lap", "周を締める", _lap, True)
    lp.add_argument("--failed", action="store_true", help="手順の失敗の印")
    lp.add_argument("--failure-key", default=None)
    lp.add_argument("--version", action="append", default=[], help="この周で出した版（記録に無いもの）")
    lp.add_argument("--returned", action="store_true", help="リリースを承認されずに人へ戻した")
    lp.add_argument("--unreadable", default=None, help="版の記録を読めなかった理由")
    st = _named(sub, "stop", "止める要求を置く", lambda s, c, a: ctl.stop(s, c, a.by, a.reason, a.now))
    st.add_argument("--by", required=True, choices=ctl.STOP_BY)
    st.add_argument("--reason", default="")
    st.add_argument("--now", action="store_true", help="周の境を待たずにその場で止める")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{identity.FIXED_WORDS['entrypoint']} {TOOL}")
    p.add_argument("--cwd", type=Path, default=None, help="対象のリポジトリの中の位置（既定はカレントディレクトリ）")
    sub = p.add_subparsers(dest="command", required=True)
    _add_writes(sub)
    _named(sub, "get", "記録を読む").add_argument("--field", choices=tuple(view(rec.LoopState())), default=None)
    c = sub.add_parser("close-plan", help="スプリントの閉じの手順と要否を決める")
    c.set_defaults(handler=_close_plan)
    c.add_argument("--pace", required=True)
    c.add_argument("--route", nargs="+", required=True, choices=sprint_decide.ROUTE_STAGES)
    c.add_argument("--mvv", action="store_true", help="MVV の判定がある")
    c.add_argument("--record-pr", type=int, default=None, help="リリース記録の PR の番号（0 は記録なし）")
    c.add_argument("--retrospective-due", action="store_true")
    r = sub.add_parser("review-args", help="レビューの起動の引数を組む")
    r.set_defaults(handler=_review_args)
    r.add_argument("--purpose", required=True, choices=("design", "code"), help="cross-review run の --purpose")
    r.add_argument("--pr", type=int, required=True)
    r.add_argument("--state-dir", required=True)
    r.add_argument("--base", required=True)
    return p


def _run(a: argparse.Namespace) -> dict[str, Any]:
    a.cwd = Path(os.path.realpath(a.cwd if a.cwd is not None else Path.cwd()))
    try:
        return a.handler(a)
    except (select_decide.CandidateError, sprint_decide.SprintError) as exc:
        return result.stopped(TOOL, "invalid", str(exc))
    except store.StoreError as exc:
        return result.stopped(TOOL, "invalid", str(exc))
    except Timeout as exc:
        return result.stopped(TOOL, "store_in_use", f"開発ループの記録の排他を取れない: {exc}")
    except GitError as exc:
        return result.stopped(TOOL, "precondition", f"git のリポジトリの中で打つ: {exc}")
    except github.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        return result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)


def main(argv: list[str]) -> int:
    return result.emit(_run(_parser().parse_args(argv)))
