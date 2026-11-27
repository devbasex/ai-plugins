"""`ndf refactor` のサブコマンド。

    ndf refactor touched --base <起点> --scope <保存先>... [--head HEAD] [--symbols <ファイル>:<名前>...] [--work <dir>]
    ndf refactor tests --changed <ファイル>... [--work <dir>]
    ndf refactor collected --paths <ファイル>... [--timeout <秒>] [--work <dir>]
    ndf refactor run --pr <N> --branch <head> --base <起点> --scope <保存先>...
                     (--required <チェック>... [--ci-test <名前>...] | --local-test [--pushed-out <ファイル>])
                     [--symbols <ファイル>:<名前>...] [--participants <ランタイム>...] [--max-fix <回>]
                     [--budget-minutes <分>] [--safety-net-minutes <分>] [--candidates <ファイル>] [--work <dir>]
    ndf refactor state --pr <N> [--work <dir>]
    ndf refactor scope-args --base <起点> [--tests <保存先>...] [--scope <保存先>...] [--work <dir>]
    ndf refactor release --pr <N> [--work <dir>]
    ndf refactor follow --pushed <ファイル> --branch <head> --remote <リモート> [--work <dir>]

- `touched`: 課題が触る関数（#622）と、着手前の範囲テストへ渡す範囲の配下のテストのファイル（#2096）。`--symbols` を
  渡せば差分から引かずにそれを使う
- `tests`: 項目が触るファイルに対応する範囲テストのファイル（#3049）。対応を引けないソースは `unmapped` に載る
- `collected`: 足したテストのファイルが全体の実行に収集されるか（#620）。収集されないファイルがあれば `test_failed` で止まる
- `run`: 1 回の実行を提案から最終ゲートまで進める（`drive`）。止まれば状態ファイルを残し、同じ引数で打ち直すと続ける。
  `--budget-minutes` は採る項目を選ぶ実装の予算、`--safety-net-minutes` はコードを書く工程の起動の秒の累計の上限
  （無ければ実効の予算 × 設定の倍率）。安全網で止まったら、上限を大きくして打ち直すと続ける。`--candidates` は実装計画が
  挙げたスプリントの終わりの候補のファイルで、提案の指示文に載る。中身が前回と違えば前回の結果も未完了の状態も使わない。
  最終ゲートの確かめ先は `--required`（必須のチェックを CI で待つ）と `--local-test`（手元でテスト全体を打つ。設定の手段が
  手元のテストのときだけ受ける）のどちらか 1 つ（#4566）。`--pushed-out` は手元の経路で合わせる先の SHA を書き出すファイル
- `state`: 状態ファイルの中身（項目・工程の起点・最終ゲートの記録）を出す
- `scope-args`: 検査のプランが `run` の `--scope` へ渡す範囲を組む（#1484）
- `release`: 作業場所を片付ける（NDF の lock だけを外す）。worktree が残らなければ状態ファイルをログの記録だけにする
- `follow`: 手元の経路で `run` が送った SHA（`--pushed-out` のファイル）へ、呼び出し元の作業場所を早送りする。SHA のコミットが
  手元にあれば取得しない（工程のプランのステップ `follow`。#4566）

結果 JSON を標準出力の最後の 1 行に出す。停止理由は `ndf.contracts.causes` の種類で返す。
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from ndf import cli, identity
from ndf.adapters import git, github
from ndf.contracts import causes, limits, result
from ndf.core.converge import participants, worktree
from ndf.domains.crossrefactor import collect, drive, final_gate, items_decide, local_gate, scope, store, workspace
from ndf.testing import declared, means
from ndf.testing import slots

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
    got = collect.collected(work, a.paths, declared.load_suites(work), timeout)
    items = [{"kind": "collect", "collected": list(got.collected), "uncollected": list(got.uncollected), "undecided": list(got.undecided)}]
    metrics = {"collected": len(got.collected), "uncollected": len(got.uncollected), "undecided": len(got.undecided)}
    if got.uncollected:
        detail = f"全体の実行に収集されないテストのファイル: {', '.join(got.uncollected)}"
        return result.stopped(TOOL, "test_failed", detail, items=items, metrics=metrics)
    summary = f"収集される {len(got.collected)} 件・判定できない {len(got.undecided)} 件"
    return result.result(TOOL, "ok", summary, items, metrics)


def _check_gate(a: argparse.Namespace) -> None:
    """最終ゲートの確かめ先の引数と設定の手段の組み合わせを確かめる（#4566 の決定 3）。合わなければ `ValueError`（`invalid`）。

    `--required` は設定の手段に関わらず受ける。`--local-test` は `--work` の設定の手段が手元のテストのときだけ受ける。
    """
    if bool(a.required) == bool(a.local_test):
        raise ValueError("--required <チェック>... と --local-test のどちらか 1 つを渡す")
    if not a.local_test:
        return
    if a.ci_test:
        raise ValueError("--local-test と --ci-test は併せて渡せない（--ci-test は必須のチェックの経路だけが使う）")
    project = local_gate.work_settings(Path(a.work))
    if not isinstance(project, dict):
        raise final_gate.GateStopped("precondition", "テストの設定のファイルがオブジェクトでない")
    kind = means.decide(project).kind
    if kind != means.Kind.LOCAL_TEST:
        raise ValueError(f"設定のテストを確かめる手段が手元のテストでない（{kind.value}）ので --local-test を受けない")


def run_config(a: argparse.Namespace) -> drive.RunConfig:
    """`run` の引数から 1 回の実行の引数を組む。候補のファイルは起動の頭でここだけ読む。"""
    _check_gate(a)
    candidates = Path(a.candidates).read_text(encoding="utf-8") if a.candidates is not None else ""
    return drive.RunConfig(
        Path(a.work),
        a.pr,
        a.branch,
        a.base,
        # 既定の起動は先頭がハイフンのパスをオプションと分けるため `./` を前置して渡す。リポジトリ相対へ戻す（#3908 の r2-4）
        scope=tuple(PurePosixPath(p).as_posix() for p in a.scope),
        required=tuple(a.required),
        tests=tuple(a.ci_test),
        symbols=tuple(a.symbols),
        participants=tuple(a.participants),
        max_fix=a.max_fix,
        budget_minutes=a.budget_minutes,
        safety_net_minutes=a.safety_net_minutes,
        candidates=candidates,
        local_test=a.local_test,
        pushed_out=Path(a.pushed_out) if a.pushed_out is not None and a.local_test else None,
    )


def _run(a: argparse.Namespace) -> dict[str, Any]:
    done = drive.drive(run_config(a))
    if done.kind == "ok":
        return result.result(TOOL, "ok", done.detail, done.items, done.metrics)
    return result.stopped(TOOL, done.kind, done.detail, items=done.items, metrics=done.metrics)


def _root(a: argparse.Namespace) -> Path:
    return workspace.root_of(git.Git(Path(a.work)), a.pr)


def _state(a: argparse.Namespace) -> dict[str, Any]:
    path = store.path_of(_root(a))
    data = store.load(path)
    items = data["items"] if "items" in data else []
    # 更新前に保存した項目は計画の担当が足した欄を持ちうる。結果のコントラクトの欄と名前が重なるものは出さない
    contracted = result.item_fields()
    shown = [
        {
            "kind": "state",
            "path": str(path),
            "phases": data["phases"] if "phases" in data else {},
            "final": data["final"] if "final" in data else None,
        },
        *({"kind": "item", **{k: v for k, v in i.items() if k not in contracted}} for i in items),
    ]
    summary = f"状態ファイル {path}: 項目 {len(items)} 件" + ("・完了" if "done" in data else "")
    return result.result(TOOL, "ok", summary, shown, items_decide.counts(items))


def _scope_args(a: argparse.Namespace) -> dict[str, Any]:
    got = scope.scope_args(Path(a.work), a.base, a.tests, a.scope)
    return result.result(TOOL, "ok", f"範囲 {len(got)} 件: {' '.join(got)}", [{"kind": "scope", "paths": got}], {"paths": len(got)})


def _follow_pushed(a: argparse.Namespace) -> dict[str, Any]:
    sha = local_gate.follow_pushed(git.Git(Path(a.work)), Path(a.pushed), a.remote, a.branch)
    return result.result(TOOL, "ok", f"送った SHA {sha} へ合わせた", [{"kind": "followed", "sha": sha}], {})


def _release(a: argparse.Namespace) -> dict[str, Any]:
    repo = git.Git(Path(a.work))
    root = _root(a)
    held = workspace.hold_pr(root.parent, a.pr)
    if held is None:
        return result.stopped(TOOL, "precondition", workspace.held_elsewhere(a.pr))
    try:
        kept, swept = _release_held(repo, root)
    finally:
        held.release()
    items = [{"kind": "kept", "reason": k} for k in kept]
    return result.result(
        TOOL,
        "ok",
        f"作業場所を片付けた（残した {len(kept)} 件・オーナーのいない lock {len(swept)} 件）",
        items,
        {"kept": len(kept), "swept": len(swept)},
    )


def _release_held(repo: git.Git, root: Path) -> tuple[list[str], list[Path]]:
    """ロックを持って片付ける。`rf<PR>/` の下に worktree が残らなければ、状態ファイルをログの記録だけにする（#4566）。

    取り除けずに残した検証用の作業場所（登録の無いディレクトリを含む）のパスは `verify_trees` に残し、調べる人と次の
    `run` か `release` が見つけられるようにする。
    """
    ws = workspace.layout(root, (*participants.assign(), participants.FIXER))
    kept = workspace.release(repo, ws)
    path = store.path_of(root)
    state = store.load(path)
    kept += local_gate.sweep_verify_trees(repo, state, lambda: store.save(path, state))
    swept = worktree.sweep_orphans(repo)
    below = [e for e in repo.worktrees() if Path(e.path).resolve().is_relative_to(root.resolve())]
    if not below and path.is_file():
        left = local_gate.listed(state, "verify_trees")
        store.save(path, {**{k: state[k] for k in workspace.LOG_ONLY_KEYS if k in state}, **({"verify_trees": left} if left else {})})
    workspace.sweep(repo, root, time.time())
    return kept, swept


HANDLERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "touched": _touched,
    "tests": _tests,
    "collected": _collected,
    "run": _run,
    "state": _state,
    "scope-args": _scope_args,
    "release": _release,
    "follow": _follow_pushed,
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
    r.add_argument("--required", nargs="+", default=[])
    r.add_argument("--local-test", action="store_true")
    r.add_argument("--pushed-out")
    r.add_argument("--ci-test", nargs="*", default=[])
    r.add_argument("--symbols", nargs="*", default=[])
    r.add_argument("--participants", nargs="*", default=[])
    r.add_argument("--max-fix", type=int, default=2)
    r.add_argument("--budget-minutes", type=int)
    r.add_argument("--safety-net-minutes", type=float)
    r.add_argument("--candidates")
    st = sub.add_parser("state", help="状態ファイルの中身")
    rl = sub.add_parser("release", help="作業場所を片付ける")
    for each in (st, rl):
        each.add_argument("--pr", type=int, required=True)
    sa = sub.add_parser("scope-args", help="検査のプランが run の --scope へ渡す範囲")
    sa.add_argument("--base", required=True)
    sa.add_argument("--tests", nargs="*", default=[])
    sa.add_argument("--scope", nargs="*", default=[])
    fo = sub.add_parser("follow", help="呼び出し元の作業場所を、手元の経路で送った SHA へ早送りする")
    fo.add_argument("--pushed", required=True)
    fo.add_argument("--branch", required=True)
    fo.add_argument("--remote", required=True)
    return r, st, rl, sa, fo


# 例外 → 停止理由の種類。上から見て最初に当たった行を採る（ValueError の派生を先に置くため、並びを保つ）
STOP_KINDS: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((scope.ScopeError,), "invalid"),
    ((store.StateBroken,), "store_broken"),
    ((collect.CollectError, worktree.WorktreeUnregistered, worktree.LockFailed, worktree.SyncRefused, git.GitError), "precondition"),
    ((github.GitHubError,), "transient"),
    ((ValueError,), "invalid"),
)


def _stop_kind(exc: Exception) -> Optional[str]:
    """例外に当たる停止理由の種類。表に無ければ None。"""
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
    except slots.Stopped as exc:
        # テストのスロットの待ちの諦めとメモリの停止（collected の収集の確認）。落ちたテストに数えずに停止理由にする（I9）
        return result.emit(result.stopped(TOOL, exc.cause, exc.summary(), items=exc.retired))
    except Exception as exc:
        kind = _stop_kind(exc)
        if kind is None:
            raise
        return result.emit(result.stopped(TOOL, kind, str(exc)))
