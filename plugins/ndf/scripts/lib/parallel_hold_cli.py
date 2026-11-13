"""`parallel.py park` の本体（#2707）。

- `--done <D>`: 答え待ちの停止でプランを流さなかった 1 スプリントのレーンが打つ。引継ぎ文書へ続きを書き、再開の印を置いてから自分のラッパーを止める
- `--clear`: 人が止めたままにするレーンの再開の印を消し、耐久ワークフローへ知らせる

`reply` は結果を出して終える `parallel.py` の同名の関数である。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import handoff
import handoff_doc
import parallel_files as pf
import parallel_hold as ph
import parallel_lane as pl
import parallel_plan as pp

SUPERVISE = Path(__file__).resolve().parent.parent / "supervise.py"
PROGRESS = SUPERVISE.with_name("progress.py")


def _lane_or_stop(main: Path, a, reply) -> pp.Lane:
    try:
        lane = pp.load_valid_plan(main, a.name).lane(a.lane)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        reply("stopped", f"並列の計画 {a.name} のレーン {a.lane} を読めない: {exc}", code=1)
    if lane is None:
        reply("stopped", f"並列の計画 {a.name} にレーン {a.lane} が無い", code=1)
    return lane


def _write_next(main: Path, lane: pp.Lane, command: str) -> None:
    """引継ぎ文書の「次にやること」の先頭へ続きの 1 行を足す。

    既存の本文は残す（承認ゲートの保留の手順が同じ節へ書いた続きのコマンドと依頼の id を、答えの後に
    `parallel_lane.lane_waits` が読むため）。同じ 1 行が既にあれば足さない。"""
    path = handoff.paths_of(main, lane.handoff_name())[0]
    text = path.read_text(encoding="utf-8")
    found = handoff_doc.find_section(text, handoff_doc.NEXT_STEP_SECTION)
    if found is None:
        return
    body = text[found.body_start : found.body_end]
    if command in body:
        return
    fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", command)), default=0) + 1)
    rest = body.lstrip("\r\n")
    block = f"\n{fence}text\n{command}\n{fence}\n\n"
    handoff.write_doc(path, handoff_doc.replace_body(text, handoff_doc.NEXT_STEP_SECTION, block + rest) or text)


def _clear(main: Path, a, reply) -> None:
    try:
        removed = ph.drop_mark(main, a.name, a.lane)
    except OSError as exc:
        reply("stopped", f"再開の印を消せない: {exc}", code=2)
    try:
        pl.send_left_stopped(main, a.name, a.lane)
    except Exception as exc:  # noqa: BLE001  耐久の記録の失敗の型は DBOS が決める。送れなければ人へ返す
        reply("stopped", f"耐久ワークフローへ送れない: {exc}", code=2, mark_removed=removed)
    reply("ok", f"レーン {a.lane} を止めたままにする（再開の印を{'消した' if removed else '持っていなかった'}）", mark_removed=removed)


def cmd_park(main: Path, a, reply) -> None:
    lane = _lane_or_stop(main, a, reply)
    if a.clear:
        _clear(main, a, reply)
    if getattr(a, "table", None):  # 工程の進行表で流すスプリントは、進行表が次の工程を決める（#3092）
        resume = f"python3 {shlex.quote(str(PROGRESS))} next {shlex.quote(a.table)}"
    else:  # 手で流したキュー（`supervise.py queue` の answer_hold の park）の続き。スプリントの工程は --table で来る
        resume = f"python3 {shlex.quote(str(SUPERVISE))} queue --resume {shlex.quote(str(a.done))}"
    if not ph.covered(ph.read_hold(main, a.name), a.lane):
        reply("ok", f"レーン {a.lane} の答え待ちの停止は解けている。止まらずに続ける", cleared=True, next=resume)
    try:
        _write_next(main, lane, resume)
        ph.place_mark(main, a.name, a.lane, "boundary")
    except (OSError, handoff.Failure) as exc:
        reply("stopped", f"引継ぎ文書か再開の印を書けない: {exc}", code=2)
    stop = pf.stop_self(main) or f"python3 {shlex.quote(str(pf.RELAY))} stop {shlex.quote(a.lane)}"
    reply("ok", f"レーン {a.lane} の続きを書いた。自分のラッパーを止める", next=stop, resume=resume)
