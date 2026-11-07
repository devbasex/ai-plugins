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


def _lane_or_stop(main: Path, a, reply) -> pp.Lane:
    try:
        lane = pp.load_valid_plan(main, a.name).lane(a.lane)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        reply("stopped", f"並列の計画 {a.name} のレーン {a.lane} を読めない: {exc}", code=1)
    if lane is None:
        reply("stopped", f"並列の計画 {a.name} にレーン {a.lane} が無い", code=1)
    return lane


def _write_next(main: Path, lane: pp.Lane, command: str) -> None:
    """引継ぎ文書の「次にやること」を続きの 1 行へ置き換える。"""
    path = handoff.paths_of(main, lane.handoff_name())[0]
    text = path.read_text(encoding="utf-8")
    fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", command)), default=0) + 1)
    path.write_text(
        handoff_doc.replace_body(text, handoff_doc.NEXT_STEP_SECTION, f"\n{fence}text\n{command}\n{fence}\n\n") or text, encoding="utf-8"
    )


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
    resume = f"python3 {shlex.quote(str(SUPERVISE))} queue --resume {shlex.quote(str(a.done))}"
    if not ph.covered(ph.read_hold(main, a.name), a.lane):
        reply("ok", f"レーン {a.lane} の答え待ちの停止は解けている。止まらずに続ける", cleared=True, next=resume)
    try:
        _write_next(main, lane, resume)
        ph.place_mark(main, a.name, a.lane, "boundary")
    except OSError as exc:
        reply("stopped", f"引継ぎ文書か再開の印を書けない: {exc}", code=2)
    stop = pf.stop_self(main) or f"python3 {shlex.quote(str(pf.RELAY))} stop {shlex.quote(a.lane)}"
    reply("ok", f"レーン {a.lane} の続きを書いた。自分のラッパーを止める", next=stop, resume=resume)
