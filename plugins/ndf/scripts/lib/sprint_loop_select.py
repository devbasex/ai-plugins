"""sprint_loop_select.py: 開発ループの選定（#2089 の決定 2。loop select / loop exclude）。"""

from __future__ import annotations

import shlex

import project_mvv as pm
import sprint_mvv

from sprint_loop_record import (
    SPRINT_STATE,
    IN_PROGRESS,
    SELECTED,
    DROPPED,
    SLOT_FIT,
    OVER_SCALE,
    PAUSE_RANK,
    Env,
    loop_dir,
    write_record,
    open_record,
    current_lap,
    boundary_stop,
    resume_command,
    stopped_result,
    stop_and_report,
    update_handoff,
    selected_issues,
)

from sprint_loop_gate import (
    loop_ok,
    refusal_order,
)


def candidates_of(ms: dict, max_issues: int) -> list[dict]:
    """マイルストーンの候補（順位の順）: 境界までの行（境界が無ければ先頭から max_issues 件）＋大きい課題の枠の 1 切れ（当てたとき）
    ＋品質の枠の課題（宣言があるとき）。"""
    rows = sorted(ms.get("rows", []), key=lambda r: r["rank"])
    b = ms.get("boundary")
    if b:
        idx = next((i for i, r in enumerate(rows) if r["number"] == b["number"]), len(rows) - 1)
        nums = {r["number"] for r in rows[: idx + 1]}
    else:
        nums = {r["number"] for r in rows[:max_issues]}
    slot = ms.get("large_slot") or {}
    if slot.get("status") == SLOT_FIT and slot.get("slice"):
        nums.add(slot["slice"])
    nums |= set((ms.get("quality_slot") or {}).get("issues") or [])
    return [r for r in rows if r["number"] in nums]


def exclusion_reason(env: Env, rec: dict, row: dict, lap_n: int, lap_created: set) -> str | None:
    n = row["number"]
    if row.get("waiting_decision"):
        return "人の判断待ち（棚卸し）"
    for w in rec.get("waiting", []):
        if w["number"] != n:
            continue
        if not w.get("answer"):
            return "人の判断待ち"
        if w["answer"] == "rejected":
            return "差し戻し"
        if w["answer"] == "approved" and not w.get("merged_at"):
            if w.get("pr") and env.pr_state(int(w["pr"])) == "MERGED":
                w["merged_at"] = env.now()
            else:
                return "設計 PR がマージ待ち"
    for e in rec.get("excluded", []):
        if e["number"] == n and e.get("lap") == lap_n:
            return e["reason"]
    if n in lap_created:
        return "周回の中で起票された（次の周回の棚卸しの後に選ぶ）"
    if row.get("size") == OVER_SCALE:
        return "容量（尺度を超える）"
    return None


def _select_candidate_rows(cands: list[dict], title: str, max_issues: int, reason_for) -> tuple[list[int], list[dict]]:
    """1 マイルストーンの候補の採否。選んだ番号と選定の行を返す。reason_for は外す理由（外さなければ None）を返す。
    max_issues の枠は品質の枠の課題に先に割り当て、残りを順位の順に埋める（品質の枠の最低件数を容量で削らない）。"""
    whys = [reason_for(r) for r in cands]  # 候補ごとに 1 回、順位の順に呼ぶ（exclusion_reason の副作用の回数と順序を保つ）
    open_ = [i for i, w in enumerate(whys) if w is None]
    take = set(([i for i in open_ if cands[i].get("quality_slot")] + [i for i in open_ if not cands[i].get("quality_slot")])[:max_issues])
    picked, here = [], []
    for i, r in enumerate(cands):
        why = whys[i] if whys[i] is not None or i in take else "容量"
        if why is None:
            picked.append(r["number"])
            reason = "品質の枠" if r.get("quality_slot") else "順位"
            here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": SELECTED, "reason": reason})
        else:
            here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": why})
    return picked, here


def choose_issues(env: Env, rec: dict, rank: dict, lap_n: int, descriptions: dict, lap_created: set = frozenset()) -> dict:
    """選定（決定 2）。入力は rank の出力・記録・マイルストーンの説明だけ。戻り値は
    {"milestone", "selection", "candidates"}。選べたマイルストーンが無ければ milestone は None。"""
    m = rank.get("metrics") or {}
    by_title = {ms["title"]: ms for ms in m.get("milestones", [])}
    order = [t for t in m.get("order", []) if t in by_title and (not rec.get("milestones") or t in rec["milestones"])]
    approvals = {(a["milestone"], a["sha256"]) for a in rec.get("sprint_mvv_approvals", [])}
    rows_out, total = [], 0
    for title in order:
        ms = by_title[title]
        cands = candidates_of(ms, rec["max_issues"])
        total += len(cands)
        text = sprint_mvv.mvv_sections(descriptions.get(title) or "")
        unapproved = text is not None and (title, pm.sha256_text(text)) not in approvals
        picked, here = _select_candidate_rows(
            cands,
            title,
            rec["max_issues"],
            (lambda r: "スプリント MVV が未承認") if unapproved else (lambda r: exclusion_reason(env, rec, r, lap_n, lap_created)),
        )
        rows_out += here
        if picked:
            cand_nums = {r["number"] for r in cands}
            rest = [r for r in sorted(ms.get("rows", []), key=lambda r: r["rank"]) if r["number"] not in cand_nums]
            rows_out += [
                {"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": "順位（切り出しの境界の外）"}
                for r in rest
            ]
            return {"milestone": title, "selection": rows_out, "candidates": total, "sprint_mvv": text}
    return {"milestone": None, "selection": rows_out, "candidates": total, "sprint_mvv": None}


def cmd_select(env: Env, name: str) -> dict:
    with open_record(env, name) as (rec, before):
        if rec.get("status") == "stopped":
            return stopped_result(env, rec, f"ループ {name} は止まっている。再開のコマンド（loop start）で理由を判定し直す")
        if current_lap(rec):
            return refusal_order("進行中の周回がある（I2）。周回を締めてから選ぶ")
        stop = boundary_stop(env, rec)
        if stop:
            return stop_and_report(env, rec, before, stop, f"止まる条件に当たった（{stop['condition']}）")
        return run_selection(env, rec, before, len(rec["laps"]) + 1)


def run_selection(env: Env, rec: dict, before: dict, n: int, lap: dict | None = None) -> dict:
    code, rank = env.rank(env.main_dir())
    if code not in (0, PAUSE_RANK):
        return refusal_order(f"upkeep.py rank が {code} を返した。選定を推さない: {rank.get('summary', '')}")
    try:
        descriptions = env.milestones(rec.get("repo") or "")
    except OSError as e:
        return refusal_order(str(e))
    got = choose_issues(env, rec, rank, n, descriptions, set((lap or {}).get("created") or []))
    if got["milestone"] is None:
        if lap is not None:
            rec["laps"].remove(lap)  # スプリントを組む前の周回は取り下げ、除外の一覧（excluded の lap）を次の選定に残す
        cond = "no_issue" if got["candidates"] == 0 else "outside_mvv"
        reasons: dict = {}
        for r in got["selection"]:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        values = (
            {"milestones": rec.get("milestones") or (rank.get("metrics") or {}).get("order", [])}
            if cond == "no_issue"
            else {"reasons": reasons}
        )
        return stop_and_report(
            env,
            rec,
            before,
            {"condition": cond, "values": values},
            "着手できる課題が無い" if cond == "no_issue" else "候補がすべて MVV の外・人の判断待ちとして外れた",
            {"selection": got["selection"]},
        )
    d = loop_dir(env, rec["name"]) / f"lap-{n}"
    new = lap or {"n": n, "outcome": IN_PROGRESS, "started_at": env.now(), "created": [], "gates": [], "deferred_issues": []}
    new.update(
        sprint=f"{rec['name']}-{n}",
        dir=str(d),
        state_path=None,
        out=str(d / "sprint"),
        milestone=got["milestone"],
        rank_digest=(rank.get("metrics") or {}).get("digest", ""),
        selection=got["selection"],
    )
    if lap is None:
        rec["laps"].append(new)
    if got["sprint_mvv"]:
        d.mkdir(parents=True, exist_ok=True)
        (d / "mvv.md").write_text(got["sprint_mvv"], encoding="utf-8")
    write_record(env, rec, before)
    chosen = selected_issues(new)
    nxt = f"モードと版を決め、対象外は loop exclude で外し、python3 {shlex.quote(str(SPRINT_STATE))} loop sprint {rec['name']} --mode <モード> --dev <開発版> を打つ"
    update_handoff(env, rec, f"周回 {n} の課題を選んだ: {' '.join(f'#{x}' for x in chosen)}", resume_command(rec), nxt)
    return loop_ok(
        f"周回 {n} の課題を選んだ（{got['milestone']}）: " + " ".join(f"#{x}" for x in chosen),
        rec,
        got["selection"],
        status="selected",
        next_=nxt,
        selected=chosen,
        excluded=[r for r in got["selection"] if r["decision"] == DROPPED],
        milestone=got["milestone"],
        rank_digest=new["rank_digest"],
    )


def cmd_exclude(env: Env, name: str, number: int, reason: str) -> dict:
    with open_record(env, name) as (rec, before):
        lap = current_lap(rec)
        if not lap or lap.get("state_path"):
            return refusal_order("スプリントを組む前の進行中の周回が無い（loop exclude は選定の後・loop sprint の前に打つ）")
        rec["excluded"].append({"number": number, "reason": reason, "lap": lap["n"]})
        return run_selection(env, rec, before, lap["n"], lap)
