"""並列の計画の見回りの判定（#2649）: 問いの取り下げ・開発ループの止まり・停滞・一時レーンの途中停止。

耐久ワークフローの中で呼ぶため、耐久ステップを呼ばず、`run`（`parallel_run._Run`）の値と観測の値だけで決める。
`run` の `ask`・`publish_questions`・`drain`・`pause` だけを呼び、`parallel_run` を import しない（循環を作らない）。

版の印 `run.judging`（観測の値の `can_judge`）が無い記録の再生では、取り下げ・開発ループの止まり・読み切りを通らない。
耐久ワークフローの呼び出し（イベントの更新・メッセージの受信）が増えると、記録の再生の順が崩れるためである（決定 4・I8）。
"""

from __future__ import annotations

import sys
from typing import Optional

import clock
import parallel_release as pr
from parallel_files import RELAY
from parallel_observe import _asking_lanes

STARTED = "started"


def _seconds_of(value) -> Optional[float]:
    """ISO 8601 の文字列をエポック秒へ。文字列でない・読めないなら None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        got = clock.parse(value, naive="utc")
    except (TypeError, ValueError):
        return None
    return got.timestamp() if got is not None else None


def _lane_commands(n: str, stop: bool = True) -> dict:
    out = {"起こし直す": f"python3 {RELAY} restart {n}"}
    return {**out, "止める": f"python3 {RELAY} stop {n}"} if stop else out


def _result_text(result) -> str:
    return "起動した" if result == STARTED else f"断られた（{result}）"


def _tries_text(tries: Optional[list]) -> str:
    """再起動の試みの回数と最後の結果（`restarts` の無い古い行では空）。"""
    if tries is None:
        return ""
    last = f"、最後の結果: {_result_text(tries[-1].get('result'))}" if tries else ""
    return f"。その後の再起動の試み {len(tries)} 回{last}"


def _withdraw_reason(q: dict, r: dict, loops: dict) -> str:
    """停滞の問い q を取り下げる理由（取り下げないなら空文字）。開発ループの止まりの問いは、記録が止まった状態でなくなるか、
    行が `running` でなくなったときに外す（一覧の時刻では外さない。両方なら記録の側を理由にする）。
    ほかは一覧の時刻が問いを出した時刻より進んだときに外す。"""
    if q.get("loop_stop"):
        why = "レーンが running でない" if r.get("state") != "running" else ""
        return "開発ループの記録が止まっていない" if q["lane"] not in loops else why
    return "レーンが動き出した" if (_seconds_of(r.get("at")) or 0) > float(q["asked_at"]) else ""


def withdraw_settled(run, rows: dict, loops: dict) -> None:
    """前提が消えた停滞の問いを取り下げる（決定 5。理由の判定は `_withdraw_reason`）。
    外した id は答え済みへ入れ、後から届いた答えを捨てる（I5）。"""
    gone = []
    for q in run.questions:
        if q.get("kind") != "stall" or not q.get("lane") or q.get("asked_at") is None:
            continue
        why = _withdraw_reason(q, rows.get(q["lane"]) or {}, loops)
        if why:
            gone.append((q["id"], why))
    if not gone:
        return
    ids = {rid for rid, _ in gone}
    run.answered |= ids
    run.questions = [q for q in run.questions if q.get("id") not in ids]
    run.publish_questions()
    for rid, why in gone:
        print(f"parallel: 問い {rid} を取り下げた（{why}）", file=sys.stderr, flush=True)


def release_halted(run, rows: dict) -> None:
    """「止める」と答えたレーンが動き出したら（人が手で起こした）、待ちの記録による保留の判定へ戻す（決定 3）。"""
    run.halted = [n for n in run.halted if (rows.get(n) or {}).get("state") not in ("running", "awaiting_approval")]


def _has_loop_stop_question(run, n: str) -> bool:
    """答えていない開発ループの止まりの問いがこのレーンにあるか（I7）。"""
    return any(q.get("lane") == n and q.get("loop_stop") for q in run.questions)


def ask_loop_stops(run, rows: dict, loops: dict, now: float, limit: float) -> None:
    """開発ループの記録が `waiting_only` 以外で止まったのに行が `running` のレーンを、区切りを待たずに問う（決定 6・I6・I7）。
    記録が今の起動より後に止まったものだけを問う。記録が止まっていないレーンは次に問える時刻を消す。"""
    for n in run.order:
        stop = loops.get(n)
        if stop is None:
            run.loop_until.pop(n, None)
            continue
        if n in run.sprint or stop.get("condition") == "waiting_only" or (rows.get(n) or {}).get("state") != "running":
            continue
        started, stopped_at = _seconds_of(run.lanes[n].get("started")), _seconds_of(stop.get("at"))
        if started is not None and stopped_at is not None and stopped_at <= started:
            continue  # 前の起動のときの止まり（続きから引き継いだレーン）
        if now < run.loop_until.get(n, 0) or _has_loop_stop_question(run, n):
            continue
        run.loop_until[n] = run.stall_until[n] = now + limit
        text = f"レーン {n} の開発ループ {stop.get('loop')} が止まった（条件: {stop.get('condition')}）。一覧の状態は running のまま"
        run.ask(
            "stall", f"{n}-l{int(now)}", n, text, ["起こし直す", "止める", "待つ"], commands=_lane_commands(n),
            loop_stop={"loop": stop.get("loop"), "condition": stop.get("condition")}, asked_at=now,
        )  # fmt: skip


def ask_stalls(run, rows: dict, now: float, limit: float) -> None:
    """動かないレーンを区切りごとに 1 回だけ問う（異常系 3）。経過は最後に動いた時刻と最後に起動できた再起動の遅い方から数える（I4）。
    再起動を断られて止まったレーンは、断られた試みにつき 1 回だけ問う（決定 3・I10）。行に `restarts` が無い古い記録では、
    試みを文面に載せず、経過は一覧の時刻だけで数える。"""
    for n in run.order:
        r = rows.get(n) or {}
        tries = r.get("restarts") if isinstance(r.get("restarts"), list) else None
        if r.get("state") == "stopped" and tries and tries[-1].get("result") != STARTED:
            _ask_refused(run, n, tries, now)
            continue
        at = _seconds_of(r.get("at"))
        ok = [_seconds_of(t.get("at")) for t in tries or [] if t.get("result") == STARTED]
        base = max([x for x in (at, *ok) if x is not None], default=None)
        if r.get("state") != "running" or base is None or now - base < limit or now < run.stall_until.get(n, 0):
            continue
        if _has_loop_stop_question(run, n):
            continue  # 答えていない開発ループの止まりの問いに重ねない（I7）
        run.stall_until[n] = now + limit
        text = f"レーン {n} が {int((now - base) // 60)} 分動いていない（最後に動いた時刻: {r.get('at')}{_tries_text(tries)}）"
        run.ask("stall", f"{n}-s{int(now)}", n, text, ["起こし直す", "止める", "待つ"], commands=_lane_commands(n), asked_at=now)


def _ask_refused(run, n: str, tries: list, now: float) -> None:
    """再起動を断られて止まったレーンの問い。選択肢は起動できないレーンの問いと同じ 2 つで、`commands` は起こし直す 1 行だけ
    （止まったレーンへ `relay.py stop` を返すと、その名前は無いと断られる）。期限（`stall_until`・`loop_until`）に縛らない。"""
    if not run.lanes[n].get("started") or n in run.left_stopped:
        return
    last = tries[-1]
    text = f"レーン {n} の再起動が断られ、止まったまま（再起動の試み {len(tries)} 回、最後の結果: {_result_text(last.get('result'))}）"
    run.ask(
        "stall", f"{n}-r{last.get('at')}", n, text, ["起こし直す", "止める"], commands=_lane_commands(n, stop=False),
        refused_at=last.get("at"), asked_at=now,
    )  # fmt: skip


def _helper_candidate(run, rows: dict) -> Optional[str]:
    """途中停止を問う候補の一時レーンの名前。リリースの途中で一時レーンが止まり、リリース承認ゲートの依頼も、その一時レーンが
    送った答えていない問いも無く、今の起動についてまだ問うていない。"""
    rel = run.current
    if rel is None or not run.launch or run.helper_asked == run.launch["seq"]:
        return None
    name = pr.helper_name(run.name, rel["n"])
    if (rows.get(name) or {}).get("state") != "stopped" or any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in run.questions):
        return None
    return None if name in _asking_lanes(run) else name


def ask_helper(run, rows: dict) -> None:
    """一時レーンがリリースの途中で止まったら問う（受け入れ条件 6g）。版の印のある観測では、行の時刻が今の起動の指示より後のときだけ
    候補とし、話題 `inbox` と `resume` を読み切ってからもう一度判定する（決定 1・2・I1・I2）。1 回の起動の指示につき 1 件（I3）。"""
    name = _helper_candidate(run, rows)
    if name is None:
        return
    if run.judging:
        at, launched = _seconds_of((rows.get(name) or {}).get("at")), run.launch.get("at")
        if at is None or (launched is not None and at <= float(launched)):
            return  # 時刻の無い行・前の起動の行
        run.drain()
        if (name := _helper_candidate(run, rows)) is None:
            return
    rel = run.current
    run.helper_asked = run.launch["seq"]
    run.ask(
        "helper", f"{name}-h{run.launch['seq']}", name, f"一時レーン {name} がリリース {rel['n']} の途中で止まった（settle もリリース承認ゲートの依頼も無い）",
        ["続きから起こし直す", "止める"], commands={"続きから起こし直す": run.launch.get("spawn")}, n=rel["n"],
    )  # fmt: skip


def track_ls_failure(run, seen: dict, limit: float) -> None:
    """一覧の失敗が停滞の区切りより長く続いたら、区切りごとに 1 回問う（#2509 の I3）。成功に戻ったら追跡を解き、答えていない
    一覧の失敗の問いを外す（I4。残すと答えを待って計画が終われない）。"""
    if run.ls_ok:
        run.ls_failed_since, run.ls_ask_until = None, 0.0
        if any(q.get("kind") == "observe" for q in run.questions):
            run.questions = [q for q in run.questions if q.get("kind") != "observe"]
            run.publish_questions()
        return
    now = float(seen.get("now") or 0)
    if run.ls_failed_since is None:
        run.ls_failed_since = now
    if now - run.ls_failed_since < limit or now < run.ls_ask_until or not seen.get("can_ask"):
        return
    run.ls_ask_until = now + limit
    mins = int((now - run.ls_failed_since) // 60)
    rebuild, rid = "ラッパーの環境を作り直す", f"{run.name}-observe-{int(now)}"
    text = f"レーンの状態の一覧（relay.py ls）が {mins} 分続けて失敗している（終了コード {seen.get('code')}: {seen.get('err') or ''}）"
    run.ask("observe", rid, None, text, [rebuild, "待つ"], commands={rebuild: f"python3 {RELAY} status"})


def forget_wake(run, lane: str) -> None:
    """レーンを起こす 1 行（`wake`）と再開待ち（`revive`・`revive_at`）から外す。"""
    run.wake = [n for n in run.wake if n != lane]
    run.revive = [n for n in run.revive if n != lane]
    run.revive_at.pop(lane, None)


def answer_refused(run, lane: str, stop: bool) -> None:
    """再起動を断られた問いへの答え（決定 3・I11）。「止める」は保留と起こす 1 行から外して `halted` へ入れ、止まったものと数える。
    「起こし直す」はレーンの種類によらず、動き出すまで依存先の起動と計画の終わりを保留する。"""
    forget_wake(run, lane)
    run.halted = [n for n in run.halted if n != lane] + ([lane] if stop else [])
    if not stop:
        run.revive, run.revive_at[lane], run.wake = sorted({*run.revive, lane}), run.seen_at, sorted({*run.wake, lane})
        run.pause("notice", lanes=[], attach=[])


def watch(run, seen: dict, rows: dict, now: float, limit: float) -> None:
    """見回りの 1 回の判定の順（設計の「見回りの 1 回の順序」）。"""
    loops = seen.get("loops") or {}
    if run.judging:
        withdraw_settled(run, rows, loops)
        release_halted(run, rows)
        ask_loop_stops(run, rows, loops, now, limit)
    ask_stalls(run, rows, now, limit)
    ask_helper(run, rows)
