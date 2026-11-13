"""レーンの様子（#2936 の `parallel.py status <計画名>`）: 計画の宣言・レーンの状態の一覧・ループの記録・計画の台帳を読み合わせ、
レーンごとに 1 行を返す。保存しない。読めない欄は None にし、例外を外へ出さない（I6）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import parallel_plan as pp
from parallel_files import RELAY

SELECTED = "選んだ"  # ループの記録の選んだ課題の印（`sprint_loop_record.SELECTED`。読むだけのため、書き手のモジュールを読み込まない）


def _ls(main: Path) -> dict[str, dict]:
    """一覧の行（名前 → 行）。読めなければ空。"""
    try:
        from parallel_repo import relay_proc

        got, res = relay_proc(main, "ls", "--json")
        if got.returncode != 0:
            return {}
        return {it["name"]: it for it in res.get("items") or [] if isinstance(it, dict) and it.get("name")}
    except Exception:  # noqa: BLE001  一覧が読めなくても他の欄は返す（I6）
        return {}


def _loop(main: Path, ln) -> Optional[dict]:
    """ループのレーンの記録（loop.json）。1 スプリントのレーン・読めないときは None。"""
    try:
        if ln.input.kind != "loop":
            return None
        import legacy_values
        from sprint_loop_names import LOOP_DIR

        rec = json.loads((ln.repo_dir(Path(main)) / LOOP_DIR / ln.input.loop / "loop.json").read_text(encoding="utf-8"))
        return legacy_values.normalize_laps(rec)
    except Exception:  # noqa: BLE001
        return None


def _lap_fields(rec: Optional[dict]) -> dict:
    """何回目のループか・今の課題・費用。取れない欄は None。"""
    out: dict = {"lap": None, "issues": None, "cost_usd": None}
    try:
        laps = [x for x in (rec or {}).get("laps") or [] if isinstance(x, dict)]
        if not laps:
            return out
        out["lap"] = laps[-1].get("n")
        picked = [r.get("number") for r in laps[-1].get("selection") or [] if isinstance(r, dict) and r.get("decision") == SELECTED]
        out["issues"] = picked or None
        costs = [float(x["cost"]) for x in laps if isinstance(x.get("cost"), (int, float))]
        out["cost_usd"] = round(sum(costs), 6) if costs else None
    except Exception:  # noqa: BLE001
        pass
    return out


def unanswered(main: Path, name: str, rows: list[dict]) -> list[dict]:
    """計画の台帳の答えていない問い（業務時間外に溜めた依頼も含む）。`rows` は `read_ledger_rows` の行。
    `status` の答えを待つレーンと、切り替えの判定（`parallel_switch`）の答えていない問いの数が使う。読めなければ例外を出す。"""
    import parallel_ledger as plg

    if "opened" in {r["kind"] for r in rows}:  # 耐久ワークフローが外した問いをチェックポイントで除く
        return plg.pending_questions(main, name)
    answered = {r["id"] for r in rows if r["kind"] == "answer"} | plg.settled_gates(rows)
    return [{"id": r["id"], "lane": r.get("by")} for r in rows if r["kind"] in plg.QUESTIONS and r["id"] not in answered]


def _ledger_view(main: Path, name: str) -> tuple[dict[str, str], set[str]]:
    """(レーン → 台帳でそのレーンが送った最後の行の時刻, 答えていない依頼を送ったレーン)。読めなければ空。"""
    try:
        import parallel_ledger as plg

        last: dict[str, str] = {}
        rows = plg.read_ledger_rows(main, name, 0).rows
        for r in rows:
            by, at = r.get("by"), r.get("at")
            if by and by != "organizer" and at and str(at) > last.get(by, ""):
                last[by] = str(at)
        pending = unanswered(main, name, rows)
        return last, {q.get("lane") for q in pending if isinstance(q, dict) and q.get("lane")}
    except Exception:  # noqa: BLE001
        return {}, set()


def _launch_waits(main: Path, name: str) -> dict[str, dict]:
    """チェックポイントのレーンの進行の起動の待ち（レーン → 待ち。#2999 の I14）。読めなければ空。"""
    try:
        import parallel_ledger as plg

        state, _ = plg.load_checkpoint(main, name)
        lanes = (state or {}).get("lanes") or {}
        return {n: st["waiting"] for n, st in lanes.items() if isinstance(st, dict) and isinstance(st.get("waiting"), dict)}
    except Exception:  # noqa: BLE001  待ちを読めなくても他の欄は返す（I6）
        return {}


def _table_view(main: Path, name: str) -> dict[str, dict]:
    """レーン → レーンの進行表の表示（順番表の状態・実行中のスプリントの進行表・行）。進行表が無いか読めなければ空。"""
    try:
        import parallel_tables as ptab

        return ptab.lane_view(main, name)
    except Exception as exc:  # noqa: BLE001  進行表を読めなくても他の欄は返す（I6）
        print(f"レーンの進行表を読めない: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {}


def lane_rows(main: Path, name: str, plan: pp.ParallelPlan) -> list[dict]:
    """計画のレーンごとの 1 行と、一覧にある計画の一時レーン（`<計画名>-rel…`・役割 helper）の 1 行。
    `progress` はレーンの進行表の表示（計画の進行表の無い取り込み前の計画では None）。
    起動を待っているレーンは `waiting` に理由とマシンの合計・このレーンの本数・上限を持つ（待っていなければ None）。"""
    rows, (last, waiting) = _ls(main), _ledger_view(main, name)
    launch_waits = _launch_waits(main, name)
    tables = _table_view(main, name)
    if tables:  # 計画の進行表があれば、答えていない依頼は進行表から読む（#3092 の受け入れ条件 16）
        waiting = {n for n, v in tables.items() if v["asking"]}
    out = []
    lanes = [(ln.name, ln) for ln in plan.lanes]
    helpers = sorted(n for n, r in rows.items() if n.startswith(f"{name}-rel") and r.get("role") == "helper")
    for n, ln in [*lanes, *((h, None) for h in helpers)]:
        st = (rows.get(n) or {}).get("state")
        asking = st == "awaiting_approval" or n in waiting
        out.append({
            "lane": n,
            "state": st,
            **_lap_fields(_loop(main, ln) if ln is not None else None),
            "last_at": last.get(n),
            "attach": f"python3 {RELAY} attach {n}" if asking else None,
            "waiting": launch_waits.get(n),
            "progress": tables.get(n),
        })  # fmt: skip
    return out


def status_summary(rows: list[dict]) -> str:
    count = lambda s: sum(1 for r in rows if r["state"] == s)  # noqa: E731
    waits = sum(1 for r in rows if r.get("waiting"))
    return (
        f"レーン {len(rows)} 本（動いている {count('running')}・承認待ち {count('awaiting_approval')}・止まった {count('stopped')}"
        f"・起動待ち {waits}）"
    )
