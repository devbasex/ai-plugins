#!/usr/bin/env python3
"""context-trial.py: 続けるか切るかの計測の回 1 つから、計測の記録の 1 行を作る（試行。#2908）。

    python3 context-trial.py row --work "#2908" --condition 閾値で切る --transcript <conductor の会話の記録>
        [--from <ISO 8601>] [--until <ISO 8601>] [--input-usd 4.0]

回の区切り:
- 始め（`--from`）: 渡さなければ、渡した会話の記録で最初に `supervise.py queue` を起動した Bash の時刻（設計のキューの起動）
- 終わり（`--until`）: 渡さなければ、始めの後で最初に答えを受けた `AskUserQuestion`（設計承認ゲートの問い）の後の、
  最初の呼び出しの時刻。会話の記録は、渡したものと、判定の記録で同じ作業の行を持つセッションのものを読む

区切りの中を `context-policy.py report` の集計（`report_work`）で数え、会話の側（conductor・引継ぎの区間・復元の区間）の
費用と token の内訳・NDF が起動した分の費用・所要（人の待ちを除く秒）・人の待ちの回数と各回の秒・切った回数（「引継ぎ開始」の印）・
復元の量を 1 行に並べる。方針と値は区切りの中の最初の判定の行のもの。起動の時点の文脈が 200,000 以下の回は「対象外」と印を付ける
（2 つの計測の条件が同じに動くため）。会話の ID・会話の本文は出さない（`docs/metrics/` の規約）。

費用の USD は入力の単価に換算した費用（`token_rates.WEIGHTS`）に `--input-usd`（100 万 token あたり。既定は Opus 5.5 の 4）を掛ける。
人が読む表の 1 行の後に step_result の 1 行の JSON。読むだけで、書き込まない。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
import context_decisions as cd  # noqa: E402
import context_policy as cp  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "context-trial"
CONDITIONS = ("続ける", "閾値で切る")
CONVERSATION = ("conductor", "handoff", "restore")
TOKENS = ("inp", "out", "read", "w5", "w1h")


def _cli():
    spec = importlib.util.spec_from_file_location("context_policy_cli", SCRIPTS / "context-policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entries(path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _blocks(e: dict, kind: str) -> list[dict]:
    content = (e.get("message") if isinstance(e.get("message"), dict) else {}).get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind] if isinstance(content, list) else []


def queue_launch(entries: list[dict]) -> float | None:
    """最初に `supervise.py queue` を起動した Bash の時刻。"""
    for e in entries:
        for b in _blocks(e, "tool_use"):
            cmd = (b.get("input") or {}).get("command") if b.get("name") == "Bash" else None
            if isinstance(cmd, str) and "supervise.py" in cmd and " queue" in cmd:
                return cd.parse_ts(e.get("timestamp"))
    return None


def waits(entries: list[dict]) -> list[tuple[float, float]]:
    """`AskUserQuestion` の (問いの時刻, 答えの時刻) の並び。答えの無い問いは入れない。"""
    asked: dict[str, float] = {}
    out = []
    for e in entries:
        ts = cd.parse_ts(e.get("timestamp"))
        for b in _blocks(e, "tool_use"):
            if b.get("name") == "AskUserQuestion" and ts is not None:
                asked[b.get("id")] = ts
        for b in _blocks(e, "tool_result"):
            if b.get("tool_use_id") in asked and ts is not None:
                out.append((asked.pop(b["tool_use_id"]), ts))
    return sorted(out)


def first_call_after(entries: list[dict], at: float) -> float | None:
    times = [t for e in entries if e.get("type") == "assistant" and (t := cd.parse_ts(e.get("timestamp"))) is not None and t > at]
    return min(times, default=None)


def trial_row(
    work: str, condition: str, transcript: str, rows: list[dict], ledger: list[dict], lo: float | None, hi: float | None, usd: float
) -> dict:
    """計測の回 1 つの行。区切りを見つけられなければ ValueError。"""
    work_rows = [r for r in cd.fill_work(rows) if (r.get("work") or "") == work]
    paths = [transcript] + sorted({r["transcript"] for r in work_rows if r.get("transcript") and r["transcript"] != transcript})
    entries = sorted((e for p in paths for e in _entries(p)), key=lambda e: cd.parse_ts(e.get("timestamp")) or 0.0)
    lo = queue_launch(_entries(transcript)) if lo is None else lo
    if lo is None:
        raise ValueError("設計のキューの起動（supervise.py queue）が会話の記録に無い。--from を渡す")
    asks = [w for w in waits(entries) if w[0] >= lo]
    if hi is None:
        hi = first_call_after(entries, asks[0][1]) if asks else None
    if hi is None:
        raise ValueError("承認ゲートの答えの後の呼び出しが見つからない。--until を渡す")
    span = (lo, hi)
    rep = _cli().report_work(work, rows, ledger, span)
    inside = [r for r in work_rows if lo <= (cd.parse_ts(r.get("at")) or float("-inf")) <= hi]
    first = next((r for r in inside if r.get("event", cd.EVENT_DECISION) == cd.EVENT_DECISION), {})
    held = [round(b - a) for a, b in asks if a <= hi]
    parts = rep["parts"]
    conv = {b: round(parts[b]["cost"] * usd / 1e6, 2) for b in CONVERSATION}
    start = first.get("context")
    return {
        "condition": condition,
        "policy": f"{first.get('policy') or cd.UNKNOWN}（判断開始の値 {first.get('decide_from')} / 運用上限 {first.get('limit')} / 余白 {first.get('margin')}）",
        "start_context": start,
        "excluded": None if isinstance(start, int) and start > cp.CURRENT_LIMIT else f"起動の時点の文脈が {cp.CURRENT_LIMIT} 以下か不明",
        "conversation_usd": round(sum(conv.values()), 2),
        "conversation_parts_usd": conv,
        "tokens": {k: sum(parts[b][k] for b in CONVERSATION) for k in TOKENS},
        "ndf_usd": round(parts["ndf"]["cost"] * usd / 1e6, 2),
        "seconds": round(hi - lo - sum(held)),
        "waits": held,
        "cuts": sum(1 for r in inside if r.get("event") == cd.MARK_HANDOFF),
        "restores": [x["context"] for x in rep["restores"]],
        "unknown_parts": rep["unknown_parts"],
    }


def table_line(r: dict) -> str:
    t = r["tokens"]
    p = r["conversation_parts_usd"]
    return (
        f"| {r['condition']} | {r['policy']} | {r['start_context']} | {r['conversation_usd']}（{p['conductor']} / {p['handoff']} / {p['restore']}） | "
        f"{t['inp']} / {t['out']} / {t['read']} / {t['w5']} / {t['w1h']} | {r['ndf_usd']} | {r['seconds']} | {len(r['waits'])}（{r['waits']}） | "
        f"{r['cuts']} | {r['restores'] or cd.UNKNOWN} | {r['excluded'] or ''} |"
    )


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("row")
    p.add_argument("--work", required=True)
    p.add_argument("--condition", required=True, choices=CONDITIONS)
    p.add_argument("--transcript", required=True)
    p.add_argument("--from", dest="from_")
    p.add_argument("--until")
    p.add_argument("--input-usd", type=float, default=4.0)
    a = ap.parse_args(argv)
    lo, hi = (cd.parse_ts(a.from_) if a.from_ else None), (cd.parse_ts(a.until) if a.until else None)
    if (a.from_ and lo is None) or (a.until and hi is None):
        emit(result(TOOL, "stopped", f"--from と --until は ISO 8601 の時刻: {a.from_} / {a.until}"), 2)
    cli = _cli()
    root = cd.toplevel(None)
    work = cd.recorded_work(a.work, None)
    try:
        row = trial_row(work, a.condition, a.transcript, cd.read_decisions(cd.decisions_repo(root)), cli._ledger(root), lo, hi, a.input_usd)
    except ValueError as e:
        emit(result(TOOL, "stopped", str(e)), 1)
    print(table_line(row))
    emit(result(TOOL, "ok", f"計測の回の 1 行を作った（{work}・{a.condition}）", [row]))


if __name__ == "__main__":
    main()
