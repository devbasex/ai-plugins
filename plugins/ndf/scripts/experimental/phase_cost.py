#!/usr/bin/env python3
"""phase_cost.py: フェーズとステップの粒度を測る表を出す（試行。#773）。

    python3 phase_cost.py [--state-glob GLOB ...] [--session ID ...] [--window-limit N]

出すもの:
- supervise.py のステップ（`<plan>-state/state.json` の `log`）の種類ごとの件数と、費用・所要・往復・
  1 往復あたりの読み込み（`(input + cache_read + cache_write) / turns`）の中央値と、`cache_write` の最大
  （新しい文脈は 1 度だけキャッシュへ書くので、最大充填の代わりに読む。`claude -p` の会話は残らない）
- `--session` を渡したときは、Agent で起動した記録の層・フェーズごとの件数・実作業の中央値・
  最大充填の最大・劣化の目安（`--window-limit`）を超えた件数・実作業 < 固定費の件数、
  worker の固定費の合計が自身の実作業を上回る supervisor の件数

人が読む表の後に step_result の 1 行の JSON。読むだけで、書き込まない。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import transcript_agents  # noqa: E402
from step_result import emit, result  # noqa: E402


def default_globs() -> list[str]:
    """既定で読む状態ディレクトリ。状態の置き場所（`NDF_SV_STATE_DIR` → `${XDG_STATE_HOME:-~/.local/state}/ndf/sv`）の下の
    `<置き場所>/*/plan-*-state` と、一時ディレクトリの下のプランの実体 `<置き場所>/plan-*`（#1142）、
    古い置き場所の `/tmp/ndf-sv/*/plan-*-state`。同じ実体は 1 度だけ読む。"""
    base = (Path(os.environ["NDF_SV_STATE_DIR"]) if os.environ.get("NDF_SV_STATE_DIR") else
            Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ndf" / "sv")
    return [str(base / "*" / "plan-*-state"), str(base / "plan-*"), "/tmp/ndf-sv/*/plan-*-state"]


def median(values: list) -> int | float | str:
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    m = statistics.median(values)
    return round(m, 2) if isinstance(m, float) and m < 100 else int(m)


def read_steps(patterns: str | list[str]) -> tuple[list[dict], int]:
    """ステップの記録を返す。読めた計画の数も返す。同じ実体（シンボリックリンクの先）は 1 度だけ読む。"""
    steps: list[dict] = []
    plans = 0
    seen: set[str] = set()
    found = [d for pat in ([patterns] if isinstance(patterns, str) else patterns) for d in sorted(glob.glob(pat))]
    for d in found:
        real = os.path.realpath(d)
        if real in seen:
            continue
        seen.add(real)
        try:
            state = json.loads((Path(d) / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plans += 1
        for entry in state.get("log") or []:
            if isinstance(entry, dict):
                steps.append({**entry, "plan": Path(d).name})
    return steps, plans


def step_rows(steps: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in steps:
        groups[(s.get("type") or "-", s.get("id") or "-")].append(s)
    rows = []
    for (kind, sid), items in groups.items():
        llm = [i["llm"] for i in items if isinstance(i.get("llm"), dict)]
        per_turn = [
            (l.get("input", 0) + l.get("cache_read", 0) + l.get("cache_write", 0)) / l["turns"]
            for l in llm if l.get("turns")
        ]
        rows.append({
            "type": kind, "step": sid, "count": len(items),
            "failed": sum(1 for i in items if i.get("exit") not in (0, None)),
            "cost_median": median([l.get("cost") for l in llm]),
            "cost_sum": round(sum(l.get("cost") or 0 for l in llm), 2),
            "seconds_median": median([i.get("seconds") for i in items]),
            "turns_median": median([l.get("turns") for l in llm]),
            "read_per_turn_median": median(per_turn),
            "cache_write_max": max((l.get("cache_write") or 0 for l in llm), default="-"),
        })
    order = {"work": 0, "judge": 1, "pr": 2, "run": 3}
    rows.sort(key=lambda r: (order.get(r["type"], 9), -r["count"], r["step"]))
    return rows


def agent_rows(records: list, window_limit: int) -> tuple[list[dict], dict]:
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        if r.layer != "conductor" and r.responses >= 3:
            groups[(r.layer, r.role)].append(r)
    rows = []
    for (layer, role), items in groups.items():
        rows.append({
            "layer": layer, "role": role, "count": len(items),
            "fixed_median": median([r.fixed for r in items]),
            "work_median": median([r.work for r in items]),
            "peak_max": max((r.peak or 0) for r in items),
            "over_limit": sum(1 for r in items if (r.peak or 0) > window_limit),
            "work_below_fixed": sum(
                1 for r in items if r.work is not None and r.fixed is not None and r.work < r.fixed
            ),
        })
    vocab = transcript_agents.POSTS + transcript_agents.TASKS + (transcript_agents.OTHER,)
    rows.sort(key=lambda r: (transcript_agents.LAYERS.index(r["layer"]), vocab.index(r["role"])
                             if r["role"] in vocab else len(vocab)))
    by_parent: dict[str, list] = defaultdict(list)
    for r in records:
        if r.layer == "worker" and r.parent_agent_id:
            by_parent[r.parent_agent_id].append(r)
    supervisors = [r for r in records if r.layer == "supervisor"]
    overuse = sum(
        1 for s in supervisors
        if by_parent.get(s.agent_id)
        and (s.fixed or 0) + sum(w.fixed or 0 for w in by_parent[s.agent_id]) > (s.work or 0)
    )
    workers = [r for r in records if r.layer == "worker"]
    return rows, {
        "supervisors": len(supervisors),
        "supervisors_with_workers": sum(1 for s in supervisors if by_parent.get(s.agent_id)),
        "supervisors_overusing_workers": overuse,
        "workers": len(workers),
        "workers_per_supervisor_median": median(
            [len(by_parent.get(s.agent_id, [])) for s in supervisors]
        ),
        "unphased_supervisors": transcript_agents.unphased_supervisors(records),
    }


def table(header: list[str], keys: list[str], rows: list[dict]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-glob", action="append", default=None,
                    help="状態ディレクトリの glob（繰り返せる）。既定は default_globs() の 3 つ")
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--window-limit", type=int, default=200_000)
    args = ap.parse_args()

    steps, plans = read_steps(args.state_glob or default_globs())
    srows = step_rows(steps)
    lines = [f"## supervise.py のステップ（計画 {plans} 件・ステップ {len(steps)} 件）", ""]
    lines += table(
        ["種類", "ステップ", "件数", "失敗", "費用の中央値", "費用の合計", "所要（秒）の中央値",
         "往復の中央値", "1 往復の読み込みの中央値", "cache_write の最大"],
        ["type", "step", "count", "failed", "cost_median", "cost_sum", "seconds_median",
         "turns_median", "read_per_turn_median", "cache_write_max"],
        srows,
    )
    metrics: dict = {"plans": plans, "steps": len(steps)}
    arows: list[dict] = []
    if args.session:
        records = transcript_agents.read_sessions(args.session, counter={})
        arows, summary = agent_rows(records, args.window_limit)
        metrics.update(summary)
        lines += ["", f"## Agent の記録（セッション {len(args.session)} 件・応答 3 以上）", ""]
        lines += table(
            ["層", "フェーズ・作業", "件数", "固定費の中央値", "実作業の中央値", "最大充填の最大",
             f"最大充填 > {args.window_limit}", "実作業 < 固定費"],
            ["layer", "role", "count", "fixed_median", "work_median", "peak_max",
             "over_limit", "work_below_fixed"],
            arows,
        )
        lines += ["", "| 指標 | 値 |", "| --- | ---: |"]
        lines += [f"| {k} | {v} |" for k, v in summary.items()]
    print("\n".join(lines))
    print()
    emit(result(
        "phase_cost", "ok",
        f"ステップ {len(steps)} 件（計画 {plans} 件）と Agent の記録 {len(args.session)} セッションを集計した",
        items=[{"kind": "step", **r} for r in srows] + [{"kind": "agent", **r} for r in arows],
        metrics=metrics,
    ))


if __name__ == "__main__":
    main()
