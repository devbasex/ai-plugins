"""upkeep_depth.py: 棚卸しの深さの設定・深さの記録・起動の判定・型の記録（#2315）。

    upkeep.py depth due review|overhaul [--loop L] [--lap N] [--no-issue] [--manual] [--open N]
    upkeep.py depth due diff [--loop L --lap N] --sprint S [--issues 1,2] [--manual]
    upkeep.py depth scope --sprint S
    upkeep.py depth record --depth diff|review|overhaul [--event ran|not_due|skipped] [--trigger a,b] [--run R]
                           [--report <統一 JSON レポート>] [--loop L] [--lap N] [--sprint S] [--cost USD] [--seconds S]
                           [--progress <プランの進行の記録> [--step <ステップの id>]]
    upkeep.py depth compare --since-ref <ref> --seconds-under S --cost-under USD [--count N] [--model M] [--depth D]
    upkeep.py causes record --classes <[{number, type, sub}] の JSON>

記録はメインディレクトリの `.ndf/backlog/` に置く（`depths.jsonl`・`causes.jsonl`。行を足すだけで書き換えない）。
`depth due` は起動するとき 0、起動しないとき 3（プランの実行条件の `skip_code`）を返し、起動しない判定を
`not_due` の行で、起動する判定を `due` の行で残す。記録が読めないときも 3 で返し、理由（`unreadable`）を行に残す。
頻度・閾値・上限は `.ndf/backlog.json` の `depths` で上書きでき、無ければ `DEFAULTS` を使う。
`depth due diff` は振り返りと差分の棚卸しの起動の判定（#2914 の決定 3）。開発ループの外（`--loop` が無い）は毎回起動し、
開発ループでは見送った回（範囲に入っていない `not_due` の行）+ 1 が `depths.diff.every` 以上で起動する。起動するときは
振り返りの範囲（見送った回と今の回・課題の番号・範囲の始まりの時刻）を `due` の行の `values.scope` に置き、
`depth scope --sprint S` がその範囲を返す（worker は深さの記録を直接読まない）。
`depth record --progress` はプランの進行の記録（supervise の `progress.jsonl`）の `--step`（既定 `refine`）の最後の
`kind: step` の行から秒・ドル・言語モデルの名前（`values.models`）を写す。読めなければ空のまま行を書く。`--seconds`・
`--cost` を渡せばその値を優先する。
`depth compare` は `<ref>` のコミットの時刻より後の実行の行のうち、秒とドルがそろい（`no_usage`）、`--model` を渡せば
言語モデルがその 1 つだけの（`model`）行を、同じ開発ループ・ループの 1 回（ループの外では同じスプリント）では最後の 1 行に
して（`duplicate`）、古い順に `--count` 件の中央値を基準と比べる。0 = 秒とドルの両方が基準を下回る / 1 = 下回らない /
3 = 件数に満たない（保留）/ 2 = 記録か ref を読めない。
"""

from __future__ import annotations

import copy
import json
import statistics
from pathlib import Path

import clock
import jsonio
import locks
import project_decl
import repo as repo_lib
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, git, git_root, result
import upkeep_rank_cmd as RC
from upkeep_depth_diff import _diff_rows, diff_due

TOOL = "backlog-refinement"
DEPTHS = ("diff", "review", "overhaul")
EVENTS = ("ran", "not_due", "skipped")
TRIGGERS = ("count", "open_growth", "carry", "no_issue", "manual", "created_over_closed", "open_rising", "cause_share", "no_loop")

# 既定の頻度・閾値・上限（設計の「棚卸しの設定」）。値は最初の 3 回の見直しの実測で見直す（決定 13）
DEFAULTS = {
    "diff": {"every": 3},  # 開発ループで振り返りと差分の棚卸しを走らせる間隔（ループの 1 回の数。#2914 の決定 2）
    "review": {"every": 3, "open_growth": 0.3, "carry_factor": 10, "issue_limit": 5, "usd": 6, "seconds": 1800},
    "overhaul": {"every": 12, "streak": 3, "cause_share": 0.3},
    "research": {"item_usd": 1, "item_seconds": 900, "grace": 300},
    "owner": {"ttl": 900, "beat": 120},
    "labels": {"needs": "needs", "seeds": "seeds", "policy": "policy"},
}

# 原因の型の既定（用語集の「原因の型」）。`defect: false` は不具合でない型（型の割合の分母から外す）
DEFAULT_CAUSE_TYPES = [
    {"id": "A", "name": "失敗の判定", "defect": True},
    {"id": "B", "name": "記録とログ", "defect": True},
    {"id": "C", "name": "回復と再開", "defect": True},
    {"id": "D", "name": "待ちと上限", "defect": True},
    {"id": "E", "name": "解決の時点", "defect": True},
    {"id": "F", "name": "並行の衝突", "defect": True},
    {"id": "G", "name": "機能と方針", "defect": False},
    {"id": "H", "name": "文書", "defect": False},
    {"id": "I", "name": "その他", "defect": True},
]


def backlog_dir(root) -> Path:
    """記録の置き場（メインディレクトリの `.ndf/backlog/`）。複数の worktree とセッションが同じ場所を見る。"""
    base = repo_lib.main_dir(root) or Path(root)
    d = Path(base) / ".ndf" / "backlog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings(root) -> dict:
    """`.ndf/backlog.json` の `depths` を既定へ重ねた設定。形が違えば終了コード 2。"""
    decl = RC.read_backlog_decl(root) or {}
    given = decl.get("depths") or {}
    if not isinstance(given, dict):
        raise StepError(".ndf/backlog.json の depths はオブジェクトで書く", EXIT_UNREADABLE)
    out = copy.deepcopy(DEFAULTS)
    for k, v in given.items():
        if k not in out or not isinstance(v, dict):
            raise StepError(f".ndf/backlog.json の depths.{k} は {' / '.join(DEFAULTS)} のオブジェクトで書く", EXIT_UNREADABLE)
        out[k].update(v)
    out["candidate_limit"] = RC.candidate_limit(None, decl)
    return out


def materials(root) -> dict | None:
    """`.ndf/project.json` の `materials`（材料の宣言）。無ければ None。読めない・形が違えば終了コード 2。"""
    data, err = project_decl.read_project_decl_checked(root)
    if err:
        raise StepError(f".ndf/project.json を読めない: {err}", EXIT_UNREADABLE)
    got = data.get("materials")
    if got is not None and not isinstance(got, dict):
        raise StepError(".ndf/project.json の materials はオブジェクトで書く", EXIT_UNREADABLE)
    return None if got is not None and set(got) == {"unknown"} else got  # 解析で決まらなかった（Unknown）は宣言なし


def cause_types(root) -> list[dict]:
    return (materials(root) or {}).get("cause_types") or DEFAULT_CAUSE_TYPES


def read_depth_rows(path: Path) -> list[dict]:
    try:
        return jsonio.read_jsonl(path, missing=[])
    except jsonio.JsonReadError as e:
        raise StepError(f"{path} を読めない: {e.detail or e}", EXIT_UNREADABLE)


def append_depth_row(path: Path, row: dict) -> None:
    locks.append_locked(path, json.dumps(row, ensure_ascii=False))


def _is_ran(row: dict, depth: str) -> bool:
    """`depth` を実行した（`event` が `ran` の）行か。"""
    return row.get("depth") == depth and row.get("event") == "ran"


def _last_ran(rows: list[dict], depth: str) -> int:
    """最後に `depth` を実行した行の位置。無ければ -1。"""
    return max((i for i, r in enumerate(rows) if _is_ran(r, depth)), default=-1)


def _ran(rows: list[dict], depth: str) -> list[dict]:
    return [r for r in rows if _is_ran(r, depth)]


def diffs_since(rows: list[dict], depth: str) -> int:
    """最後の `depth` の `ran` の行より後の差分の棚卸しの `ran` の行の数（前提 1 の数え方）。"""
    return len(_ran(rows[_last_ran(rows, depth) + 1 :], "diff"))


def cause_share(causes: list[dict], types: list[dict], open_numbers: set | None) -> tuple[str | None, float]:
    """不具合の型（`defect: false` を除く）のうち最も多い型と、その割合。同じ番号は最後の行を採る。"""
    defect = {t["id"] for t in types if t.get("defect", True)}
    last = {}
    for r in causes:
        if isinstance(r.get("number"), int):
            last[r["number"]] = r.get("type")
    rows = [t for n, t in last.items() if (open_numbers is None or n in open_numbers) and t in defect]
    if not rows:
        return None, 0.0
    top = max(set(rows), key=rows.count)
    return top, rows.count(top) / len(rows)


def _due_review(rows: list[dict], cfg: dict, open_now: int | None, no_issue) -> tuple[list, dict]:
    """review の起動条件（件数・課題なし・open の増加・持ち越し）。"""
    trig, values = [], {}
    c = cfg["review"]
    values["count"] = diffs_since(rows, "review")
    if values["count"] >= c["every"]:
        trig.append("count")
    if no_issue:
        trig.append("no_issue")
    prev = next((r for r in reversed(_ran(rows, "review")) if isinstance(r.get("open_after"), int)), None)
    if prev and open_now is not None and prev["open_after"] > 0:
        values["open_growth"] = round((open_now - prev["open_after"]) / prev["open_after"], 4)
        if values["open_growth"] >= c["open_growth"]:
            trig.append("open_growth")
    last_diff = next((r for r in reversed(_ran(rows, "diff"))), None)
    carry = ((last_diff or {}).get("values") or {}).get("carry")
    if isinstance(carry, int):
        values["carry"] = carry
        if carry > c["carry_factor"] * cfg["candidate_limit"]:
            trig.append("carry")
    return trig, values


def _due_overhaul(rows: list[dict], cfg: dict, causes, types, open_numbers) -> tuple[list, dict]:
    """overhaul の起動条件（件数・作成超過・open の連続増加・原因の偏り）。"""
    trig, values = [], {}
    c = cfg["overhaul"]
    values["count"] = diffs_since(rows, "overhaul")
    if values["count"] >= c["every"]:
        trig.append("count")
    reviews = _ran(rows[_last_ran(rows, "overhaul") + 1 :], "review")[-c["streak"] :]
    if len(reviews) >= c["streak"]:
        if all((r.get("created") or 0) > (r.get("closed") or 0) for r in reviews):
            trig.append("created_over_closed")
        if all((r.get("open_after") or 0) > (r.get("open_before") or 0) for r in reviews):
            trig.append("open_rising")
    top, share = cause_share(list(causes), list(types), open_numbers)
    values["cause_share"] = {"type": top, "share": round(share, 4)}
    if top and share > c["cause_share"]:
        trig.append("cause_share")
    return trig, values


def last_trigger(rows: list[dict], depth: str) -> list | None:
    """最後の `ran` の行より後で、`depth` を起動した判定（`due` の行）の条件。無ければ None。"""
    for r in reversed(rows):
        if r.get("depth") != depth:
            continue
        if r.get("event") == "ran":
            return None
        if r.get("event") == "due":
            return list(r.get("trigger") or [])
    return None


def due(
    depth: str, rows: list[dict], cfg: dict, *, open_now: int | None, no_issue=False, manual=False, causes=(), types=(), open_numbers=None
):
    """(起動した条件, 判定に使った値)。条件が空なら起動しない。"""
    if depth == "review":
        trig, values = _due_review(rows, cfg, open_now, no_issue)
    else:
        trig, values = _due_overhaul(rows, cfg, causes, types, open_numbers)
    if manual:
        trig.insert(0, "manual")
    if open_now is not None:
        values["open"] = open_now
    return trig, values


def _scope(a) -> dict:
    return {"loop": getattr(a, "loop", None), "lap": getattr(a, "lap", None), "sprint": getattr(a, "sprint", None)}


def _open_now(root, a) -> tuple[int | None, set | None]:
    """open の件数と番号。--open を渡せば GitHub を読まない（番号は分からない）。"""
    if getattr(a, "open", None) is not None:
        return a.open, None
    from upkeep_gh import Gh, list_issues, target_repo

    issues = list_issues(Gh(target_repo(root, getattr(a, "repo", None))), "state=open")
    return len(issues), {i["number"] for i in issues}


def _cmd_due_diff(a, d: Path, base: dict, cfg: dict, rows: list[dict]):
    """振り返りと差分の棚卸しの起動の判定。起動しないときは要約に次に走る時点を載せる（I7）。"""
    trig, values = diff_due(rows, cfg, {k: base[k] for k in ("loop", "lap", "sprint", "issues")}, manual=a.manual)
    append_depth_row(d / "depths.jsonl", {**base, "event": "due" if trig else "not_due", "trigger": trig, "values": values})
    m = {"trigger": trig, "values": values}
    if not trig:
        what = f"前に走らせてから {values['count']} 回目。次は {values['next_in']} 回後"
        emit(result(TOOL, "stopped", f"振り返りと差分の棚卸しは起動しない（{what}）", [], m), EXIT_PRECONDITION)
    laps = len(values["scope"]["laps"])
    emit(result(TOOL, "ok", f"振り返りと差分の棚卸しを起動する（{'・'.join(trig)}。範囲はループの 1 回 {laps} 回分）", [], m))


def cmd_due(a):
    root = git_root(a.root)
    d = backlog_dir(root)
    base = {"at": clock.now_iso("utc"), "depth": a.depth, **_scope(a), "run": None}
    if a.depth == "diff":
        base["issues"] = a.issues or []
    try:
        cfg = settings(root)
        rows = read_depth_rows(d / "depths.jsonl")
        if a.depth == "diff":
            _cmd_due_diff(a, d, base, cfg, rows)
        causes = read_depth_rows(d / "causes.jsonl") if a.depth == "overhaul" else []
        types = cause_types(root) if a.depth == "overhaul" else []
        open_now, open_numbers = _open_now(root, a)
    except StepError as e:
        append_depth_row(
            d / "depths.jsonl", {**base, "event": "not_due", "trigger": [], "values": {"reason": "unreadable", "detail": str(e)}}
        )
        emit(result(TOOL, "stopped", f"記録を読めないため起動しない: {e}", [], {"reason": "unreadable"}), EXIT_PRECONDITION)
    trig, values = due(
        a.depth, rows, cfg, open_now=open_now, no_issue=a.no_issue, manual=a.manual, causes=causes, types=types, open_numbers=open_numbers
    )
    if not trig:
        append_depth_row(d / "depths.jsonl", {**base, "event": "not_due", "trigger": [], "values": values})
        emit(
            result(
                TOOL,
                "stopped",
                f"{a.depth} は起動しない（{json.dumps(values, ensure_ascii=False)}）",
                [],
                {"trigger": [], "values": values},
            ),
            EXIT_PRECONDITION,
        )
    # 起動する判定も行に残す（`report --depth` と見直しの手順が、起動した条件をここから読む）
    append_depth_row(d / "depths.jsonl", {**base, "event": "due", "trigger": trig, "values": values})
    emit(result(TOOL, "ok", f"{a.depth} を起動する（{'・'.join(trig)}）", [], {"trigger": trig, "values": values}))


def cmd_scope(a):
    """スプリントの振り返りの範囲（最後の diff の `due` の行の `values.scope`）。無ければ 3。"""
    rows = read_depth_rows(backlog_dir(git_root(a.root)) / "depths.jsonl")
    got = next((r for _, r in reversed(_diff_rows(rows, "due")) if r.get("sprint") == a.sprint), None)
    scope = ((got or {}).get("values") or {}).get("scope")
    if not scope:
        emit(result(TOOL, "stopped", f"スプリント {a.sprint} の振り返りの範囲が無い", [], {"sprint": a.sprint}), EXIT_PRECONDITION)
    emit(result(TOOL, "ok", f"振り返りの範囲: ループの 1 回 {len(scope['laps'])} 回分・課題 {len(scope['issues'])} 件", scope["laps"], scope))


def _report_metrics(path: str | None) -> dict:
    if not path:
        return {}
    got = jsonio.read(path, missing={}, broken={}, want=dict)
    return got.get("metrics") or {}


def _state_values(a, root) -> dict:
    """差分の棚卸しの値（open・持ち越し・閉じた件数）を upkeep の記録（candidates.json・apply.json）から写す。"""
    from upkeep_gh import target_repo

    try:
        repo = target_repo(root, getattr(a, "repo", None))
    except StepError:
        return {}
    sd = a.state_dir_of(getattr(a, "state_dir", None), repo)
    cands = (jsonio.read(sd / "candidates.json", missing={}, broken={}, want=dict).get("metrics")) or {}
    applied = (jsonio.read(sd / "apply.json", missing={}, broken={}, want=dict).get("metrics")) or {}
    out = {}
    if isinstance(cands.get("open"), int):
        out["open_before"] = cands["open"]
    if isinstance(cands.get("deferred"), list):
        out["carry"] = len(cands["deferred"])
    if isinstance(applied.get("closed"), list):
        out["closed"] = len(applied["closed"])
    return out


def _step_usage(path: str | None, step: str) -> dict:
    """プランの進行の記録の `step` の最後の `kind: step` の行の秒・ドル・言語モデル（#2315 の決定 2・決定 4）。読めなければ空。"""
    if not path:
        return {}
    rows = jsonio.read_jsonl(path, missing=[], broken=[], skip_broken=True)
    last = next((r for r in reversed(rows) if r.get("kind") == "step" and r.get("step") == step), None)
    if last is None:
        return {}
    out = {k: last[k] for k in ("seconds", "cost") if isinstance(last.get(k), (int, float)) and not isinstance(last[k], bool)}
    if isinstance(last.get("models"), dict):
        out["models"] = sorted(last["models"])
    return out


def cmd_record(a):
    root = git_root(a.root)
    d = backlog_dir(root)
    trig = [t for t in (a.trigger or "").split(",") if t]
    bad = [t for t in trig if t not in TRIGGERS]
    if bad:
        raise StepError(f"--trigger は {' / '.join(TRIGGERS)} のどれか: {', '.join(bad)}", EXIT_UNREADABLE)
    rep = _report_metrics(a.report)
    st = _state_values(a, root) if a.depth == "diff" and a.event == "ran" else {}
    row = {
        "at": clock.now_iso("utc"),
        "depth": a.depth,
        "event": a.event,
        "trigger": trig or rep.get("trigger") or [],
        "values": {k: st[k] for k in ("carry",) if k in st},
        **_scope(a),
        "run": a.run,
    }
    for k in ("open_before", "open_after", "closed", "created"):
        v = rep.get(k, st.get(k))
        row[k] = len(v) if isinstance(v, list) else v
    for k in ("skipped_aspects", "order", "research"):
        if rep.get(k):
            row[k] = rep[k]
    usage = _step_usage(getattr(a, "progress", None), getattr(a, "step", None) or "refine")
    if "models" in usage:
        row["values"]["models"] = usage["models"]
    row.update(
        cost=a.cost if a.cost is not None else usage.get("cost"),
        seconds=a.seconds if a.seconds is not None else usage.get("seconds"),
        report=a.report,
    )
    append_depth_row(d / "depths.jsonl", row)
    emit(result(TOOL, "ok", f"深さの記録へ {a.depth} の {a.event} を足した", [row], {"path": str(d / "depths.jsonl")}))


def _period_key(r: dict, k: int):
    """同じループの 1 回を 1 回と数える鍵。開発ループの外ではスプリント、どちらも無ければ行ごとに別。"""
    if r.get("loop") is not None:
        return ("loop", r["loop"], r.get("lap"))
    if r.get("sprint") is not None:
        return ("sprint", r["sprint"])
    return ("row", k)


def compare_rows(rows: list[dict], since, *, depth: str, model: str | None) -> tuple[list[dict], list[dict]]:
    """(使える行を古い順, 外した行と理由)。秒・ドル・言語モデルで絞ってから同じループの 1 回の重複を除く（#2315 の決定 5）。"""
    picked, excluded = [], []
    for k, r in enumerate(rows):
        at = clock.parse(r.get("at"), naive="utc")
        if not _is_ran(r, depth) or at is None or at <= since:
            continue
        if not all(isinstance(r.get(x), (int, float)) and not isinstance(r.get(x), bool) for x in ("seconds", "cost")):
            excluded.append((r, "no_usage"))
        elif model is not None and ((r.get("values") or {}).get("models")) != [model]:
            excluded.append((r, "model"))
        else:
            picked.append((k, r))
    last = {_period_key(r, k): k for k, r in picked}
    kept = [r for k, r in picked if last[_period_key(r, k)] == k]
    excluded += [(r, "duplicate") for k, r in picked if last[_period_key(r, k)] != k]
    return kept, [{**_brief(r), "result": "excluded", "reason": why} for r, why in excluded]


def _brief(r: dict) -> dict:
    return {k: r.get(k) for k in ("at", "sprint", "loop", "lap", "seconds", "cost")}


def cmd_compare(a):
    root = git_root(a.root)
    p = git(root, "log", "-1", "--format=%cI", a.since_ref, check=False)
    since = clock.parse(p.stdout.strip(), naive="utc") if p.returncode == 0 else None
    if since is None:
        raise StepError(f"ref を読めない: {a.since_ref}", EXIT_UNREADABLE)
    kept, excluded = compare_rows(read_depth_rows(backlog_dir(root) / "depths.jsonl"), since, depth=a.depth, model=a.model)
    used = kept[: a.count]
    baseline = {"seconds": a.seconds_under, "cost": a.cost_under}
    m = {"count": len(used), "needed": a.count, "median_seconds": None, "median_cost": None, "baseline": baseline, "delta": None}
    items = [{**_brief(r), "result": "used"} for r in used] + excluded
    if len(used) < a.count:
        out = result(TOOL, "stopped", f"{a.depth} の行が {len(used)} / {a.count} 件で、判定を保留する", items, {**m, "judged": False})
        emit(out, EXIT_PRECONDITION)
    med = {"seconds": statistics.median(r["seconds"] for r in used), "cost": statistics.median(r["cost"] for r in used)}
    m.update(median_seconds=med["seconds"], median_cost=med["cost"], judged=True)
    m["delta"] = {k: round(med[k] - baseline[k], 4) for k in baseline}
    under = all(med[k] < baseline[k] for k in baseline)
    summary = f"{a.depth} の中央値 {med['seconds']} 秒・{med['cost']} ドル（基準 {a.seconds_under} 秒・{a.cost_under} ドル）"
    emit(result(TOOL, "ok" if under else "stopped", summary + ("は基準を下回る" if under else "は基準を下回らない"), items, m),
         None if under else EXIT_VIOLATION)  # fmt: skip


def cmd_causes(a):
    root = git_root(a.root)
    d = backlog_dir(root)
    rows = jsonio.read(a.classes, missing=None, broken=None)
    if not isinstance(rows, list):
        raise StepError(f"--classes は [{{number, type, sub}}] の配列で書く: {a.classes}", EXIT_UNREADABLE)
    ids = {t["id"] for t in cause_types(root)}
    bad = [r for r in rows if not isinstance(r, dict) or not isinstance(r.get("number"), int) or r.get("type") not in ids]
    bad += [r for r in rows if isinstance(r, dict) and any(s not in ids for s in r.get("sub") or [])]
    if bad:
        raise StepError(f"型が cause_types に無い行がある（{len(bad)} 件）: {json.dumps(bad[:3], ensure_ascii=False)}", EXIT_UNREADABLE)
    at = clock.now_iso("utc")
    for r in rows:
        append_depth_row(
            d / "causes.jsonl", {"number": r["number"], "type": r["type"], "sub": list(r.get("sub") or []), "at": at, "depth": a.depth}
        )
    emit(result(TOOL, "ok", f"型の記録へ {len(rows)} 行を足した", [], {"path": str(d / "causes.jsonl"), "rows": len(rows)}))


def _numbers(s: str) -> list[int]:
    return [int(x.lstrip("#")) for x in s.replace(" ", ",").split(",") if x.strip()]


def add_depth_parsers(sub, common, state_dir) -> None:
    dp = sub.add_parser("depth").add_subparsers(dest="depth_cmd", required=True)
    du = dp.add_parser("due", parents=[common])
    du.add_argument("depth", choices=DEPTHS)
    du.add_argument("--loop", default=None)
    du.add_argument("--lap", type=int, default=None)
    du.add_argument("--sprint", default=None, help="diff: 今のスプリントの名前（振り返りの範囲に置く）")
    du.add_argument("--issues", type=_numbers, default=None, help="diff: 今の回の課題の番号（カンマ区切り）")
    du.add_argument("--no-issue", action="store_true", help="選べる課題が尽きた（loop select の review_first）")
    du.add_argument("--manual", action="store_true", help="人が呼んだ（ループの外の単独の起動）")
    du.add_argument("--open", type=int, default=None, help="open の件数（省くと GitHub から数える）")
    du.set_defaults(func=cmd_due)
    ds = dp.add_parser("scope", parents=[common])
    ds.add_argument("--sprint", required=True)
    ds.set_defaults(func=cmd_scope)
    dr = dp.add_parser("record", parents=[common])
    dr.add_argument("--depth", choices=DEPTHS, required=True)
    dr.add_argument("--event", choices=EVENTS, default="ran")
    dr.add_argument("--trigger", default=None)
    dr.add_argument("--run", default=None)
    dr.add_argument("--report", default=None, help="統一 JSON レポート（open の前後・閉じた・起票した・飛ばした側面を写す）")
    dr.add_argument("--loop", default=None)
    dr.add_argument("--lap", type=int, default=None)
    dr.add_argument("--sprint", default=None)
    dr.add_argument("--cost", type=float, default=None)
    dr.add_argument("--seconds", type=float, default=None)
    dr.add_argument(
        "--progress", default=None, help="プランの進行の記録（progress.jsonl）。--step の最後の行から秒・ドル・言語モデルを写す"
    )
    dr.add_argument("--step", default="refine", help="--progress で読むステップの id")
    dr.set_defaults(func=cmd_record, state_dir_of=state_dir)
    dc = dp.add_parser("compare", parents=[common])
    dc.add_argument("--since-ref", required=True, help="この ref のコミットの時刻より後の行だけを使う（リリースした正式版のタグ）")
    dc.add_argument("--seconds-under", type=float, required=True)
    dc.add_argument("--cost-under", type=float, required=True)
    dc.add_argument("--count", type=int, default=3)
    dc.add_argument("--model", default=None, help="values.models がこの 1 つだけの行に限る")
    dc.add_argument("--depth", choices=DEPTHS, default="diff")
    dc.set_defaults(func=cmd_compare)
    cp = sub.add_parser("causes").add_subparsers(dest="causes_cmd", required=True)
    cr = cp.add_parser("record", parents=[common])
    cr.add_argument("--classes", required=True)
    cr.add_argument("--depth", choices=("review", "overhaul"), default="review")
    cr.set_defaults(func=cmd_causes)
