"""範囲テストのテンプレートの試走（#1630）。`commands/setup.py` の `init` が着手前のテストの後に呼ぶ。

改修の範囲テストは計画の `test_targets`（テストのファイル）をテンプレートの `{paths}` へ入れる。ファイルを受けない
テンプレート（unittest の `discover -s {paths}` など）だと、コードと関係なく毎回落ちて修正へ回る。そこで改修の前に、
`scope_command` を持つテストの suite ごとにテストのファイルを最大 2 本入れて 1 回走らせ、受けるかを見分ける。
**判定が `ok` の suite だけが続く。** 不備（`defect`）か判定できない（`undecided`）なら、直す箇所を出して止める。

試走のコマンドは改修の範囲テストと同じ `test_strategy.fill` で組む（コマンドの語を見ない）。ファイルの選び方と判定は
`test_strategy.probe_paths` / `template_verdict` が持ち、ここは候補を集めて走らせ、記録を組むだけである。
記録を組んで `<tmp_dir>/template-check.json` へ書くのはこのモジュールだけで、`init` は返った記録を状態ファイルの
`template_checks` へそのまま置く。着手前のテストの記録（`baseline_test`）とテストの戦略は書き換えない。
"""

from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import statefile
import test_strategy as ts
import test_triage

from . import die, info, launch
from .gitfacts import run_with_timeout
from .scope import test_files_under, test_locations, tracked_files_under

RECORD_NAME = "template-check.json"
NO_TIME = "着手前のテストの上限の残りが無い"
NO_FILES = "候補のファイルが無い"


@dataclass
class Report:
    """試走の結果。`stopped` が真なら `message` を出して止める。`suites` は状態ファイルの `template_checks`。"""

    stopped: bool
    suites: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    path: Optional[pathlib.Path] = None


def targets_of(strategy: ts.Strategy) -> list[ts.Suite]:
    """試走の対象の suite。`round-only` は改修の範囲テストを使わないため、静的解析は `{paths}` に変更したファイルが入るため対象にしない。"""
    if strategy.name == ts.ROUND_ONLY:
        return []
    return strategy.scoped_suites(ts.TEST)


def _decl_index(decl: dict[str, Any], suite: ts.Suite) -> Optional[int]:
    test = (decl or {}).get("test")
    rows = test.get("suites") if isinstance(test, dict) else None
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        if isinstance(row, dict) and str(row.get("name") or "suite") == suite.name and row.get("scope_command") == suite.scope_command:
            return i
    return None


def _fix_at(strategy: ts.Strategy, decl: dict[str, Any], suite: ts.Suite, flag: Optional[str]) -> tuple[Optional[int], str]:
    """直す箇所。宣言の suite は `test.suites[<番号>].scope_command`、引数の雛形は引数の名前。"""
    if strategy.source == "args" and suite.name == "args":
        return None, f"{flag or '--round-test'} の値"
    index = _decl_index(decl, suite)
    if index is None:
        return None, f".ndf/project.json の test.suites（name: {suite.name}）の scope_command"
    return index, f".ndf/project.json の test.suites[{index}].scope_command"


def _probe_candidates(
    strategy: ts.Strategy, suite: ts.Suite, scope: list[str], work: pathlib.Path
) -> tuple[list[str], list[str], list[str]]:
    """`--scope` のテストの置き場所の配下・リポジトリ全体・suite の `paths` の配下の候補（選ぶのは `probe_paths`）。"""
    w = str(work)
    in_scope = [f for loc in test_locations(scope, w) for f in test_files_under(loc, w)]
    repo = test_files_under(".", w)
    under = [f for root in ts.probe_roots(suite) for f in tracked_files_under(root, w)]
    return in_scope, repo, under


def _reference(baseline: dict[str, Any], suite: ts.Suite, source: Optional[str]) -> str:
    """基準。`--scope` の候補はその suite の着手前のテストの成否、ほかは全体（`command`）で通ったときだけ `green`。"""
    status = (baseline.get("suites") or {}).get(suite.name)
    if source == ts.PROBE_FROM_SCOPE:
        return status if status in (ts.REFERENCE_GREEN, ts.REFERENCE_RED) else ts.REFERENCE_NONE
    whole = baseline.get("mode") in ("whole", "round") and bool(suite.command)
    return ts.REFERENCE_GREEN if whole and status == ts.REFERENCE_GREEN else ts.REFERENCE_NONE


def _run_probe(
    command: Optional[str], work: pathlib.Path, log: pathlib.Path, budget: tuple[float, float]
) -> tuple[Optional[ts.Outcome], str]:
    """試走を 1 回走らせ、(結果, 走らせなかった理由) を返す。command が無ければ NO_FILES、上限の残りが 1 秒未満なら NO_TIME。"""
    if command is None:
        return None, NO_FILES
    left, started = budget
    if left - (time.monotonic() - started) < 1:
        return None, NO_TIME
    code, timed_out = test_triage.run_within(left, started, lambda limit: run_with_timeout(command, str(work), limit, output=log))
    result = ts.outcome(code, timed_out)
    if result.launch_failed:
        launch.stop(None, {}, "baseline", command, result, log)
    return result, NO_FILES


def _check_one(
    strategy: ts.Strategy,
    suite: ts.Suite,
    ctx: dict[str, Any],
    budget: tuple[float, float],
    i: int,
) -> dict[str, Any]:
    baseline, work = ctx["baseline"], ctx["work"]
    failures = baseline.get("existing_failures")
    failing = list(test_triage.by_file(list(failures or [])))
    pick = ts.probe_paths(strategy, suite, *_probe_candidates(strategy, suite, ctx["scope"], work), failing)
    index, fix_at = _fix_at(strategy, ctx["decl"], suite, ctx["flag"])
    reference = _reference(baseline, suite, pick.source)
    command = ts.fill(str(suite.scope_command), pick.paths) if pick.paths else None
    log = ctx["tmp_dir"] / f"template-check-{i}.log"
    result, not_run = _run_probe(command, work, log, budget)
    known = pick.source == ts.PROBE_FROM_SCOPE and failures is not None
    verdict = ts.template_verdict(result, reference, known, pick.owned, not_run)
    return {
        "suite": suite.name,
        "index": index,
        "fix_at": fix_at,
        "scope_command": suite.scope_command,
        "paths": list(pick.paths),
        "source": pick.source,
        "owned": pick.owned,
        "command": command,
        "code": result.code if result else None,
        "outcome": result.status if result else "not_run",
        "reference": reference,
        "verdict": verdict.status,
        "reason": verdict.reason,
        "log": str(log) if result else None,
    }


def stop_text(row: dict[str, Any]) -> str:
    """止めるときの文（suite ごとに 1 段）。`undecided` でも直す箇所と `--round-test` の直し方を必ず出す。"""
    head = "受けません" if row["verdict"] == ts.PROBE_DEFECT else "受けるかを確かめられません"
    if row["code"] is not None:
        ran = f"{row['command']} → 終了コード {row['code']}（{row['reason']}。着手前のテスト: {row['reference']}）"
    else:
        ran = f"走らせていない（{row['reason']}）"
    ways = ["ファイルを受けるコマンドへ直す", "--round-test に {paths} を含まない全体テストを渡す"]
    if not row["paths"]:
        ways.append("その suite の paths にテストの置き場所を宣言する")
    lines = [
        f"⛔ 範囲テストのテンプレートが {{paths}} に入るテストのファイルを{head}（suite: {row['suite']}）",
        f"   今のテンプレート: {row['scope_command']}",
        f"   入れたパス: {' '.join(row['paths']) or '（なし）'}",
        f"   試走: {ran}",
        f"   直す箇所: {row['fix_at']}（{{paths}} にはテストのファイルのパスが入る。{'か、'.join(ways)}）",
    ]
    if row.get("log"):
        lines.append(f"   ログ: {row['log']}")
    return "\n".join(lines)


def check_templates(
    strategy: ts.Strategy,
    baseline: dict[str, Any],
    scope: list[str],
    work: pathlib.Path,
    tmp_dir: pathlib.Path,
    timeout: int,
    *,
    decl: Optional[dict[str, Any]] = None,
    flag: Optional[str] = None,
) -> Report:
    """試走の対象の suite ごとに 1 回ずつ走らせ、記録を `<tmp_dir>/template-check.json` へ書いて返す。

    上限は着手前のテストと同じ `timeout` から、着手前のテストの所要（`baseline["seconds"]`）を引いた残りを
    suite 群で分け合う。`flag` は雛形を渡した引数の名前（`--round-test` / `--baseline-test`）。"""
    suites = targets_of(strategy)
    if not suites:
        return Report(False)
    left = float(timeout) - float(baseline.get("seconds") or 0.0)
    budget = (left, time.monotonic())
    ctx = {"baseline": baseline, "work": work, "scope": list(scope), "decl": decl or {}, "flag": flag, "tmp_dir": tmp_dir}
    rows = [_check_one(strategy, suite, ctx, budget, i) for i, suite in enumerate(suites)]
    # 試走が書いた JUnit を残さない（後の手順が前の実行の結果を読まないため）
    test_triage.clear_junit(str(work), strategy)
    stopped = any(r["verdict"] != ts.PROBE_OK for r in rows)
    path = tmp_dir / RECORD_NAME
    path.write_text(
        json.dumps({"checked_at": statefile.now(), "stopped": stopped, "suites": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    message = "\n".join(stop_text(r) for r in rows if r["verdict"] != ts.PROBE_OK)
    return Report(stopped, rows, message, path)


def report_summary(report: Report) -> str:
    """続けるときの 1 行。"""
    shown = "、".join(f"{r['suite']}（{' '.join(r['paths'])}）" for r in report.suites)
    return f"✅ 範囲テストのテンプレートを試走: {shown}"


def enforce(args: Any, prep: Any, baseline: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    """`init` の入口。試走して、`ok` でない suite があれば状態ファイルを作らずに止める（終了コード 4。#1630 の I5）。
    続けるときは状態ファイルの `template_checks` に置く記録を返す。"""
    round_test = getattr(args, "round_test", None)
    flag = "--round-test" if ts.has_paths(round_test) else "--baseline-test"
    report = check_templates(prep.strategy, baseline, list(args.scope), prep.work, prep.tmp_dir, timeout, decl=prep.decl, flag=flag)
    if report.stopped:
        info(report.message)
        die(f"範囲テストのテンプレートを直してから打ち直してください（記録: {report.path}）")
    if report.suites:
        info(report_summary(report))
    return report.suites
