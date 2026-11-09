"""sprint_loop_select.py: 開発ループの選定（#2089 の決定 2。loop select / loop exclude）。"""

from __future__ import annotations

import os
import re
import shlex
import sys

import design_pr_merge
import gate1_requests as gr1
import project_mvv as pm
import sprint_mvv

from sprint_loop_record import (
    SPRINT_STATE,
    UPKEEP,
    IN_PROGRESS,
    SELECTED,
    DROPPED,
    SLOT_FIT,
    PAUSE_RANK,
    Env,
    loop_dir,
    write_record,
    open_record,
    current_lap,
    boundary_stop,
    held_stop,
    pending_issues,
    pending_laps,
    waiting_only,
    IMMEDIATE_STOPS,
    resume_command,
    stopped_result,
    stop_and_report,
    update_handoff,
    selected_issues,
)

from sprint_loop_gate import (
    MERGED_RESULTS,
    carried_over,
    design_head,
    giveback_design,
    lane_gate1,
    loop_ok,
    note_merge,
    reask_design,
    refusal_order,
    select_cmd,
)
from sprint_loop_gate2 import pending_step
from sprint_loop_exclusion import PENDING_REASON, PRODUCTION_REASON, exclusion_reason, prerequisite_reason  # noqa: F401


def review_first(env: Env, rec: dict, before: dict, got: dict) -> dict:
    """選べる課題が尽きたとき、止まる前に見直しを 1 回だけ挟む（#2315 の決定 12）。

    ループの前の 1 回を締めてから挟んでいなければ `review_first` を返し、見直しを流して選定をやり直すコマンドを `next` に置く。
    見直しの後も 0 件なら、次の選定は今までどおり `no_issue` で止まる。"""
    rec["review_first"] = len(rec["laps"])
    write_record(env, rec, before)
    root = shlex.quote(str(env.main_dir()))
    due = f"python3 {shlex.quote(str(UPKEEP))} depth due review --no-issue --loop {rec['name']} --root {root}"
    nxt = f"{due} を打ち、0 なら /ndf:backlog-refinement --depth review を無人で通す。終えたら {select_cmd(rec['name'])} を打ち直す"
    update_handoff(env, rec, "選べる課題が尽きた。止まる前に見直しを 1 回挟む", resume_command(rec), nxt)
    return loop_ok("選べる課題が尽きた。止まる前に見直しを 1 回挟む", rec, got["selection"], status="review_first", next_=nxt)


def candidates_of(ms: dict, max_issues: int, held: set[int] = frozenset()) -> list[dict]:
    """マイルストーンの候補（順位の順）: 境界までの行（境界が無ければ判断待ちを除いた先頭から max_issues 件まで）＋大きい課題の枠の 1 切れ（当てたとき）
    ＋品質の枠の課題（宣言があるとき）。"""
    rows = sorted(ms.get("rows", []), key=lambda r: r["rank"])
    b = ms.get("boundary")
    if b and b.get("empty"):  # 枠で容量を使い切った: 通常の候補は 0 件
        nums = set()
    elif b:
        idx = next((i for i, r in enumerate(rows) if r["number"] == b["number"]), len(rows) - 1)
        nums = {r["number"] for r in rows[: idx + 1]}
    else:  # 判断待ち・保留したループの 1 回（held）・前提の課題の実装待ちの行は枠を使わない（選ばれない行で max_issues を埋めると、後ろの課題が候補に上がらない）
        free = [r for r in rows if not r.get("waiting_decision") and not r.get("prerequisites") and r["number"] not in held]
        open_rows = free[:max_issues]
        last = rows.index(open_rows[-1]) if len(open_rows) == max_issues else len(rows) - 1  # 枠が埋まらなければ末尾まで
        nums = {r["number"] for r in rows[: last + 1]}
    slot = ms.get("large_slot") or {}
    if slot.get("status") == SLOT_FIT and slot.get("slice"):
        nums.add(slot["slice"])
    nums |= set((ms.get("quality_slot") or {}).get("issues") or [])
    return [r for r in rows if r["number"] in nums]


APPROVED_REASON = "承認した設計"


def approved_designs(rec: dict, reselecting: int | None = None) -> set[int]:
    """承認し、設計 PR のマージが済み、その後のループの 1 回でまだ選ばれていない課題（#2507 の I7）。
    reselecting は取り直しているループの回の番号で、そのループの 1 回の前の選定は「選ばれた」に数えない。"""
    out = set()
    for w in rec.get("waiting", []):
        if w.get("answer") != "approved" or not w.get("merged_at"):
            continue
        n = w["number"]
        later = [lap for lap in rec.get("laps", []) if lap.get("n", 0) > (w.get("lap") or 0) and lap.get("n") != reselecting]
        if not any(r.get("number") == n and r.get("decision") == SELECTED for lap in later for r in lap.get("selection") or []):
            out.add(n)
    return out


def first_issues(rec: dict, reselecting: int | None = None) -> set[int]:
    """順位によらず先頭に選ぶ課題（#2436 の 6e・#2507）: 設計を直して出し直す課題・デプロイを差し戻されたループの 1 回の直し・
    承認とマージが済んだ設計の課題。"""
    last = {w["number"]: w for w in rec.get("waiting", [])}
    revise = {n for n, w in last.items() if w.get("answer") == "revise"}
    fixes = {n for f in rec.get("fixes", []) if not f.get("fixed_by") for n in f.get("issues") or []}
    return revise | fixes | approved_designs(rec, reselecting)


# ---------------------------------------------------------------- 承認した設計のマージし直し（#2507 の I6）

STREAK_LIMIT = 3  # 同じ分類の not_green がこのループの回数だけ続いたら利用者へ戻す（既定。測っていない）
UNSOLVABLE = ("stale_again",)  # 時間で解けない not_green の分類


def settle_approved_designs(env: Env, rec: dict) -> list[str]:
    """ループの 1 回の境（選定の前）で、承認済みで未マージの設計 PR ごとに、前のマージの結果に応じた手を 1 回だけ打つ。
    マージ済みになった設計 PR は後片付けを打つ。失敗はその課題を外す理由にとどめ、ループの 1 回は止めない。打った手の 1 行を返す。"""
    lap_n = len(rec.get("laps", [])) + 1
    notes = []
    for w in rec.get("waiting", []):
        if w.get("answer") != "approved" or not w.get("pr"):
            continue
        if not w.get("merged_at"):
            if line := _settle_one(env, rec, w, lap_n):
                notes.append(line)
        if w.get("answer") == "approved" and w.get("merged_at") and not w.get("cleaned"):
            code, so, se = env.run([sys.executable, str(design_pr_merge.MERGED_STEPS), "cleanup", str(w["pr"])], cwd=str(env.main_dir()))
            w["cleaned"] = env.now() if code == 0 else None
            if code != 0:
                w["cleanup_error"] = (se or so).strip()[:200]
    return notes


def _settle_one(env: Env, rec: dict, w: dict, lap_n: int) -> str | None:
    """「ループの 1 回の境が結果ごとに打つ手」の表（設計 #2507）の 1 行を打つ。"""
    m = w.get("merge") or {}
    res, cls, pr = m.get("result"), m.get("class"), int(w["pr"])
    if res in MERGED_RESULTS:
        w["merged_at"] = env.now()
        return None
    if res == "no_stamp" or (res == "head_moved" and not carried_over(env, w)):  # 取り込みのコミットだけなら承認を引き継いでマージし直す
        return f"#{w['number']} の設計 PR #{pr} を新しい先端で問い直した" if reask_design(env, rec, w, design_head(env, w)) else None
    if res == "production":  # 宛先はループの 1 回を重ねても変わらない。人がリリース承認ゲートでマージしたかだけを読む
        if env.pr_state(pr) == "MERGED":
            w["merged_at"] = env.now()
        return None
    if res == "failed":
        return _giveback(env, rec, w, m.get("reason") or "マージできない", lap_n)
    if res == "conflict":
        return _settle_conflict(env, rec, w, m, lap_n)
    if res == "not_green" and (cls in UNSOLVABLE or m.get("streak", 0) >= STREAK_LIMIT):
        return _giveback(env, rec, w, m.get("reason") or "検査が緑にならない", lap_n)
    used = m.get("infra_reruns", 0) if m.get("stamp") == (w.get("stamp") or "") else 0
    if res == "not_green" and cls == "infra" and used >= design_pr_merge.INFRA_RERUNS:
        return _giveback(env, rec, w, f"CI の基盤待ちが再実行 {design_pr_merge.INFRA_RERUNS} 回で解けない", lap_n)
    budget = design_pr_merge.INFRA_RERUNS - used  # 残りを再実行する層へ渡す（前の分類が基盤待ちでなくても累計で上限を守る）
    out = design_pr_merge.merge_approved(env.main_dir(), pr, w.get("stamp"), run=env.run, infra_budget=budget)
    got = note_merge(w, out, lap_n, env.now())
    return f"#{w['number']} の設計 PR #{pr} をマージし直した（{got['result']}）"


def _giveback(env: Env, rec: dict, w: dict, reason: str, lap_n: int) -> str | None:
    if giveback_design(env, rec, w, reason, lap_n):
        return f"#{w['number']} の設計 PR #{w['pr']} をマージできず、利用者へ戻した（{reason}）"
    return None


def design_worktree(env: Env, branch: str) -> str | None:
    """設計のブランチを持つ worktree のパス。無ければ None。"""
    if not branch:
        return None
    code, so, _ = env.run(["git", "worktree", "list", "--porcelain"], cwd=str(env.main_dir()))
    path = None
    for line in (so or "").splitlines() if code == 0 else []:
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def _settle_conflict(env: Env, rec: dict, w: dict, m: dict, lap_n: int) -> str | None:
    """宛先と衝突した設計 PR: 設計の worktree でベースブランチを取り込んで push する。先端が進めば新しい先端で問い直し、
    衝突が残れば取り込みを閉じて利用者へ戻す。"""
    wt = design_worktree(env, m.get("branch") or "")
    if not wt:
        return _giveback(env, rec, w, f"宛先と衝突している（設計の worktree が無い）: {m.get('reason') or ''}", lap_n)
    code, so, se = env.run([sys.executable, str(design_pr_merge.MERGED_STEPS), "base-sync", "--pr", str(w["pr"]), "--push"], cwd=wt)
    if code == 0:
        head = design_head(env, w)
        if head and head != (w.get("stamp") or "") and carried_over(env, w, head):
            w.pop("merge", None)  # 取り込みのコミットだけなので承認を引き継ぎ、次の境でマージし直す
            return f"#{w['number']} の設計 PR #{w['pr']} にベースブランチを取り込み、承認を引き継いだ"
        if head and head != (w.get("stamp") or "") and reask_design(env, rec, w, head):
            return f"#{w['number']} の設計 PR #{w['pr']} にベースブランチを取り込み、新しい先端で問い直した"
    if code == 1:
        env.run(["git", "merge", "--abort"], cwd=wt)
    return _giveback(env, rec, w, f"宛先と衝突し、ベースブランチを取り込めない（{code}）: {(se or so).strip()[:160]}", lap_n)


def selected_reason(row: dict, first: set[int], approved: set[int]) -> str:
    """選んだ課題の理由。承認した設計 → 直しの先頭 → 品質の枠 → 順位 の順に当てる。"""
    return (
        APPROVED_REASON if row["number"] in approved
        else "直しの先頭" if row["number"] in first
        else "品質の枠" if row.get("quality_slot")
        else "順位"
    )  # fmt: skip


def _take_order(cands: list[dict], open_: list[int], first: set[int]) -> tuple[list[int], list[int]]:
    """枠を割り当てる順（先頭に選ぶ課題 → 品質の枠 → 残り）と、先頭に選ぶ課題の添字を返す。"""
    head = [i for i in open_ if cands[i]["number"] in first]
    rest = [i for i in open_ if i not in head]
    take_order = head + [i for i in rest if cands[i].get("quality_slot")] + [i for i in rest if not cands[i].get("quality_slot")]
    return take_order, head


def _select_candidate_rows(
    cands: list[dict], title: str, max_issues: int, reason_for, first: set[int] = frozenset(), approved: set[int] = frozenset()
) -> tuple[list[int], list[dict]]:
    """1 マイルストーンの候補の採否。選んだ番号と選定の行を返す。reason_for は外す理由（外さなければ None）を返す。
    max_issues の枠は、先頭に選ぶ課題（first。直して出し直す・デプロイの直し）→ 品質の枠の課題 → 順位の順に割り当てる
    （品質の枠の最低件数を容量で削らない）。"""
    whys = [reason_for(r) for r in cands]  # 候補ごとに 1 回、順位の順に呼ぶ（exclusion_reason の副作用の回数と順序を保つ）
    open_ = [i for i, w in enumerate(whys) if w is None]
    take_order, head = _take_order(cands, open_, first)
    take = take_order[:max_issues]
    picked, here = [], []
    for i in sorted(range(len(cands)), key=lambda i: (i not in head, i)):  # 先頭に選ぶ課題だけを前へ。ほかは順位の順
        r = cands[i]
        why = whys[i] if whys[i] is not None or i in take else "容量"
        if why is None:
            picked.append(r["number"])
            reason = selected_reason(r, first, approved)
            here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": SELECTED, "reason": reason})
        else:
            here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": why})
    return picked, here


def _rows_by_rank(ms: dict) -> list[dict]:
    return sorted(ms.get("rows", []), key=lambda r: r["rank"])


def milestone_candidates(ms: dict, rec: dict, first: set, lap_n: int, prior: dict | tuple = ()) -> list[dict]:
    """マイルストーンの候補（切り出しの境界の内）に、境界の外にある先に選ぶ課題を順位の順で足す。
    保留したループの 1 回の課題・今のループの 1 回で外した課題（loop exclude）・前のループの待ちの課題は、境界が無いときの枠を使わない（#2627）。"""
    lap_excluded = {e["number"] for e in rec.get("excluded", []) if e.get("lap") == lap_n}
    cands = candidates_of(ms, rec["max_issues"], pending_issues(rec) | lap_excluded | set(prior))
    have = {r["number"] for r in cands}
    return cands + [r for r in _rows_by_rank(ms) if r["number"] in first and r["number"] not in have]


def outside_boundary_rows(ms: dict, cands: list[dict], title: str) -> list[dict]:
    """選ばれたマイルストーンで候補に入らなかった行（切り出しの境界の外）の落選行。"""
    cand_nums = {r["number"] for r in cands}
    return [
        {"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": "順位（切り出しの境界の外）"}
        for r in _rows_by_rank(ms)
        if r["number"] not in cand_nums
    ]


def choose_issues(
    env: Env, rec: dict, rank: dict, lap_n: int, descriptions: dict, lap_created: set = frozenset(), prior: dict | tuple = ()
) -> dict:
    """選定（決定 2）。入力は rank の出力・記録・マイルストーンの説明と、前のループの待ち（課題 → ループの名前）だけ。戻り値は
    {"milestone", "selection", "candidates"}。選べたマイルストーンが無ければ milestone は None。"""
    m = rank.get("metrics") or {}
    by_title = {ms["title"]: ms for ms in m.get("milestones", [])}
    order = [t for t in m.get("order", []) if t in by_title and (not rec.get("milestones") or t in rec["milestones"])]
    approvals = {(a["milestone"], a["sha256"]) for a in rec.get("sprint_mvv_approvals", [])}
    rows_out, total = [], 0
    first = first_issues(rec, lap_n)
    approved = approved_designs(rec, lap_n)
    for title in order:
        ms = by_title[title]
        cands = milestone_candidates(ms, rec, first, lap_n, prior)
        total += len(cands)
        text = sprint_mvv.mvv_sections(descriptions.get(title) or "")
        unapproved = text is not None and (title, pm.sha256_text(text)) not in approvals
        picked, here = _select_candidate_rows(
            cands,
            title,
            rec["max_issues"],
            (lambda r: "スプリント MVV が未承認") if unapproved else (lambda r: exclusion_reason(env, rec, r, lap_n, lap_created, prior)),
            first,
            approved,
        )
        rows_out += here
        if picked:
            rows_out += outside_boundary_rows(ms, cands, title)
            return {"milestone": title, "selection": rows_out, "candidates": total, "sprint_mvv": text}
    return {"milestone": None, "selection": rows_out, "candidates": total, "sprint_mvv": None}


def cmd_select(env: Env, name: str) -> dict:
    with open_record(env, name) as (rec, before):
        if rec.get("status") == "stopped":
            return stopped_result(env, rec, f"ループ {name} は止まっている。再開のコマンド（loop start）で理由を判定し直す")
        if current_lap(rec):
            return refusal_order("進行中のループの 1 回がある（I2）。ループの 1 回を締めてから選ぶ")
        return boundary_then_select(env, rec, before)


def boundary_then_select(env: Env, rec: dict, before: dict) -> dict:
    """ループの 1 回の境（#2436 の lane-loop の「ループの 1 回の境」の 1〜9）: すぐ止まる条件 → 届いた答えの処理と保留したループの 1 回のまとめ →
    保留の上限・待ちだけ → ほかの止まる条件 → 選定。"""
    stop = boundary_stop(env, rec)
    if stop and stop["condition"] in IMMEDIATE_STOPS:
        return stop_and_report(env, rec, before, stop, f"止まる条件に当たった（{stop['condition']}）")
    import sprint_loop

    got = pending_step(env, rec, before, sprint_loop.apply_gate1_answers)
    if got is not None:
        return got
    settle_approved_designs(env, rec)  # 選定の前に、承認済みで未マージの設計 PR をマージし直す（#2507）
    notes = gr1.lane_sends(env, rec, lane_gate1)
    stop = held_stop(rec, boundary_stop(env, rec))
    if stop:
        return stop_and_report(env, rec, before, stop, f"止まる条件に当たった（{stop['condition']}）")
    return run_selection(env, rec, before, len(rec["laps"]) + 1, notes=notes)


def resume_answer_hold(env: Env, rec: dict, before: dict, stop: dict, lap: dict | None, nxt: str) -> tuple[dict | None, str]:
    """答え待ちの停止で止まったループの再開: 停止の範囲に残っていれば同じ条件で止まり直す（#2707）。"""
    if (again := boundary_stop(env, rec)) and again["condition"] == "answer_hold":
        return stop_and_report(env, rec, before, again, "答え待ちの停止が続いている"), nxt
    return None, nxt


def has_held_work(rec: dict) -> bool:
    """答えを待つ作業があるか（保留したループの 1 回か、オーガナイザーへ送って答えの無い設計の承認）。"""
    return bool(pending_laps(rec)) or any(w.get("request") and not w.get("answer") for w in rec.get("waiting", []))


EXCLUDE_ENV = "NDF_LOOP_EXCLUDE"
EXCLUDE_REASON = "別の実行が持つ課題"


def env_excluded(raw: str | None) -> list[int]:
    """`NDF_LOOP_EXCLUDE` の課題の番号（`,` か空白で区切る。番号でない語は読まない）。Factory の実行の制御が、同じリポジトリの
    別の実行が持つ課題を渡す（#2347 の決定 3）。"""
    return sorted({int(x) for x in re.split(r"[\s,]+", raw or "") if x.isdigit()})


def add_env_exclusions(rec: dict, n: int) -> None:
    """環境変数の課題をループの 1 回 n の除外へ足す（同じ番号とループの 1 回の行が既にあれば足さない）。"""
    have = {e["number"] for e in rec.get("excluded", []) if e.get("lap") == n}
    for num in env_excluded(os.environ.get(EXCLUDE_ENV)):
        if num not in have:
            rec.setdefault("excluded", []).append({"number": num, "reason": EXCLUDE_REASON, "lap": n})


def _stop_without_issue(env: Env, rec: dict, before: dict, got: dict, rank: dict, lap: dict | None) -> dict:
    """選べる課題が無いときの止まり方（待ちだけ・見直しを先に・no_issue・outside_mvv）。"""
    if lap is not None:
        rec["laps"].remove(lap)  # スプリントを組むループの前の 1 回は取り下げ、除外の一覧（excluded の lap）を次の選定に残す
    if has_held_work(rec):  # 選べる課題が無く答えを待つ作業だけが残る: no_issue で止まらず、見直しも挟まない（異常系 6）
        return stop_and_report(
            env, rec, before, waiting_only(rec, "no_selectable"), "手元の作業が待ちだけになった", {"selection": got["selection"]}
        )
    cond = "no_issue" if got["candidates"] == 0 else "outside_mvv"
    if cond == "no_issue" and rec.get("review_first") != len(rec["laps"]):
        return review_first(env, rec, before, got)
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


def _open_lap(env: Env, rec: dict, before: dict, got: dict, rank: dict, n: int, lap: dict | None) -> dict:
    """ループの 1 回 n の行を作って（または取り直したループの 1 回を書き換えて）記録する。選んだ課題の直しを済みにし、スプリントの MVV を置く。"""
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
    for f in rec.get("fixes", []):  # 直しのループの 1 回: 差し戻されたループの 1 回の課題を選んだら、その直しを済みにする
        if not f.get("fixed_by") and set(f.get("issues") or []) & set(selected_issues(new)):
            f["fixed_by"] = n
    if got["sprint_mvv"]:
        d.mkdir(parents=True, exist_ok=True)
        (d / "mvv.md").write_text(got["sprint_mvv"], encoding="utf-8")
    write_record(env, rec, before)
    return new


def run_selection(env: Env, rec: dict, before: dict, n: int, lap: dict | None = None, notes: list[dict] = ()) -> dict:
    add_env_exclusions(rec, n)
    code, rank = env.rank(env.main_dir())
    if code not in (0, PAUSE_RANK):
        return refusal_order(f"upkeep.py rank が {code} を返した。選定を推さない: {rank.get('summary', '')}")
    try:
        descriptions = env.milestones(rec.get("repo") or "")
    except OSError as e:
        return refusal_order(str(e))
    got = choose_issues(env, rec, rank, n, descriptions, set((lap or {}).get("created") or []), gr1.prior_issues(env))
    if got["milestone"] is None:
        return _stop_without_issue(env, rec, before, got, rank, lap)
    new = _open_lap(env, rec, before, got, rank, n, lap)
    chosen = selected_issues(new)
    nxt = f"モードを決め、対象外は loop exclude で外し、python3 {shlex.quote(str(SPRINT_STATE))} loop sprint {rec['name']} --mode <モード> を打つ"
    update_handoff(env, rec, f"ループの {n} 回目 の課題を選んだ: {' '.join(f'#{x}' for x in chosen)}", resume_command(rec), nxt)
    return loop_ok(
        f"ループの {n} 回目 の課題を選んだ（{got['milestone']}）: " + " ".join(f"#{x}" for x in chosen),
        rec,
        [*got["selection"], *notes],
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
            return refusal_order("スプリントを組む前の進行中のループの 1 回が無い（loop exclude は選定の後・loop sprint の前に打つ）")
        rec["excluded"].append({"number": number, "reason": reason, "lap": lap["n"]})
        return run_selection(env, rec, before, lap["n"], lap)
