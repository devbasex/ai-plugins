"""`parallel.py` の `start`・`wait` の本体（#2647 の決定 12。`parallel.py` の行数を上限に収める）。

引数の定義（`build_parser`）は契約の検査が静的に読むため `parallel.py` に残す。どの関数も、結果を出して終える
`reply`（`parallel.py` の同名の関数）を受け取り、それで終える。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import parallel_answer as pa
import parallel_external_cli as px
import parallel_hold as ph
import parallel_homes
import parallel_lane as pl
import parallel_plan as pp


def wait_line(self_path: Path, name: str) -> str:
    return f"python3 {self_path} wait {name}"


def start(a, reply, main: Path, checked_plan, self_path: Path) -> None:
    """`run` をセッションから切り離して起動する。どの経路でも打ったセッションへオーガナイザーの印を付ける（#2637）。"""
    px.refuse_in_factory_run(reply)
    plan, items, metrics = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがあるため、レーンを 1 本も起動しない", items, 1, **metrics)
    import parallel_run as pr_run

    env, notes = pr_run.environment_report(main, plan)
    notes = [*notes, *ph.limit_items(main)]
    parallel_homes.record_home(main)  # 別のリポジトリのレーンと計画の外の判定が読む計画の所在の一覧（#2733）
    pid = pl.live_run_pid(main, a.name)
    if pid is not None:
        marked = pr_run.mark_organizer(main, a.name)  # 打ったセッションへ付け直す（#2637）
        reply("ok", f"run は動いている（pid {pid}）", notes, pid=pid, organizer=marked, next=wait_line(self_path, a.name), environment=env)
    if pl.run_status(main, a.name) is None:
        px.manual_or_none(main, plan, reply)
    marked = pr_run.mark_organizer(main, a.name)  # run.pid の死んだ途中の計画は step_init を流し直さないため、ここで付ける
    log = pp.plan_dir(main, a.name) / "run.log"
    pl.clear_settled(main, a.name)  # 切り離した run が消す前に判定が読んでも、動いていないと読まない
    with open(pp.plan_dir(main, a.name) / "run.log", "ab") as f:
        p = subprocess.Popen([sys.executable, str(self_path), "run", a.name], cwd=main, stdout=f, stderr=f, start_new_session=True)
    (pp.plan_dir(main, a.name) / "run.pid").write_text(f"{p.pid}\n")
    nxt = wait_line(self_path, a.name)
    reply("ok", f"run を切り離して起動した（pid {p.pid}）", notes, pid=p.pid, log=str(log), organizer=marked, next=nxt, environment=env)


def wait(a, reply, main: Path, self_path: Path) -> None:
    """状態の変化か終わりまで待つ。"""
    import durable

    if not pl.record_exists(main, a.name):
        reply("stopped", f"並列の計画 {a.name} の耐久の記録が無い", code=2)
    keys = ["pause", "questions", "answer-hold"]
    got = durable.wait_from(
        pl.KIND, pl.record_identity(main, a.name), pl.workflow_id(a.name), keys, a.after, a.timeout, start=pl.record_start(main)
    )
    if got.kind == "timeout":
        reply(
            "stopped",
            f"{a.timeout:.0f} 秒の上限に達した。同じ wait を打ち直す",
            code=3,
            seq=a.after,
            next=wait_line(self_path, a.name) + f" --after {a.after}" + (f" --changes-after {a.changes_after}" if a.changes_after else ""),
        )
    if got.kind == "event":
        ev = dict(got.value)
        key = ev.pop("key")
        action = ev.pop("action", None) if key != "questions" else "questions"  # reply の action と重ねない
        if action == "running":
            reply("stopped", "まだ変化が無い", code=3, seq=ev.get("seq"))
        items = pa.with_repo(main, a.name, ev.pop("items", None) or []) if action == "questions" else ev.pop("items", None) or []
        if action == ph.CONDITION:
            ev.update(ph.wait_changes(main, a.name, a.changes_after, ev))
        reply("ok", f"並列の計画 {a.name}: {action}", items, action=action, **ev)
    if got.kind == "done":
        o = got.value or {}
        lanes = [{"lane": n, **st} for n, st in sorted((o.get("lanes") or {}).items())]
        rows = {"releases": o.get("releases"), "prior_waits": o.get("prior_waits") or []}
        reply("ok", f"並列の計画 {a.name} は {o.get('status')} で終わった", lanes, action=o.get("status"), **rows)
    reply("stopped", f"耐久ワークフローが {got.kind} で終わった: {got.value}", code=1, action=got.kind)
