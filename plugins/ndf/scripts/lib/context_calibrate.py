"""費用の見込みの較正（`calibrate`。#2262 の決定 5・6）。

判定の記録と会話の記録の全体を読む重い側で、hook は使わない（hook が使うのは `context_decisions` の記録の追記と
小さな JSON の読み書きだけ。決定 6）。
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict

import context_decisions as cd
import context_policy as cp
from usage_ledger import now_utc

CACHE_1H = 3600  # 呼び出しの間隔がこれを超えたら、1 時間のキャッシュが切れたとみなす


def _median_of(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _mark_times(marks: list[dict], event: str) -> list[float]:
    return sorted(t for m in marks if m.get("event") == event and (t := cd.parse_ts(m.get("at"))))


def _restore_sample(calls: list[dict], marks: list[dict], by_next: bool) -> tuple[int, int] | None:
    """ndf-next で始まり復元の印があるセッションの、最初の復元までの (直前のコンテキスト量, 呼び出し数)。"""
    restored = _mark_times(marks, cd.MARK_RESTORED)
    if not (by_next and restored):
        return None
    before = [c for c in calls if c["ts"] is not None and c["ts"] <= restored[0]]
    return (before[-1]["context"], len(before)) if before else None


def _handoff_sample(calls: list[dict], marks: list[dict]) -> tuple[int, int] | None:
    """最後の引継ぎの印から後の (呼び出し数, 出力 token の合計)。印が無ければ None。"""
    handoffs = _mark_times(marks, cd.MARK_HANDOFF)
    if not handoffs:
        return None
    after = [c for c in calls if c["ts"] is not None and c["ts"] >= handoffs[-1]]
    return len(after), sum(c["out"] for c in after)


def _stage_segments(calls: list[dict], rows: list[dict]) -> list[tuple[str, int, int]]:
    """「継続」の判定で区切った工程ごとの (工程, 呼び出し数, キャッシュ切れの数)。最後の区間と空の区間は除く。"""
    launches = sorted(
        ((t, r["stage"]) for r in rows if r.get("stage") and r.get("verdict") == cp.CONTINUE and (t := cd.parse_ts(r.get("at")))),
    )
    out = []
    for (start, stage), nxt in zip(launches, [t for t, _ in launches[1:]] + [None]):
        seg = [c for c in calls if c["ts"] is not None and c["ts"] >= start and (nxt is None or c["ts"] < nxt)]
        if not seg or nxt is None:
            continue
        gaps = sum(1 for a, b in zip(seg, seg[1:]) if b["ts"] - a["ts"] > CACHE_1H)
        out.append((stage, len(seg), gaps))
    return out


def calibrate(rows: list[dict], since: float | None = None) -> dict:
    """判定の記録と会話の記録から費用の見込みを作る（決定 5・6）。標本が 3 未満の値は None。"""
    rows = [r for r in cd.fill_work(rows) if since is None or (cd.parse_ts(r.get("at")) or 0) >= since]
    sessions = cd.sessions_of(rows)
    restore_q, restore_n, handoff_h, handoff_out = [], [], [], []
    stage_k: dict[str, list] = defaultdict(list)
    stage_m: dict[str, list] = defaultdict(list)
    spans = {}
    cache: dict[str, tuple] = {}
    for sid, s in sessions.items():
        cache[sid] = cd.read_calls(s["transcript"], set()) if s["transcript"] else ([], None, False)
        ts = [c["ts"] for c in cache[sid][0] if c["ts"]]
        if ts:
            spans[sid] = (min(ts), max(ts))
    by_session = cd.attach_marks([r for r in rows if r.get("event") in cd.MARKS], spans)
    for sid, s in sessions.items():
        calls, _, by_next = cache[sid]
        marks = by_session.get(sid, [])
        if (restore := _restore_sample(calls, marks, by_next)) is not None:
            restore_q.append(restore[0])
            restore_n.append(restore[1])
        if (handoff := _handoff_sample(calls, marks)) is not None:
            handoff_h.append(handoff[0])
            handoff_out.append(handoff[1])
        for stage, n, gaps in _stage_segments(calls, s["rows"]):
            for key in (stage, "*"):
                stage_k[key].append(n)
                stage_m[key].append(gaps)

    def med(values):
        return _median_of(values) if len(values) >= cp.MIN_SAMPLES else None

    return {
        "restore_context": med(restore_q),
        "restore_calls": med(restore_n),
        "handoff_calls": med(handoff_h),
        "handoff_output": med(handoff_out),
        "stage_calls": {k: _median_of(v) for k, v in stage_k.items()},
        "stage_expiries": {k: _median_of(v) for k, v in stage_m.items()},
        "samples": {"restore": len(restore_q), "handoff": len(handoff_h)} | {f"stage:{k}": len(v) for k, v in stage_k.items()},
        "at": now_utc(),
    }


def calibrate_repo(root: str | None, days: int = 30) -> tuple[dict, bool]:
    """そのリポジトリの直近 `days` 日の記録から見込みを較正して書く。(見込み, 書けたか)。"""
    repo = cd.decisions_repo(root)
    body = calibrate(cd.read_decisions(repo), since=time.time() - days * 86400)
    return body, cd.write_cost_estimates(body, repo)
