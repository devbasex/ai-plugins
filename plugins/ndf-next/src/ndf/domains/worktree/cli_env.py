"""`ndf worktree` の副命令のうち、worktree の環境を扱うもの（依存の用意・ローカルの動作検証・テスト環境）。

    ndf worktree deps <worktree> [--if-unprepared]
    ndf worktree localenv <setup|verify|healthcheck|aim|mode> [worktree]
    ndf worktree testenv <env|tag|bake|up|stop|down|test|compose-env|reap|reclaim|expose|unexpose> [worktree] [オプション]

宣言（か、その節）が無いリポジトリでは何もせず、`ok` と「適用外」の要約を返す。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from ndf.contracts import result
from ndf.domains.worktree import container, deps, localenv, localenv_decide, testenv, testenv_sweep
from ndf.testing.command import parse_period

LOCALENV = ("setup", "verify", "healthcheck", "aim", "mode")
TESTENV = ("env", "tag", "bake", "up", "stop", "down", "test", "compose-env", "reap", "reclaim", "expose", "unexpose")
NOT_APPLICABLE = "適用外（宣言か、その節が無い）"


def add_parsers(sub: Any) -> None:
    d = sub.add_parser(deps.SECTION, help="宣言の deps 節に従って worktree の依存を用意する")
    d.add_argument("worktree", type=Path)
    d.add_argument("--if-unprepared", action="store_true", help="今の HEAD と宣言で用意した印があれば何もしない")
    lo = sub.add_parser(localenv.SECTION, help="worktree でのローカルの動作検証")
    lo.add_argument("action", choices=LOCALENV)
    lo.add_argument("worktree", type=Path, nargs="?")
    te = sub.add_parser(testenv.SECTION, help="worktree ごとのテスト環境")
    te.add_argument("action", choices=TESTENV)
    te.add_argument("worktree", type=Path, nargs="?")
    te.add_argument("--profile")
    te.add_argument("--tag", dest="golden")
    te.add_argument("--kind")
    te.add_argument("--out", type=Path)
    te.add_argument("--idle")
    te.add_argument("--volumes", action="store_true")
    te.add_argument("--wait-service", action="append", default=[], dest="wait")


def _where(args: argparse.Namespace) -> Path:
    return Path(os.path.realpath(args.worktree if args.worktree is not None else args.cwd))


def run_deps(tool: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        got = deps.prepare(_where(args), if_unprepared=args.if_unprepared)
    except deps.DepsError as exc:
        return result.stopped(tool, "precondition", f"依存の用意: 宣言が壊れている（{exc}）")
    if got.failed:
        return result.stopped(
            tool, "check_failed", f"依存の用意: 失敗（{got.failed}・{got.seconds} 秒）", items=[{"line": x} for x in got.tail]
        )
    summary = "依存の用意: 用意の印があった" if got.skipped else f"依存の用意: 済み（{got.steps} 件・{got.seconds} 秒）"
    return result.result(tool, result.OK, summary, metrics=deps.summary(got))


def _verdict(tool: str, v: localenv_decide.Verdict, items: list[str]) -> dict[str, Any]:
    rows = [{"path": x} for x in items]
    if v.result == localenv_decide.MISMATCH:
        return result.stopped(tool, "check_failed", v.text, items=rows)
    if v.result == localenv_decide.NOT_RUNNING:
        return result.stopped(tool, "not_applicable", v.text)
    return result.result(tool, result.OK, v.text, rows, {"result": v.result})


def run_localenv(tool: str, args: argparse.Namespace) -> dict[str, Any]:
    t = localenv.target(_where(args))
    if t is None:
        return result.result(tool, result.OK, NOT_APPLICABLE)
    shell = container.Shell.from_env()
    try:
        if args.action == "setup":
            return result.result(tool, result.OK, f"複製した: {localenv.setup(t)} 件")
        if args.action == "aim":
            return result.result(tool, result.OK, f"ローカル環境が指すコードを {t.path} へ向けた（コンテナ {localenv.aim(t, shell)} 件）")
    except localenv.LocalenvError as exc:
        return result.stopped(tool, "precondition", str(exc))
    if args.action == "mode":
        verdict, hits = localenv.mode(t)
        return _verdict(tool, verdict, hits)
    if args.action == "verify":
        return _verdict(tool, localenv.verify(t, shell), [])
    verdict, rc = localenv.healthcheck(t, shell)
    if verdict.result != localenv_decide.MATCH:
        return _verdict(tool, verdict, [])
    if rc is None:
        return result.result(tool, result.OK, "動作確認のコマンドが宣言されていない")
    if rc != 0:
        return result.stopped(tool, "check_failed", f"動作確認のコマンドが失敗した（終了コード {rc}）")
    return result.result(tool, result.OK, "動作確認が通った")


def _te_env(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    row = testenv.assign(t)
    out = {k: row[k] for k in ("environment", "slot", "worktree", "branch", "ports")}
    return result.result(tool, result.OK, f"テスト環境 {row['environment']}（スロット {row['slot']}）", [out], outputs=out)


def _te_tag(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    value = testenv.tag(t)
    if value is None:
        return result.result(tool, result.OK, "golden_tag_paths が宣言されていない")
    return result.result(tool, result.OK, f"基準のタグ: {value}", outputs={"tag": value})


def _te_bake(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    if not args.golden:
        return result.stopped(tool, "invalid", "--tag が要る")
    created, existing = testenv.bake(t, args.golden)
    return result.result(
        tool, result.OK, f"基準を {created} 件作った（既にあった {existing} 件）", metrics={"created": created, "existing": existing}
    )


def _rc(tool: str, label: str, rc: int) -> dict[str, Any]:
    if rc != 0:
        return result.stopped(tool, "unknown", f"{label} が失敗した（終了コード {rc}）")
    return result.result(tool, result.OK, f"{label} を終えた")


def _te_test(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    if not args.kind:
        return result.stopped(tool, "invalid", "--kind が要る")
    rc = testenv.run_test(t, args.kind, args.out)
    if rc != 0:
        return result.stopped(tool, "test_failed", f"テスト（{args.kind}）が終了コード {rc} で終わった", metrics={"exit": rc})
    return result.result(tool, result.OK, f"テスト（{args.kind}）が通った", metrics={"exit": rc})


def _te_compose_env(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    values = testenv.compose_env(t)
    return result.result(
        tool,
        result.OK,
        "割り当ては無い" if not values else f"渡す値 {len(values)} 件",
        [{"key": k, "value": v} for k, v in values.items()],
        outputs=values,
    )


def _te_reap(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    try:
        idle = parse_period(args.idle if args.idle else "")
    except ValueError as exc:
        return result.stopped(tool, "invalid", f"--idle: {exc}")
    items = testenv_sweep.reap(t, idle)
    return result.result(tool, result.OK, f"止めた: {sum(1 for i in items if i['result'] == 'stopped')} 件", items)


def _te_reclaim(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    items = testenv.reclaim(t)
    return result.result(
        tool,
        result.OK,
        f"回収した: {sum(1 for i in items if i['result'] != 'kept')} 件（残した {sum(1 for i in items if i['result'] == 'kept')} 件）",
        items,
    )


def _te_expose(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    url = testenv_sweep.expose(t)
    return result.result(tool, result.OK, f"公開した: {url}", outputs={"url": url})


def _te_unexpose(tool: str, t: testenv.Target, args: argparse.Namespace) -> dict[str, Any]:
    url = testenv_sweep.unexpose(t)
    return result.result(tool, result.OK, "開いている公開は無い" if url is None else f"公開を閉じた: {url}")


TESTENV_HANDLERS: dict[str, Callable[[str, testenv.Target, argparse.Namespace], dict[str, Any]]] = {
    "env": _te_env,
    "tag": _te_tag,
    "bake": _te_bake,
    "up": lambda tool, t, a: _rc(tool, "起動", testenv.up(t, a.profile, a.golden, tuple(a.wait))),
    "stop": lambda tool, t, a: _rc(tool, "停止", testenv.stop(t)),
    "down": lambda tool, t, a: _rc(tool, "破棄", testenv.down(t, a.volumes)),
    "test": _te_test,
    "compose-env": _te_compose_env,
    "reap": _te_reap,
    "reclaim": _te_reclaim,
    "expose": _te_expose,
    "unexpose": _te_unexpose,
}


def run_testenv(tool: str, args: argparse.Namespace) -> dict[str, Any]:
    t = testenv.target(_where(args))
    if t is None:
        return result.result(tool, result.OK, NOT_APPLICABLE)
    try:
        return TESTENV_HANDLERS[args.action](tool, t, args)
    except testenv.TestenvError as exc:
        return result.stopped(tool, "precondition", str(exc))
    except container.ContainerError as exc:
        return result.stopped(tool, "deps_missing", str(exc))
