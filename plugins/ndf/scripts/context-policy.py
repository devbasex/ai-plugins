#!/usr/bin/env python3
r"""再起動の方針の適用値・判定の記録・費用の見込み・作業ごとの集計（#2262）。LLM を呼ばない。

| 副命令 | 何をする | 終了コード |
| --- | --- | --- |
| `resolve [--root <dir>] [--policy current\|trial]` | 適用値と値ごとの出所を返す（壊れた設定は現行方式で返し `config_error` を持つ） | 0 / 2 引数の誤り |
| `publish` | SessionStart の入力（標準入力の JSON）から適用値の受け渡しのファイルを書く。壊れた設定なら 1 行を出す | 常に 0 |
| `record --verdict 再起動 --reason <理由> --work <識別子>` | 再起動の 1 行を追記する | 0 / 1 書けない / 2 理由が 6 つ以外 |
| `record --event 引継ぎ開始\|復元完了 --work <識別子>` | 印の 1 行を追記する | 0 / 1 書けない / 2 印が 2 つ以外 |
| `calibrate [--days 30]` | 判定の記録と会話の記録から費用の見込みを較正して書く | 0 / 1 書けない |
| `report --work <識別子> \| --all [--since <日付>] [--format json\|table]` | 作業ごとの区分の token・秒・費用・再起動・自動圧縮・品質 | 0 / 2 引数の誤り |

区分は conductor・NDF が起動した分（サブエージェントと使用量の帳簿）・引継ぎの区間（「引継ぎ開始」の印から）・
復元の区間（引継ぎ文書で始まったセッションの「復元完了」の印まで）で、時刻で排他に割り当てる。同じ `message.id` は
1 回だけ数え、帳簿にあるセッションの会話の記録は読まない（決定 13）。取れない値は 0 でなく「不明」と出す。
規約は skills/development-workflow/references/context-window.md にある。

    python3 context-policy.py resolve
    python3 context-policy.py record --event 引継ぎ開始 --work "#2262"
    python3 context-policy.py report --work "#2262" --format table
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import context_decisions as cd  # noqa: E402
import context_policy as cp  # noqa: E402
import step_result as sr  # noqa: E402

TOOL = "context-policy"
IDLE_CAP = 1800  # 呼び出しの間隔をこの秒数で打ち切る（利用者の返答待ちを所要に入れない。token-usage.py と同じ）
BUCKETS = ("conductor", "ndf", "handoff", "restore")


# --- 集計 ---------------------------------------------------------------------------------------------


def call_cost(c: Mapping) -> float:
    from token_rates import WEIGHTS, read_rate

    return (
        WEIGHTS["inp"] * c["inp"] + WEIGHTS["out"] * c["out"] + WEIGHTS["w5"] * c["w5"] + WEIGHTS["w1h"] * c["w1h"]
    ) + read_rate(c.get("model")) * c["read"]


def busy_seconds(times: Iterable[float | None]) -> float:
    ts = sorted(t for t in times if t is not None)
    return sum(min(b - a, IDLE_CAP) for a, b in zip(ts, ts[1:]))


def ledger_calls(row: Mapping) -> dict | None:
    """使用量の帳簿の 1 行を 1 つの呼び出しの形にする。usage が取れなければ None。"""
    from usage_ledger import cache_writes

    u = row.get("usage")
    if not isinstance(u, dict):
        return None
    w5, w1h = cache_writes(u)
    inp, read = int(u.get("input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
    return {
        "ts": cd.parse_ts(row.get("at")),
        "model": row.get("model") or None,
        "inp": inp,
        "out": int(u.get("output_tokens") or 0),
        "read": read,
        "w5": w5,
        "w1h": w1h,
        "context": inp + read + w5 + w1h,
        "seconds": row.get("seconds") if isinstance(row.get("seconds"), (int, float)) else None,
    }


def _bucket_of(ts: float | None, restore_end: float | None, handoff_from: float | None) -> str:
    if ts is not None and restore_end is not None and ts <= restore_end:
        return "restore"
    if ts is not None and handoff_from is not None and ts >= handoff_from:
        return "handoff"
    return "conductor"


def _empty_totals() -> dict:
    return {k: 0 for k in ("inp", "out", "read", "w5", "w1h")} | {"calls": 0, "seconds": 0.0, "cost": 0.0}


def _add_call(t: dict, c: Mapping) -> None:
    for k in ("inp", "out", "read", "w5", "w1h"):
        t[k] += c[k]
    t["calls"] += 1
    t["cost"] += call_cost(c)


def session_parts(sid: str, transcript: str | None, marks: list[dict], ledger_rows: list[dict], excluded: set, seen: set) -> dict:
    """1 つのセッションの呼び出しを区分へ時刻で排他に割り当てる（決定 13）。"""
    calls, compacts, by_next = cd.read_calls(transcript, seen) if transcript and sid not in excluded else ([], None, False)
    restored = [cd.parse_ts(m.get("at")) for m in marks if m.get("event") == cd.MARK_RESTORED]
    handoffs = [cd.parse_ts(m.get("at")) for m in marks if m.get("event") == cd.MARK_HANDOFF]
    restore_end = min((t for t in restored if t is not None), default=None) if by_next else None
    handoff_from = max((t for t in handoffs if t is not None), default=None)
    parts = {b: _empty_totals() for b in BUCKETS}
    times: dict[str, list] = defaultdict(list)
    for c in calls:
        b = _bucket_of(c["ts"], restore_end, handoff_from)
        _add_call(parts[b], c)
        times[b].append(c["ts"])
    if transcript:
        sub = Path(str(transcript).removesuffix(".jsonl")) / "subagents"
        for f in sorted(sub.glob("*.jsonl")) if sub.is_dir() else []:
            sub_calls, _, _ = cd.read_calls(f, seen)
            for c in sub_calls:
                _add_call(parts["ndf"], c)
            parts["ndf"]["seconds"] += busy_seconds(c["ts"] for c in sub_calls)
    span = (min((c["ts"] for c in calls if c["ts"]), default=None), max((c["ts"] for c in calls if c["ts"]), default=None))
    for row in ledger_rows:
        at = cd.parse_ts(row.get("at"))
        mine = row.get("parent_session_id") == sid or (
            not row.get("parent_session_id") and span[0] is not None and at is not None and span[0] <= at <= span[1]
        )
        key = ("ledger", row.get("session_id"), row.get("at"), row.get("step"))
        c = ledger_calls(row) if mine and key not in seen else None
        if c is not None:
            seen.add(key)
            _add_call(parts["ndf"], c)
            parts["ndf"]["seconds"] += c["seconds"] or 0.0
    for b, ts in times.items():
        parts[b]["seconds"] += busy_seconds(ts)
    return {
        "parts": parts,
        "compacts": compacts,
        "restore_known": not by_next or restore_end is not None,
        "started_by_next": by_next,
        "handoff_known": handoff_from is not None,
        "span": span,
    }


def report_work(work: str, rows: list[dict], ledger_rows: list[dict] | None = None) -> dict:
    """作業の識別子 1 つの集計（受け入れ条件 12・13、I12〜I14）。"""
    ledger_rows = ledger_rows or []
    excluded = {r.get("session_id") for r in ledger_rows if r.get("session_id")}
    rows = [r for r in cd.fill_work(rows) if (r.get("work") or "") == work]
    sessions = cd.sessions_of(rows)
    seen: set = set()
    spans = {}
    marks = [r for r in rows if r.get("event") in cd.MARKS]
    for sid, s in sessions.items():
        calls, _, _ = cd.read_calls(s["transcript"], set()) if s["transcript"] and sid not in excluded else ([], None, False)
        ts = [c["ts"] for c in calls if c["ts"]] + [cd.parse_ts(r.get("at")) for r in s["rows"] if cd.parse_ts(r.get("at"))]
        if ts:
            spans[sid] = (min(ts), max(ts))
    by_session = cd.attach_marks(marks, spans)
    totals = {b: _empty_totals() for b in BUCKETS}
    compacts: int | None = 0
    no_restore_mark = no_handoff_mark = 0
    for sid, s in sessions.items():
        summary = session_parts(sid, s["transcript"], by_session.get(sid, []), ledger_rows, excluded, seen)
        for b in BUCKETS:
            for k, v in summary["parts"][b].items():
                totals[b][k] += v
        compacts = None if compacts is None or summary["compacts"] is None else compacts + summary["compacts"]
        no_restore_mark += 0 if summary["restore_known"] else 1
        restarted = any(r.get("verdict") == "再起動" for r in s["rows"])
        no_handoff_mark += 1 if restarted and not summary["handoff_known"] else 0
    restarts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("verdict") == "再起動":
            restarts[r.get("reason") or cd.UNKNOWN] += 1
    keep = defaultdict(int)
    for r in rows:
        if r.get("event", cd.EVENT_DECISION) == cd.EVENT_DECISION and r.get("verdict") == "継続" and r.get("stage"):
            keep[r["stage"]] += 1
    for b in BUCKETS:
        totals[b]["cost"] = round(totals[b]["cost"])
        totals[b]["seconds"] = round(totals[b]["seconds"])
    return {
        "work": work,
        "sessions": len(sessions),
        "parts": totals,
        "unknown_parts": {
            "restore": no_restore_mark,
            "handoff": no_handoff_mark,
        },
        "restarts": dict(restarts),
        "restart_count": sum(restarts.values()),
        "auto_compacts": cd.UNKNOWN if compacts is None or not sessions else compacts,
        "quality": {
            "rework": sum(1 for n in keep.values() if n >= 2) if keep else cd.UNKNOWN,
            "review_fixes": cd.UNKNOWN,
            "missed_requirements": cd.UNKNOWN,
        },
        "no_policy_rows": sum(1 for r in rows if "policy" not in r),
    }


def works(rows: list[dict]) -> list[str]:
    return sorted({r.get("work") for r in cd.fill_work(rows) if r.get("work")})


# --- 副命令 -------------------------------------------------------------------------------------------


def cmd_resolve(a) -> None:
    applied = cp.resolve_policy(cd.toplevel(a.root), os.environ, {"policy": a.policy})
    d = applied.to_dict()
    summary = f"再起動の方針: {applied.policy}（判断開始の値 {applied.decide_from} / 運用上限 {applied.limit} / 余白 {applied.margin}）"
    if applied.config_error:
        summary += f"。設定が壊れているため読まなかった: {applied.config_error}"
    sr.emit(sr.result(TOOL, "ok", summary, [d], {"policy": applied.policy, "limit": applied.limit}))


def cmd_publish(_a) -> None:
    try:
        raw = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    sid = raw.get("session_id") if isinstance(raw.get("session_id"), str) else ""
    try:
        applied = cp.resolve_policy(cd.toplevel(raw.get("cwd") if isinstance(raw.get("cwd"), str) else None))
        if sid:
            cd.write_applied(sid, applied)
        if applied.config_error:
            print(f"NDF: {applied.config_error}。再起動の方針の設定を読まずに現行方式の既定で判定する。")
    except Exception as e:  # noqa: BLE001 — SessionStart を落とさない
        print(f"context-policy publish: {e}", file=sys.stderr)
    raise SystemExit(0)


def cmd_record(a) -> None:
    if (a.verdict is None) == (a.event is None):
        sr.emit(sr.result(TOOL, "stopped", "--verdict 再起動 か --event のどちらか 1 つを渡す"), sr.EXIT_UNREADABLE)
    if a.event is not None and a.event not in cd.MARKS:
        sr.emit(sr.result(TOOL, "stopped", f"印は {' / '.join(cd.MARKS)} のどれか: {a.event}"), sr.EXIT_UNREADABLE)
    if a.verdict is not None and (a.verdict != cp.RESTART or a.reason not in cp.REASONS):
        sr.emit(sr.result(TOOL, "stopped", f"--verdict 再起動 と、理由（{' / '.join(cp.REASONS)}）を渡す"), sr.EXIT_UNREADABLE)
    root = cd.toplevel(None)
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") if a.runtime == "claude" else None
    row = cli_row(a.runtime, "record", sid or None, a.work, root, a.context)
    if a.event:
        row.update(event=a.event)
    else:
        row.update(stage="cut", verdict=cp.RESTART, reason=a.reason)
    if not cd.append_decision(row, cd.decisions_repo(root)):
        sr.emit(sr.result(TOOL, "stopped", "判定の記録へ書けない", metrics={"dir": str(cd.context_state_dir())}), sr.EXIT_VIOLATION)
    sr.emit(sr.result(TOOL, "ok", f"{a.event or a.verdict} を記録した（{a.work}）", [row]))


def cli_row(runtime: str, source: str, sid: str | None, work: str, root: str | None, context: int | None) -> dict:
    """CLI と sprint-state.py が書く行（判定をしない。`band` は null）。"""
    return cd.boundary_row(runtime=runtime, source=source, session_id=sid, work=work, context=context, applied=cp.resolve_policy(root))


def cmd_calibrate(a) -> None:
    body, ok = cd.calibrate_repo(cd.toplevel(None), a.days)
    if not ok:
        sr.emit(sr.result(TOOL, "stopped", "見込みのファイルへ書けない"), sr.EXIT_VIOLATION)
    sr.emit(sr.result(TOOL, "ok", f"見込みを較正した（標本 {body['samples']}）", [body], {"samples": body["samples"]}))


def _ledger(root: str | None) -> list[dict]:
    try:
        import usage_ledger

        return usage_ledger.read_usage_ledger("claude", repo=cd.decisions_repo(root))
    except Exception:  # noqa: BLE001 — 帳簿が読めなければ NDF が起動した分は会話の記録の分だけにする
        return []


def _report_table(reports: list[dict]) -> str:
    lines = ["| 作業 | 区分 | 呼び出し | 入力 | 出力 | 読込 | 書込 5 分 | 書込 1 時間 | 秒 | 換算の費用 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in reports:
        for b in BUCKETS:
            t = r["parts"][b]
            lines.append(f"| {r['work']} | {b} | {t['calls']} | {t['inp']} | {t['out']} | {t['read']} | {t['w5']} | {t['w1h']} | {t['seconds']} | {t['cost']} |")
    lines.append("")
    for r in reports:
        lines.append(
            f"- {r['work']}: セッション {r['sessions']}、再起動 {r['restart_count']}（{r['restarts']}）、自動圧縮 {r['auto_compacts']}、"
            f"品質 {r['quality']}、印の無い区間 {r['unknown_parts']}、方針の記録が無い行 {r['no_policy_rows']}（現行方式として読んだ）"
        )
    return "\n".join(lines)


def cmd_report(a) -> None:
    if bool(a.work) == bool(a.all):
        sr.emit(sr.result(TOOL, "stopped", "--work か --all のどちらか 1 つを渡す"), sr.EXIT_UNREADABLE)
    since = cd.parse_ts(f"{a.since}T00:00:00Z") if a.since else None
    if a.since and since is None:
        sr.emit(sr.result(TOOL, "stopped", f"--since は YYYY-MM-DD: {a.since}"), sr.EXIT_UNREADABLE)
    root = cd.toplevel(None)
    rows = [r for r in cd.read_decisions(cd.decisions_repo(root)) if since is None or (cd.parse_ts(r.get("at")) or 0) >= since]
    ledger = _ledger(root)
    targets = [a.work] if a.work else works(rows)
    reports = [report_work(w, rows, ledger) for w in targets]
    if a.format == "table":
        print(_report_table(reports))
        raise SystemExit(0)
    metrics = {"works": len(reports), "no_policy_rows": sum(r["no_policy_rows"] for r in reports)}
    sr.emit(sr.result(TOOL, "ok", f"{len(reports)} 件の作業を集計した", reports, metrics))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--root")
    p.add_argument("--policy", choices=cp.POLICIES)
    sub.add_parser("publish")
    p = sub.add_parser("record")
    p.add_argument("--verdict")
    p.add_argument("--reason")
    p.add_argument("--event")
    p.add_argument("--work", required=True)
    p.add_argument("--runtime", choices=("claude", "codex"), default="claude")
    p.add_argument("--context", type=int)
    p = sub.add_parser("calibrate")
    p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("report")
    p.add_argument("--work")
    p.add_argument("--all", action="store_true")
    p.add_argument("--since")
    p.add_argument("--format", choices=("json", "table"), default="json")
    a = ap.parse_args(argv)
    {"resolve": cmd_resolve, "publish": cmd_publish, "record": cmd_record, "calibrate": cmd_calibrate, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
