"""sprint_loop_cli.py: `sprint-state.py loop` の引数と振り分け（#2089）。中身は sprint_loop.py が持つ。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import clock
import step_result


def run_loop_command(a, update) -> dict | None:
    """`sprint-state.py loop` の副命令を振り分ける。update は周回を締める前にスプリント状態を埋める関数（`update` と同じ）。"""
    import sprint_loop as sl  # loop のときだけ読む
    import sprint_loop_gate as sl_gate
    import sprint_loop_select as sl_select

    env = sl.Env(Path(a.root or ".").resolve())
    budget = {"laps": getattr(a, "budget_laps", None), "usd": getattr(a, "budget_usd", None), "hours": getattr(a, "budget_hours", None)}
    try:
        if a.loop_cmd == "start":
            name = a.loop_name or "l" + clock.now_iso("utc")[:10].replace("-", "")
            ms = [m.strip() for m in (a.milestones or "").split(",") if m.strip()]
            return sl.cmd_start(env, name, ms, budget, goal=not a.no_goal)
        handlers = {
            "select": lambda: sl_select.cmd_select(env, a.loop_name),
            "exclude": lambda: sl_select.cmd_exclude(env, a.loop_name, a.issue, a.reason),
            "sprint": lambda: sl.cmd_sprint(env, a.loop_name, a.mode, a.dev, a.prod, list(a.extra)),
            "gate": lambda: sl_gate.cmd_gate(env, a.loop_name, a.done),
            "answer": lambda: sl.cmd_answer(env, a.loop_name, a.issue, a.approved, a.pr),
            "approve-mvv": lambda: sl.cmd_approve_mvv(env, a.loop_name, a.milestone),
            "lap": lambda: sl.cmd_lap(env, a.loop_name, a.failed, a.done, a.deferred_issues or [], update=update),
            "stop": lambda: sl.cmd_stop(env, a.loop_name),
            "status": lambda: sl.cmd_status(env, a.loop_name),
            "list": lambda: sl.cmd_list(env, a.loop_name),
        }
        out = handlers[a.loop_cmd]()
        if a.loop_cmd in ("status", "list") and not a.json:
            print(
                "\n".join([out["summary"], *(json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x) for x in out["items"])])
            )
            return None
        return out
    except sl.LoopError as e:
        return step_result.result(sl.TOOL, "stopped", str(e), [], {"status": "refused"})


def split_extra(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """`loop sprint ... -- <引数>` の `--` から後を切り分ける（解析する引数, new sprint へ渡す引数）。
    argparse の REMAINDER は位置引数の後の必須のオプションまで吸うため、解析の前に分ける。`loop sprint` でなければ (argv, None)。"""
    if argv[:2] != ["loop", "sprint"] or "--" not in argv:
        return argv, None
    at = argv.index("--")
    return argv[:at], argv[at + 1 :]


def parse_args(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    """`sprint-state.py` の引数を解析する。`loop sprint` の `--` から後は解析せず `extra` に入れる。"""
    head, extra = split_extra(argv)
    a = parser.parse_args(head)
    if extra is not None:
        a.extra = extra
    return a


def add_loop_parser(sub, cmd_update) -> None:
    """`sprint-state.py` の副命令 `loop` の引数を足す。cmd_update は `sprint-state.py update` の本体（周回を締める前に呼ぶ）。"""
    lp = sub.add_parser("loop", help="開発ループ（#2089）")
    lp.set_defaults(
        loop_run=lambda a: run_loop_command(a, lambda state, dones: cmd_update(argparse.Namespace(sprint=state, done=dones, next=None)))
    )
    ls = lp.add_subparsers(dest="loop_cmd", required=True)

    def name(p, optional=False):
        p.add_argument("loop_name", nargs="?" if optional else None, help="ループの名前（英数字・- ・_）")
        p.add_argument("--root", help="リポジトリの根（既定はカレント。記録はメインディレクトリの .ndf/loop/ に置く）")
        return p

    s = name(ls.add_parser("start"), optional=True)
    s.add_argument("--milestones", help="選定の対象を絞るマイルストーン（, 区切り）")
    s.add_argument("--budget-laps", type=float)
    s.add_argument("--budget-usd", type=float)
    s.add_argument("--budget-hours", type=float)
    s.add_argument("--no-goal", action="store_true", help="/goal で始めていない（再開のコマンドに /goal を付けない）")
    name(ls.add_parser("select"))
    s = name(ls.add_parser("exclude"))
    s.add_argument("issue", type=int)
    s.add_argument("--reason", required=True)
    s = name(ls.add_parser("sprint"))
    s.add_argument("--mode", required=True)
    s.add_argument("--dev", required=True)
    s.add_argument("--prod")
    s.set_defaults(extra=[])  # -- の後は split_extra が切り分けて supervise.py new sprint へそのまま渡す
    s = name(ls.add_parser("gate"))
    s.add_argument("--done", required=True)
    s = name(ls.add_parser("answer"))
    s.add_argument("issue", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--approved", action="store_true")
    g.add_argument("--rejected", action="store_true")
    s.add_argument("--pr", type=int)
    s = name(ls.add_parser("approve-mvv"))
    s.add_argument("--milestone", required=True)
    s = name(ls.add_parser("lap"))
    s.add_argument("--failed", action="store_true", help="その周回を先へ進められない（失敗で締める）")
    s.add_argument("--done", help="周回の queue の done（承認ゲートで止まっていれば loop gate と同じに扱う）")
    s.add_argument("--deferred-issues", type=int, nargs="+", help="上限に達して Issue へ回した指摘の Issue 番号（決定 13）")
    name(ls.add_parser("stop"))
    for k in ("status", "list"):
        name(ls.add_parser(k)).add_argument("--json", action="store_true")
