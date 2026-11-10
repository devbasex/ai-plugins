"""`parallel.py` の `start`・`wait` の本体（#2647 の決定 12。`parallel.py` の行数を上限に収める）。

引数の定義（`build_parser`）は契約の検査が静的に読むため `parallel.py` に残す。どの関数も、結果を出して終える
`reply`（`parallel.py` の同名の関数）を受け取り、それで終える。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import parallel_answer as pa
import parallel_defer as pdf
import parallel_external_cli as px
import parallel_hold as ph
import parallel_homes
import parallel_lane as pl
import parallel_ledger as plg
import parallel_plan as pp
import parallel_route as prt


def wait_line(self_path: Path, name: str) -> str:
    return f"python3 {self_path} wait {name}"


def start_plan(a, reply, main: Path, checked_plan, self_path: Path) -> None:
    """`run` をセッションから切り離して起動する。どの経路でも打ったセッションへオーガナイザーの印を付ける（#2637）。"""
    px.refuse_in_factory_run(reply)
    plan, items, metrics = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがあるため、レーンを 1 本も起動しない", items, 1, **metrics)
    import parallel_run as pr_run

    env, notes = pr_run.environment_report(main, plan)
    notes = [*notes, *prt.start_warnings(main)]
    parallel_homes.record_home(main)  # 別のリポジトリのレーンと計画の外の判定が読む計画の所在の一覧（#2733）
    pid = pl.live_run_pid(main, a.name)
    if pid is not None:
        marked = pr_run.mark_organizer(main, a.name)  # 打ったセッションへ付け直す（#2637）
        reply("ok", f"run は動いている（pid {pid}）", notes, pid=pid, organizer=marked, next=wait_line(self_path, a.name), environment=env)
    status = pl.run_status(main, a.name)
    state = plg.plan_state(main, a.name, status)
    if state == "closed" and status != "SUCCESS":
        reply("stopped", f"並列の計画 {a.name} は閉じている（作り直さない）", notes, 1)
    if status is None:
        px.manual_or_none(main, plan, reply)
    marked = pr_run.mark_organizer(main, a.name)  # run.pid の死んだ途中の計画は step_init を流し直さないため、ここで付ける
    rebuild = (
        {"rebuilt": True, "restored": plg.restored_counts(plg.restore(main, a.name, opening=state == "legacy"))}
        if state in ("lost", "legacy")
        else {}
    )
    try:  # 台帳を書けない置き場では run を起動しない（E1）
        rid = os.environ.get("NDF_RELAY_NAME") or ""
        plg.append_ledger_row(
            main,
            a.name,
            "role",
            f"role:{plg.now_utc()}:{os.getpid()}",
            {"plan_dir": pl.record_identity(main, a.name)},
            by="organizer",
            rid=rid,
        )
    except OSError as exc:
        reply("stopped", f"計画の台帳へ書けないため run を起動しない: {exc}", notes, 2)
    log = pp.plan_dir(main, a.name) / "run.log"
    pl.clear_settled(main, a.name)  # 切り離した run が消す前に判定が読んでも、動いていないと読まない
    with open(pp.plan_dir(main, a.name) / "run.log", "ab") as f:
        p = subprocess.Popen([sys.executable, str(self_path), "run", a.name], cwd=main, stdout=f, stderr=f, start_new_session=True)
    (pp.plan_dir(main, a.name) / "run.pid").write_text(f"{p.pid}\n")
    nxt = wait_line(self_path, a.name)
    head = "失われた計画を作り直す run" if rebuild else "run"
    reply(
        "ok",
        f"{head}を切り離して起動した（pid {p.pid}）",
        notes,
        pid=p.pid,
        log=str(log),
        organizer=marked,
        next=nxt,
        environment=env,
        **rebuild,
    )


def batch_reply(reply, main: Path, name: str, extra: dict) -> None:
    """業務時間内で溜めた依頼があれば、答えていない依頼の全件を 1 回で返して終える。無ければ何もしない。"""
    got = pdf.due_batch(main, name, pdf.hours_of(main))
    if got is not None:
        items, m = got
        reply("ok", f"並列の計画 {name}: questions（業務時間外に溜めた依頼をまとめて示す）", items, action="questions", **{**extra, **m})


WAIT_KEYS = ("questions", "answer-hold")  # オーガナイザーへ返すイベント。`pause` は一時レーンが読み、wait は読まない（#2936）


def has_unanswered(main: Path, name: str, items: list[dict]) -> bool:
    """問いの一覧に、オーガナイザーがまだ答えを送っていない依頼があるか（`answers-sent.json` に無い id。まとめた `also` も含む）。"""
    import jsonio

    sent = jsonio.read(pp.plan_dir(main, name) / pa.SENT, missing=[], broken=[], want=list)
    done = {s.get("id") for s in sent if isinstance(s, dict)}
    return any(i not in done for q in items if isinstance(q, dict) for i in (q.get("id"), *(q.get("also") or [])) if i)


def wait_event(main: Path, name: str, after: int, timeout: float):
    """`questions`・`answer-hold` のイベントか終わりまで待つ。答えを送った依頼だけの `questions` は読み飛ばして待ち続ける（決定 5）。
    (結果, 読み飛ばした最後の seq) を返す。"""
    import time

    import durable

    end = time.monotonic() + timeout
    while True:
        got = durable.wait_from(
            pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), list(WAIT_KEYS), after,
            max(0.0, end - time.monotonic()), start=pl.record_start(main),
        )  # fmt: skip
        if got.kind != "event" or got.value.get("key") != "questions" or has_unanswered(main, name, got.value.get("items") or []):
            return got, after
        after = int(got.value.get("seq") or after)


def wait_plan(a, reply, main: Path, self_path: Path) -> None:
    """人へ問うもの・答え待ちの停止の変化・終わり・異常まで待つ（#2936 の I5）。"""

    restart = f"python3 {self_path} start {a.name}"
    if not pl.record_exists(main, a.name):
        if plg.plan_state(main, a.name, None) == "lost":
            if pl.live_run_pid(main, a.name) is not None:  # 作り直しの run が記録を開く前
                reply("stopped", "run が耐久ワークフローを作り直している。同じ wait を打ち直す", code=3, next=wait_line(self_path, a.name))
            reply(
                "stopped", f"並列の計画 {a.name} の耐久の記録が無い（失われた計画）。start で作り直す", code=1, action="error", next=restart
            )
        reply("stopped", f"並列の計画 {a.name} の耐久の記録が無い", code=2)
    extra = plg.ledger_report(main, a.name, prt.waiting_requests(main, a.name))
    batch_reply(reply, main, a.name, extra)  # 業務時間に入った後の最初の wait は溜めた依頼をまとめて返す（#2913 の E7）
    hours = pdf.hours_of(main)
    got, after = wait_event(main, a.name, a.after, pdf.clip_timeout(a.timeout, hours))  # 業務時間外は次の業務時間の始まりで切る
    if got.kind == "timeout":
        batch_reply(reply, main, a.name, extra)
        reply(
            "stopped",
            f"{a.timeout:.0f} 秒の上限に達した。同じ wait を打ち直す",
            code=3,
            seq=after,
            next=wait_line(self_path, a.name) + f" --after {after}" + (f" --changes-after {a.changes_after}" if a.changes_after else ""),
            **extra,
        )
    if got.kind == "event":
        ev = dict(got.value)
        key = ev.pop("key")
        action = ev.pop("action", None) if key != "questions" else "questions"  # reply の action と重ねない
        items = pa.with_repo(main, a.name, ev.pop("items", None) or []) if action == "questions" else ev.pop("items", None) or []
        if action == "questions":  # 業務時間外は gate1・ask を溜め、今問う依頼だけを返す（#2913 の E6）
            items, kept = pdf.route(main, a.name, items, pdf.hours_of(main))
            if kept and not items:
                d = kept["deferred"]
                nxt = wait_line(self_path, a.name) + f" --after {ev.get('seq')}"
                reply(
                    "stopped",
                    f"業務時間外の依頼を {d['count']} 件溜めた（業務時間の始まり {d['until']}）",
                    code=3,
                    next=nxt,
                    **{**extra, **ev, **kept},
                )
            ev.update(kept)
        if action == ph.CONDITION:
            ev.update(ph.wait_changes(main, a.name, a.changes_after, ev))
        reply("ok", f"並列の計画 {a.name}: {action}", items, action=action, **{**extra, **ev})
    if got.kind == "done":
        o = got.value or {}
        lanes = [{"lane": n, **st} for n, st in sorted((o.get("lanes") or {}).items())]
        rows = {"releases": o.get("releases"), "prior_waits": o.get("prior_waits") or []}
        reply("ok", f"並列の計画 {a.name} は {o.get('status')} で終わった", lanes, action=o.get("status"), **rows, **extra)
    reply(
        "stopped",
        f"耐久ワークフローが {got.kind} で終わった: {got.value}。start で作り直す",
        code=1,
        action=got.kind,
        next=restart,
        **extra,
    )
