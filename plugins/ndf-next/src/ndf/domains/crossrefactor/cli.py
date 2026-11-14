"""`ndf refactor` の副命令。

    ndf refactor touched --base <起点> --scope <置き場>... [--head HEAD] [--symbols <ファイル>:<名前>...] [--work <dir>]
    ndf refactor tests --changed <ファイル>... [--work <dir>]
    ndf refactor collected --paths <ファイル>... [--timeout <秒>] [--work <dir>]
    ndf refactor run --pr <N> --branch <head> --base <起点> --scope <置き場>... --required <チェック>... [--ci-test <名前>...]
                     [--symbols <ファイル>:<名前>...] [--participants <ランタイム>...] [--max-fix <回>]
                     [--budget-minutes <分>] [--safety-net-minutes <分>] [--work <dir>]
    ndf refactor state --pr <N> [--work <dir>]
    ndf refactor scope-args --base <起点> [--tests <置き場>...] [--scope <置き場>...] [--work <dir>]
    ndf refactor release --pr <N> [--work <dir>]

- `touched`: 課題が触る関数（#622）と、着手前の範囲テストへ渡す範囲の配下のテストのファイル（#2096）。`--symbols` を
  渡せば差分から引かずにそれを使う
- `tests`: 項目が触るファイルに対応する範囲テストのファイル（#3049）。対応を引けないソースは `unmapped` に載る
- `collected`: 足したテストのファイルが全体の実行に収集されるか（#620）。収集されないファイルがあれば `test_failed` で止まる
- `run`: 1 回の実行を提案から最終ゲートまで進める（`drive`）。止まれば状態ファイルを残し、同じ引数で打ち直すと続ける。
  `--budget-minutes` は採る項目を選ぶ実装の予算、`--safety-net-minutes` はコードを書く工程の起動の秒の累計の上限
  （無ければ実効の予算 × 宣言の倍率）。安全網で止まったら、上限を大きくして打ち直すと続ける
- `state`: 状態ファイルの中身（項目・工程の起点・最終ゲートの記録）を出す
- `scope-args`: 検査のプランが `run` の `--scope` へ渡す範囲を組む（#1484）
- `release`: 作業場所を片付ける（NDF の lock だけを外す）

結果 JSON を標準出力の最後の 1 行に出す。止まった理由は `ndf.contracts.causes` の種類で返す。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.adapters import git, github
from ndf.contracts import causes, limits, result
from ndf.core.converge import participants, worktree
from ndf.domains.crossrefactor import collect, drive, final_gate, items_decide, scope, store, workspace
from ndf.testing import limits as test_limits

TOOL = cli.tool_of(__name__)


def _touched(a: argparse.Namespace) -> dict[str, Any]:
    work = Path(a.work)
    touched = scope.touched_or_explicit(work, a.base, a.head, a.scope, a.symbols)
    files = scope.scope_test_files(work, a.scope)
    items = [{"kind": "touched", **touched.as_json()}, {"kind": "scope_tests", "files": files}]
    n = sum(len(s) for s in touched.symbols.values())
    summary = f"触る関数 {n} 件・ファイル単位 {len(touched.whole_files)} 件・範囲テストのファイル {len(files)} 件"
    metrics = {"symbols": n, "whole_files": len(touched.whole_files), "scope_tests": len(files)}
    return result.result(TOOL, "ok", summary, items, metrics)


def _tests(a: argparse.Namespace) -> dict[str, Any]:
    picked = scope.tests_for_change(Path(a.work), a.changed)
    summary = f"範囲テストのファイル {len(picked.files)} 件・対応の無いソース {len(picked.unmapped)} 件"
    metrics = {"files": len(picked.files), "unmapped": len(picked.unmapped), "unreadable": len(picked.unreadable)}
    return result.result(TOOL, "ok", summary, [{"kind": "tests", **picked.as_json()}], metrics)


def _collected(a: argparse.Namespace) -> dict[str, Any]:
    work = Path(a.work)
    timeout = a.timeout if a.timeout is not None else limits.seconds(collect.COLLECT_POLICY)
    got = collect.collected(work, a.paths, test_limits.load_suites(work), timeout)
    items = [{"kind": "collect", "collected": list(got.collected), "uncollected": list(got.uncollected), "undecided": list(got.undecided)}]
    metrics = {"collected": len(got.collected), "uncollected": len(got.uncollected), "undecided": len(got.undecided)}
    if got.uncollected:
        detail = f"全体の実行に収集されないテストのファイル: {', '.join(got.uncollected)}"
        return result.stopped(TOOL, "test_failed", detail, items=items, metrics=metrics)
    summary = f"収集される {len(got.collected)} 件・判定できない {len(got.undecided)} 件"
    return result.result(TOOL, "ok", summary, items, metrics)


def _run(a: argparse.Namespace) -> dict[str, Any]:
    cfg = drive.RunConfig(
        Path(a.work),
        a.pr,
        a.branch,
        a.base,
        scope=tuple(a.scope),
        required=tuple(a.required),
        tests=tuple(a.ci_test),
        symbols=tuple(a.symbols),
        participants=tuple(a.participants),
        max_fix=a.max_fix,
        budget_minutes=a.budget_minutes,
        safety_net_minutes=a.safety_net_minutes,
    )
    done = drive.drive(cfg)
    if done.kind == "ok":
        return result.result(TOOL, "ok", done.detail, done.items, done.metrics)
    return result.stopped(TOOL, done.kind, done.detail, items=done.items, metrics=done.metrics)


def _root(a: argparse.Namespace) -> Path:
    return workspace.root_of(git.Git(Path(a.work)), a.pr)


def _state(a: argparse.Namespace) -> dict[str, Any]:
    path = store.path_of(_root(a))
    data = store.load(path)
    items = data["items"] if "items" in data else []
    shown = [
        {
            "kind": "state",
            "path": str(path),
            "phases": data["phases"] if "phases" in data else {},
            "final": data["final"] if "final" in data else None,
        },
        *({"kind": "item", **i} for i in items),
    ]
    summary = f"状態ファイル {path}: 項目 {len(items)} 件" + ("・完了" if "done" in data else "")
    return result.result(TOOL, "ok", summary, shown, items_decide.counts(items))


def _scope_args(a: argparse.Namespace) -> dict[str, Any]:
    got = scope.scope_args(Path(a.work), a.base, a.tests, a.scope)
    return result.result(TOOL, "ok", f"範囲 {len(got)} 件: {' '.join(got)}", [{"kind": "scope", "paths": got}], {"paths": len(got)})


def _release(a: argparse.Namespace) -> dict[str, Any]:
    repo = git.Git(Path(a.work))
    ws = workspace.layout(_root(a), (*participants.assign(), participants.FIXER))
    kept = workspace.release(repo, ws)
    swept = worktree.sweep_orphans(repo)
    items = [{"kind": "kept", "reason": k} for k in kept]
    return result.result(
        TOOL,
        "ok",
        f"作業場所を片付けた（残した {len(kept)} 件・持ち主のいない lock {len(swept)} 件）",
        items,
        {"kept": len(kept), "swept": len(swept)},
    )


HANDLERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "touched": _touched,
    "tests": _tests,
    "collected": _collected,
    "run": _run,
    "state": _state,
    "scope-args": _scope_args,
    "release": _release,
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{identity.FIXED_WORDS['entrypoint']} {TOOL}", description="cross-refactoring の範囲とテストの選び方")
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("touched", help="課題が触る関数と、着手前の範囲テストのファイル")
    s.add_argument("--base", required=True)
    s.add_argument("--head", default="HEAD")
    s.add_argument("--scope", nargs="+", required=True)
    s.add_argument("--symbols", nargs="*", default=[])
    t = sub.add_parser("tests", help="項目が触るファイルに対応する範囲テストのファイル")
    t.add_argument("--changed", nargs="+", required=True)
    c = sub.add_parser("collected", help="足したテストが全体の実行に収集されるか")
    c.add_argument("--paths", nargs="+", required=True)
    c.add_argument("--timeout", type=float)
    for each in (s, t, c, *_drive_parsers(sub)):
        each.add_argument("--work", default=".")
    return p


def _drive_parsers(sub: Any) -> tuple[argparse.ArgumentParser, ...]:
    r = sub.add_parser("run", help="1 回の実行を提案から最終ゲートまで進める")
    r.add_argument("--pr", type=int, required=True)
    r.add_argument("--branch", required=True)
    r.add_argument("--base", required=True)
    r.add_argument("--scope", nargs="+", required=True)
    r.add_argument("--required", nargs="+", required=True)
    r.add_argument("--ci-test", nargs="*", default=[])
    r.add_argument("--symbols", nargs="*", default=[])
    r.add_argument("--participants", nargs="*", default=[])
    r.add_argument("--max-fix", type=int, default=2)
    r.add_argument("--budget-minutes", type=int)
    r.add_argument("--safety-net-minutes", type=float)
    st = sub.add_parser("state", help="状態ファイルの中身")
    rl = sub.add_parser("release", help="作業場所を片付ける")
    for each in (st, rl):
        each.add_argument("--pr", type=int, required=True)
    sa = sub.add_parser("scope-args", help="検査のプランが run の --scope へ渡す範囲")
    sa.add_argument("--base", required=True)
    sa.add_argument("--tests", nargs="*", default=[])
    sa.add_argument("--scope", nargs="*", default=[])
    return r, st, rl, sa


# 例外 → 止まった理由の種類。上から見て最初に当たった行を採る（ValueError の派生を先に置くため、並びを保つ）
STOP_KINDS: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((scope.ScopeError,), "invalid"),
    ((store.StateBroken,), "store_broken"),
    ((collect.CollectError, worktree.WorktreeUnregistered, worktree.LockFailed, worktree.SyncRefused, git.GitError), "precondition"),
    ((github.GitHubError,), "transient"),
    ((ValueError,), "invalid"),
)


def _stop_kind(exc: Exception) -> Optional[str]:
    """例外に当たる止まった理由の種類。表に無ければ None。"""
    return next((kind for types, kind in STOP_KINDS if isinstance(exc, types)), None)


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        return result.emit(HANDLERS[args.action](args))
    except final_gate.GateStopped as exc:
        return result.emit(result.stopped(TOOL, exc.kind, exc.detail))
    except Exception as exc:
        kind = _stop_kind(exc)
        if kind is None:
            raise
        return result.emit(result.stopped(TOOL, kind, str(exc)))
