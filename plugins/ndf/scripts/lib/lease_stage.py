"""lease_stage.py: 実行の核の持ち主の段（#2342）。入口の本体が入力を解いた後に `hold` を 1 回呼ぶ。

    # 入口（契約の行に `lease` を宣言したもの）の本体
    lease_stage.hold("skills/cross-review/scripts/drive.py", pr=args.pr)          # 副命令の無い入口
    lease_stage.hold("scripts/sprint-state.py", "loop start", name=loop_name)    # 副命令ごとの宣言
    lease_stage.touch_held()                                                    # 長く動く本体が動いた時刻を書く

- 契約の `lease` に副命令の宣言が無ければ何もしない（受け入れ条件 10）
- 生きた持ち主がいれば stopped・3、鍵を取る副命令でランタイムのセッションが無ければ stopped・1 で終える。案内は
  「持ち主が終わってから同じコマンドを打ち直す」だけで、`--run`・`finish`・`kill` を書かない
- 取った鍵は、実行の核（`run_core.main`）の上なら本体の終わり（finally・SIGTERM・時間切れ）で、核の外の入口なら
  終了時（atexit・SIGTERM の受け手・`durable.exit_leaving_pending`）に 1 回だけ返す。単位 `run` は取った・引き取った
  鍵だけを返し（祖先から継いだ鍵は返さない）、単位 `session` は `release` を宣言した副命令だけが返す
- 取った結果は結果 JSON の `lease`（`key`・`outcome`・持ち主の実行の識別子・pid・最後に動いた時刻）に出す

使う入口は `deps.require("locks", "procs")` を先に呼ぶ（持ち主の部品 `lease.py` が filelock と psutil を要する）。
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import lease
import run_core
import step_result

LEASE_REFUSED = step_result.EXIT_PRECONDITION
NO_SESSION_SUMMARY = "ランタイムのセッション（Claude Code・Codex）の外では、セッション単位の鍵を取れない"
REFUSED_NEXT = "持ち主の実行が終わってから同じコマンドを打ち直す。待てなければ、止まった理由と持ち主の pid を添えて人へ戻す"


@dataclass
class _Held:
    outcome: lease.Outcome
    unit: str
    release: bool
    done: bool = False


_HELD: list[_Held] = []
_STANDALONE_RUN: str | None = None
_STANDALONE_HOOKED = False


def lease_decl(entry_id: str, sub: str = "") -> dict | None:
    """入口の契約の `lease` のうち、副命令 `sub` の宣言。無ければ None。"""
    got = ((run_core.contract_of(entry_id) or {}).get("lease") or {}).get(sub)
    return got if isinstance(got, dict) else None


def _stage_run_id() -> str:
    global _STANDALONE_RUN
    ctx = run_core.run_context()
    if ctx is not None:
        return ctx.run_id
    _STANDALONE_RUN = _STANDALONE_RUN or uuid.uuid4().hex[:12]
    return _STANDALONE_RUN


def with_lease(obj: dict) -> dict:
    """結果に取った鍵（`lease`）を足した写し。取っていなければそのまま（記録へ書く結果を標準出力の結果と同じ形にする）。"""
    held = lease_json()
    return {**obj, "lease": held} if held else obj


def reset() -> None:
    """取った鍵の控えを空にする（1 つのプロセスで入口の本体を何度も呼ぶテストが、前の呼び出しの鍵を持ち越さない）。"""
    _HELD.clear()
    step_result.ATTACH.pop("lease", None)


def lease_json() -> dict | None:
    """結果 JSON の `lease`（最後に取った鍵）。取っていなければ None。"""
    return _HELD[-1].outcome.as_json() if _HELD else None


def _refusal_summary(out: lease.Outcome) -> str:
    return f"持ち主の鍵 {out.key} は別の実行 {out.holder_run} が持っている（pid {out.holder_pid}・最後に動いた時刻 {out.holder_seen_at}）"


def _stop_lease(entry_id: str, summary: str, code: int, name: str, nxt: str | None = None, lease_json_: dict | None = None) -> NoReturn:
    """持ち主の段の止まり。核の上なら本体の止まりとして核へ返し、核の外なら結果を出して終える。"""
    obj = step_result.result(
        Path(entry_id).stem or "run-core", "stopped", summary, gaps=[{"kind": "lease", "name": name, "reason": summary}], next=nxt
    )
    if lease_json_:
        obj["lease"] = lease_json_
    if step_result.CAPTURE:
        raise step_result.Emitted(obj, code)
    ctx = run_core.run_context()
    kind = ctx.stdout_kind if ctx else (run_core.contract_of(entry_id) or {}).get("stdout", "")
    result_file = ctx.result_file if ctx else os.environ.pop(run_core.RESULT_ENV, None)
    run_core.deliver(obj, stdout_kind=kind, print_result=True, result_file=result_file, run_id=_stage_run_id())
    sys.stdout.flush()
    sys.stderr.flush()
    raise SystemExit(code)


def hold(entry_id: str, sub: str = "", *, repo_root: Path | str | None = None, **inputs) -> lease.Outcome | None:
    """持ち主の鍵を取る（E1〜E5）。契約に宣言が無ければ None。止まるときは戻らない。

    鍵のリポジトリは `repo_root`（入口の `--root`）から決める。省けば作業ディレクトリ。"""
    raw = lease_decl(entry_id, sub)
    if raw is None:
        return None
    decl = lease.Decl.of(raw)
    try:
        key = decl.key_of(inputs)
    except (KeyError, IndexError) as e:
        _stop_lease(entry_id, f"持ち主の鍵 {decl.key} を作る入力が無い: {e}", step_result.EXIT_UNREADABLE, "input")
    try:
        out = lease.acquire(decl, key, f"{entry_id} {sub}".strip(), _stage_run_id(), lease.lease_repo(repo_root))
    except lease.LeaseError as e:
        _stop_lease(entry_id, str(e), step_result.EXIT_VIOLATION, "record")
    if out.outcome == "refused":
        _stop_lease(entry_id, _refusal_summary(out), LEASE_REFUSED, "refused", REFUSED_NEXT, out.as_json())
    if out.outcome == "no_session" and not decl.release:
        _stop_lease(entry_id, NO_SESSION_SUMMARY, step_result.EXIT_VIOLATION, "no_session", lease_json_=out.as_json())
    _HELD.append(_Held(out, decl.unit, decl.release))
    step_result.ATTACH["lease"] = out.as_json()
    ctx = run_core.run_context()
    if ctx is None or ctx.entry_id != entry_id:
        _hook_standalone(entry_id)
    return out


def touch_held() -> None:
    """長く動く本体が最後に動いた時刻を書く（持ち主の記録の持ち主が自分のときだけ）。"""
    for h in _HELD:
        if not h.done and h.outcome.holder is not None:
            lease.touch_lease(h.outcome.key, h.outcome.holder, h.outcome.repo, h.outcome.path)


def release_held() -> None:
    """取った鍵を返す（E7）。2 回目は何もしない。"""
    for h in _HELD:
        if h.done:
            continue
        h.done = True
        out = h.outcome
        if (h.unit == "run" and out.outcome in ("acquired", "reclaimed")) or (h.unit == "session" and h.release):
            lease.release_lease(out.key, out.holder, out.repo, out.path)
        elif h.unit == "session":
            lease.touch_lease(out.key, out.holder, out.repo, out.path)


def _hook_standalone(entry_id: str) -> None:
    """核の外で動く入口の返し方。自分がその入口のプロセスのときだけ atexit と SIGTERM の受け手を置く。"""
    global _STANDALONE_HOOKED
    if _STANDALONE_HOOKED or run_core.entry_of_script(sys.argv[0]) != entry_id:
        return
    _STANDALONE_HOOKED = True
    import atexit

    atexit.register(release_held)
    if threading.current_thread() is not threading.main_thread():
        return
    prev = signal.getsignal(signal.SIGTERM)

    def _on_term(signum, frame):
        release_held()
        if callable(prev):
            prev(signum, frame)
            return
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    signal.signal(signal.SIGTERM, _on_term)
