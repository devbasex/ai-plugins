"""利用者の入力で数え手を数え直す guard（docs/specifications/ndf-rebuild.md の決定 92）。
中継が送った入力（マーカーがある）では数え直さない。

中継は入力を送る前に `relay.markers.mark` で `<状態の保存先>/relay/markers/<セッション>.json` を書く。ここで
`relay.markers.take` がマーカーを消せたら、その入力は中継が送ったものである（決定 111）。

起床の hook の登録を渡した中継の子では、子を止める排他の中で起床の状態の世代を入力の時刻に書き換え、眠る hook の
予約を失効させる（#4068 の詳細設計の「UserPromptSubmit の guard」）。起床のフラグが在る間の、頭が `WAKE_HEAD` の
入力は中継が送った起床の文として数え直さない（設計の決定 10）。
"""

from __future__ import annotations

import time
from typing import Any

from ndf.domains.hooks import tables
from ndf.domains.hooks.guards import relay_busy
from ndf.domains.hooks.verdict import Verdict, passing
from ndf.domains.relay import keep_cache, markers, signals

# 世代に書く入力の時刻（テストが差し替える）
clock = time.time


def _relayed(ctx, svc) -> bool:
    try:
        return markers.take(svc.state_dir, ctx.session_id)
    except markers.MarkerError:  # noqa: NDF401 ファイル: 形の違うセッションの ID にはマーカーが無い（利用者の入力）
        return False


def _rewake_child(ctx) -> Any:
    """起床の状態を書いてよい中継の子なら（保存先, 起動の ID）。ほかは None。"""
    directory, launch = signals.relay_dir(ctx.env), signals.relay_launch(ctx.env)
    session = ctx.session_id
    if directory is None or launch is None or session != signals.relay_session(ctx.env):
        return None
    return (directory, launch) if signals.keep_path(directory, session) is not None else None


def _keep_turn(ctx) -> bool:
    """世代を入力の時刻に書き換え、待ちの間の通常の入力なら `opened` を書く。起床の文の入力なら真。"""
    found = _rewake_child(ctx)
    if found is None:
        return False
    directory, launch = found
    wait = relay_busy.lock_wait()
    with signals.held(directory / signals.STOP_LOCK, wait) as got:
        if not got or not signals.rewake_on(directory, launch):
            return False
        before = signals.read_keep(directory, ctx.session_id)
        prompt = ctx.payload.get("prompt")
        woke = before is not None and bool(before.get("pending")) and isinstance(prompt, str) and prompt.startswith(keep_cache.WAKE_HEAD)
        now = clock()
        _, keep = signals.bump_generation(directory, launch, ctx.session_id, signals.Generation(now, signals.BY_TURN))
        if "wait_id" in keep and not woke and "opened" not in keep:
            signals.update_keep(directory, ctx.session_id, {**keep, "opened": now})
        return woke


def _woke(ctx) -> bool:
    """起床の状態を書き、起床の文の入力かを返す。書けなくても入力は止めない（利用者の入力として数え直す）。"""
    try:
        return _keep_turn(ctx)
    except (OSError, signals.SignalError, tables.TableError):  # noqa: NDF401 ファイル: 起床の状態を書けなくても入力は通す
        return False


def check(ctx, svc) -> Verdict:
    relayed, woke = _relayed(ctx, svc), _woke(ctx)
    source = svc.counter.reset(source="relay" if relayed or woke else "human")
    return passing(turn_source=source)
