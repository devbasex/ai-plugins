"""作業ごとの token・秒・費用・再起動・自動圧縮・品質・復元の量の集計（#3569。旧い `context-policy.py report` の
`report_work` まわりの移し先）。

区分は conductor・NDF が起動した分（サブエージェントと使用量の帳簿）・引継ぎの区間（「引継ぎ開始」の印から）・
復元の区間（引継ぎ文書で始まったセッションの「復元完了」の印まで）で、時刻で排他に割り当てる。同じ `message.id` は
1 回だけ数え、帳簿にあるセッションの会話の記録は読まない。取れない値は 0 でなく「不明」と出す。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from ndf import identity
from ndf.domains.records import jsonl, usage
from ndf.domains.relay import context_policy as cp
from ndf.domains.relay import context_records as cr

IDLE_CAP = 1800  # 呼び出しの間隔をこの秒数で打ち切る（利用者の返答待ちを所要に入れない）
TOKENS = ("inp", "out", "read", "w5", "w1h")
# 入力の単価を 1 とした換算の重み（旧い token_rates.WEIGHTS と同じ）
WEIGHTS = {"inp": 1.0, "w5": 1.25, "w1h": 2.0, "out": 5.0}
# cache read の倍率（そのモデルの input 単価との比）。`message.model` の先頭で引き、無いモデルは READ_RATE_DEFAULT
READ_RATES = (("claude-opus-5-5", 0.05), ("claude-sonnet-5-5", 0.05), ("claude-fable-5-2", 0.025), ("claude-fable-5-1", 0.025))
READ_RATE_DEFAULT = 0.1
ALL_TIME = [(float("-inf"), float("inf"))]
Windows = list[tuple[float, float]]
# 区分の名前（NDF が起動した分は identity.FINAL_NAME）。結果の `parts` のキーで、旧い report と同じ綴り
CONDUCTOR, HANDOFF, RESTORE = "conductor", "handoff", "restore"


def buckets() -> tuple[str, str, str, str]:
    """区分の名前（conductor・NDF が起動した分・引継ぎ・復元）。結果の `parts` のキーで、旧い report と同じ綴り。"""
    return (CONDUCTOR, identity.FINAL_NAME, HANDOFF, RESTORE)


def read_rate(model: Optional[str]) -> float:
    for prefix, rate in READ_RATES:
        if model and model.startswith(prefix):
            return rate
    return READ_RATE_DEFAULT


def call_cost(c: Mapping[str, Any]) -> float:
    return sum(WEIGHTS[k] * c[k] for k in ("inp", "out", "w5", "w1h")) + read_rate(c.get("model")) * c["read"]


def busy_seconds(times: Iterable[Optional[float]]) -> float:
    ts = sorted(t for t in times if t is not None)
    return sum(min(b - a, IDLE_CAP) for a, b in zip(ts, ts[1:]))


def load_ledger(environ: Mapping[str, str]) -> list[dict[str, Any]]:
    """新しいプラグインの使用量の台帳の行。読めなければ空の一覧（NDF が起動した分は会話の記録の分だけにする）。"""
    try:
        return jsonl.read_rows(usage.ledger_path(environ))[0]
    except (LookupError, OSError, UnicodeDecodeError):
        return []  # 台帳が読めなければ NDF が起動した分は会話の記録の分だけにする


def _ledger_sid(row: Mapping[str, Any]) -> Optional[str]:
    sid = row.get("session_id") if row.get("session_id") else row.get("agent_id")
    return sid if isinstance(sid, str) else None


def ledger_call(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """台帳の 1 行を 1 つの呼び出しの形にする。usage が取れなければ None。"""
    u = row.get("usage")
    if not isinstance(u, dict):
        return None
    seconds = row.get("seconds") if isinstance(row.get("seconds"), (int, float)) else None
    model = row.get("model") if row.get("model") else None
    return cr.usage_call(u, cr.parse_ts(row.get("at")), model) | {"seconds": seconds}


def _empty_totals() -> dict[str, Any]:
    return {k: 0 for k in TOKENS} | {"calls": 0, "seconds": 0.0, "cost": 0.0}


def _add_call(t: dict[str, Any], c: Mapping[str, Any]) -> None:
    for k in TOKENS:
        t[k] += c[k]
    t["calls"] += 1
    t["cost"] += call_cost(c)


def _bucket_of(ts: Optional[float], restore_end: Optional[float], handoff_from: Optional[float]) -> str:
    if ts is not None and restore_end is not None and ts <= restore_end:
        return RESTORE
    if ts is not None and handoff_from is not None and ts >= handoff_from:
        return HANDOFF
    return CONDUCTOR


def _within(ts: Optional[float], windows: Windows) -> bool:
    if windows == ALL_TIME:
        return True
    return ts is not None and any(a <= ts < b for a, b in windows)


def _in_span(at: Optional[float], span: Optional[tuple[float, float]]) -> bool:
    return span is None or (at is not None and span[0] <= at <= span[1])


def work_windows(rows: list[dict[str, Any]], work: str) -> dict[str, Windows]:
    """セッションごとに、判定の行の作業の識別子が `work` である時間の区間（次に別の作業の行が来るまで）。
    セッションの最初の行が `work` なら、それより前の呼び出しも含める。`rows` は fill_work の後で `at` の順。"""
    out: dict[str, Windows] = {}
    state: dict[str, tuple[str, float]] = {}
    for r in rows:
        sid, w, at = r.get("session_id"), cr.work_of(r), cr.parse_ts(r.get("at"))
        if not sid or not w or at is None:
            continue
        if sid not in state:
            state[sid] = (w, float("-inf"))
            continue
        cur, since = state[sid]
        if w != cur:
            if cur == work:
                out.setdefault(sid, []).append((since, at))
            state[sid] = (w, at)
    for sid, (cur, since) in state.items():
        if cur == work:
            out.setdefault(sid, []).append((since, float("inf")))
    return out


def clip_windows(windows: Windows, span: Optional[tuple[float, float]]) -> Windows:
    """作業の区間を時間の区切り `span`（両端を含む）で切る。区切りが無ければそのまま。"""
    if span is None:
        return windows
    lo, hi = span
    return [(max(a, lo), min(b, hi + 1e-6)) for a, b in windows if max(a, lo) < min(b, hi + 1e-6)]


class _Scope(NamedTuple):
    """1 つの作業の集計で共有する入力。"""

    ledger: list[dict[str, Any]]
    excluded: set[str]
    seen: set[Any]


def _add_subagents(ndf: dict[str, Any], transcript: str, scope: _Scope, windows: Windows) -> None:
    """会話の記録の隣の `subagents/` の呼び出しと秒を、NDF が起動した分へ足す。"""
    sub = Path(str(transcript).removesuffix(".jsonl")) / "subagents"
    for f in sorted(sub.glob("*.jsonl")) if sub.is_dir() else []:
        if f.stem.removeprefix("agent-") in scope.excluded:
            continue  # 帳簿へ書いたサブエージェントは帳簿の行で数える
        calls = [c for c in cr.read_calls(f, scope.seen)[0] if _within(c["ts"], windows)]
        for c in calls:
            _add_call(ndf, c)
        ndf["seconds"] += busy_seconds(c["ts"] for c in calls)


def _add_ledger(ndf: dict[str, Any], sid: str, span: tuple[Optional[float], Optional[float]], scope: _Scope, windows: Windows) -> None:
    """このセッションが親か、親の無い行で時刻がセッションの範囲に入る台帳の行を、NDF が起動した分へ 1 回だけ足す。"""
    for row in scope.ledger:
        at = cr.parse_ts(row.get("at"))
        parent = row.get("parent_session_id")
        mine = parent == sid or (not parent and span[0] is not None and span[1] is not None and at is not None and span[0] <= at <= span[1])
        key = ("ledger", _ledger_sid(row), row.get("at"), row.get("step"))
        c = ledger_call(row) if mine and key not in scope.seen and _within(at, windows) else None
        if c is not None:
            scope.seen.add(key)
            _add_call(ndf, c)
            ndf["seconds"] += c["seconds"] if c["seconds"] else 0.0


def session_parts(sid: str, transcript: Optional[str], marks: list[dict[str, Any]], scope: _Scope, windows: Windows) -> dict[str, Any]:
    """1 つのセッションの呼び出しを区分へ時刻で排他に割り当てる。`windows` の区間の呼び出しだけを数える。"""
    calls, compacts, by_next = cr.read_calls(transcript, scope.seen) if transcript and sid not in scope.excluded else ([], None, False)
    calls = [c for c in calls if _within(c["ts"], windows)]
    restore_end, handoff_from = cr.mark_bounds(marks, by_next)
    parts = {b: _empty_totals() for b in buckets()}
    times: dict[str, list[Optional[float]]] = defaultdict(list)
    for c in calls:
        b = _bucket_of(c["ts"], restore_end, handoff_from)
        _add_call(parts[b], c)
        times[b].append(c["ts"])
    if transcript:
        _add_subagents(parts[identity.FINAL_NAME], transcript, scope, windows)
    stamps = [c["ts"] for c in calls if c["ts"]]
    span = (min(stamps, default=None), max(stamps, default=None))
    _add_ledger(parts[identity.FINAL_NAME], sid, span, scope, windows)
    for b, ts in times.items():
        parts[b]["seconds"] += busy_seconds(ts)
    restored = [c for c in calls if restore_end is not None and c["ts"] is not None and c["ts"] <= restore_end]
    return {
        "parts": parts,
        "restore": {"context": restored[-1]["context"], "calls": len(restored)} if restored else None,
        "compacts": compacts,
        "restore_known": not by_next or restore_end is not None,
        "handoff_known": handoff_from is not None,
    }


def _session_spans(sessions: Mapping[str, Any], excluded: set[str], windows: Mapping[str, Windows]) -> dict[str, tuple[float, float]]:
    """セッションごとの (最初, 最後) の時刻。作業の区間の呼び出しと判定の記録の行から取る（印の割り当てに使う）。"""
    spans = {}
    for sid, s in sessions.items():
        calls = cr.read_calls(s["transcript"], set())[0] if s["transcript"] and sid not in excluded else []
        w = windows[sid] if sid in windows else ALL_TIME
        ts = [c["ts"] for c in calls if c["ts"] and _within(c["ts"], w)]
        ts += [t for r in s["rows"] if (t := cr.parse_ts(r.get("at")))]
        if ts:
            spans[sid] = (min(ts), max(ts))
    return spans


def _sum_sessions(
    sessions: Mapping[str, Any], by_session: Mapping[Any, Any], scope: _Scope, windows: Mapping[str, Windows], restarted: set[Any]
) -> dict[str, Any]:
    """セッションごとの区分を合算し、自動圧縮の数と印の無い区間の数を数える。"""
    totals = {b: _empty_totals() for b in buckets()}
    compacts: Optional[int] = 0
    unknown = {"restore": 0, "handoff": 0}
    restores: list[dict[str, Any]] = []
    for sid, s in sessions.items():
        got = session_parts(
            sid, s["transcript"], by_session[sid] if sid in by_session else [], scope, windows[sid] if sid in windows else ALL_TIME
        )
        restores += [got["restore"]] if got["restore"] else []
        for b in buckets():
            for k, v in got["parts"][b].items():
                totals[b][k] += v
        compacts = None if compacts is None or got["compacts"] is None else compacts + got["compacts"]
        unknown["restore"] += 0 if got["restore_known"] else 1
        unknown["handoff"] += 1 if sid in restarted and not got["handoff_known"] else 0
    for b in buckets():
        totals[b]["cost"] = round(totals[b]["cost"])
        totals[b]["seconds"] = round(totals[b]["seconds"])
    return {"parts": totals, "compacts": compacts, "unknown_parts": unknown, "restores": restores}


def _restarts_by_reason(rows: list[dict[str, Any]]) -> dict[str, int]:
    restarts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("verdict") == cp.RESTART:
            restarts[r["reason"] if r.get("reason") else cr.UNKNOWN] += 1
    return dict(restarts)


def _rework(rows: list[dict[str, Any]]) -> int | str:
    """「継続」の判定が 2 回以上ある工程の数（やり直し）。「継続」の判定が無ければ「不明」。"""
    keep: dict[str, int] = defaultdict(int)
    for r in rows:
        event = r["event"] if "event" in r else cr.EVENT_DECISION
        if event == cr.EVENT_DECISION and r.get("verdict") == cp.CONTINUE and r.get("stage"):
            keep[r["stage"]] += 1
    return sum(1 for n in keep.values() if n >= 2) if keep else cr.UNKNOWN


def work_scope(
    work: str, rows: list[dict[str, Any]], span: Optional[tuple[float, float]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Windows]]:
    """作業の対象の行（期間で絞る前）・そのうち span に入る行・セッションごとの区間。"""
    filled = cr.fill_work(rows)
    windows = work_windows(filled, work)
    mine = cr.of_work(filled, work)
    if span is not None:
        windows = {sid: clip_windows(w, span) for sid, w in windows.items()}
        sids = {r.get("session_id") for r in mine}
        windows |= {sid: clip_windows(ALL_TIME, span) for sid in sids if sid and sid not in windows}
    return mine, [r for r in mine if _in_span(cr.parse_ts(r.get("at")), span)], windows


def report(work: str, rows: list[dict[str, Any]], ledger: list[dict[str, Any]], span: Optional[tuple[float, float]]) -> dict[str, Any]:
    """作業の識別子 1 つの集計。`span`（UNIX 時刻の (始め, 終わり)、両端を含む）を渡すと、その中の呼び出しと
    判定の記録の行だけを数える。"""
    excluded = {sid for r in ledger if (sid := _ledger_sid(r))}
    every, mine, windows = work_scope(work, rows, span)
    sessions = cr.sessions_of(every)
    # 会話の記録のパスは、セッションの最初の行（別の作業の行のことがある）だけが持つ
    known = cr.sessions_of(rows)
    for sid, s in sessions.items():
        s["transcript"] = s["transcript"] or known[sid]["transcript"]
    by_session = cr.attach_marks([r for r in every if r.get("event") in cr.MARKS], _session_spans(sessions, excluded, windows))
    restarted = {r.get("session_id") for r in mine if r.get("verdict") == cp.RESTART}
    summed = _sum_sessions(sessions, by_session, _Scope(ledger, excluded, set()), windows, restarted)
    restarts = _restarts_by_reason(mine)
    compacts = summed["compacts"]
    return {
        "work": work,
        "sessions": len(sessions),
        "parts": summed["parts"],
        "unknown_parts": summed["unknown_parts"],
        "restores": summed["restores"],
        "restarts": restarts,
        "restart_count": sum(restarts.values()),
        "auto_compacts": cr.UNKNOWN if compacts is None or not sessions else compacts,
        "quality": {"rework": _rework(mine), "review_fixes": cr.UNKNOWN, "missed_requirements": cr.UNKNOWN},
        "no_policy_rows": sum(1 for r in mine if "policy" not in r),
    }


def works(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({w for r in cr.fill_work(rows) if (w := cr.work_of(r))})
