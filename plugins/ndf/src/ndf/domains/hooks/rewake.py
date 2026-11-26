"""裏の作業を待つ子のキャッシュを寿命の手前で起床させる hook（#4068・#4199 の詳細設計の起床の hook のセクション）。

中継のラッパーが、確かめたバージョンの子にだけ `--settings` で Stop の `asyncRewake` の登録を渡す。Stop ごとに
バックグラウンドで起動し、今回の応答を確かめて世代を書き、寿命 − 余白の時点まで眠って確かめ直し、起床させるなら
起床の文を標準エラーへ書いて終了コード 2 で終わる。ほかはすべて 0 で、例外も 0 にする（Stop を壊さない）。

世代・状態・起床のフラグ・起床の記録の読み書きは、すべて子を止める排他（`signals.STOP_LOCK`）の中で行う。眠る間の世代の
読み取りだけは排他を取らない（起床の直前に排他の中で突き合わせ直す）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional, TextIO

from ndf.adapters import transcript
from ndf.domains.hooks import tables
from ndf.domains.hooks.guards import relay_busy
from ndf.domains.relay import keep_cache, signals
from ndf.domains.relay.keep_cache import LastTurn, Ratios
from ndf.domains.relay.signals import Generation

# 起床の理由（状態の `stopped`・`wait_stop` と `stop_waking` の行の `reason`）
MISS, FORM, LIMIT, UNMATCHED = "miss", "form", "limit", "unmatched"
# 一時的な理由（状態の `noted` に 1 度だけ書く）
PAST, TTL_UNKNOWN = "past", "ttl_unknown"

QFor = Callable[[str, str], Ratios]


class Target(NamedTuple):
    """起床の hook が扱う子の会話の Stop。"""

    relay_dir: Path
    launch: str
    session_id: str
    transcript: str
    works: int
    monitors: int
    last_message: Optional[str]
    hook_id: str
    started: float


class Settled(NamedTuple):
    """`_settle` の結果。`reason` が None のときだけ眠る（`gen` は `sleeps` 真の世代、`q`・`until` が在る）。"""

    keep: Optional[dict]
    turn: Optional[LastTurn]
    gen: Optional[Generation]
    q: Optional[Ratios]
    until: Optional[float]
    stale: bool
    reason: Optional[str]


def _stale(reason: str) -> Settled:
    return Settled(None, None, None, None, None, True, reason)


def run(
    argv: list,
    stdin: TextIO = sys.stdin,
    stderr: TextIO = sys.stderr,
    environ: Mapping[str, str] = os.environ,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """起床させるなら 2、ほかは 0。例外はすべて 0。"""
    try:
        started = clock()
        raw = stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        t = _target(payload, environ, started) if isinstance(payload, dict) else None
        return 0 if t is None else _handle_stop(t, stderr, clock, sleep)
    except Exception:  # noqa: BLE001, NDF401 外部コマンド: 起床の hook の失敗で Stop を壊さない（次の Stop で起動し直る）
        return 0


def _target(payload: Mapping[str, Any], environ: Mapping[str, str], started: float) -> Optional[Target]:
    """ラッパーが登録を渡した中継の子の会話の Stop なら `Target`。ほか（入れ子の claude・登録の無い起動）は None。"""
    session, path = payload.get("session_id"), payload.get("transcript_path")
    found = signals.rewake_child(environ, session)
    if found is None or not isinstance(path, str) or not path:
        return None
    directory, launch = found
    tasks = payload.get("background_tasks")
    works, monitors = relay_busy.work_count(tasks, relay_busy.table_params()) if isinstance(tasks, list) else (0, 0)
    last = payload.get("last_assistant_message")
    return Target(directory, launch, session, path, works, monitors, last if isinstance(last, str) else None, uuid.uuid4().hex, started)


def _q_for(model: str, ttl: str) -> Ratios:
    return keep_cache.ratios(tables.policy_value, model, ttl)


def _handle_stop(t: Target, stderr: TextIO, clock: Callable[[], float], sleep: Callable[[float], None]) -> int:
    """「処理の手順」の 2〜6。"""
    keep = signals.read_keep(t.relay_dir, t.session_id)
    if t.works == 0 and not (keep and ("wait_id" in keep or keep.get("pending"))):
        return 0
    tail = transcript.read_tail(t.transcript, int(tables.policy_value("hook.transcript-tail", "count")))
    read = keep_cache.last_turn(tail, t.last_message, t.started)
    if read.reason == "newer":
        return 0
    if read.reason == FORM:
        _formed(t, read.ref, tail, _q_for)
        return 0
    if read.turn is None:
        _arrival(t, read.reason)
        return 0
    settled = _settle(t, read.turn, tail, _q_for, clock)
    if settled.reason is not None or not _sleep_until(t, settled.gen, settled.until, settled.q.interval, clock, sleep):
        return 0
    return _finish(t, settled.gen, settled.turn, settled.q, stderr, clock)


@contextmanager
def _locked(t: Target) -> Iterator[bool]:
    """子を止める排他を取り、今の起動が `t.launch` なら真を渡す。"""
    with signals.held(t.relay_dir / signals.STOP_LOCK, relay_busy.lock_wait()) as got:
        yield got and signals.current_launch(t.relay_dir) == t.launch


def _cache_row(t: Target, kind: str, wait_id: Any, **fields: Any) -> None:
    signals.append_cache_log(t.relay_dir, t.launch, t.session_id, kind, wait_id, **fields)


def _stop_waking(t: Target, keep: Mapping[str, Any], reason: str, scope: str) -> None:
    _cache_row(t, "stop_waking", keep.get("wait_id"), reason=reason, scope=scope, k=keep.get("k"), n_max=keep.get("n_max"))


def _note_once(t: Target, keep: dict, reason: str) -> bool:
    """一時的な理由の `stop_waking`（`scope` `once`）を会話で 1 度だけ書く。書いたら真。"""
    found = keep.get("noted")
    noted = list(found) if isinstance(found, list) else []
    if reason in noted:
        return False
    _stop_waking(t, keep, reason, "once")
    keep["noted"] = [*noted, reason]
    return True


def _keep_of(t: Target) -> dict:
    found = signals.read_keep(t.relay_dir, t.session_id)
    return {} if found is None else found


def _arrival(t: Target, reason: str) -> None:
    """今回の応答がまだ記録に無い Stop: 先行の予約を失効させ、その予約の起床させていない待ちを取り消す（到着による失効）。"""
    with _locked(t) as ok:
        if not ok:
            return
        keep = _keep_of(t)
        gen = signals.generation_of(keep)
        wait_id = keep.get("wait_id")
        revoked = signals.revoke_generation(keep, t.started)
        voided = revoked and signals.void_wait(keep, gen.at)
        if voided:
            _cache_row(t, "wait_void", wait_id, reason=signals.BY_ARRIVAL)
        if t.works >= 1 or revoked:
            message = gen.message_id if gen else None
            _cache_row(t, "arrival", wait_id, message_id=message, works=t.works, revoked=revoked, voided=voided)
        noted = _note_once(t, keep, reason)
        if revoked or noted:
            signals.update_keep(t.relay_dir, t.session_id, keep)


def _stop_session(t: Target, keep: dict, reason: str) -> None:
    """会話を止める理由（`form` / `miss`）で、書いていなければ `stopped` と `stop_waking`（`scope` `session`）を書く。"""
    if keep.get("stopped"):
        return
    keep["stopped"] = reason
    _stop_waking(t, keep, reason, "session")


def _formed(t: Target, ref: Optional[tuple], tail: str, q_for: QFor) -> None:
    """今回の応答の形が違う Stop（「処理の手順」の 3 の `form` の分岐）。次の予約は作らない。"""
    with _locked(t) as ok:
        if not ok:
            return
        if ref is None:
            keep = _keep_of(t)
        else:
            got, keep = signals.bump_generation(t.relay_dir, t.launch, t.session_id, Generation(ref[1], t.hook_id, ref[0]), t.works)
            if got != "taken":
                return
            if keep.get("pending"):
                keep = _reconcile(t, keep, tail, q_for)
        _stop_session(t, keep, FORM)
        signals.update_keep(t.relay_dir, t.session_id, keep)


def _reconcile(t: Target, keep: dict, tail: str, q_for: QFor) -> dict:
    """起床のフラグを突き合わせ、`wake_result` を 1 行書いてフラグを消した状態を返す（排他の中で呼ぶ）。"""
    flag = keep["pending"]
    reply = keep_cache.wake_reply(tail, flag["after"])
    q = q_for(flag["model"], flag["ttl"])
    row: dict[str, Any] = {
        "wake_id": flag["wake_id"],
        "tokens": flag["tokens"],
        "model": flag["model"],
        "ttl": flag["ttl"],
        "w_1h": q.w_1h,
        "w_5m": q.w_5m,
        "r": q.r,
        "out": q.out,
        "hit": None,
        "first_read": None,
    }
    keep = {k: v for k, v in keep.items() if k != "pending"}
    if reply.kind == "wake":
        u = reply.usage
        hit = keep_cache.cache_hit(reply.first_read, flag["tokens"], q.hit)
        row.update(hit=hit, first_read=reply.first_read, calls=u.calls, cache_read=u.cache_read, output=u.output, priced=u.priced)
        if u.priced:
            row.update(cache_write_1h=u.cache_write_1h, cache_write_5m=u.cache_write_5m)
    elif reply.kind == "preempted":
        row.update(calls=0, cache_read=0, cache_write_1h=0, cache_write_5m=0, output=0, priced=True)
    else:
        row.update(priced=False)
    _cache_row(t, "wake_result", keep.get("wait_id"), outcome="woke" if reply.kind == "wake" else reply.kind, **row)
    if reply.kind == "wake" and not row["hit"]:
        _stop_session(t, keep, MISS)
    if reply.kind == "unknown" and "wait_id" in keep:
        keep["wait_stop"] = UNMATCHED
        _stop_waking(t, keep, UNMATCHED, "wait")
    return keep


def _end_wait(t: Target, keep: dict, turn: LastTurn, tail: str, q_for: QFor) -> None:
    """待ちを終える入力を確かめたら `wait_end` を書き、待ちの項目を消す（`_settle` の (5)）。"""
    last = keep.get("last")
    if "wait_id" not in keep or not isinstance(last, dict):
        return
    op = keep_cache.wake_opening(tail, turn.message_id)
    opened = keep.get("opened")
    if op.kind == "normal" and op.at > keep["wait_id"]:
        at, first = op.at, op.first_read
    elif op.kind in ("normal", "unknown") and isinstance(opened, (int, float)) and opened > last["at"]:
        at, first = opened, None
    else:
        return
    q = q_for(last["model"], last["ttl"])
    hit = None if first is None else keep_cache.cache_hit(first, last["tokens"], q.hit)
    fields = {"opened_at": at, "age": at - last["at"], "k": keep.get("k"), "hit": hit, "cache_read": first}
    _cache_row(t, "wait_end", keep["wait_id"], **fields, tokens=last["tokens"], ttl=last["ttl"], model=last["model"], w=q.w, r=q.r)
    signals.drop_wait(keep)


def _start_wait(t: Target, keep: dict, turn: LastTurn, gen: Generation, q_for: QFor) -> Optional[str]:
    """待つ子で待ちが無ければ待ちを始める（`_settle` の (6)）。眠らない理由を返す。"""
    if t.works == 0:
        return "idle"
    if signals.question_present(t.relay_dir):
        return "question"
    if "wait_id" in keep:
        return None
    if turn.ttl is None:
        _note_once(t, keep, TTL_UNKNOWN)
        return TTL_UNKNOWN
    n = keep_cache.n_max(turn.tokens, q_for(turn.model, turn.ttl))
    keep.update(wait_id=gen.at, k=0, spent=0, n_max=n)
    _cache_row(t, "wait_start", gen.at, tokens=turn.tokens, ttl=turn.ttl, model=turn.model, n_max=n)
    return None


def _sleep_plan(t: Target, keep: dict, turn: LastTurn, q_for: QFor, clock: Callable[[], float]) -> tuple:
    """眠るかを決める（`_settle` の (8)）。（理由, 倍率, 起床の時点）。"""
    try:
        q = q_for(turn.model, turn.ttl)
    except tables.TableError:  # 倍率を読めない設定では眠らない（`ndf check` が落とす）
        return "ratios", None, None
    until = keep_cache.wake_at(turn.at, turn.ttl, q.wake_margin)
    if until is None:
        return "short_ttl", None, None
    if until <= clock():
        _note_once(t, keep, PAST)
        return PAST, None, None
    return None, q, until


def _settle(t: Target, turn: LastTurn, tail: str, q_for: QFor, clock: Callable[[], float]) -> Settled:
    """子を止める排他の中で、世代を取り、寿命を補い、フラグと待ちを精算し、眠るかを決める（`_settle` の手順 (1)〜(8)）。"""
    with _locked(t) as ok:
        if not ok:
            return _stale("launch")
        gen = Generation(turn.at, t.hook_id, turn.message_id)
        got, keep = signals.bump_generation(t.relay_dir, t.launch, t.session_id, gen, t.works)
        if got != "taken":
            return _stale(got)
        ttl = turn.ttl if turn.ttl is not None else keep.get("ttl")
        if ttl is not None:
            keep["ttl"] = ttl
        turn = turn._replace(ttl=ttl)
        if keep.get("pending"):
            keep = _reconcile(t, keep, tail, q_for)
        _end_wait(t, keep, turn, tail, q_for)
        reason = "stopped" if keep.get("stopped") else ("wait_stop" if keep.get("wait_stop") else None)
        if reason is None:
            reason = _start_wait(t, keep, turn, gen, q_for)
        keep["last"] = {"message_id": turn.message_id, "tokens": turn.tokens, "model": turn.model, "ttl": turn.ttl, "at": turn.at}
        q = until = None
        if reason is None:
            reason, q, until = _sleep_plan(t, keep, turn, q_for, clock)
        if reason is None:
            gen = gen._replace(sleeps=True)
            keep = signals.with_generation(keep, gen)
        signals.update_keep(t.relay_dir, t.session_id, keep)
        return Settled(keep, turn, gen, q, until, False, reason)


def _sleep_until(
    t: Target, gen: Generation, until: float, interval: float, clock: Callable[[], float], sleep: Callable[[float], None]
) -> bool:
    """`until` まで `interval` ごとに世代だけを読み（排他を取らない）、`gen` のままなら真。"""
    while True:
        now = clock()
        if now >= until:
            return True
        sleep(min(interval, until - now))
        if signals.generation_of(signals.read_keep(t.relay_dir, t.session_id)) != gen:
            return False


def _finish(t: Target, gen: Generation, turn: LastTurn, q: Ratios, stderr: TextIO, clock: Callable[[], float]) -> int:
    """排他の中で世代・起動・質問のファイルを突き合わせ、次の手を決めて書く。起床させるなら起床の文を書いて 2。"""
    with _locked(t) as ok:
        if not ok:
            return 0
        keep = _keep_of(t)
        if signals.generation_of(keep) != gen or signals.question_present(t.relay_dir):
            return 0
        if keep.get("stopped") or keep.get("wait_stop") or "wait_id" not in keep:
            return 0
        k, limit, spent = keep["k"], keep["n_max"], keep["spent"]
        step = keep_cache.wake_step(k, limit, spent, turn.tokens, q)
        if step.kind == "stop":
            keep["wait_stop"] = LIMIT
            signals.update_keep(t.relay_dir, t.session_id, keep)
            _stop_waking(t, keep, LIMIT, "wait")
            return 0
        now = clock()
        wake_id = uuid.uuid4().hex
        flag = {"wake_id": wake_id, "tokens": turn.tokens, "at": now, "after": turn.message_id, "model": turn.model, "ttl": turn.ttl}
        keep.update(pending=flag, k=k + 1, spent=spent + keep_cache.wake_cost(turn.tokens, q))
        keep = signals.with_generation(keep, Generation(now, t.hook_id))
        try:
            signals.update_keep(t.relay_dir, t.session_id, keep)
        except OSError:  # noqa: NDF401 ファイル: 状態を書けなければ起床させない（k・pending は書かれていない）
            return 0
        _wake_row(t, keep, wake_id, turn, now, limit)
        stderr.write(keep_cache.WAKE_TEXT.format(k=k + 1, n_max=limit) + "\n")
        return 2


def _wake_row(t: Target, keep: Mapping[str, Any], wake_id: str, turn: LastTurn, now: float, limit: int) -> None:
    """`wake` の行を書く。書けなくても状態は起床を持つので起床させる（集計は `wake` の無い `wake_result` を数える）。"""
    with suppress(OSError):  # noqa: NDF402 ファイル: 記録を書けなくても起床は状態のフラグで突き合わせる
        _cache_row(
            t,
            "wake",
            keep["wait_id"],
            wake_id=wake_id,
            tokens=turn.tokens,
            age=now - turn.at,
            k=keep["k"],
            n_max=limit,
            spent=keep["spent"],
        )
