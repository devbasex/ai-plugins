"""開発ループのリリース承認ゲートの答えの読み取りと、承認の後の続きのコマンド（sprint_loop から分けた）。

並列の計画のレーン（#2436）では、リリース承認ゲートの答えはリリースの依頼の答えとして耐久の記録に届く。周回の境の `pending_step` が
保留の周回（リリース待ち・質問待ち）と、オーガナイザーへ送った設計の承認の答えを待たずに読み、残りの工程（まとめ）を流す。"""

from __future__ import annotations

import shlex
from pathlib import Path

import clock
import gate_rejections as gr
import jsonio

from sprint_loop_record import (
    RELEASE_GATE,
    canonical,
    SPRINT_STATE,
    SUPERVISE,
    FAILED,
    RETURNED,
    QUESTION_PENDING,
    RELEASE_PENDING,
    Env,
    pending_laps,
    selected_issues,
    write_record,
)
from sprint_loop_gate import (
    after_close,
    build_close,
    finish_lap,
    gate2_resume_step,
    loop_ok,
    plan_info,
    queue_command,
    stages_after,
    wait_lap,
)


EPOCH = clock.parse("1970-01-01T00:00:00Z")  # 時刻の無い答え（止まった時刻の無い古い記録のとき）の並びの値


def gate2_answer(lap: dict, since: str | None = None) -> str | None:
    """リリース承認ゲートの答え: 止まった時刻（since）より後の、周回の状態の利用者の承認と差し戻しの記録のうち最新のもの。
    approved / rejected、どちらも無ければ None。since より前の答え（同じ周回の前の承認ゲートへの答え）は数えない。"""
    path = lap.get("state_path") or ""
    state = jsonio.read(path, missing={}, broken={}, want=dict) if path else {}
    try:
        rows = gr.read_rejections()
    except jsonio.JsonReadError:
        rows = []
    sp = str(Path(path).resolve()) if path else None
    # 承認の行（#2183）は状態ファイルの gates の方で読む
    rejections = [r for r in rows if gr.is_rejection(r) and canonical(r.get("gate")) == RELEASE_GATE and r.get("sprint") == sp]
    return latest_gate2_answer(state.get("gates", []), rejections, since)


def latest_gate2_answer(gates: list[dict], rejections: list[dict], since: str | None) -> str | None:
    """リリース承認ゲートの利用者の承認（gates）と差し戻し（rejections）のうち、since 以後で最新のもの。同じ時刻は承認を採る。"""
    after = clock.parse(since, naive="utc") if since else None

    def fresh(at):
        t = clock.parse(at, naive="utc") if at else None
        if after is None:
            return t or EPOCH
        return t if t is not None and t >= after else None

    answers = [(k, "approved") for g in gates if canonical(g.get("name")) == RELEASE_GATE and g.get("by") != "mvv" and (k := fresh(g.get("at")))]
    answers += [(k, "rejected") for r in rejections if (k := fresh(r.get("at")))]
    return max(answers, key=lambda a: a[0])[1] if answers else None


def gate2_pipeline(env: Env, rec: dict, lap: dict, stop: dict) -> str:
    """リリース承認ゲートの承認の後の続き: 止まった計画を経路に合う再開のステップから続け → 同じ queue の残りのステージと
    まとめの queue → wait → loop lap の 1 行。続けるステップが無い計画（gate-2 のプラン）は手で行う本番を先に示す。"""
    # 止まった記録の values.pr は検査のスプリント PR で、記録の PR ではない。まとめは {queue_record} で流す時点に探す（#2230 の決定 5）
    close = build_close(env, rec, lap, lap.get("issues_kept") or selected_issues(lap))
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    plan = lap.get("gate2_plan") or lap.get("prod_plan") or "<本番のプラン>"
    moved = {w["number"] for w in rec.get("waiting", []) if w.get("lap") == lap["n"]}
    rest = [
        [p for p in s if not (set(plan_info(p).get("課題") or []) and set(plan_info(p).get("課題") or []) <= moved)]
        for s in stages_after(lap, plan)
    ]
    stages = [s for s in rest if s] + [close]
    queue = queue_command(stages, done)
    lap.setdefault("dones", []).append(str(done))
    step = gate2_resume_step(plan)
    if step is None:
        return f"手で行う本番のステージ（{plan} の後）を終えてから: {queue} && {wait_lap(rec, done)}"
    run = f"python3 {shlex.quote(str(SUPERVISE))} run {shlex.quote(plan)} --from {step}"
    return f"{run} && {queue} && {wait_lap(rec, done)}"


# ---------------------------------------------------------------- 保留の周回（#2436 の受け入れ条件 6a・6d・6e）


def pending_requests(rec: dict) -> dict[str, list[str]]:
    """答えを読む依頼の id を計画名ごとに: 保留の周回の依頼と、オーガナイザーへ送った答えの無い設計の承認。"""
    out: dict[str, list[str]] = {}
    for lap in pending_laps(rec):
        part = lap.get("release") if lap["outcome"] == RELEASE_PENDING else lap.get("question")
        if part and part.get("request"):
            out.setdefault(part.get("plan_name") or "", []).append(part["request"])
    for w in rec.get("waiting", []):
        if w.get("request") and not w.get("answer"):
            out.setdefault(w.get("plan") or "", []).append(w["request"])
    return out


def read_answers(env: Env, rec: dict) -> dict[str, dict]:
    """依頼の id ごとの {"answer", "defer"}（待たない）。"""
    got: dict[str, dict] = {}
    for plan, ids in pending_requests(rec).items():
        got.update(env.lane_answers(plan, ids))
    return got


def answer_of(answers: dict, rid: str | None) -> dict | None:
    a = (answers.get(rid or "") or {}).get("answer")
    return a if isinstance(a, dict) else None


def finish_pipeline(env: Env, rec: dict, lap: dict) -> str:
    """保留の周回の残り（質問待ちなら止まったプランの続き → 同じ queue の残りのステージ）とまとめを流し、
    `loop finish` で締め直す 1 行。組んだ行は記録へ残し、打ち直しでは同じ行を返す（まとめを二重に流さない）。"""
    part = lap["release"] if lap["outcome"] == RELEASE_PENDING else lap["question"]
    if (part.get("finish") or {}).get("pipeline"):
        return part["finish"]["pipeline"]
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    lap.setdefault("dones", []).append(str(done))
    head = ""
    if lap["outcome"] == RELEASE_PENDING:
        stages = list(part.get("stages") or [])
    else:
        head = f"python3 {shlex.quote(str(SUPERVISE))} run {shlex.quote(part['plan'])} --from {shlex.quote(part['step'])} && "
        stages = [st for st in stages_after(lap, part["plan"]) if st != lap.get("close_plans")]
    stages = [st for st in [*stages, part.get("close_plans") or lap.get("close_plans") or []] if st]
    finish = f"python3 {shlex.quote(str(SPRINT_STATE))} loop finish {rec['name']} {lap['n']} --done {shlex.quote(str(done))}"
    wait = f"python3 {shlex.quote(str(SUPERVISE))} wait {shlex.quote(str(done))}"
    body = f"{queue_command(stages, done)} && {wait} && " if stages else ""
    pipeline = f"{head}{body}{finish}"
    part["finish"] = {"started_at": env.now(), "done": str(done), "pipeline": pipeline}
    return pipeline


def finish_result(env: Env, rec: dict, before: dict, lap: dict) -> dict:
    pipeline = finish_pipeline(env, rec, lap)
    write_record(env, rec, before)
    return loop_ok(
        f"周回 {lap['n']}（{lap['outcome']}）の答えが届いた。残りの工程を流してから次の周回へ進む",
        rec,
        [{"lap": lap["n"], "outcome": lap["outcome"]}],
        status="finish",
        next_=pipeline,
        lap=lap["n"],
        pipeline=pipeline,
    )


def answered_lap(rec: dict, answers: dict, results: tuple[str, ...] = ("released",)) -> dict | None:
    """答えの届いた最も古い保留の周回（リリース待ちは results の答え、質問待ちはどの答えでも）。"""
    for lap in pending_laps(rec):
        if lap["outcome"] == QUESTION_PENDING:
            if answer_of(answers, (lap.get("question") or {}).get("request")):
                return lap
        elif (a := answer_of(answers, (lap.get("release") or {}).get("request"))) and a.get("result") in results:
            return lap
    return None


def settle_release(env: Env, rec: dict, lap: dict, a: dict) -> None:
    """リリース待ちの周回へ届いた答えを書く。差し戻しは人へ戻した、失敗は失敗で締め直す（まとめは流さない）。"""
    lap["release"]["answer"] = {**a, "read_at": env.now()}
    if a.get("result") == "released":
        if a.get("version"):
            lap.setdefault("versions", {})["prod"] = a["version"]
        return
    if a.get("result") == "rejected":
        finish_lap(env, rec, lap, RETURNED)
        rec.setdefault("fixes", []).append(
            {"lap": lap["n"], "issues": lap.get("issues_kept") or selected_issues(lap), "reason": a.get("reason") or "", "at": env.now()}
        )
        return
    finish_lap(env, rec, lap, FAILED)
    lap["failure"] = {"issues": lap.get("issues_kept") or selected_issues(lap), "kind": "リリース", "step": "リリースの失敗"}


def settle_refused(env: Env, rec: dict, answers: dict) -> bool:
    """リリース待ちの周回へ届いた差し戻し・失敗の答えを書いて締め直す（まとめは流さない）。1 つでも書いたら True。"""
    settled = False
    for lap in pending_laps(rec):
        a = answer_of(answers, (lap.get("release") or {}).get("request")) if lap["outcome"] == RELEASE_PENDING else None
        if a and a.get("result") != "released":
            settle_release(env, rec, lap, a)
            settled = True
    return settled


def pending_step(env: Env, rec: dict, before: dict, apply_gate1=None) -> dict | None:
    """周回の境で、答えの届いた依頼を古い順に扱う（境の 2〜6）。まとめを流す周回があればその結果を返し、無ければ None。"""
    answers = read_answers(env, rec)
    if apply_gate1 is not None:
        apply_gate1(env, rec, answers)
    settle_refused(env, rec, answers)
    lap = answered_lap(rec, answers)
    if lap is None:
        return None
    if lap["outcome"] == RELEASE_PENDING and not (lap["release"].get("answer") or {}).get("result"):
        settle_release(env, rec, lap, answer_of(answers, lap["release"]["request"]))
    return finish_result(env, rec, before, lap)


def pending_pipeline(env: Env, rec: dict, before: dict) -> dict:
    """`loop pending`: 答えの届いた保留の周回のまとめの pipeline だけを返す（止まったレーンの残りを一時レーンが流す。異常系 11）。"""
    answers = read_answers(env, rec)
    settled = settle_refused(env, rec, answers)  # 差し戻し・失敗も締め直して残さない（全レーンが止まった後の入口でも）
    lap = answered_lap(rec, answers)
    if lap is None:
        if settled:
            write_record(env, rec, before)
        return loop_ok("答えの届いた保留の周回は無い" + ("（差し戻し・失敗は締め直した）" if settled else ""), rec, status="none")
    if lap["outcome"] == RELEASE_PENDING and not (lap["release"].get("answer") or {}).get("result"):
        settle_release(env, rec, lap, answer_of(answers, lap["release"]["request"]))
    return finish_result(env, rec, before, lap)


def finish_held(env: Env, rec: dict, before: dict, lap: dict, items: list[dict], state: dict) -> dict:
    """`loop finish`: 保留の周回の残りの done を読み、周回を 1 回だけ締め直して周回の境へ戻る。
    質問待ちの続きがリリースの依頼で止まったら、リリース待ちへ移して答えを待つ。"""
    from sprint_loop_gate import DONE_RESULTS, RELEASE_REQUEST_PHASE, handle_release_pending

    part = lap["release"] if lap["outcome"] == RELEASE_PENDING else lap["question"]
    (part.setdefault("finish", {}))["ended_at"] = env.now()
    request = next(
        (i["plan"] for i in items if i.get("result") == "関門" and plan_info(i.get("plan") or "").get("フェーズ") == RELEASE_REQUEST_PHASE),
        None,
    )
    if lap["outcome"] == QUESTION_PENDING and request:
        lap["outcome"] = "進行中"
        return handle_release_pending(env, rec, before, lap, request)
    bad = [i for i in items if i.get("unfilled") or i.get("result") not in DONE_RESULTS]
    version = (lap.get("versions") or {}).get("prod")
    finish_lap(env, rec, lap, FAILED if bad or not items else "完了", state)
    if version and lap["outcome"] == "完了":
        lap.setdefault("versions", {})["prod"] = version
    return after_close(env, rec, before, f"周回 {lap['n']} の残りを流し、{lap['outcome']} で締め直した")
