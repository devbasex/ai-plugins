"""upkeep_report.py: `upkeep.py report`。candidates・rank・apply の記録から完了報告の値を返す。"""

from __future__ import annotations

from step_result import EXIT_PRECONDITION, StepError, emit, git_root, result
from upkeep_gh import target_repo
from upkeep_rank_config import SLOT_SPLIT


def cmd_report(a, ctx):
    root = git_root(a.root)
    repo = target_repo(root, a.repo)
    sd = ctx.state_dir(a.state_dir, repo)
    cand, app, rank = (ctx.read_state(sd / f"{f}.json") for f in ("candidates", "apply", "rank"))
    if cand is None and app is None and rank is None:
        raise StepError(f"記録が無い（candidates も apply もまだ打っていない）: {sd}", EXIT_PRECONDITION)
    items, metrics = [], {"repo": repo}
    if cand:
        cm = cand["metrics"]
        metrics.update(
            {
                "targets": cm["candidates"],
                "by_route": cm["by_route"],
                "deferred": cm["deferred"],
                "notes": cm.get("notes", []),
                "empty_milestones": [i["name"] for i in cand["items"] if i["kind"] == "milestone"],
            }
        )
        items.append(
            {
                "kind": "section",
                "name": "対象",
                "result": "ok",
                "value": f"{cm['candidates']} 件（" + "・".join(f"{r} {c}" for r, c in cm["by_route"].items() if c) + "）"
                if cm["candidates"]
                else "0 件のため飛ばした",
            }
        )
    if app:
        am = app["metrics"]
        rnd = am.get("round") or {**am, "runs": 1}  # round を持たない前の版の記録は、最後の 1 回だけを数える
        applied, closed, waits = rnd["applied"], rnd["closed"], rnd.get("waits", [])
        metrics.update(
            {
                "verdicts": am["verdicts"],
                "applied": len(applied),
                "closed": len(closed),
                "apply_runs": rnd["runs"],
                "returned": len(am["returned"]),
                "skipped_changed": am["skipped_changed"],
                "needs_approval": am["needs_approval"],
                "failed": am["failed"],
                "pending": am["pending"],
                "partial": am["partial"],
                "wait_count": len(waits),
                "wait_seconds": round(sum(w["seconds"] for w in waits), 1),
            }
        )
        items += [
            {
                "kind": "section",
                "name": "区分の内訳",
                "result": "ok",
                "value": "・".join(f"{v} {c}" for v, c in am["verdicts"].items() if c),
            },
            {
                "kind": "section",
                "name": "反映",
                "result": "partial" if am["partial"] else "ok",
                "value": f"直した {len(applied) - len(closed)} 件・閉じた {len(closed)} 件・返した {len(am['returned'])} 件",
            },
            {"kind": "section", "name": "待った回数", "result": "ok", "value": f"{len(waits)} 回・計 {metrics['wait_seconds']:g} 秒"},
        ]
    rank_items, rank_metrics = report_sections(rank, app)
    items += rank_items
    metrics.update(rank_metrics)
    summary = " / ".join(f"{i['name']}: {i['value']}" for i in items)
    emit(result(ctx.tool, "ok", summary, items, metrics))


def report_sections(rank_out, app) -> tuple[list, dict]:
    """rank.json と apply の記録から、完了報告の順位付けの節と値を作る。"""
    m = (rank_out or {}).get("metrics") or {}
    if not m.get("milestones"):
        return [], {}
    split = [x["large_slot"]["issue"] for x in m["milestones"] if (x.get("large_slot") or {}).get("status") == SLOT_SPLIT]
    tables = ((app or {}).get("metrics") or {}).get("tables") or {}
    metrics = {
        "ranked": {x["title"]: len(x["rows"]) for x in m["milestones"]},
        "boundaries": {x["title"]: x["boundary"] for x in m["milestones"] if x.get("boundary")},
        "forward": m.get("forward", []),
        "backward": m.get("backward", []),
        "changes": m.get("changes", []),
        "split_needed": split,
        "waiting_decision": m.get("waiting_decision", []),
        "needs_detail": m.get("needs_detail", {}),
        "rank_excluded": m.get("excluded", []),
        "tables": tables,
    }

    def bnd(x):
        b = x.get("boundary")
        return f"（境界 #{b['number']}・Size の和 {b['size_sum']} / 容量 {b['capacity']}）" if b else ""

    def moves(xs):
        return "・".join(f"#{c['number']} {c['from'] or '未設定'}→{c['to']}（{c['decision']}）" for c in xs) or "なし"

    kinds = {"up": "上がった", "down": "下がった", "new": "入った", "removed": "外れた", "unknown_previous": "前回が不明"}
    change_text = "・".join(
        f"#{c['number']} {kinds[c['kind']]}（{c['milestone']}"
        + (f" {c['from_rank']}→{c['to_rank']}" if c["from_rank"] and c["to_rank"] else "")
        + (f"、{'・'.join(c['columns'])}" if c["columns"] else "")
        + "）"
        for c in m.get("changes", [])
    )
    items = [
        {
            "kind": "section",
            "name": "順位",
            "result": "ok",
            "value": "・".join(f"{x['title']} {len(x['rows'])} 件{bnd(x)}" for x in m["milestones"]),
        },
        {"kind": "section", "name": "前回からの変化", "result": "ok", "value": change_text or "なし"},
        {"kind": "section", "name": "前倒し", "result": "ok", "value": moves(m.get("forward", []))},
        {"kind": "section", "name": "後ろ倒し", "result": "ok", "value": moves(m.get("backward", []))},
        {"kind": "section", "name": "分割が要る", "result": "ok", "value": " ".join(f"#{n}" for n in split) or "なし"},
        {
            "kind": "section",
            "name": "人の判断待ち",
            "result": "ok",
            "value": " ".join(f"#{n}" for n in metrics["waiting_decision"]) or "なし",
        },
    ]
    if tables:
        items.append(
            {
                "kind": "section",
                "name": "順位の表",
                "result": "ok",
                "value": f"書いた {len(tables.get('written', []))} 本・変更なし {len(tables.get('unchanged', []))} 本",
            }
        )
    return items, metrics
