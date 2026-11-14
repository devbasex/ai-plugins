"""環境の python の hook の入口の本体（#3345 の決定 88〜94）。

登録の command が用意済みの環境の python で `hooks/entry.py` を起動し、`hooks/entry.py` が `run_entry(sys.argv)` を
呼ぶ。`argv[1]` がランタイム、`argv[2]` が事象、標準入力が payload である。

1. `strict` の guard を、`dispatch` と依存の import より先に呼ぶ。止める答えならそれを出して終わる
2. 入力を `HookContext` で 1 回だけ解析し、guard の表を順に呼ぶ（`dispatch.run`）
3. 判定を契約表の形で出す。注入は事象ごとの上限の字数で切る
4. 計測の 1 行を書く（書けなくても判定は変えない）

例外・締め切り超え・知らない事象・入力の形の違いは、出力なし・終了コード 0 で通す。SessionStart のときだけ、
古い計測の記録を消し（`metrics.prune`）、根の目印と環境の最後に使った時刻を書く。
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional, TextIO

from ndf.domains.hooks import tables

STRICT_GUARD = "merge-production"


class _Deadline(BaseException):
    """締め切りの割り込み。guard の `except Exception` に捕まらないよう BaseException から作る。"""


def _on_alarm(signum: int, frame: Any) -> None:
    raise _Deadline()


def run_entry(argv: List[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """環境の python の入口。いつも 0。"""
    started = time.monotonic()
    record: dict = {"runtime": None, "event": None, "tool": None, "path": "entry", "guards": [], "verdict": "pass", "error": ""}
    out = ""
    state_dir = _state_dir()
    try:
        out = _entry(argv, (stdin or sys.stdin).read(), record, state_dir, started)
    except _Deadline:
        record["error"], out = "deadline", ""
    except Exception as exc:  # noqa: BLE001 利用者の入力: 入口のどの失敗も出力なし・0 で通す（IH4）
        record["error"], out = type(exc).__name__, ""
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if out:
        (stdout or sys.stdout).write(out)
    record["total_ms"] = round((time.monotonic() - started) * 1000, 3)
    if state_dir is not None:
        _write_metrics(state_dir, record)
    return 0


def _state_dir() -> Optional[Path]:
    from ndf import identity

    try:
        return identity.load().state_dir
    except identity.IdentityError:  # noqa: NDF401 ファイル: plugin.json を読めなければ状態を書かずに通す
        return None


def _write_metrics(state_dir: Path, record: dict) -> None:
    from ndf.domains.hooks import metrics

    metrics.write_line(state_dir, record)


def _entry(argv: List[str], text: str, record: dict, state_dir: Optional[Path], started: float) -> str:
    runtime, event = argv[1], argv[2]
    record.update(runtime=runtime, event=event)
    spec = tables.event_spec(runtime, event)
    if spec is None:
        record["error"] = "unknown_event"
        return ""
    from ndf.domains.hooks.context import parse_payload

    payload = parse_payload(text)
    if payload is None:
        record["error"] = "input_shape"
        return ""
    record["tool"] = payload.get("tool_name")
    off = tables.off_names(os.environ.get("NDF_HOOK_OFF"))
    strict_out = _strict(runtime, event, payload, off, record)
    if strict_out is not None:
        return strict_out
    return _guards(runtime, event, payload, off, record, (state_dir, started))


def _strict(runtime: str, event: str, payload: dict, off: frozenset, record: dict) -> Optional[str]:
    """`strict` の guard。止める答えなら出力、ほかは None。"""
    if tables.is_off(STRICT_GUARD, off):
        return None
    from ndf.domains.hooks import strict

    began = time.monotonic()
    verdict = strict.check(payload, str(payload.get("cwd") or os.getcwd()))
    kind = "deny" if verdict is not None else "pass"
    record["guards"].append({"name": STRICT_GUARD, "ms": round((time.monotonic() - began) * 1000, 3), "kind": kind, "error": ""})
    if verdict is None:
        return None
    record["verdict"] = "deny"
    return tables.output_for(runtime, event, "deny", verdict.reason)


def _guards(runtime: str, event: str, payload: dict, off: frozenset, record: dict, where: tuple) -> str:
    from ndf.domains.hooks import budget, dispatch
    from ndf.domains.hooks.context import HookContext
    from ndf.domains.hooks.counter import Counter

    state_dir, started = where
    found = budget.budget_for(runtime, event)
    if found is None or state_dir is None:
        record["error"] = "no_budget" if found is None else "no_state"
        return ""
    remaining = found.deadline - (time.monotonic() - started)
    if remaining <= 0:
        record["error"] = "deadline"
        return ""
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    ctx = HookContext(runtime, event, payload, tail_bytes=int(tables.policy_value("hook.transcript-tail", "count")))
    missing = ctx.missing(tables.event_spec(runtime, event)["reads"])
    if missing:
        record["error"] = "input_shape"
        record["missing"] = missing
        return ""
    if event == "SessionStart":
        _session_start(state_dir)
    with Counter.open(ctx.session_id, state_dir) as counter:
        outcome = dispatch.run(ctx, dispatch.select(dispatch.load_specs(), ctx, off), found, counter, state_dir)
        record["pushbacks"] = counter.data["pushbacks"] if counter.usable else None
    _, out = dispatch.render(outcome, runtime, event, found, state_dir / "hooks" / "notes")
    if outcome.limited:
        _notify_wait(state_dir, ctx, outcome.notes.get("limited_reason"))
    record["guards"] += [asdict(r) for r in outcome.runs]
    record.update(
        verdict="limited" if outcome.limited else outcome.verdict.kind,
        inject_chars=outcome.notes.pop("inject_chars", 0),
        notes=outcome.notes,
        error=record["error"] or outcome.stopped,
    )
    return out


def _session_start(state_dir: Path) -> None:
    """SessionStart の entry だけが行う片付けと印。"""
    from ndf.domains.hooks import env, metrics

    metrics.prune(state_dir, float(tables.policy_value("hook.metrics-keep", "seconds")) / 86400)
    ident = env.Ident.of(tables.PLUGIN_ROOT)
    env.mark_root(ident)
    env.touch_used(ident)


def notify_configured() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL_ID"))


def _notify_wait(state_dir: Path, ctx: Any, reason: Optional[str]) -> None:
    """差し戻しの上限で通した Stop の待ちの知らせを送信待ちへ書く（決定 92）。"""
    if not notify_configured():
        return
    from ndf.domains.hooks import outbox

    outbox.enqueue(state_dir, "wait", {"text": f"差し戻しの上限で止めずに通した: {reason or ''}", "cwd": ctx.cwd, "session_id": ctx.session_id})  # noqa: NDF501 ファイル: 理由の無い差し戻しは空
    outbox.kick(state_dir)
