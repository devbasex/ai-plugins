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
    items, metrics, gaps = [], {"repo": repo}, []
    if cand:
        cm = cand["metrics"]
        metrics.update(
            {
                "targets": cm["candidates"],
                "by_route": cm["by_route"],
                "deferred": cm["deferred"],
                "limit": cm.get("limit"),
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
        app_items, app_metrics, app_gaps = _apply_report(app)
        items += app_items
        metrics.update(app_metrics)
        gaps += app_gaps
    rank_items, rank_metrics = report_sections(rank, app)
    items += rank_items
    metrics.update(rank_metrics)
    summary = " / ".join(f"{i['name']}: {i['value']}" for i in items)
    if gaps:  # 足し合わせられない値を 0 件の ok と読ませない
        summary = "足し合わせられない記録がある: " + gaps[0]["reason"] + " / " + summary
    emit(result(ctx.tool, "stopped" if gaps else "ok", summary, items, metrics, gaps=gaps))


def _apply_report(app) -> tuple[list, dict, list]:
    """apply の記録から、表示項目・集計値・足し合わせられない記録の欠けを組み立てる。"""
    am = app["metrics"]
    rnd = am.get("round") or {**am, "runs": 1}  # round を持たない前の版の記録は、最後の 1 回だけを数える
    applied, closed, waits = rnd["applied"], rnd["closed"], rnd.get("waits", [])
    verdicts, needs_approval, returned = _round_verdicts(am, rnd)
    gaps = []
    if rnd.get("lost") or (rnd["runs"] > 1 and "verdict_of" not in rnd):
        gaps.append(
            {
                "kind": "record",
                "name": "apply.json",
                "reason": "同じ回の前の apply の区分が記録に無く、区分の内訳・承認待ち・返した課題を足し合わせられない。"
                "candidates から打ち直す",
            }
        )
    metrics = {
        "verdicts": verdicts,
        "applied": len(applied),
        "closed": len(closed),
        "apply_runs": rnd["runs"],
        "returned": len(returned),
        "returned_numbers": returned,
        "skipped_changed": am["skipped_changed"],
        "needs_approval": needs_approval,
        "failed": am["failed"],
        "pending": am["pending"],
        "partial": am["partial"],
        "wait_count": len(waits),
        "wait_seconds": round(sum(w["seconds"] for w in waits), 1),
    }
    items = [
        {
            "kind": "section",
            "name": "区分の内訳",
            "result": "ok",
            "value": "・".join(f"{v} {c}" for v, c in verdicts.items() if c),
        },
        {
            "kind": "section",
            "name": "反映",
            "result": "partial" if am["partial"] else "ok",
            "value": f"直した {len(applied) - len(closed)} 件・閉じた {len(closed)} 件・返した {len(returned)} 件",
        },
        {"kind": "section", "name": "待った回数", "result": "ok", "value": f"{len(waits)} 回・計 {metrics['wait_seconds']:g} 秒"},
    ]
    return items, metrics, gaps


def next_round(prev, actions, buckets, closed, waits) -> dict:
    """同じ回（前の candidates 以降）の apply を足し合わせた記録。report がこれを読む。

    区分と結果は課題の番号ごとに持ち、同じ番号は後の apply の値を採る（承認後の打ち直しが前の承認待ちを消す）。
    前の apply の記録が番号ごとの値を持たない（前の版の記録）なら足し合わせられないので、`lost` を立てて回の終わりまで残す。
    """
    verdict_of = {**(prev.get("verdict_of") or {}), **{str(act["number"]): act["verdict"] for act in actions}}
    result_of = dict(prev.get("result_of") or {})
    for bucket, numbers in buckets.items():
        result_of.update({str(n): bucket for n in numbers})
    lost = bool(prev.get("lost")) or (prev.get("runs", 0) > 0 and "verdict_of" not in prev)
    return {
        "applied": sorted(set(prev.get("applied", [])) | set(buckets["applied"])),
        "closed": sorted(set(prev.get("closed", [])) | set(closed)),
        "waits": prev.get("waits", []) + waits,
        "runs": prev.get("runs", 0) + 1,
        "verdict_of": verdict_of,
        "result_of": result_of,
        **({"lost": True} if lost else {}),
    }


def _round_verdicts(am, rnd) -> tuple[dict, list, list]:
    """同じ回の区分の内訳・承認待ち・返した課題。番号ごとの記録（後の apply の値）から数え、無ければ最後の apply の値。"""
    if "verdict_of" not in rnd:
        return am["verdicts"], am["needs_approval"], am["returned"]
    verdicts = {v: 0 for v in am["verdicts"]}
    for v in rnd["verdict_of"].values():
        verdicts[v] = verdicts.get(v, 0) + 1
    by = rnd.get("result_of") or {}
    return verdicts, *(sorted(int(n) for n, b in by.items() if b == want) for want in ("needs_approval", "returned"))


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
        if b and b.get("empty"):
            return f"（境界なし・枠で容量 {b['capacity']} を使い切った）"
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
