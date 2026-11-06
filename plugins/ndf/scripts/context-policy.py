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
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import context_calibrate  # noqa: E402
import context_decisions as cd  # noqa: E402
import context_policy as cp  # noqa: E402
import legacy_values  # noqa: E402
import step_result as sr  # noqa: E402

TOOL = "context-policy"
IDLE_CAP = 1800  # 呼び出しの間隔をこの秒数で打ち切る（利用者の返答待ちを所要に入れない。token-usage.py と同じ）
BUCKETS = ("conductor", "ndf", "handoff", "restore")


# --- 集計 ---------------------------------------------------------------------------------------------


def call_cost(c: Mapping) -> float:
    from token_rates import WEIGHTS, read_rate

    return (WEIGHTS["inp"] * c["inp"] + WEIGHTS["out"] * c["out"] + WEIGHTS["w5"] * c["w5"] + WEIGHTS["w1h"] * c["w1h"]) + read_rate(
        c.get("model")
    ) * c["read"]


def busy_seconds(times: Iterable[float | None]) -> float:
    ts = sorted(t for t in times if t is not None)
    return sum(min(b - a, IDLE_CAP) for a, b in zip(ts, ts[1:]))


def ledger_calls(row: Mapping) -> dict | None:
    """使用量の帳簿の 1 行を 1 つの呼び出しの形にする。usage が取れなければ None。"""
    u = row.get("usage")
    if not isinstance(u, dict):
        return None
    seconds = row.get("seconds") if isinstance(row.get("seconds"), (int, float)) else None
    return cd.usage_call(u, cd.parse_ts(row.get("at")), row.get("model") or None) | {"seconds": seconds}


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


ALL_TIME = [(float("-inf"), float("inf"))]


def work_windows(rows: list[dict], work: str) -> dict[str, list[tuple[float, float]]]:
    """セッションごとに、判定の行の作業の識別子が `work` である時間の区間（次に別の作業の行が来るまで）。

    同じセッションで #1 の後に #2 を扱うと、#1 の集計は #2 の行より前の呼び出しだけを数える（作業ごとに二重に数えない）。
    セッションの最初の行が `work` なら、それより前の呼び出しも含める。`rows` は fill_work の後で `at` の順。"""
    out: dict[str, list[tuple[float, float]]] = {}
    state: dict[str, tuple[str, float]] = {}  # sid → (今の作業, 始まり)
    for r in rows:
        sid, w, at = r.get("session_id"), r.get("work") or "", cd.parse_ts(r.get("at"))
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


def _within(ts: float | None, windows: list[tuple[float, float]]) -> bool:
    if windows == ALL_TIME:
        return True
    return ts is not None and any(a <= ts < b for a, b in windows)


def _mark_bounds(marks: list[dict], by_next: bool) -> tuple[float | None, float | None]:
    """(復元の区間の終わり, 引継ぎの区間の始まり)。復元は ndf-next で始まったセッションの最初の「復元完了」、引継ぎは最後の「引継ぎ開始」。"""
    restored = [cd.parse_ts(m.get("at")) for m in marks if m.get("event") == cd.MARK_RESTORED]
    handoffs = [cd.parse_ts(m.get("at")) for m in marks if m.get("event") == cd.MARK_HANDOFF]
    restore_end = min((t for t in restored if t is not None), default=None) if by_next else None
    handoff_from = max((t for t in handoffs if t is not None), default=None)
    return restore_end, handoff_from


def _add_subagents(ndf: dict, transcript: str, excluded: set, seen: set, windows: list[tuple[float, float]]) -> None:
    """会話の記録の隣の `subagents/` の呼び出しと秒を、NDF が起動した分へ足す。"""
    sub = Path(str(transcript).removesuffix(".jsonl")) / "subagents"
    for f in sorted(sub.glob("*.jsonl")) if sub.is_dir() else []:
        if f.stem.removeprefix("agent-") in excluded:
            continue  # SubagentStop が帳簿へ書いたサブエージェント（session_id = agent_id）は帳簿の行で数える
        sub_calls, _, _ = cd.read_calls(f, seen)
        sub_calls = [c for c in sub_calls if _within(c["ts"], windows)]
        for c in sub_calls:
            _add_call(ndf, c)
        ndf["seconds"] += busy_seconds(c["ts"] for c in sub_calls)


def _add_ledger(ndf: dict, sid: str, span: tuple, ledger_rows: list[dict], seen: set, windows: list[tuple[float, float]]) -> None:
    """このセッションが親か、親の無い行で時刻がセッションの範囲に入る帳簿の行を、NDF が起動した分へ 1 回だけ足す。"""
    for row in ledger_rows:
        at = cd.parse_ts(row.get("at"))
        mine = row.get("parent_session_id") == sid or (
            not row.get("parent_session_id") and span[0] is not None and at is not None and span[0] <= at <= span[1]
        )
        key = ("ledger", row.get("session_id"), row.get("at"), row.get("step"))
        c = ledger_calls(row) if mine and key not in seen and _within(at, windows) else None
        if c is not None:
            seen.add(key)
            _add_call(ndf, c)
            ndf["seconds"] += c["seconds"] or 0.0


def session_parts(
    sid: str,
    transcript: str | None,
    marks: list[dict],
    ledger_rows: list[dict],
    excluded: set,
    seen: set,
    windows: list[tuple[float, float]] | None = None,
) -> dict:
    """1 つのセッションの呼び出しを区分へ時刻で排他に割り当てる（決定 13）。`windows` を渡すと、その区間の呼び出しだけを数える。"""
    windows = ALL_TIME if windows is None else windows
    calls, compacts, by_next = cd.read_calls(transcript, seen) if transcript and sid not in excluded else ([], None, False)
    calls = [c for c in calls if _within(c["ts"], windows)]
    restore_end, handoff_from = _mark_bounds(marks, by_next)
    parts = {b: _empty_totals() for b in BUCKETS}
    times: dict[str, list] = defaultdict(list)
    for c in calls:
        b = _bucket_of(c["ts"], restore_end, handoff_from)
        _add_call(parts[b], c)
        times[b].append(c["ts"])
    if transcript:
        _add_subagents(parts["ndf"], transcript, excluded, seen, windows)
    span = (min((c["ts"] for c in calls if c["ts"]), default=None), max((c["ts"] for c in calls if c["ts"]), default=None))
    _add_ledger(parts["ndf"], sid, span, ledger_rows, seen, windows)
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


def _session_spans(sessions: dict, excluded: set, windows: dict) -> dict[str, tuple[float, float]]:
    """セッションごとの (最初, 最後) の時刻。作業の区間の呼び出しと判定の記録の行から取る（印の割り当てに使う）。"""
    spans = {}
    for sid, s in sessions.items():
        calls, _, _ = cd.read_calls(s["transcript"], set()) if s["transcript"] and sid not in excluded else ([], None, False)
        calls = [c for c in calls if _within(c["ts"], windows.get(sid, ALL_TIME))]
        ts = [c["ts"] for c in calls if c["ts"]] + [cd.parse_ts(r.get("at")) for r in s["rows"] if cd.parse_ts(r.get("at"))]
        if ts:
            spans[sid] = (min(ts), max(ts))
    return spans


def _sum_sessions(sessions: dict, by_session: dict, ledger_rows: list[dict], excluded: set, windows: dict) -> dict:
    """セッションごとの区分を合算し、自動圧縮の数と印の無い区間の数を数える。"""
    totals = {b: _empty_totals() for b in BUCKETS}
    compacts: int | None = 0
    no_restore_mark = no_handoff_mark = 0
    seen: set = set()
    for sid, s in sessions.items():
        summary = session_parts(sid, s["transcript"], by_session.get(sid, []), ledger_rows, excluded, seen, windows.get(sid))
        for b in BUCKETS:
            for k, v in summary["parts"][b].items():
                totals[b][k] += v
        compacts = None if compacts is None or summary["compacts"] is None else compacts + summary["compacts"]
        no_restore_mark += 0 if summary["restore_known"] else 1
        restarted = any(r.get("verdict") == cp.RESTART for r in s["rows"])
        no_handoff_mark += 1 if restarted and not summary["handoff_known"] else 0
    for b in BUCKETS:
        totals[b]["cost"] = round(totals[b]["cost"])
        totals[b]["seconds"] = round(totals[b]["seconds"])
    return {"parts": totals, "compacts": compacts, "unknown_parts": {"restore": no_restore_mark, "handoff": no_handoff_mark}}


def _restarts_by_reason(rows: list[dict]) -> dict[str, int]:
    restarts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("verdict") == cp.RESTART:
            restarts[r.get("reason") or cd.UNKNOWN] += 1
    return dict(restarts)


def _rework(rows: list[dict]) -> int | str:
    """「継続」の判定が 2 回以上ある工程の数（やり直し）。「継続」の判定が無ければ「不明」。"""
    keep: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("event", cd.EVENT_DECISION) == cd.EVENT_DECISION and r.get("verdict") == cp.CONTINUE and r.get("stage"):
            keep[legacy_values.to_current("stage", r["stage"])] += 1
    return sum(1 for n in keep.values() if n >= 2) if keep else cd.UNKNOWN


def report_work(work: str, rows: list[dict], ledger_rows: list[dict] | None = None) -> dict:
    """作業の識別子 1 つの集計（受け入れ条件 12・13、I12〜I14）。"""
    ledger_rows = ledger_rows or []
    excluded = {r.get("session_id") for r in ledger_rows if r.get("session_id")}
    filled = cd.fill_work(rows)
    windows = work_windows(filled, work)
    rows = [r for r in filled if (r.get("work") or "") == work]
    sessions = cd.sessions_of(rows)
    by_session = cd.attach_marks([r for r in rows if r.get("event") in cd.MARKS], _session_spans(sessions, excluded, windows))
    summed = _sum_sessions(sessions, by_session, ledger_rows, excluded, windows)
    restarts = _restarts_by_reason(rows)
    compacts = summed["compacts"]
    return {
        "work": work,
        "sessions": len(sessions),
        "parts": summed["parts"],
        "unknown_parts": summed["unknown_parts"],
        "restarts": restarts,
        "restart_count": sum(restarts.values()),
        "auto_compacts": cd.UNKNOWN if compacts is None or not sessions else compacts,
        "quality": {"rework": _rework(rows), "review_fixes": cd.UNKNOWN, "missed_requirements": cd.UNKNOWN},
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
    row = cd.record_row(a.runtime, "record", a.work, cp.resolve_policy(root), a.context, a.event, a.reason)
    if not cd.append_decision(row, cd.decisions_repo(root)):
        sr.emit(sr.result(TOOL, "stopped", "判定の記録へ書けない", metrics={"dir": str(cd.context_state_dir())}), sr.EXIT_VIOLATION)
    sr.emit(sr.result(TOOL, "ok", f"{a.event or a.verdict} を記録した（{a.work}）", [row]))


def cmd_calibrate(a) -> None:
    body, ok = context_calibrate.calibrate_repo(cd.toplevel(None), a.days)
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
    lines = [
        "| 作業 | 区分 | 呼び出し | 入力 | 出力 | 読込 | 書込 5 分 | 書込 1 時間 | 秒 | 換算の費用 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in reports:
        for b in BUCKETS:
            t = r["parts"][b]
            lines.append(
                f"| {r['work']} | {b} | {t['calls']} | {t['inp']} | {t['out']} | {t['read']} | {t['w5']} | {t['w1h']} | {t['seconds']} | {t['cost']} |"
            )
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
