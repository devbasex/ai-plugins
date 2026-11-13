"""sprint_loop_gate.py: 開発ループの 1 回の締めと、設計承認ゲートの人の判断待ちの行の扱い（#2089）。承認ゲートの答え待ちの振り分けは sprint_loop_table が持つ（#3092）。"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path

import gh_sections
import jsonio
import project_mvv as pm
import revise_note
from state_get import NOT_DONE
import step_result
from design_pr_merge import MERGED_RESULTS  # 正本は design_pr_merge。sprint_loop・sprint_loop_select はここから読む
from gate1_requests import carried_over, design_head, lane_unmatched
from lap_cost import lap_cost
from lap_close import closed_versions, copy_state_gates
from sprint_loop_backlog import lap_depths, lap_research
from sprint_loop_record import (
    TOOL,
    SPRINT_STATE,
    UPKEEP,
    EXIT_REFUSED,
    IN_PROGRESS,
    FAILED,
    PENDING,
    DESIGN_GATE,
    FINISHED,
    NOT_RUN,
    LoopError,
    Env,
    write_record,
    current_lap,
    consumed,
    boundary_stop,
    held_stop,
    resume_command,
    stop_and_report,
    update_handoff,
    selected_issues,
)


def loop_ok(summary: str, rec: dict, items=None, status: str = "ok", next_: str | None = None, **metrics) -> dict:
    lap = current_lap(rec)
    m = {
        "status": status,
        "lap": lap["n"] if lap else len(rec.get("laps", [])),
        "consumed": consumed(rec),
        "budget": rec.get("budget"),
        **metrics,
    }
    if next_:
        m["next"] = next_
    return step_result.result(TOOL, "ok", summary, items or [], m, next=next_)


def select_cmd(name: str) -> str:
    return f"python3 {shlex.quote(str(SPRINT_STATE))} loop select {name}"


def refusal_order(summary: str) -> dict:
    return {**step_result.result(TOOL, "stopped", summary, [], {"status": "refused"}), "exit": EXIT_REFUSED}


def refused_stop(env: Env, rec: dict, before: dict, step: str, code: int, summary: str) -> dict:
    stop = {"condition": "refused", "values": {"step": step, "exit": code, "summary": summary[:300]}}
    return stop_and_report(env, rec, before, stop, f"ループの 1 回を組めなかった（{step}）: {summary[:200]}")


def copy_mvv_gate(rec: dict, lap: dict, state: Path, mvv: Path) -> None:
    """承認済みのスプリント MVV をループの 1 回の状態へ承認ゲート MVV の記録として写す（決定 11）。"""
    sha = pm.sha256_text(mvv.read_text(encoding="utf-8"))
    hit = next((a for a in rec.get("sprint_mvv_approvals", []) if a["milestone"] == lap["milestone"] and a["sha256"] == sha), None)
    if not hit:
        return
    m = jsonio.read(state, want=dict)
    file_sha = hashlib.sha256(Path((m.get("mvv") or {}).get("path") or mvv).read_bytes()).hexdigest()
    entry = {"name": "MVV", "what": f"開発ループ {rec['name']} の承認（{hit['at']}）", "at": hit["at"], "sha256": file_sha}
    m["gates"] = [g for g in m.get("gates", []) if g.get("name") != "MVV"] + [entry]
    jsonio.write_atomic(state, m, indent=1)


# ---------------------------------------------------------------- gate


def pr_numbers(value) -> list[int]:
    """判定の記録の `pr`（mvv-gate.py の `--pr` の並び。古い記録や手で渡した値は 1 つの数）を番号の並びにする。"""
    items = value if isinstance(value, list) else [value]
    out = []
    for x in items:
        m = re.search(r"(\d+)\s*$", str(x if x is not None else ""))
        if m:
            out.append(int(m.group(1)))
    return out


def last_verdict(state_path: str, gate: str, pr: int | None = None) -> dict:
    """MVV 判定の記録（状態の置き場の jsonl）の、このループの 1 回の状態の最後の判定の行（承認ゲートの種類は design / release）。pr を渡せば、その PR を判定した行に
    絞る（並行の設計で別の課題の判定を写さない）。無ければ空。"""
    try:
        rows = jsonio.read_jsonl(pm.gate_log_path(), missing=[], skip_broken=True)
    except jsonio.JsonReadError:
        rows = []
    sp = str(Path(state_path).resolve())
    hits = [r for r in rows if r.get("gate") == gate and str(Path(r.get("sprint") or "").resolve()) == sp]
    if pr is not None:
        hits = [r for r in hits if pr in pr_numbers(r.get("pr"))]
    return hits[-1] if hits else {}


def gate_copy(name: str, row: dict) -> dict:
    return {
        "name": name,
        "by": "mvv",
        "verdict": row.get("verdict", "unknown"),
        "basis": row.get("basis") or [],
        "pr": row.get("pr"),
        "at": row.get("at", ""),
    }


def waiting_section(rec: dict, w: dict) -> str:
    how = f"python3 {SPRINT_STATE} loop answer {rec['name']} {w['number']} --approved|--rejected|--revise --note <指摘ファイル>"
    pr = f"#{w['pr']}" if w.get("pr") else "—"
    lines = [
        f"- 判定: {w.get('verdict', 'unknown')}（承認ゲート {w['gate']}）",
        f"- 理由: {w.get('reason') or '—'}",
        f"- 根拠の項目: {', '.join(w.get('basis') or []) or '—'}",
        f"- 設計 Pull Request: {pr}",
        f"- 答え: {w.get('answer') or 'まだ'}",
        *([f"- 設計 PR のマージ: {w['merge'].get('result')}（{w['merge'].get('reason') or '—'}）"] if w.get("merge") else []),
        *([f"- 利用者へ戻した理由: {w['returned']}"] if w.get("returned") and not w.get("answer") else []),
        f"- 答え方: `{how}`",
    ]
    if w.get("answer") == "revise":  # 指摘の全文は残る節に置き、ここは回を指す（#2510 の決定 6）
        lines.append(f"- 指摘: 節「{revise_note.HISTORY.removeprefix('## ')}」の開発ループ {rec['name']} の第 {w['revise_count']} 回")
        if w.get("missing"):
            lines.append(f"- 届いていない項目: {'、'.join(w['missing'])}")
    return "\n".join(lines)


def mark_issue(env: Env, rec: dict, w: dict) -> str | None:
    """課題の本文に「人の判断待ち（開発ループ <名>）」の節を書く（本文全体を書き直す）。直して出し直すの行は、回を残す節
    「出し直しの指摘の履歴」へも 1 回分を追記する。書けなければ理由を返す。"""
    heading = f"## 人の判断待ち（開発ループ {rec['name']}）"
    try:
        body = env.issue_body(w["number"])
        if w.get("answer") == "revise":
            body = revise_note.with_history(body, rec["name"], w)
        env.edit_issue(w["number"], gh_sections.replace_section(body, heading, waiting_section(rec, w)))
    except OSError as e:
        return str(e)
    return None


def refresh_state(lap: dict, update: Callable | None) -> None:
    """ループの 1 回を締める前に、ループの 1 回の done からスプリント状態を埋める（承認ゲートで締めるループの 1 回も、設計で使った費用と秒を数える）。"""
    if update and lap.get("state_path"):
        update(lap["state_path"], [d for d in lap.get("dones", []) if Path(d).is_file()])


def finish_lap(env: Env, rec: dict, lap: dict, outcome: str, state: dict | None = None) -> None:
    """保留したループの 1 回（リリース待ち・質問待ち）を 1 回だけ締め直す（I1 の例外）。結果・終わった時刻・費用と秒・振り返り・棚卸しを書き換える。"""
    if lap.get("outcome") not in PENDING:
        raise LoopError(f"ループの {lap['n']} 回目 は保留したループの 1 回でない（締め直しは 1 回だけ）")
    lap["outcome"] = IN_PROGRESS
    close_lap(env, rec, lap, outcome, state)


def waiting_row(env: Env, lap: dict, item: dict, row: dict, pr, n) -> dict:
    """設計承認ゲートで止まった課題 1 つの判断待ちの行（判定の記録から組む。設計 PR があれば承認した先端を付ける）。"""
    reasons = row.get("reasons") or []
    w = {
        "number": int(n), "reason": "／".join(reasons) or row.get("verdict", "判定を読めない"), "verdict": row.get("verdict", "unknown"),
        "basis": row.get("basis") or [], "boundary": row.get("boundary") or [], "gate": 1, "pr": pr, "lap": lap["n"],
        "at": env.now(), "report": item.get("report") or "", "state": lap["state_path"],
    }  # fmt: skip
    if pr is not None:  # 承認した先端（#2507）: 並列の計画の外でも書き、答えの時点の先端と比べる
        w["stamp"] = design_head(env, w)
    return w


def _enqueue_waiting(env: Env, rec: dict, w: dict, item: dict, lap: dict, n) -> None:
    """表の検査で自動に進めるか、レーンならオーガナイザーへ依頼してから、判断待ちへ足す。"""
    auto = revise_note.apply_table_check(env, rec, w, int(n))
    if not auto and w["pr"] is not None and (sent := lane_gate1(env, w, item.get("report") or "", lap["state_path"])):
        w.update(sent)  # 並列の計画のレーン: 設計の承認をオーガナイザーへ依頼し、答えはループの 1 回の境で読む（#2436 の 6c）
    rec["waiting"].append(w)
    if auto:
        revise_note.count_round(rec, w)


def wait_issues(env: Env, rec: dict, lap: dict, item: dict, row: dict, pr, issues, notes: list[str]) -> list[int]:
    """設計承認ゲートで止まった課題を 1 つずつ人の判断待ちへ足し、課題の本文へ節を書く。足した課題を返す。書けなかった理由は notes へ。"""
    moved = []
    for n in issues:
        w = waiting_row(env, lap, item, row, pr, n)
        _enqueue_waiting(env, rec, w, item, lap, n)
        lap.setdefault("gates", []).append(gate_copy(DESIGN_GATE, row))
        moved.append(int(n))
        if err := mark_issue(env, rec, w):
            notes.append(err)
    return moved


def lane_gate1(env: Env, w: dict, report: str, state: str, suffix: str = "", stamp: str | None = None) -> dict | None:
    """並列の計画のレーンなら、設計の承認の依頼（`kind: gate1`）をオーガナイザーへ送り、{request, plan, stamp, report, state}
    を返す。レーンでない・送れなければ None（今どおり人の判断待ちとして残す）。印は設計 PR の先頭の SHA（問いの時点の印）。
    同じ承認した先端で利用者へ戻すとき（#2507）は、`stamp` に承認した先端を、`suffix` に依頼の id の接尾辞を渡す
    （同じ id の依頼は答え済みとして受け取られないため）。依頼の理由にはマージの結果の理由（`w.returned`）も載せ、
    利用者へ戻した依頼には印 `returned` を付ける（MVV 判定でオーガナイザーが自分で承認しないため。#2507 の決定 9）。"""
    try:
        import parallel_lane

        got = parallel_lane.lane_of(env.main_dir())
        if not got:
            return None
        stamp = stamp or design_head(env, w)
        reasons = [x for x in (w.get("reason"), w.get("returned")) if x]
        rid = parallel_lane.request_gate1(
            parallel_lane.home_of(got, env.main_dir()), got[0], got[1].name, w["number"], int(w["pr"]), report, state, stamp or "0" * 12,
            verdict=w.get("verdict"), reasons=reasons, boundary=list(w.get("boundary") or []), suffix=suffix,
            **({"returned": True} if w.get("returned") else {}),
        )  # fmt: skip
    except Exception:  # noqa: BLE001 — 送れなければ今どおり人の判断待ち（loop answer で答える）
        return None
    return {"request": rid, "plan": got[0], "stamp": stamp, "report": report, "state": state}


def regate1(env: Env, w: dict, answered: bool = True) -> bool:
    """承認の答えを当てる前に、問いの時点の設計 PR の先端（stamp）と今の先端を比べる。取り込みのコミットだけなら行の先端を
    書き換えて当てる（False）。それ以外は新しい先端で問い直し、読めなければ次の境で読み直す（どちらも True。古い範囲への答えで
    承認を書かない）。印の無い古い依頼は比べない。問い直すときは前の依頼を `requests_before` へ移す（`answered` は今の依頼への
    答えを読んだか。読んだ答えは当たらないため 1 回だけ知らせ、判定済みで移す）。"""
    stamp = w.get("stamp") or ""
    if not stamp.strip("0"):
        return False
    head = design_head(env, w)
    if carried_over(env, w, head):
        return False
    if head and (sent := lane_gate1(env, dict(w, returned=None), w.get("report") or "", w.get("state") or "")):
        if answered:
            lane_unmatched(
                env,
                w["request"],
                f"設計 PR #{w['pr']} が問いの後に取り込み以外で動いた（{stamp[:12]} → {head[:12]}）。新しい先端で問い直した",
            )
        w.setdefault("requests_before", []).append({"id": w["request"], "stamp": stamp, "settled": answered})
        w.pop("returned", None)  # 前に利用者へ戻した印と理由は新しい先端の問いに当てはまらない
        w.update(sent, restamped=stamp)
    return True


def note_merge(w: dict, out: dict, lap: int, now: str) -> dict:
    """承認した設計のマージの結果（`design_pr_merge.merge_approved` の出力）を人の判断待ちの行の `merge` へ書く（#2507）。
    同じ承認した先端で同じ result と class が続いたループの回数（streak）と、基盤待ちのための再実行の累計（infra_reruns）を数える。
    infra_reruns は分類が変わっても 0 に戻さない（基盤待ちと実行中が交互に現れても再実行を上限なく増やさない）。
    足すのは実際に打った再実行の回数（`infra_rerun`。1 回の merge-when-green の中の複数回も数える）。"""
    prev = w.get("merge") or {}
    stamp = w.get("stamp") or ""
    same = prev.get("stamp") == stamp
    streak = prev.get("streak", 0) + 1 if same and (prev.get("result"), prev.get("class")) == (out.get("result"), out.get("class")) else 1
    reruns = (prev.get("infra_reruns", 0) if same else 0) + int(out.get("infra_rerun") or 0)
    w["merge"] = {**out, "lap": lap, "streak": streak, "infra_reruns": reruns, "stamp": stamp}
    if out.get("result") in MERGED_RESULTS and not w.get("merged_at"):
        w["merged_at"] = now
    return w["merge"]


def _reopen_waiting(w: dict) -> None:  # 前の回答者 by と利用者へ戻した印 returned も外す（残すと次の問いを読み違える）
    for k in ("answer", "answered_at", "by", "merge", "merged_at", "returned"):
        w.pop(k, None)


def reask_design(env: Env, rec: dict, w: dict, head: str) -> bool:
    """承認した設計 PR の先端が承認の後に動いた（または承認した先端が記録に無い）: 新しい先端で問い直す（#2507）。
    レーンは新しい先端で依頼を送り直し、送れなければ何も変えずにループの次の 1 回の境で打ち直す（古い答えを当てないため）。
    1 本のループは行の承認した先端を書き換え、答えを外して人の判断待ちへ戻す。問い直したら True。"""
    if not head:
        return False
    old = w.get("stamp") or ""
    if w.get("request"):
        sent = lane_gate1(env, dict(w, returned=None), w.get("report") or "", w.get("state") or "")
        if not sent:
            return False
        _reopen_waiting(w)
        w.update(sent, restamped=old)
    else:
        _reopen_waiting(w)
        w.update(stamp=head, restamped=old)
    mark_issue(env, rec, w)
    return True


def giveback_design(env: Env, rec: dict, w: dict, reason: str, lap: int) -> bool:
    """時間で解けないマージの結果を、承認した先端のまま利用者へ戻す（#2507 の決定 9）。依頼にマージの結果の理由を載せる。
    利用者は「直して出し直す」「やらない」か、手を打ってから「承認」を選ぶ。戻した行のマージの結果（streak・infra_reruns）は消す。
    レーンは同じ先端でも別の依頼にするため id に `-m<ループの 1 回>-<戻した回数 givebacks。消さない>` を足す。送れなければ何も変えずにループの次の 1 回の境で打ち直す。"""
    w["returned"] = reason
    if w.get("request"):
        n = w.get("givebacks", 0) + 1
        sent = lane_gate1(env, w, w.get("report") or "", w.get("state") or "", suffix=f"-m{lap}-{n}", stamp=w.get("stamp") or "")
        if not sent:
            w.pop("returned", None)
            return False
        _reopen_waiting(w)
        w.update(sent, givebacks=n)
    else:
        _reopen_waiting(w)
    w["returned"] = reason  # _reopen_waiting が外すため書き直す
    mark_issue(env, rec, w)
    return True


PROJECTS_SYNC = SPRINT_STATE.parent / "projects-sync.sh"


def mode_decision(env: Env, trigger: str, issues: list[int], from_mode: str | None, mode: str) -> dict:
    """モードの決定の記録の 1 件（`loop sprint` か設計承認ゲートでの決め直し）。"""
    return {"at": env.now(), "trigger": trigger, "issues": list(issues), "from_mode": from_mode, "mode": mode}


def sync_mode(env: Env, issues: list[int], mode: str) -> list[str]:
    """課題の本文の「進行」の `モード:` の行にスプリントのモードを書く（#2923 の決定 6）。書けなくても止めず、
    書けなかった課題ごとに理由を返す。"""
    notes = []
    for n in issues:
        try:
            code, so, se = env.run(["bash", str(PROJECTS_SYNC), str(n), "mode", mode], cwd=str(env.main_dir()))
        except OSError as e:
            code, so, se = 1, "", str(e)
        if code != 0:
            notes.append(f"#{n} の本文の「進行」にモードを書けなかった（{code}）: {(se or so).strip()[:200]}")
    return notes


# ---------------------------------------------------------------- lap


def failure_key(state: dict) -> dict | None:
    """スプリント状態の行（完了でもまだでもない最初の行）の課題・種類・止まったところ（決定 8）。理由の文は含めない。"""
    for p in state.get("plans", []):
        row = p.get("row") or {}
        if row.get("result") not in (None, NOT_DONE, NOT_RUN, *FINISHED):  # 承認ゲートは loop gate が扱い、流さなかった計画は失敗の元でない
            return {"issues": sorted(p.get("issues") or []), "kind": p.get("kind", ""), "step": row.get("result")}
    return None


def refine_summary(env: Env, rec: dict, lap: dict) -> dict:
    """棚卸しの結果（upkeep の state dir の candidates.json と rank.json）をループの 1 回の記録の形にする。

    起動の判定が見送り、差分の棚卸しを走らせなかった回は空にして `skipped` の印を付ける（#2914 の決定 6）。"""
    diff = {r.get("event") for r in lap.get("depths") or [] if r.get("depth") == "diff"}
    if "not_due" in diff and "ran" not in diff:
        return {"report": "", "rank_changes": [], "deferred": [], "skipped": True}
    sd = env.upkeep_dir(rec.get("repo") or "")
    cands = jsonio.read(sd / "candidates.json", missing={}, broken={}, want=dict)
    rank = jsonio.read(sd / "rank.json", missing={}, broken={}, want=dict)
    return {
        "report": str(sd / "candidates.json") if cands else "",
        "rank_changes": (rank.get("metrics") or {}).get("changes") or [],
        "deferred": (cands.get("metrics") or {}).get("deferred") or [],
    }


def research_command(root: str, rec: dict, lap: dict, done: Path) -> str:
    """待ち（`supervise.py wait`）と並べて背景で打つ、待ち時間の調査の 1 行（#2315 の決定 7）。"""
    import parallel_lane

    plans = done.with_suffix(".plans.json")
    args = ["python3", str(UPKEEP), "research", "run", "--root", root, "--until", str(done), "--queue", str(plans)]
    args += ["--max", str(parallel_lane.queue_max(root))]  # 調査はまとめの queue と同じ並列の上限に数える
    return shlex.join([*args, "--lap", str(lap["n"]), "--loop", rec["name"]])


def owner_line(env: Env) -> tuple[str, dict | None]:
    """見直しの持ち主の行（`.ndf/backlog/owner.json`）。"""
    path = env.main_dir() / ".ndf" / "backlog" / "owner.json"
    got = jsonio.read(path, missing=None, broken=None, want=dict)  # run_owner は耐久の記録（dbos）へ届くため読まない
    if not got or got.get("finished_at"):
        return "見直しの持ち主: なし", got
    return f"見直しの持ち主: {got.get('run')}（{got.get('what')}・最後に動いた時刻 {got.get('seen_at')}）", got


def backlog_summary(env: Env, rec: dict, lap: dict) -> None:
    """ループの 1 回の間の棚卸しの深さの行と、調査の費用と秒をループの 1 回の記録へ写す（#2315）。

    調査の費用は呼び出しごとの記録（`research/calls.jsonl`。結果を捨てた呼び出しも含む）から集める。背景の調査はループの 1 回を
    締めた後にも終わるため、このループのすべてのループの 1 回を記録から数え直し、締め済みのループの 1 回へ後から終わった費用を精算する。"""
    d = env.main_dir() / ".ndf" / "backlog"
    rows = jsonio.read_jsonl(d / "depths.jsonl", missing=[], skip_broken=True)
    lap["depths"] = lap_depths(rows, lap.get("started_at") or "")
    calls = jsonio.read_jsonl(d / "research" / "calls.jsonl", missing=[], skip_broken=True)
    for each in {id(x): x for x in [*rec.get("laps", []), lap]}.values():
        research = lap_research(calls, rec.get("name"), each.get("n"))
        if research is not None:
            each["research"] = research


def close_lap(env: Env, rec: dict, lap: dict, outcome: str, state: dict | None = None, deferred_issues=(), done_rows=()) -> None:
    """ループの 1 回を締め、スプリント状態とまとめの記録から値を写して固定する。"""
    if lap.get("outcome") != IN_PROGRESS:
        raise LoopError(f"ループの {lap['n']} 回目 は締めてある（I1）")
    state = (
        state if state is not None else (jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {})
    )
    rows = [p.get("row") or {} for p in state.get("plans", [])]
    lap["cost"], lap["cost_parts"] = lap_cost(lap, round(sum(float(r.get("cost") or 0) for r in rows), 6))
    lap["seconds"] = round(sum(float(r.get("seconds") or 0) for r in rows), 3)
    lap["versions"] = closed_versions(lap, state)
    copy_state_gates(lap, state)
    close_rep = next((p for p in state.get("plans", []) if p.get("kind") == "まとめ"), None)
    if close_rep:
        lap["retro"] = (close_rep.get("row") or {}).get("report", "")
    backlog_summary(env, rec, lap)
    lap["refine"] = refine_summary(env, rec, lap)
    lap["deferred_issues"] = sorted(set(lap.get("deferred_issues") or []) | {int(n) for n in deferred_issues})
    lap["failure"] = failure_key(state) if outcome == FAILED else None
    if outcome == FAILED and lap["failure"] is None:
        lap["failure"] = {"issues": selected_issues(lap), "kind": "ループの 1 回", "step": "conductor"}
    lap["relay_dir"] = os.environ.get("NDF_RELAY_DIR")
    lap["outcome"], lap["ended_at"] = outcome, env.now()


def after_close(env: Env, rec: dict, before: dict, summary: str) -> dict:
    """ループの 1 回を締めた後の境の判定。止まれば 3、続けば ndf_next を返す。答え待ちの停止は次の選定が読む（#2913 の決定 5）。"""
    stop = held_stop(rec, boundary_stop(env, rec, hold=False))
    if stop:
        return stop_and_report(env, rec, before, stop, f"{summary}。止まる条件に当たった（{stop['condition']}）")
    write_record(env, rec, before)
    nxt = resume_command(rec)
    update_handoff(env, rec, f"ループの {rec['laps'][-1]['n']} 回目 を締めた。ループの次の 1 回へ", nxt, select_cmd(rec["name"]))
    return loop_ok(
        f"{summary}。ループの次の 1 回へ進む", rec, [rec["laps"][-1]], status="continue", next_=nxt, ndf_next=f"```ndf-next\n{nxt}\n```"
    )
