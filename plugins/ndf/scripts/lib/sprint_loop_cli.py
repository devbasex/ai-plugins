"""sprint_loop_cli.py: `sprint-state.py loop` の引数と振り分け（#2089）。中身は sprint_loop.py が持つ。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import clock
import state_get
import step_result
from sprint_loop_stop import STOP_BY

ENTRY = "scripts/sprint-state.py"  # 持ち主の契約を引く入口（プラグインの根からのパス）


def budget_value(text: str) -> float | str:
    """`--budget-*` の値: 数か既定へ戻す指示 `default`（#2406）。どちらでもなければ引数の誤り（終了コード 2）。"""
    from sprint_loop_record import BUDGET_DEFAULT  # loop のときだけ読む

    if text == BUDGET_DEFAULT:
        return BUDGET_DEFAULT
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"数か {BUDGET_DEFAULT} を渡す: {text!r}") from None


def environment_result(root: Path, as_json: bool) -> dict | None:
    """`loop environment`: 実行環境の報告を出す。読めない項目があっても終了コードは 0。"""
    import run_environment

    report = run_environment.survey(root)
    if not as_json:
        print("\n".join([report["reason"], json.dumps(report, ensure_ascii=False)]))
        return None
    return step_result.result("sprint-state", "ok", report["reason"], run_environment.notes(report), report)


def run_loop_command(a, update) -> dict | None:
    """`sprint-state.py loop` の副命令を振り分ける。update はループの 1 回を締める前にスプリント状態を埋める関数（`update` と同じ）。

    先に開発ループの持ち主（単位 session）を取る。宣言の無い副命令（答える・読むだけ）は何もしない（#2342）。"""
    if a.loop_cmd == "start":
        a.loop_name = a.loop_name or "l" + clock.now_iso("utc")[:10].replace("-", "")
        import parallel_guard

        if getattr(a, "outside_plan", None) is None:  # 空の理由は渡したものとして判定で断る
            a.outside_plan = stored_outside_plan(a.root, a.loop_name)
        if (blocked := parallel_guard.entry_check(a.root, a.outside_plan, "sprint-state.py loop start")) is not None:
            return blocked
    import lease_stage  # loop のときだけ読む（filelock と psutil を要する）

    # 鍵のリポジトリはループの記録と同じ --root から決める（カレントから決めると、別の場所から同じループを別の鍵で進められる）
    held = lease_stage.hold(ENTRY, f"loop {a.loop_cmd}", repo_root=getattr(a, "root", None), name=getattr(a, "loop_name", None))
    out = _dispatch(a, update)
    if held is not None and isinstance(out, dict):
        out = {**out, "lease": held.as_json()}
    return out


def stored_outside_plan(root: str | None, name: str) -> str | None:
    """ループの記録に残した例外の起動の理由（`--outside-plan`。#2733）。記録が無い・読めなければ None。
    再開の `loop start` が理由を渡さなくても、例外として始めたループは例外のまま続ける。"""
    import sprint_loop as sl

    try:
        got = sl.read_loop_record(sl.Env(Path(root or ".").resolve()), name).get("outside_plan")
    except Exception:  # noqa: BLE001 — 記録が無い・読めなければ理由なし（判定は通常どおり）
        return None
    return got if isinstance(got, str) and got.strip() else None


def keep_outside_plan(env, name: str, reason: str | None) -> None:
    """例外の起動の理由をループの記録の `outside_plan` へ残す（#2733）。ループが組む `supervise.py new sprint` へ引き継ぐ。
    理由が無い・記録が無い（始めるのを断った）・同じ理由なら何もしない。"""
    import sprint_loop_record as slr

    reason = (reason or "").strip()
    if not reason or not slr.loop_record_path(env, name).is_file():
        return
    with slr.open_record(env, name) as (rec, before):
        if rec.get("outside_plan") != reason:
            rec["outside_plan"] = reason
            slr.write_record(env, rec, before)


def _dispatch(a, update) -> dict | None:
    import sprint_loop as sl  # loop のときだけ読む
    import sprint_loop_gate as sl_gate
    import sprint_loop_select as sl_select

    env = sl.Env(Path(a.root or ".").resolve())
    budget = {"laps": getattr(a, "budget_laps", None), "usd": getattr(a, "budget_usd", None), "hours": getattr(a, "budget_hours", None)}
    try:
        if a.loop_cmd == "start":
            name = a.loop_name
            ms = [m.strip() for m in (a.milestones or "").split(",") if m.strip()]
            out = sl.cmd_start(env, name, ms, budget, goal=not a.no_goal)
            keep_outside_plan(env, name, getattr(a, "outside_plan", None))
            return out
        if a.loop_cmd == "environment":
            return environment_result(env.main_dir(), a.json)
        handlers = {
            "select": lambda: sl_select.cmd_select(env, a.loop_name),
            "exclude": lambda: sl_select.cmd_exclude(env, a.loop_name, a.issue, a.reason),
            "sprint": lambda: sl.cmd_sprint(env, a.loop_name, a.mode, a.dev, a.prod, list(a.extra), a.judgments),
            "gate": lambda: sl_gate.cmd_gate(env, a.loop_name, a.done, update=update),
            "answer": lambda: sl.cmd_answer(env, a.loop_name, a.issue, a.approved, a.pr, revise=a.revise, note=a.note),
            "approve-mvv": lambda: sl.cmd_approve_mvv(env, a.loop_name, a.milestone),
            "lap": lambda: sl.cmd_lap(env, a.loop_name, a.failed, a.done, a.deferred_issues or [], update=update),
            "finish": lambda: sl.cmd_finish(env, a.loop_name, a.lap, a.done, update=update),
            "pending": lambda: sl.cmd_pending(env, a.loop_name),
            "reclose": lambda: sl.cmd_reclose(env, a.loop_name),
            "hold-question": lambda: sl.cmd_hold_question(env, a.loop_name, a.ref, a.plan, a.step, a.parallel),
            "stop": lambda: sl.cmd_stop(env, a.loop_name, a.by, a.reason),
            "status": lambda: sl.cmd_status(env, a.loop_name),
            "list": lambda: sl.cmd_list(env, a.loop_name),
            "get": lambda: state_get.loop_get(env, a.loop_name, a.field),
        }
        if a.loop_cmd == "answer" and a.revise and not a.note:
            return step_result.result(sl.TOOL, "stopped", "--revise には --note <指摘ファイル> が要る", [], {"status": "refused"})
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
    """`sprint-state.py` の副命令 `loop` の引数を足す。cmd_update は `sprint-state.py update` の本体（ループの 1 回を締める前に呼ぶ）。"""
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
    s.add_argument("--budget-laps", type=budget_value, help="数か default（宣言の値へ戻す。宣言に無ければ無制限）")
    s.add_argument("--budget-usd", type=budget_value)
    s.add_argument("--budget-hours", type=budget_value)
    s.add_argument("--no-goal", action="store_true", help="/goal で始めていない（再開のコマンドに /goal を付けない）")
    s.add_argument("--outside-plan", metavar="理由", help="動いている並列の計画の外で例外として始める（理由はマシンの記録に残る。#2733）")
    name(ls.add_parser("select"))
    s = name(ls.add_parser("exclude"))
    s.add_argument("issue", type=int)
    s.add_argument("--reason", required=True)
    s = name(ls.add_parser("sprint"))
    s.add_argument(
        "--judgments",
        required=True,
        metavar="ファイル",
        help="判定の根拠のファイル（JSON。mode-materials.py --out の下書きを埋めたもの。#2923）",
    )
    s.add_argument("--mode", help="照合するモード。省くと判定の根拠から決まるモードで組む")
    s.add_argument("--dev", help="明示の開発版。省くと流す直前に次に空いている版を決める")
    s.add_argument("--prod", help="明示の本番の版（--dev と一緒に渡す）")
    s.set_defaults(extra=[])  # -- の後は split_extra が切り分けて supervise.py new sprint へそのまま渡す
    s = name(ls.add_parser("gate"))
    s.add_argument("--done", required=True)
    s = name(ls.add_parser("answer"))
    s.add_argument("issue", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--approved", action="store_true")
    g.add_argument("--rejected", action="store_true")
    g.add_argument("--revise", action="store_true", help="直して出し直す（--note の指摘を課題の本文に書き、ループの次の 1 回で先頭に選ぶ）")
    s.add_argument("--pr", type=int)
    s.add_argument("--note", help="--revise: 指摘を書いたファイル")
    s = name(ls.add_parser("approve-mvv"))
    s.add_argument("--milestone", required=True)
    s = name(ls.add_parser("lap"))
    s.add_argument("--failed", action="store_true", help="そのループの 1 回を先へ進められない（失敗で締める）")
    s.add_argument("--done", help="ループの 1 回の queue の done（承認ゲートで止まっていれば承認ゲートの副命令と同じに扱う）")
    s.add_argument("--deferred-issues", type=int, nargs="+", help="上限に達して Issue へ回した指摘の Issue 番号（決定 13）")
    s = name(ls.add_parser("finish"))
    s.add_argument("lap", type=int, help="締め直す保留したループの 1 回（リリース待ち・質問待ち）")
    s.add_argument("--done", required=True, help="残りの工程（まとめ）の queue の done")
    name(ls.add_parser("pending"))
    name(ls.add_parser("reclose", help="リリースまで届いたのに失敗で締めたループの 1 回を完了で締め直す（#2911）"))
    s = name(ls.add_parser("hold-question"))
    s.add_argument("--ref", required=True, help="オーガナイザーへ送った問いの依頼の id")
    s.add_argument("--plan", required=True, help="問いで止まったプラン")
    s.add_argument("--step", required=True, help="答えの後に続けるステップ")
    s.add_argument("--parallel", help="並列の計画名（省くと NDF_RELAY_NAME のレーンが属する計画）")
    s = name(ls.add_parser("stop"))
    s.add_argument(
        "--by",
        choices=STOP_BY,
        default="unknown",
        help="置いた主体（人・利用者の指示を受けたオーガナイザー・不明。既定 unknown）",
    )
    s.add_argument("--reason", default="", help="止める理由（止める指示のファイルに書く）")
    for k in ("status", "list"):
        name(ls.add_parser(k)).add_argument("--json", action="store_true")
    s = name(ls.add_parser("get", help="読むだけ。次の操作を next に、--field の値を metrics に返す（#2909）"))
    s.add_argument("--field", action="append", help="JSON Pointer（ループの記録を根にする。繰り返し）")
    s = ls.add_parser("environment", help="実行環境を調べ、同時の数の候補を出す（宣言は書かない）")
    s.add_argument("--root", help="リポジトリの根（既定はカレント）")
    s.add_argument("--json", action="store_true")
