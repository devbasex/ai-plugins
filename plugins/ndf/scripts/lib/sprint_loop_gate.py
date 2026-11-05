"""sprint_loop_gate.py: 開発ループの周回の締め・まとめのステージ・承認ゲートの扱い（#2089）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

import gh_sections
import jsonio
import project_mvv as pm
import step_result

from sprint_loop_record import (
    TOOL,
    SPRINT_STATE,
    SUPERVISE,
    UPKEEP,
    EXIT_REFUSED,
    IN_PROGRESS,
    FAILED,
    RETURNED,
    RELEASE_PENDING,
    PENDING,
    GATE_1,
    GATE_2,
    DESIGN_PHASE,
    FINISHED,
    NOT_RUN,
    LoopError,
    Env,
    write_record,
    open_record,
    current_lap,
    consumed,
    boundary_stop,
    held_stop,
    mvv_change_stop,
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


DONE_RESULTS = ("完了", NOT_RUN)  # 周回を完了で締められる done の結果（流さなかった計画は前のステージの結果に従う）


def queue_command(stages: list[list[str]], done: Path) -> str:
    """まとめの queue の 1 行: 先頭のステージを --max（レーンならレーンのプランの本数）で流し、残りのステージを --then でつなぎ、--done で終わる。"""
    import parallel_lane

    head = f"python3 {shlex.quote(str(SUPERVISE))} queue " + " ".join(map(shlex.quote, stages[0])) + f" --max {parallel_lane.queue_max()}"
    return head + "".join(" --then " + " ".join(map(shlex.quote, s)) for s in stages[1:]) + f" --done {shlex.quote(str(done))}"


def wait_lap(rec: dict, done: Path) -> str:
    return f"python3 {shlex.quote(str(SUPERVISE))} wait {shlex.quote(str(done))} && python3 {shlex.quote(str(SPRINT_STATE))} loop lap {rec['name']} --done {shlex.quote(str(done))}"


def sprint_pipeline(index: list[dict], close: list[str], done: Path, rec: dict) -> tuple[str, str]:
    """manifest のステージ配列から queue と pipeline の文字列を組む。最初の command の後に手で行うステージか
    別の command（単独の queue の承認ゲート 2）があれば、それを終えるまでまとめへ進めない。"""
    first = next(e for e in index if e.get("command"))
    hands = [e for e in index[index.index(first) + 1 :] if e.get("command") or "manual" in e]  # 関門は queue と loop gate が扱う
    if not hands:
        queue = first["command"] + " --then " + " ".join(map(shlex.quote, close)) + f" --done {shlex.quote(str(done))}"
        return queue, f"{queue} && {wait_lap(rec, done)}"
    queue = queue_command([close], done)
    todo = " → ".join(e.get("command") or f"{e['name']}（{e['manual']} で行う）" for e in hands)
    return queue, f"{first['command']} を流し、続けて {todo} を終えてから: {queue} && {wait_lap(rec, done)}"


def refusal_order(summary: str) -> dict:
    return {**step_result.result(TOOL, "stopped", summary, [], {"status": "refused"}), "exit": EXIT_REFUSED}


# ---------------------------------------------------------------- sprint


def build_close(env: Env, rec: dict, lap: dict, issues: list[int], tag: str = "close") -> list[str]:
    """まとめのステージ（new close --pace auto）を組み、プランのパスを返す。断れば LoopError。
    記録の PR は渡さない（まとめのプランが {queue_record} を持ち、流す時点に探す。#2230）。
    版も渡さない。周回の版はもう出ているため、明示すると最終の検査で変更があったときに既出で止まる。
    まとめの配布の版は流す時点に次に空いている版で決める（#2164）。"""
    prev = rec["laps"][lap["n"] - 2] if lap["n"] >= 2 else None
    carry = ((prev or {}).get("refine") or {}).get("deferred") or []
    out = Path(lap["dir"]) / tag
    cmd = [sys.executable, str(SUPERVISE), "new", "close", "--pace", "auto", "--name", lap["sprint"], "--worktree", str(env.main_dir())]
    cmd += ["--issue", *map(str, issues), "--state", lap["state_path"], "--out", str(out), "--mode", lap.get("mode") or "standard"]
    if carry:
        cmd += ["--carry", *map(str, carry)]
    if lap.get("milestone"):
        cmd += ["--milestone", lap["milestone"]]
    code, so, se = env.run(cmd, cwd=str(env.main_dir()))
    lap["close_command"] = shlex.join(cmd)
    if code != 0:
        raise LoopError(f"new close --pace auto が断った（{code}）: {(se or so).strip()[:300]}")
    manifest = json.loads((out / "sprint.json").read_text(encoding="utf-8"))
    return [p for e in manifest["ステージ"] for p in e.get("plans", [])]


def refused_stop(env: Env, rec: dict, before: dict, step: str, code: int, summary: str) -> dict:
    stop = {"condition": "refused", "values": {"step": step, "exit": code, "summary": summary[:300]}}
    return stop_and_report(env, rec, before, stop, f"周回を組めなかった（{step}）: {summary[:200]}")


def copy_mvv_gate(rec: dict, lap: dict, state: Path, mvv: Path) -> None:
    """承認済みのスプリント MVV を周回の状態へ承認ゲート MVV の記録として写す（決定 11）。"""
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


def read_done_items(path: str) -> list[dict]:
    return jsonio.read(path, want=dict).get("items", [])


def plan_info(plan: str) -> dict:
    try:
        return json.loads(Path(plan).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pr_numbers(value) -> list[int]:
    """判定の記録の `pr`（mvv-gate.py の `--pr` の並び。古い記録や手で渡した値は 1 つの数）を番号の並びにする。"""
    items = value if isinstance(value, list) else [value]
    out = []
    for x in items:
        m = re.search(r"(\d+)\s*$", str(x if x is not None else ""))
        if m:
            out.append(int(m.group(1)))
    return out


def report_pr(item: dict) -> int | None:
    """done の項目の報告にある計画の Pull Request の番号（設計のプランなら設計 PR）。読めなければ None。"""
    try:
        text = Path(item.get("report") or "").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^- Pull Request: (.*)$", text, re.M)
    nums = pr_numbers(m.group(1).strip()) if m else []
    return nums[0] if nums else None


def last_verdict(state_path: str, gate: str, pr: int | None = None) -> dict:
    """mvv-gate.jsonl の、この周回の状態の最後の判定の行（gate は design / release）。pr を渡せば、その PR を判定した行に
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
    if w.get("note"):  # 直して出し直す（#2436 の 6e）: 次の周回の設計がこの指摘を読む
        lines += ["", "### 直して出し直す指摘", "", w["note"]]
    return "\n".join(lines)


def mark_issue(env: Env, rec: dict, w: dict) -> str | None:
    """課題の本文に「人の判断待ち（開発ループ <名>）」の節を書く（本文全体を書き直す）。書けなければ理由を返す。"""
    heading = f"## 人の判断待ち（開発ループ {rec['name']}）"
    try:
        body = env.issue_body(w["number"])
        env.edit_issue(w["number"], gh_sections.replace_section(body, heading, waiting_section(rec, w)))
    except OSError as e:
        return str(e)
    return None


def refresh_state(lap: dict, update: Callable | None) -> None:
    """周回を締める前に、周回の done からスプリント状態を埋める（承認ゲートで締める周回も、設計で使った費用と秒を数える）。"""
    if update and lap.get("state_path"):
        update(lap["state_path"], [d for d in lap.get("dones", []) if Path(d).is_file()])


def cmd_gate(env: Env, name: str, done: str, update: Callable | None = None) -> dict:
    with open_record(env, name) as (rec, before):
        lap = current_lap(rec)
        if not lap or not lap.get("state_path"):
            return refusal_order("スプリントを組んだ進行中の周回が無い")
        refresh_state(lap, update)
        return handle_gate(env, rec, before, lap, read_done_items(done))


def handle_gate(env: Env, rec: dict, before: dict, lap: dict, items: list[dict]) -> dict:
    gated = [i for i in items if i.get("result") == "関門"]
    if not gated:
        return refusal_order("done に承認ゲートで止まった計画が無い")
    if stop := mvv_change_stop(env, rec):
        return stop_and_report(env, rec, before, stop, "プロジェクト MVV が改訂された（承認ゲートの機械のチェック）")
    request = next((i["plan"] for i in gated if plan_info(i["plan"]).get("フェーズ") == RELEASE_REQUEST_PHASE), None)
    if request:  # 並列の計画のレーンが配布の依頼を送った: 承認ゲート 2 として止まらず、配布待ちで締めて次の周回へ（#2436 の 6a）
        return handle_release_pending(env, rec, before, lap, request)
    gate1 = [i for i in gated if plan_info(i["plan"]).get("フェーズ") == DESIGN_PHASE]
    if len(gate1) != len(gated):
        return handle_gate2(env, rec, before, lap, next(i["plan"] for i in gated if i not in gate1))
    unfinished = unfinished_design_issues(items)
    moved, kept, notes = move_to_waiting(env, rec, lap, gate1, unfinished)
    if unfinished:
        notes.append(f"設計が完了していない課題を続きから外した（{' '.join(f'#{n}' for n in unfinished)}）")
    if not kept:
        close_lap(env, rec, lap, FAILED if unfinished else RETURNED)
        return after_close(env, rec, before, f"承認ゲート 1 で全課題を人の判断待ちへ移した（{' '.join(f'#{n}' for n in moved)}）")
    try:
        queue, done = continuation(env, rec, lap, moved, kept)
    except (LoopError, OSError, ValueError, KeyError, StopIteration) as e:
        return refused_stop(env, rec, before, CONTINUATION_STEP, 1, str(e))
    pipeline = f"{queue} && {wait_lap(rec, done)}"
    write_record(env, rec, before)
    update_handoff(
        env, rec, f"承認ゲート 1 で {' '.join(f'#{n}' for n in moved)} を人の判断待ちへ移し、残りで続ける", resume_command(rec), pipeline
    )
    return loop_ok(
        f"承認ゲート 1 で {' '.join(f'#{n}' for n in moved)} を人の判断待ちへ移した。残り {' '.join(f'#{n}' for n in kept)} で続ける",
        rec,
        [{"waiting": moved}, *({"note": x} for x in notes)],
        status="continue",
        next_=pipeline,
        gate=1,
        waiting=moved,
        queue=queue,
        pipeline=pipeline,
    )


def handle_gate2(env: Env, rec: dict, before: dict, lap: dict, plan: str | None = None) -> dict:
    """承認ゲート 2 を人へ戻す。本番を出さずに止まる。plan は承認ゲート 2 で止まった計画（承認の後にそこから続ける）。"""
    row = last_verdict(lap["state_path"], "release")
    lap.setdefault("gates", []).append(gate_copy(GATE_2, row))
    if plan:
        lap["gate2_plan"] = plan
    prs = pr_numbers(row.get("pr"))
    values = {"pr": prs[0] if len(prs) == 1 else prs or None, "verdict": row.get("verdict", "unknown"), "reasons": row.get("reasons") or []}
    stop = {"condition": "gate2_returned", "values": values}
    return stop_and_report(env, rec, before, stop, "承認ゲート 2 を人へ戻した。本番を出さずに止まる（利用者の承認を求める）", {"gate": 2})


RELEASE_REQUEST_PHASE = "配布の依頼"  # parallel_release.RELEASE_REQUEST_PHASE（配布の依頼のプランの「フェーズ」）


def request_record(plan: str) -> tuple[Path, dict]:
    """配布の依頼のプランの状態ディレクトリの `work/request.txt`（`parallel.py request` が書く）のパスと中身。"""
    out = Path(plan).with_name(Path(plan).stem + "-state") / "work" / "request.txt"
    return out, jsonio.read(out, missing={}, broken={}, want=dict)


def handle_release_pending(env: Env, rec: dict, before: dict, lap: dict, plan: str) -> dict:
    """配布の依頼を送った周回を「配布待ち」で締める。まとめ（同じ queue の残りのステージと close）は流さずに記録へ残し、
    答えを待たずに次の周回へ進む。答えは周回の境（`pending_step`）で読む。"""
    out, sent = request_record(plan)
    rest = [s for s in stages_after(lap, plan) if s and s != lap.get("close_plans")]
    lap["release"] = {
        "plan": plan,
        "out": str(out),
        "plan_name": sent.get("plan") or plan_info(plan).get("並列の計画"),
        "request": sent.get("id"),
        "stages": rest,
        "close_plans": lap.get("close_plans") or [],
    }
    close_lap(env, rec, lap, RELEASE_PENDING)
    lap["held_at"] = lap["ended_at"]
    return after_close(env, rec, before, f"周回 {lap['n']} の配布の依頼（{sent.get('id') or '識別子なし'}）を送り、配布待ちで締めた")


def finish_lap(env: Env, rec: dict, lap: dict, outcome: str, state: dict | None = None) -> None:
    """保留の周回（配布待ち・質問待ち）を 1 回だけ締め直す（I1 の例外）。結果・終わった時刻・費用と秒・振り返り・棚卸しを書き換える。"""
    if lap.get("outcome") not in PENDING:
        raise LoopError(f"周回 {lap['n']} は保留の周回でない（締め直しは 1 回だけ）")
    lap["outcome"] = IN_PROGRESS
    close_lap(env, rec, lap, outcome, state)


def unfinished_design_issues(items: list[dict]) -> list[int]:
    """done のうち、設計のプランが完了も関門もしなかった（失敗・流さなかった）課題。設計と承認を経ていないため続きに入れない。"""
    out: set[int] = set()
    for i in items:
        info = plan_info(i.get("plan") or "")
        if info.get("フェーズ") == DESIGN_PHASE and i.get("result") not in FINISHED:
            out |= {int(n) for n in info.get("課題", [])}
    return sorted(out)


def move_to_waiting(
    env: Env, rec: dict, lap: dict, gate1: list[dict], unfinished: list[int] = ()
) -> tuple[list[int], list[int], list[str]]:
    """承認ゲート 1 で止まった計画の課題を人の判断待ちへ移す。移した課題・残る課題・書けなかった理由を返す。
    unfinished（設計が完了していない課題）は判断待ちへ移さず、残る課題からも外す。"""
    moved, notes = [], []
    for i in gate1:
        pr = report_pr(i)
        row = last_verdict(lap["state_path"], "design", pr) if pr is not None else {}
        for n in plan_info(i["plan"]).get("課題", []):
            reasons = row.get("reasons") or []
            w = {
                "number": int(n), "reason": "／".join(reasons) or row.get("verdict", "判定を読めない"), "verdict": row.get("verdict", "unknown"),
                "basis": row.get("basis") or [], "boundary": row.get("boundary") or [], "gate": 1, "pr": pr, "lap": lap["n"],
                "at": env.now(), "report": i.get("report") or "", "state": lap["state_path"],
            }  # fmt: skip
            if pr is not None:  # 承認した先端（#2507）: 並列の計画の外でも書き、答えの時点の先端と比べる
                w["stamp"] = design_head(env, w)
            if pr is not None and (sent := lane_gate1(env, w, i.get("report") or "", lap["state_path"])):
                w.update(sent)  # 並列の計画のレーン: 設計の承認をオーガナイザーへ依頼し、答えは周回の境で読む（#2436 の 6c）
            rec["waiting"].append(w)
            lap.setdefault("gates", []).append(gate_copy(GATE_1, row))
            moved.append(int(n))
            if err := mark_issue(env, rec, w):
                notes.append(err)
    kept = [n for n in (lap.get("issues_kept") or selected_issues(lap)) if n not in moved and n not in unfinished]
    lap["issues_kept"] = kept
    return moved, kept, notes


def lane_gate1(env: Env, w: dict, report: str, state: str, suffix: str = "", stamp: str | None = None) -> dict | None:
    """並列の計画のレーンなら、設計の承認の依頼（`kind: gate1`）をオーガナイザーへ送り、{request, plan, stamp, report, state}
    を返す。レーンでない・送れなければ None（今どおり人の判断待ちとして残す）。印は設計 PR の先頭の SHA（問いの時点の印）。
    同じ承認した先端で利用者へ戻すとき（#2507）は、`stamp` に承認した先端を、`suffix` に依頼の id の接尾辞を渡す
    （同じ id の依頼は答え済みとして受け取られないため）。依頼の理由にはマージの結果の理由（`w.returned`）も載せる。"""
    try:
        import parallel_lane

        got = parallel_lane.lane_of(env.main_dir())
        if not got:
            return None
        stamp = stamp or design_head(env, w)
        reasons = [x for x in (w.get("reason"), w.get("returned")) if x]
        rid = parallel_lane.request_gate1(
            env.main_dir(), got[0], got[1].name, w["number"], int(w["pr"]), report, state, stamp or "0" * 12,
            verdict=w.get("verdict"), reasons=reasons, boundary=list(w.get("boundary") or []), suffix=suffix,
        )  # fmt: skip
    except Exception:  # noqa: BLE001 — 送れなければ今どおり人の判断待ち（loop answer で答える）
        return None
    return {"request": rid, "plan": got[0], "stamp": stamp, "report": report, "state": state}


def design_head(env: Env, w: dict) -> str:
    code, so, _ = env.run(["gh", "pr", "view", str(w["pr"]), "--json", "headRefOid", "-q", ".headRefOid"], cwd=str(env.main_dir()))
    return so.strip() if code == 0 else ""


def regate1(env: Env, w: dict) -> bool:
    """承認の答えを当てる前に、問いの時点の設計 PR の先端（stamp）と今の先端を比べる。動いていれば新しい先端で問い直し、
    読めなければ次の境で読み直す（どちらも True。古い範囲への答えで承認を書かない）。印の無い古い依頼は比べない。"""
    stamp = w.get("stamp") or ""
    if not stamp.strip("0"):
        return False
    head = design_head(env, w)
    if head == stamp:
        return False
    if head and (sent := lane_gate1(env, w, w.get("report") or "", w.get("state") or "")):
        w.update(sent, restamped=stamp)
    return True


MERGED_RESULTS = ("merged", "already_merged")


def note_merge(w: dict, out: dict, lap: int, now: str) -> dict:
    """承認した設計のマージの結果（`design_pr_merge.merge_approved` の出力）を人の判断待ちの行の `merge` へ書く（#2507）。
    同じ承認した先端で同じ result と class が続いた周回の数（streak）と、基盤待ちのための再実行の累計（infra_reruns）を数える。
    infra_reruns は分類が変わっても 0 に戻さない（基盤待ちと実行中が交互に現れても再実行を上限なく増やさない）。"""
    prev = w.get("merge") or {}
    stamp = w.get("stamp") or ""
    same = prev.get("stamp") == stamp
    streak = prev.get("streak", 0) + 1 if same and (prev.get("result"), prev.get("class")) == (out.get("result"), out.get("class")) else 1
    reruns = (prev.get("infra_reruns", 0) if same else 0) + (1 if out.get("infra_rerun") else 0)
    w["merge"] = {**out, "lap": lap, "streak": streak, "infra_reruns": reruns, "stamp": stamp}
    if out.get("result") in MERGED_RESULTS and not w.get("merged_at"):
        w["merged_at"] = now
    return w["merge"]


def _reopen(w: dict) -> None:
    for k in ("answer", "answered_at", "merge", "merged_at"):
        w.pop(k, None)


def reask_design(env: Env, rec: dict, w: dict, head: str) -> bool:
    """承認した設計 PR の先端が承認の後に動いた（または承認した先端が記録に無い）: 新しい先端で問い直す（#2507）。
    レーンは新しい先端で依頼を送り直し、送れなければ何も変えずに次の周回の境で打ち直す（古い答えを当てないため）。
    1 本のループは行の承認した先端を書き換え、答えを外して人の判断待ちへ戻す。問い直したら True。"""
    if not head:
        return False
    old = w.get("stamp") or ""
    if w.get("request"):
        sent = lane_gate1(env, w, w.get("report") or "", w.get("state") or "")
        if not sent:
            return False
        _reopen(w)
        w.update(sent, restamped=old)
    else:
        _reopen(w)
        w.update(stamp=head, restamped=old)
    mark_issue(env, rec, w)
    return True


def giveback_design(env: Env, rec: dict, w: dict, reason: str, lap: int) -> bool:
    """時間で解けないマージの結果を、承認した先端のまま利用者へ戻す（#2507 の決定 9）。依頼にマージの結果の理由を載せる。
    利用者は「直して出し直す」「やらない」か、手を打ってから「承認」を選ぶ。戻した行のマージの結果（streak・infra_reruns）は消す。
    レーンは同じ先端でも別の依頼にするため id に `-m<周回>` を足す。送れなければ何も変えずに次の周回の境で打ち直す。"""
    w["returned"] = reason
    if w.get("request"):
        sent = lane_gate1(env, w, w.get("report") or "", w.get("state") or "", suffix=f"-m{lap}", stamp=w.get("stamp") or "")
        if not sent:
            w.pop("returned", None)
            return False
        _reopen(w)
        w.update(sent)
    else:
        _reopen(w)
    mark_issue(env, rec, w)
    return True


def drop_issues(plan: str, moved: list[int]) -> None:
    """残りの課題でも流す共通の計画（検査など）から、人の判断待ちへ移した課題を外して書き戻す。
    計画の `課題` と、pr のステップの材料の `closes`（スプリント PR の Closes の行）の両方から外す。"""
    info = plan_info(plan)
    gone = set(moved)
    issues = info.get("課題") or []
    steps = [s for s in info.get("steps") or [] if isinstance(s, dict) and isinstance(s.get("materials"), dict)]
    if not (gone & set(issues)) and not any(gone & set(s["materials"].get("closes") or []) for s in steps):
        return
    info["課題"] = [n for n in issues if n not in gone]
    for s in steps:
        if "closes" in s["materials"]:
            s["materials"]["closes"] = [n for n in s["materials"]["closes"] if n not in gone]
    jsonio.write_atomic(Path(plan), info, indent=2)


CONTINUATION_STEP = "続きの queue"  # 承認ゲート 1 の後の続きを組めなかったときの refused の step（loop start が組み直す）


def continuation(env: Env, rec: dict, lap: dict, moved: list[int], kept: list[int]) -> tuple[str, Path]:
    """承認ゲート 1 の後の続きの queue: 関門 1 の後の then_of のステージから、外した課題だけの計画を除き、
    外した課題を除いた new close --pace auto を最後の --then につなぐ。"""
    index = json.loads(Path(lap["manifest"]).read_text(encoding="utf-8"))["ステージ"]
    at = next(i for i, e in enumerate(index) if e.get("name") == GATE_1)
    moved = sorted(set(moved) | (set(selected_issues(lap)) - set(kept)))  # 設計が完了していない課題の計画も外す
    stages = []
    for e in index[at + 1 :]:
        plans = [
            p for p in e.get("plans", []) if not (set(plan_info(p).get("課題", [])) and set(plan_info(p).get("課題", [])) <= set(moved))
        ]
        for p in plans:
            drop_issues(p, moved)
        if plans:
            stages.append(plans)
    tag = f"close-{len(lap.get('dones', [])) + 1}"
    stages.append(build_close(env, rec, lap, kept, tag=tag))
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    lap.setdefault("dones", []).append(str(done))
    return queue_command(stages, done), done


GATE_2_RESUME_STEPS = ("bump", "promote-approved", "merge-approved")  # 承認ゲート 2 の承認の後に入るステップ（pace.md の続け方）


def gate2_resume_step(plan: str) -> str | None:
    """承認ゲート 2 で止まった計画を承認の後に続けるステップ: 雛形の本番は bump、昇格は promote-approved、
    マージの merge-gate で止まった計画は merge-approved。どれも無い（gate-2 のプラン）なら None（手で行う本番へ進む）。"""
    ids = {s.get("id") for s in plan_info(plan).get("steps") or [] if isinstance(s, dict)}
    return next((s for s in GATE_2_RESUME_STEPS if s in ids), None)


def stages_after(lap: dict, plan: str) -> list[list[str]]:
    """plan のステージより後で、同じ queue が --then で流すはずだったステージの計画（承認ゲート 2 で流れずに残ったもの）。"""
    try:
        index = json.loads(Path(lap.get("manifest") or "").read_text(encoding="utf-8"))["ステージ"]
    except (OSError, ValueError, KeyError):
        return []
    at = next((i for i, e in enumerate(index) if plan in (e.get("plans") or [])), None)
    if at is None:
        return []
    key = index[at].get("then_of") or index[at].get("name")
    return [e["plans"] for e in index[at + 1 :] if e.get("plans") and e.get("then_of") == key]


# ---------------------------------------------------------------- lap


def failure_key(state: dict) -> dict | None:
    """スプリント状態の行（完了でもまだでもない最初の行）の課題・種類・止まったところ（決定 8）。理由の文は含めない。"""
    for p in state.get("plans", []):
        row = p.get("row") or {}
        if row.get("result") not in (None, "まだ", NOT_RUN, *FINISHED):  # 関門は loop gate が扱い、流さなかった計画は失敗の元でない
            return {"issues": sorted(p.get("issues") or []), "kind": p.get("kind", ""), "step": row.get("result")}
    return None


def copy_state_gates(lap: dict, state: dict) -> None:
    """スプリント状態の承認ゲートを周回へ写す（写してあるものは重ねない）。"""
    for g in state.get("gates", []):
        copy = {
            "name": g.get("name"),
            "by": g.get("by", "user"),
            "verdict": g.get("verdict", g.get("outcome", "approved")),
            "basis": g.get("basis") or [],
            "pr": g.get("pr"),
            "at": g.get("at", ""),
        }
        if copy not in lap.setdefault("gates", []):
            lap["gates"].append(copy)


def refine_summary(env: Env, rec: dict) -> dict:
    """棚卸しの結果（upkeep の state dir の candidates.json と rank.json）を周回の記録の形にする。"""
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
    """周回の間の棚卸しの深さの行と、調査の費用と秒を周回の記録へ写す（#2315）。

    調査の費用は呼び出しごとの記録（`research/calls.jsonl`。結果を捨てた呼び出しも含む）から集める。背景の調査は周回を
    締めた後にも終わるため、このループのすべての周回を記録から数え直し、締め済みの周回へ後から終わった費用を精算する。"""
    d = env.main_dir() / ".ndf" / "backlog"
    since = lap.get("started_at") or ""
    rows = jsonio.read_jsonl(d / "depths.jsonl", missing=[], skip_broken=True)
    lap["depths"] = [{k: r.get(k) for k in ("depth", "event", "trigger", "cost", "seconds")} for r in rows if (r.get("at") or "") >= since]
    calls = jsonio.read_jsonl(d / "research" / "calls.jsonl", missing=[], skip_broken=True)
    for each in {id(x): x for x in [*rec.get("laps", []), lap]}.values():
        mine = [r for r in calls if r.get("lap") == each.get("n") and (r.get("by") or {}).get("loop") == rec.get("name")]
        if mine:
            each["research"] = {
                "items": [r.get("id") for r in mine if r.get("ok")],
                "cost": round(sum(float(r.get("cost") or 0) for r in mine), 6),
                "seconds": round(sum(float(r.get("seconds") or 0) for r in mine), 3),
            }


def close_lap(env: Env, rec: dict, lap: dict, outcome: str, state: dict | None = None, deferred_issues=(), done_rows=()) -> None:
    """周回を締め、スプリント状態とまとめの記録から値を写して固定する。"""
    if lap.get("outcome") != IN_PROGRESS:
        raise LoopError(f"周回 {lap['n']} は締めてある（I1）")
    state = (
        state if state is not None else (jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {})
    )
    rows = [p.get("row") or {} for p in state.get("plans", [])]
    lap["cost"] = round(sum(float(r.get("cost") or 0) for r in rows), 6)
    lap["seconds"] = round(sum(float(r.get("seconds") or 0) for r in rows), 3)
    lap["versions"] = state.get("versions") or lap.get("versions") or {}
    copy_state_gates(lap, state)
    close_rep = next((p for p in state.get("plans", []) if p.get("kind") == "まとめ"), None)
    if close_rep:
        lap["retro"] = (close_rep.get("row") or {}).get("report", "")
    lap["refine"] = refine_summary(env, rec)
    backlog_summary(env, rec, lap)
    lap["deferred_issues"] = sorted(set(lap.get("deferred_issues") or []) | {int(n) for n in deferred_issues})
    lap["failure"] = failure_key(state) if outcome == FAILED else None
    if outcome == FAILED and lap["failure"] is None:
        lap["failure"] = {"issues": selected_issues(lap), "kind": "周回", "step": "conductor"}
    lap["relay_dir"] = os.environ.get("NDF_RELAY_DIR")
    lap["outcome"], lap["ended_at"] = outcome, env.now()


def after_close(env: Env, rec: dict, before: dict, summary: str) -> dict:
    """周回を締めた後の境の判定。止まれば 3、続けば ndf_next を返す。"""
    stop = held_stop(rec, boundary_stop(env, rec))
    if stop:
        return stop_and_report(env, rec, before, stop, f"{summary}。止まる条件に当たった（{stop['condition']}）")
    write_record(env, rec, before)
    nxt = resume_command(rec)
    update_handoff(env, rec, f"周回 {rec['laps'][-1]['n']} を締めた。次の周回へ", nxt, select_cmd(rec["name"]))
    return loop_ok(f"{summary}。次の周回へ進む", rec, [rec["laps"][-1]], status="continue", next_=nxt, ndf_next=f"```ndf-next\n{nxt}\n```")
