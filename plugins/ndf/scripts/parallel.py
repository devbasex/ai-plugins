#!/usr/bin/env python3
"""並列の計画と進行の CLI（#2436）。結果は統一 JSON レポートを 1 行で標準出力へ出す。

| 副命令 | 役割 |
| --- | --- |
| `init <計画名>` | 標準入力の下書きを検査し、`plan.json`・`plan.md`・レーンごとの引継ぎ文書を書く（在る文書は上書きせず、何も書かない） |
| `check <計画名>` | 並列の計画を検査する（誤りは 1、警告だけなら 0） |
| `start <計画名>` | `run` をセッションから切り離して起動する（動いていれば今の pid を返す） |
| `run <計画名>` | 耐久ワークフローを始めるか続け、終わるまで動き続ける。ラッパーの外・tmux が無ければ手で起動する 1 行を返す（3） |
| `wait <計画名> [--after <seq>] [--timeout 秒]` | 状態の変化（notice / release / confirmed / questions）か終わりまで待つ。上限なら 3 |
| `present <計画名> <連番> <提示> --approval <承認資料>` | 範囲の Pull Request をレーンへ対応づけ、提示の置き場（`releases/<連番>/<提示>/`）へ書く |
| `confirm <計画名> <連番> [--take new\\|first]` | 承認ゲート 2 の承認を送る |
| `settle <計画名> <連番> (--done <D> \\| --rejected [--reason 文])` | 本番の配布の結果を送る |
| `stop <計画名> [--force]` | 計画の全レーンを止め、耐久ワークフローを終える（配布の途中は settle を待つ。`--force` は待たない） |
| `request <計画名> --lane <名> --state <パス> [--prs ...] [--issues ...]` | 配布の依頼を送る（10）か、すでに `main` にあれば送らない（0） |
| `await <計画名> --from <パス> [--timeout 秒]` | 依頼の答えを読む（released 0・rejected 10・failed 1・まだ無い 3） |
| `request-ask <計画名> --lane <名>` | 標準入力の問い（`tool_input`・`cwd`・`ledger_id`）をオーガナイザーへ送る（送り手でなければ 4） |
| `request-gate2 <計画名> <連番> <提示> --report <承認資料>` | 一時レーンが承認ゲート 2 の依頼を送る |
| `answer <計画名> <依頼> (--approved \\| --revise --note <パス> \\| --rejected \\| --choice <答え>...)` | 問いの答えを送る（持ち主のレーンが止まっていれば `metrics.next` に起こす 1 行） |
| `helper <計画名> <連番> <提示>` | 一時レーンが流すもの（`phase`・`pipeline`・`settle`・`gate2`）を返す |

置き場はメインディレクトリの `.ndf/parallel/<計画名>/`（コミットしない）。正本の手順は
`skills/development-workflow/references/parallel.md` と `skills/issue-plan-strategy/references/parallel-plan.md` にある。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import deps  # noqa: E402

deps.require("durable", "schema", "md", "mdtable", "versions")

import decl  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "parallel"
SELF = Path(__file__).resolve()


def main_of() -> Path:
    cwd = os.getcwd()
    return decl.main_dir(cwd) or Path(cwd)


def reply(status: str, summary: str, items=None, code: int | None = None, **metrics):
    emit(result(TOOL, status, summary, items, metrics), code)


def checked_plan(main: Path, name: str) -> tuple[pp.ParallelPlan | None, list[dict], dict]:
    try:
        data = pp.load_plan(main, name)
    except FileNotFoundError:
        reply("stopped", f"並列の計画 {name} が無い（置き場: {pp.plan_dir(main, name)}/plan.json）", code=1)
    except (OSError, ValueError) as exc:
        reply("stopped", f"並列の計画 {name} を読めない: {exc}", code=2)
    return pp.check_data(data)


def cmd_init(a) -> None:
    main = main_of()
    try:
        raw = json.loads(sys.stdin.read())
    except ValueError as exc:
        reply("stopped", f"下書きを読めない: {exc}", code=2)
    if not isinstance(raw, dict):
        reply("stopped", "下書きが JSON のオブジェクトでない", code=2)
    raw = {**raw, "name": a.name}
    plan, items, metrics = pp.check_data(raw)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがある（{metrics['errors']} 件）。何も書いていない", items, 1, **metrics)
    import handoff

    try:
        written = pp.write_all(main, plan, raw)
    except handoff.Failure as exc:
        reply("stopped", f"引継ぎ文書の置き場が無い: {exc}", items, exc.code)
    except FileExistsError as exc:
        reply("stopped", f"在る文書を上書きしないため何も書いていない: {exc}", items, 1, existing=str(exc).split(", "))
    except OSError as exc:
        reply("stopped", f"書けない: {exc}", items, 2)
    reply("ok", f"並列の計画 {a.name} を書いた（レーン {len(plan.lanes)} 本）", [{"path": str(p)} for p in written] + items, **metrics)


def cmd_check(a) -> None:
    plan, items, metrics = checked_plan(main_of(), a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがある（{metrics['errors']} 件）", items, 1, **metrics)
    reply("ok", f"並列の計画 {a.name} の検査を通った（警告 {metrics['warnings']} 件）", items, **metrics)


# ---- 進行 ----


def _manual_or_none(main: Path, plan: pp.ParallelPlan) -> None:
    """ラッパーの外・tmux が無いときは、レーンごとの手で起動する 1 行を返して 3 で終える（受け入れ条件 9）。"""
    import parallel_run as pr_run

    why = None
    if not os.environ.get("NDF_RELAY_DIR"):
        why = "ラッパーの外（NDF_RELAY_DIR が無い）"
    else:
        _, res = pr_run.relay(main, "ls", "--json")
        m = res.get("metrics") or {}
        if not m.get("tmux"):
            why = "tmux が無い"
        elif not m.get("hub"):
            why = "統括役が居ない"
    if why:
        lines = pp.manual_commands(plan)
        reply(
            "stopped",
            f"{why}ため、レーンを起動しない。手で起動する 1 行を items に並べた",
            [{"lane": ln.name, "command": c} for ln, c in zip(plan.lanes, lines)],
            3,
            manual=True,
        )


def _wait_line(name: str) -> str:
    return f"python3 {SELF} wait {name}"


def cmd_run(a) -> None:
    main = main_of()
    plan, items, metrics = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがあるため、レーンを 1 本も起動しない", items, 1, **metrics)
    if pl.run_status(main, a.name) is None:
        _manual_or_none(main, plan)
    import durable
    import parallel_run as pr_run

    try:
        got, resumed = pr_run.run_plan(main, a.name)
    except durable.DurableError as exc:
        if "動いている" in str(exc):
            # 設計の終了コード 5 は統一 JSON レポートの終了コードの決まり（step_result.code_matches）に無いため、
            # 動いている run を続ける答え（ok・running）として返す
            reply("ok", f"同じ計画の run が動いている。wait で待つ: {exc}", running=True, next=_wait_line(a.name))
        reply("stopped", f"耐久の記録を開けない: {exc}", code=2)
    status = got.get("status") if isinstance(got, dict) else None
    lanes = [{"lane": n, **st} for n, st in sorted((got or {}).get("lanes", {}).items())]
    reply("ok", f"並列の計画 {a.name} は {status} で終わった", lanes, resumed=resumed, action=status, releases=(got or {}).get("releases"))


def cmd_start(a) -> None:
    main = main_of()
    plan, items, metrics = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがあるため、レーンを 1 本も起動しない", items, 1, **metrics)
    pidfile = pp.plan_dir(main, a.name) / "run.pid"
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        reply("ok", f"run は動いている（pid {pid}）", pid=pid, next=_wait_line(a.name))
    except (OSError, ValueError):
        pass
    if pl.run_status(main, a.name) is None:
        _manual_or_none(main, plan)
    log = pp.plan_dir(main, a.name) / "run.log"
    with open(pp.plan_dir(main, a.name) / "run.log", "ab") as f:
        p = subprocess.Popen([sys.executable, str(SELF), "run", a.name], cwd=main, stdout=f, stderr=f, start_new_session=True)
    (pp.plan_dir(main, a.name) / "run.pid").write_text(f"{p.pid}\n")
    reply("ok", f"run を切り離して起動した（pid {p.pid}）", pid=p.pid, log=str(log), next=_wait_line(a.name))


def cmd_wait(a) -> None:
    import durable

    main = main_of()
    if not pl.record_exists(main, a.name):
        reply("stopped", f"並列の計画 {a.name} の耐久の記録が無い", code=2)
    got = durable.wait_from(pl.KIND, pl.record_identity(main, a.name), pl.workflow_id(a.name), ["pause", "questions"], a.after, a.timeout)
    if got.kind == "timeout":
        reply(
            "stopped",
            f"{a.timeout:.0f} 秒の上限に達した。同じ wait を打ち直す",
            code=3,
            seq=a.after,
            next=_wait_line(a.name) + f" --after {a.after}",
        )
    if got.kind == "event":
        ev = dict(got.value)
        key = ev.pop("key")
        action = ev.pop("action", None) if key == "pause" else "questions"  # reply の action と重ねない
        if action == "running":
            reply("stopped", "まだ変化が無い", code=3, seq=ev.get("seq"))
        items = ev.pop("items", None) or []
        reply("ok", f"並列の計画 {a.name}: {action}", items, action=action, **ev)
    if got.kind == "done":
        o = got.value or {}
        lanes = [{"lane": n, **st} for n, st in sorted((o.get("lanes") or {}).items())]
        reply("ok", f"並列の計画 {a.name} は {o.get('status')} で終わった", lanes, action=o.get("status"), releases=o.get("releases"))
    reply("stopped", f"耐久ワークフローが {got.kind} で終わった: {got.value}", code=1, action=got.kind)


def cmd_present(a) -> None:
    import parallel_release as pr

    main = main_of()
    plan, items, metrics = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがある", items, 1)
    d = pr.release_dir(main, a.name, a.n, a.k)
    p1 = pr.release_dir(main, a.name, a.n, 1) / "presented.json"
    first = json.loads(p1.read_text(encoding="utf-8")) if a.k != 1 and p1.is_file() else None
    reqs = _requests_for(main, a.name)
    try:
        got = pr.present(main, plan, Path(a.approval), reqs, d, first)
    except ValueError as exc:
        reply("stopped", f"承認資料に承認したコミットが無い: {exc}", code=1)
    except OSError as exc:
        reply("stopped", f"読めない: {exc}", code=2)
    reply(
        "ok",
        f"提示 {a.n}-{a.k}: {got['sha'][:12]} まで（Pull Request {len(got['prs'])} 本）",
        [{"lane": n, **r} for n, r in got["lanes"].items()],
        sha=got["sha"],
        orphans=got["orphans"],
    )


def _requests_for(main: Path, name: str) -> list[dict]:
    """オーガナイザーが読める依頼（`releases/requests.json`。耐久ワークフローが配布を組むたびに書く。無ければ空）。"""
    p = pp.plan_dir(main, name) / "releases" / "requests.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _resume_plan(main: Path, name: str, msg: dict, key: str) -> None:
    import durable

    durable.send_to(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), msg, "resume", key)


def cmd_confirm(a) -> None:
    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    wid = pl.workflow_id(a.name)
    _resume_plan(main, a.name, {"n": a.n, "phase": "confirm", "take": a.take}, f"{wid}:resume:confirm:{a.n}:{a.take or '-'}")
    reply("ok", f"配布 {a.n} の承認を送った", n=a.n, take=a.take)


def cmd_settle(a) -> None:
    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    if a.rejected:
        res, version = "rejected", None
    else:
        try:
            done = json.loads(Path(a.done).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reply("stopped", f"done を読めない: {exc}", code=2)
        results = [str(i.get("result")) for i in done.get("items") or [] if isinstance(i, dict)]
        if "関門" in results:
            reply("stopped", "本番のプランが承認ゲートで止まっている（送らない）", code=4)
        res = "released" if results and all(r == "完了" for r in results) else "failed"
        version = done.get("version") or (done.get("metrics") or {}).get("version")
    wid = pl.workflow_id(a.name)
    _resume_plan(
        main, a.name, {"n": a.n, "phase": "settle", "result": res, "version": version, "reason": a.reason}, f"{wid}:resume:settle:{a.n}"
    )
    reply("ok", f"配布 {a.n} の結果 {res} を送った", result=res, version=version)


def cmd_stop(a) -> None:
    import durable
    import parallel_run as pr_run

    main = main_of()
    plan, items, _ = checked_plan(main, a.name)
    if plan is None:
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがある", items, 1)
    if pl.run_status(main, a.name) in pl.RUNNING:
        wid = pl.workflow_id(a.name)
        msg, key = ({"kind": "stop", "force": True}, f"{wid}:inbox:stop:force") if a.force else ({"kind": "stop"}, f"{wid}:inbox:stop")
        durable.send_to(pl.KIND, pl.record_identity(main, a.name), wid, msg, "inbox", key)
    left = [ln.name for ln in plan.lanes if pr_run.relay(main, "stop", ln.name, "--json")[0] not in (0, 3)]
    if left:
        reply("stopped", f"止まらないレーンがある: {', '.join(left)}", [{"lane": n} for n in left], 1, not_stopped=left)
    reply("ok", f"並列の計画 {a.name} のレーン {len(plan.lanes)} 本へ停止を打った", [{"lane": ln.name} for ln in plan.lanes])


# ---- レーンの側 ----


def cmd_request(a) -> None:
    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    if a.prs and pl.merged_on_production(main, a.prs):
        version = pl.released_version(main, a.name, a.prs)
        pl.write_request(Path(a.state), {"result": "released", "version": version, "prs": a.prs})
        reply("ok", "自分の Pull Request はすでに本番チャネルにある。依頼を送らずに続きへ進む", result="released", version=version)
    rid = pl.send_release_request(main, a.name, a.lane, a.prs, a.issues, Path(a.state))
    pl.write_request(Path(a.state), {"id": rid, "plan": a.name, "lane": a.lane, "prs": a.prs})
    reply("gate", f"配布の依頼 {rid} を送った（配布待ち）。答えは parallel.py await で読む", code=10, id=rid, result="requested")


def cmd_await(a) -> None:
    main = main_of()
    try:
        sent = json.loads(Path(getattr(a, "from")).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reply("stopped", f"依頼の記録を読めない: {exc}", code=2)
    if sent.get("result") == "released" and not sent.get("id"):
        reply("ok", "配布済み（依頼を送っていない）", result="released", version=sent.get("version"), deferred=None)
    ans, dfr = pl.read_answer(main, a.name, sent["id"], a.timeout)
    if ans is None:
        reply("stopped", "答えがまだ無い", code=3, deferred=dfr)
    res = ans.get("result")
    code = {"released": 0, "rejected": 10}.get(res, 1)
    status = {0: "ok", 10: "gate"}.get(code, "stopped")
    reply(
        status,
        f"配布の依頼 {sent['id']} の答え: {res}",
        code=code,
        result=res,
        version=ans.get("version"),
        release=ans.get("release"),
        reason=ans.get("reason"),
        deferred=dfr,
    )


# ---- 問いと一時レーン（#2436 の 6c・6d・6f） ----


def cmd_request_ask(a) -> None:
    main = main_of()
    try:
        got = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        reply("stopped", f"標準入力の問いを読めない: {exc}", code=2)
    who = pl.sender_of(main, a.lane, os.environ.get("NDF_RELAY_DIR"))
    if who is None:
        reply("stopped", f"{a.lane} は動いている計画のレーンでも一時レーンでもない（問いは通す）", code=4)
    if who[0] != a.name:
        reply("stopped", f"{a.lane} は計画 {who[0]} の送り手で、{a.name} の送り手でない", code=4)
    rid = pl.request_ask(main, a.name, a.lane, got.get("tool_input") or {}, got.get("cwd") or os.getcwd(), str(got.get("ledger_id") or ""))
    reply("ok", f"問いの依頼 {rid} をオーガナイザーへ送った", id=rid)


def cmd_request_gate2(a) -> None:
    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    rid = pl.request_gate2(main, a.name, a.n, a.k, a.report)
    reply("ok", f"承認ゲート 2 の依頼 {rid} をオーガナイザーへ送った。答えを待つ間は止まる", id=rid)


def _owner_restart(main: Path, name: str, rid: str, choice: list[str] | None = None) -> str | None:
    """答えの後の 1 行。自分で立てた問い（stall・helper・spawn）は選んだ答えの commands、ほかは止まった持ち主の restart。"""
    import durable
    import parallel_run as pr_run

    items = (durable.event_from(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), "questions", 0) or {}).get("items") or []
    q = next((q for q in items if q.get("id") == rid), {})
    if q.get("kind") in ("stall", "helper", "spawn") or not (lane := q.get("lane")):
        return next((q["commands"][c] for c in choice or [] if (q.get("commands") or {}).get(c)), None)
    code, got = pr_run.relay(main, "ls", "--json")
    state = next((i.get("state") for i in got.get("items") or [] if i.get("name") == lane), None) if code == 0 else None
    return None if state in ("running", "awaiting_approval") else f"python3 {pr_run.RELAY} restart {lane}"


def cmd_answer(a) -> None:
    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    note = None
    if a.revise:
        if not a.note:
            reply("stopped", "--revise には --note <指摘ファイル> が要る", code=1)
        try:
            note = Path(a.note).read_text(encoding="utf-8").strip()
        except OSError as exc:
            reply("stopped", f"指摘ファイルを読めない: {exc}", code=2)
    result = "approved" if a.approved else "revise" if a.revise else "rejected" if a.rejected else "answered"
    nxt = _owner_restart(main, a.name, a.id, a.choice)
    pl.send_answer(main, a.name, a.id, result, note=note, choice=a.choice)
    reply("ok", f"依頼 {a.id} へ答え（{result}）を送った", id=a.id, result=result, next=nxt)


def cmd_helper(a) -> None:
    import parallel_run as pr_run

    main = main_of()
    if pl.run_status(main, a.name) not in pl.RUNNING:
        reply("stopped", f"並列の計画 {a.name} が動いていない", code=1)
    task = pr_run.helper_task(main, a.name, a.n, a.k)
    reply("ok", f"一時レーンの仕事: 配布 {a.n} の提示 {a.k}（{task['phase']}）", **task)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="parallel.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init")
    ini.add_argument("name")
    chk = sub.add_parser("check")
    chk.add_argument("name")
    sta = sub.add_parser("start")
    sta.add_argument("name")
    run_ = sub.add_parser("run")
    run_.add_argument("name")
    stp = sub.add_parser("stop")
    stp.add_argument("name")
    stp.add_argument("--force", action="store_true", help="配布の途中でも settle を待たずに終え、一時レーンも止める")
    w = sub.add_parser("wait")
    w.add_argument("name")
    w.add_argument("--after", type=int, default=0)
    w.add_argument("--timeout", type=float, default=3600)
    p = sub.add_parser("present")
    p.add_argument("name")
    p.add_argument("n", type=int)
    p.add_argument("k", type=int)
    p.add_argument("--approval", required=True)
    c = sub.add_parser("confirm")
    c.add_argument("name")
    c.add_argument("n", type=int)
    c.add_argument("--take", choices=("new", "first"))
    s = sub.add_parser("settle")
    s.add_argument("name")
    s.add_argument("n", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--done")
    g.add_argument("--rejected", action="store_true")
    s.add_argument("--reason")
    r = sub.add_parser("request")
    r.add_argument("name")
    r.add_argument("--lane", required=True)
    r.add_argument("--prs", type=int, nargs="*", default=[])
    r.add_argument("--issues", type=int, nargs="*", default=[])
    r.add_argument("--state", required=True)
    aw = sub.add_parser("await")
    aw.add_argument("name")
    aw.add_argument("--from", required=True)
    aw.add_argument("--timeout", type=float, default=0)
    ra = sub.add_parser("request-ask")
    ra.add_argument("name")
    ra.add_argument("--lane", required=True)
    rg = sub.add_parser("request-gate2")
    rg.add_argument("name")
    rg.add_argument("n", type=int)
    rg.add_argument("k", type=int)
    rg.add_argument("--report", required=True)
    an = sub.add_parser("answer")
    an.add_argument("name")
    an.add_argument("id")
    ag = an.add_mutually_exclusive_group(required=True)
    ag.add_argument("--approved", action="store_true")
    ag.add_argument("--revise", action="store_true")
    ag.add_argument("--rejected", action="store_true")
    ag.add_argument("--choice", nargs="+")
    an.add_argument("--note")
    hp = sub.add_parser("helper")
    hp.add_argument("name")
    hp.add_argument("n", type=int)
    hp.add_argument("k", type=int)
    return ap


def main(argv=None) -> None:
    a = build_parser().parse_args(argv)
    {
        "init": cmd_init,
        "check": cmd_check,
        "start": cmd_start,
        "run": cmd_run,
        "wait": cmd_wait,
        "present": cmd_present,
        "confirm": cmd_confirm,
        "settle": cmd_settle,
        "stop": cmd_stop,
        "request": cmd_request,
        "await": cmd_await,
        "request-ask": cmd_request_ask,
        "request-gate2": cmd_request_gate2,
        "answer": cmd_answer,
        "helper": cmd_helper,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
