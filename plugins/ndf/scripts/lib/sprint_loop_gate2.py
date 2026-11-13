"""開発ループのリリース承認ゲートの答えの読み取りと、承認の後の続きのコマンド（sprint_loop から分けた）。

並列の計画のレーン（#2436）では、リリース承認ゲートの答えはリリースの依頼の答えとして耐久の記録に届く。ループの 1 回の境の `pending_step` が
保留したループの 1 回（リリース待ち・質問待ち）と、オーガナイザーへ送った設計の承認の答えを待たずに読み、残りの工程（まとめ）を流す。"""

from __future__ import annotations

import shlex
from pathlib import Path

import clock
import gate_rejections as gr
import jsonio
import step_result

from sprint_loop_record import (
    RELEASE_GATE,
    canonical,
    SPRINT_STATE,
    TOOL,
    DONE,
    FAILED,
    RETURNED,
    QUESTION_PENDING,
    RELEASE_PENDING,
    Env,
    pending_laps,
    selected_issues,
    open_record,
    write_record,
)
from sprint_loop_gate import (
    after_close,
    finish_lap,
    loop_ok,
)
from sprint_loop_table import cancel_table, table_finish_ok, table_finish_pipeline, table_gate2_pipeline, table_versions


EPOCH = clock.parse("1970-01-01T00:00:00Z")  # 時刻の無い答え（止まった時刻の無い古い記録のとき）の並びの値


def gate2_answer(lap: dict, since: str | None = None) -> str | None:
    """リリース承認ゲートの答え: 止まった時刻（since）より後の、ループの 1 回の状態の利用者の承認と差し戻しの記録のうち最新のもの。
    approved / rejected、どちらも無ければ None。since より前の答え（同じループの 1 回の前の承認ゲートへの答え）は数えない。"""
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

    answers = [
        (k, "approved") for g in gates if canonical(g.get("name")) == RELEASE_GATE and g.get("by") != "mvv" and (k := fresh(g.get("at")))
    ]
    answers += [(k, "rejected") for r in rejections if (k := fresh(r.get("at")))]
    return max(answers, key=lambda a: a[0])[1] if answers else None


def gate2_pipeline(env: Env, rec: dict, lap: dict, stop: dict) -> str:
    """リリース承認ゲートの承認の後の続き: 進行表の待ちの行へ承認を書き、同じ進行表を次の工程から流して締める（#3092）。
    進行表を持たないループの 1 回（この版より前に組んだもの）は、手で行う本番を終えてからスプリント状態の行で締める。"""
    if lap.get("table"):
        return table_gate2_pipeline(rec, lap)
    return f"手で行う本番を終えてから: python3 {shlex.quote(str(SPRINT_STATE))} loop lap {rec['name']}"


# ---------------------------------------------------------------- 保留したループの 1 回（#2436 の受け入れ条件 6a・6d・6e）


def pending_requests(rec: dict) -> dict[str, list[str]]:
    """答えを読む依頼の id を計画名ごとに: 保留したループの 1 回の依頼と、オーガナイザーへ送った答えの無い設計の承認。"""
    out: dict[str, list[str]] = {}
    for lap in pending_laps(rec):
        part = lap.get("release") if lap["outcome"] == RELEASE_PENDING else lap.get("question")
        if part and part.get("request"):
            out.setdefault(part.get("plan_name") or "", []).append(part["request"])
    for w in rec.get("waiting", []):
        if w.get("answer"):
            continue
        before = [b["id"] for b in w.get("requests_before") or [] if not b.get("settled")]
        ids = [*([w["request"]] if w.get("request") else []), *before]
        if ids:
            out.setdefault(w.get("plan") or "", []).extend(ids)
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
    """保留したループの 1 回の残り（答えを進行表の待ちの行へ届けて、同じ進行表の残りのまとめ）を流し、`loop finish` で締め直す 1 行。
    組んだ行は記録へ残し、打ち直しでは同じ行を返す（まとめを二重に流さない）。進行表を持たないループの 1 回（この版より前に組んだもの）は
    残りを流さずに締め直す。"""
    part = lap["release"] if lap["outcome"] == RELEASE_PENDING else lap["question"]
    if (part.get("finish") or {}).get("pipeline"):
        return part["finish"]["pipeline"]
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    lap.setdefault("dones", []).append(str(done))
    if lap.get("table"):
        pipeline = table_finish_pipeline(rec, lap, done)
    else:
        pipeline = f"python3 {shlex.quote(str(SPRINT_STATE))} loop finish {rec['name']} {lap['n']} --done {shlex.quote(str(done))}"
    part["finish"] = {"started_at": env.now(), "done": str(done), "pipeline": pipeline}
    return pipeline


def finish_result(env: Env, rec: dict, before: dict, lap: dict) -> dict:
    pipeline = finish_pipeline(env, rec, lap)
    write_record(env, rec, before)
    return loop_ok(
        f"ループの {lap['n']} 回目（{lap['outcome']}）の答えが届いた。残りの工程を流してからループの次の 1 回へ進む",
        rec,
        [{"lap": lap["n"], "outcome": lap["outcome"]}],
        status="finish",
        next_=pipeline,
        lap=lap["n"],
        pipeline=pipeline,
    )


def answered_lap(rec: dict, answers: dict, results: tuple[str, ...] = ("released",)) -> dict | None:
    """答えの届いた最も古い保留したループの 1 回（リリース待ちは results の答え、質問待ちはどの答えでも）。"""
    for lap in pending_laps(rec):
        if lap["outcome"] == QUESTION_PENDING:
            if answer_of(answers, (lap.get("question") or {}).get("request")):
                return lap
        elif (a := answer_of(answers, (lap.get("release") or {}).get("request"))) and a.get("result") in results:
            return lap
    return None


def settle_release(env: Env, rec: dict, lap: dict, a: dict) -> None:
    """リリース待ちのループの 1 回へ届いた答えを書く。差し戻しは人へ戻した、失敗は失敗で締め直す（まとめは流さない）。"""
    lap["release"]["answer"] = {**a, "read_at": env.now()}
    if a.get("result") == "released":
        if a.get("version"):
            lap.setdefault("versions", {})["prod"] = a["version"]
        return
    if lap.get("table"):  # 差し戻し・失敗の進行表は流し直さない（まとめは流さない）。中止できなくても締める
        cancel_table(env, lap, f"リリースの依頼の答え: {a.get('result')}")
    if a.get("result") == "rejected":
        finish_lap(env, rec, lap, RETURNED)
        rec.setdefault("fixes", []).append(
            {"lap": lap["n"], "issues": lap.get("issues_kept") or selected_issues(lap), "reason": a.get("reason") or "", "at": env.now()}
        )
        return
    finish_lap(env, rec, lap, FAILED)
    lap["failure"] = {"issues": lap.get("issues_kept") or selected_issues(lap), "kind": "リリース", "step": "リリースの失敗"}


def settle_refused(env: Env, rec: dict, answers: dict) -> bool:
    """リリース待ちのループの 1 回へ届いた差し戻し・失敗の答えを書いて締め直す（まとめは流さない）。1 つでも書いたら True。"""
    settled = False
    for lap in pending_laps(rec):
        a = answer_of(answers, (lap.get("release") or {}).get("request")) if lap["outcome"] == RELEASE_PENDING else None
        if a and a.get("result") != "released":
            settle_release(env, rec, lap, a)
            settled = True
    return settled


def pending_step(env: Env, rec: dict, before: dict, apply_gate1=None) -> dict | None:
    """ループの 1 回の境で、答えの届いた依頼を古い順に扱う（境の 2〜6）。まとめを流すループの 1 回があればその結果を返し、無ければ None。"""
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
    """`loop pending`: 答えの届いた保留したループの 1 回のまとめの pipeline だけを返す（止まったレーンの残りを一時レーンが流す。異常系 11）。"""
    answers = read_answers(env, rec)
    settled = settle_refused(env, rec, answers)  # 差し戻し・失敗も締め直して残さない（全レーンが止まった後の入口でも）
    lap = answered_lap(rec, answers)
    if lap is None:
        if settled:
            write_record(env, rec, before)
        return loop_ok(
            "答えの届いた保留したループの 1 回は無い" + ("（差し戻し・失敗は締め直した）" if settled else ""), rec, status="none"
        )
    if lap["outcome"] == RELEASE_PENDING and not (lap["release"].get("answer") or {}).get("result"):
        settle_release(env, rec, lap, answer_of(answers, lap["release"]["request"]))
    return finish_result(env, rec, before, lap)


def finish_held_table(env: Env, rec: dict, before: dict, lap: dict, done: str, state: dict) -> dict:
    """`loop finish`（進行表で流したループの 1 回）: `progress.py next` の出力で 1 回だけ締め直す。リリースまで届いたループの 1 回は
    完了で締め、後のまとめの失敗は失敗の鍵とは別に残す（#2911）。"""
    part = lap["release"] if lap["outcome"] == RELEASE_PENDING else lap["question"]
    (part.setdefault("finish", {}))["ended_at"] = env.now()
    ok = table_finish_ok(done)
    version = (lap.get("versions") or {}).get("prod")
    released = lap["outcome"] == RELEASE_PENDING and released_answer(lap)
    table_versions(env, lap)
    finish_lap(env, rec, lap, DONE if released or ok else FAILED, state)
    if released and not ok:
        lap["close_failure"] = {"issues": lap.get("issues_kept") or selected_issues(lap), "kind": "まとめ", "step": "止まった"}
    if version and lap["outcome"] == DONE:
        lap.setdefault("versions", {})["prod"] = version
    return after_close(env, rec, before, f"ループの {lap['n']} 回目 の残りを流し、{lap['outcome']} で締め直した")


def released_answer(lap: dict) -> dict | None:
    """ループの 1 回の記録に残ったリリースの答えが released ならその答え。"""
    a = (lap.get("release") or {}).get("answer") or {}
    return a if a.get("result") == "released" else None


def reclose_released(env: Env, rec: dict) -> list[int]:
    """前の版がリリースの後のまとめの失敗で「失敗」と書いたループの 1 回を、記録に残ったリリースの答え（released）から完了で締め直す（#2911）。
    失敗の鍵はまとめの失敗（close_failure）へ移し、版を戻す。締め直したループの 1 回の番号を返す（2 度目は何もしない）。"""
    done = []
    for lap in rec.get("laps", []):
        a = released_answer(lap)
        if lap.get("outcome") != FAILED or not a:
            continue
        lap["outcome"], lap["close_failure"], lap["failure"] = DONE, lap.get("failure"), None
        if a.get("version"):
            lap.setdefault("versions", {})["prod"] = a["version"]
        lap["reclosed_at"] = env.now()
        done.append(lap["n"])
    return done


def cmd_reclose(env: Env, name: str) -> dict:
    """`loop reclose`: リリースまで届いたのに「失敗」で締めたループの 1 回を、記録のリリースの答えから完了で締め直す（#2911）。
    締め直すループの 1 回の行だけを I1 の検査から外し、ほかの終わったループの 1 回の行は書き換えない。"""
    with open_record(env, name) as (rec, before):
        done = reclose_released(env, rec)
        if done:
            write_record(env, rec, {**before, "laps": [lap for lap in before.get("laps", []) if lap["n"] not in done]})
        laps = [{"lap": n, "outcome": DONE} for n in done]
        summary = f"ループの {done} 回目 を完了で締め直した" if done else "締め直すループの 1 回は無い"
        return step_result.result(TOOL, "ok", summary, laps, {"reclosed": done})
