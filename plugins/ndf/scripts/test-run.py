#!/usr/bin/env python3
"""テストの実行の入口（#1334 F2・F4・F5）。supervise の run のステップが呼ぶ。

    python3 test-run.py scope --paths <パス>... [--root DIR] [--template CMD]
    python3 test-run.py whole [--pr N] [--base <ブランチ>] [--root DIR] [--template CMD]

宣言（`.ndf/project.json` の `test`。無ければ `.ndf/supervise.json` の `test.command` を 1 つの suite として読む）から
戦略を解き（`lib/test_strategy.resolve`）、範囲テストは雛形の `{paths}` を置き換えて走らせ、全体テストは戦略が手元なら
suite の `command` を、CI に任せる戦略なら Pull Request のチェックを待って JUnit を読む。落ちたテストは
`lib/test_triage.classify` で フレーキー・既存失敗・変更起因 に分ける。`--template` は引数の雛形で、宣言より先に効く。

終了コード: 0 = 通った（フレーキーと既存失敗だけのときも 0。`items` に分類を持つ）/ 1 = 変更起因の失敗がある /
2 = 判断できない（CI が上限までに終わらない・`gh` が使えない・宣言の不足。理由と待った秒を `summary` に出す）。
出力は `lib/step_result.py` の形の 1 行の JSON。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

import repo as repo_lib  # noqa: E402
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "test-run"
LOG_DIR = pathlib.Path(".ndf") / "tmp" / "test-run"


def _supervise_decl(root: pathlib.Path) -> dict:
    f = root / ".ndf" / "supervise.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve(root: pathlib.Path, template: str | None) -> tuple[ts.Strategy, dict, list[str]]:
    decl, note = ts.decl_of(root, _supervise_decl(root))
    notes = [note] if note else []
    strategy = ts.resolve(decl, baseline_test=template)
    w, w_source = ts.whole_seconds(decl)
    c = ts.ci_wall_seconds(decl, (strategy.ci or {}).get("check") if strategy.ci else None)
    limits = ts.limits(strategy, None, whole_seconds_value=w, whole_source=w_source, ci_seconds=c)
    return strategy, limits, notes


def _git_out(root: pathlib.Path, *args: str) -> str | None:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def _base_sha(root: pathlib.Path, base: str | None) -> str | None:
    """着手前の HEAD とみなす SHA = `--base`（無ければ worktree.json の base_branch）と HEAD の merge-base。"""
    branch = base or repo_lib.declared_base(root) or repo_lib.base_branch(root)
    if not branch:
        return None
    for ref in (f"origin/{branch}", branch):
        sha = _git_out(root, "merge-base", ref, "HEAD")
        if sha:
            return sha
    return None


def _classify(root: pathlib.Path, strategy: ts.Strategy, limits: dict, base: str | None, ci_xmls: list[bytes] | None = None) -> dict:
    work = str(root)
    if ci_xmls is not None:
        failed, reason = test_triage.merged_failed_ids(ci_xmls, test_triage.tracked_files(work))
    else:
        failed, reason = test_triage.read_junit(work, strategy)
    if failed is not None and not failed:
        failed, reason = None, "JUnit に落ちたテストが無い"
    return test_triage.classify(
        work=work,
        strategy=strategy,
        failed=failed,
        fallback_reason=reason,
        base_sha=_base_sha(root, base),
        timeout=int(limits["test_timeout"]),
        log_dir=root / LOG_DIR,
        existing_failures=[],
    )


def _emit_outcome(strategy: ts.Strategy, limits: dict, notes: list[str], triage: dict | None, passed: bool, detail: str) -> int:
    items = [{"strategy": strategy.name, "source": strategy.source, "limits": {k: v for k, v in limits.items() if k != "basis"}}]
    for note in notes:
        items.append({"note": note})
    if triage is not None:
        items.append({k: triage.get(k) for k in ("failed_tests", "flaky", "preexisting", "caused", "fallback_reason")})
    caused = bool(triage and triage.get("caused"))
    if passed:
        status, code = "ok", 0
    elif triage is not None and triage.get("fallback_reason") is None and not caused:
        status, code = "ok", 0
        detail += "（落ちたテストはフレーキーと既存失敗だけ）"
    elif triage is not None and triage.get("fallback_reason"):
        status, code = "stopped", 2
        detail += f"。見分けを全体の走らせ直しに落とした: {triage['fallback_reason']}"
    else:
        status, code = "stopped", 1
    emit(result(TOOL, status, detail, items, {"caused": len((triage or {}).get("caused") or [])}))
    return code


def cmd_scope(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a.template)
    words = test_triage.rerun_words(strategy, list(a.paths)) if strategy.name != ts.ROUND_ONLY else [[strategy.round_command]]
    if not words:
        emit(result(TOOL, "stopped", "範囲テストの雛形（scope_command）を持つ suite が無い", [], {}))
        return 2
    test_triage.clear_junit(str(root), strategy)
    (root / LOG_DIR).mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    failed_any = False
    for i, w in enumerate(words):
        command = w[0] if strategy.name == ts.ROUND_ONLY else w
        code, timed_out = test_triage.run_command(command, str(root), int(limits["test_timeout"]), root / LOG_DIR / f"scope-{i}.log")
        if timed_out:
            emit(result(TOOL, "stopped", f"範囲テストが {limits['test_timeout']} 秒で終わらなかった", [], {}))
            return 2
        failed_any = failed_any or code != 0
    seconds = round(time.monotonic() - started, 1)
    detail = f"範囲テスト {len(words)} 本（{seconds} 秒 / 戦略 {strategy.name}）"
    if not failed_any:
        return _emit_outcome(strategy, limits, notes, None, True, detail)
    triage = _classify(root, strategy, limits, a.base) if strategy.name != ts.ROUND_ONLY else {"fallback_reason": "round-only は JUnit を読まない"}
    return _emit_outcome(strategy, limits, notes, triage, False, detail + " が落ちた")


class _Resp:
    def __init__(self, body):
        self.body = body


def _wait_ci(root: pathlib.Path, strategy: ts.Strategy, limits: dict, notes: list[str], base: str | None) -> int:
    """Pull Request のチェックを上限まで待ち、落ちたら CI の JUnit で見分ける（F4）。"""
    import gh_checks

    owner_repo = repo_lib.owner_repo(root)
    sha = _git_out(root, "rev-parse", "HEAD") or ""
    checks = list((strategy.ci or {}).get("checks") or [])
    if not owner_repo or not sha:
        emit(result(TOOL, "stopped", "origin のリポジトリか HEAD が分からず、CI を読めない", [], {}))
        return 2
    if not checks:
        emit(result(TOOL, "stopped", "待つチェックの名前が無い（test.ci.check か ci.required_checks）", [], {}))
        return 2

    def runs_now():
        return gh_checks.fetch_check_runs(owner_repo, sha, rest_get=lambda p: _Resp(test_triage.gh_json(p)))

    def fetch():
        runs = runs_now()
        if runs is None:
            return None
        results = [gh_checks.check_result(runs, name) for name in checks]
        if any(r is None for r in results):
            return None
        if any(r == "pending" for r in results):
            return "pending"
        return "success" if all(r == "success" for r in results) else "failure"

    outcome, waited, attempts = test_triage.wait_check(fetch, float(limits["ci_wait_timeout"]))
    detail = f"CI のチェック {', '.join(checks)}（{sha[:7]} / 待ち {waited:.0f} 秒・照会 {attempts} 回 / 上限 {limits['ci_wait_timeout']} 秒）"
    if outcome is None:
        emit(result(TOOL, "stopped", f"{detail} の結論を得られなかった（上限か照会の失敗）", [{"waited_seconds": waited}], {}))
        return 2
    if outcome == "success":
        return _emit_outcome(strategy, limits, notes, None, True, detail)
    xmls = test_triage.ci_junit_xmls(owner_repo, sha, checks, (strategy.ci or {}).get("junit_artifacts"), fetch_runs=runs_now)
    triage = _classify(root, strategy, limits, base, ci_xmls=xmls)
    return _emit_outcome(strategy, limits, notes, triage, False, detail + f" の結論は {outcome}")


def cmd_whole(a) -> int:
    root = pathlib.Path(a.root).resolve()
    strategy, limits, notes = _resolve(root, a.template)
    if strategy.whole_on_ci:
        return _wait_ci(root, strategy, limits, notes, a.base)
    commands = strategy.whole_commands()
    if not commands:
        emit(result(TOOL, "stopped", "全体テストのコマンド（suites[].command）が無い", [], {}))
        return 2
    test_triage.clear_junit(str(root), strategy)
    (root / LOG_DIR).mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    failed_any = False
    for i, command in enumerate(commands):
        code, timed_out = test_triage.run_command(command, str(root), int(limits["whole_timeout"]), root / LOG_DIR / f"whole-{i}.log")
        if timed_out:
            emit(result(TOOL, "stopped", f"全体テストが {limits['whole_timeout']} 秒で終わらなかった", [], {}))
            return 2
        failed_any = failed_any or code != 0
    seconds = round(time.monotonic() - started, 1)
    detail = f"全体テスト {len(commands)} 本（{seconds} 秒 / 戦略 {strategy.name}）"
    if not failed_any:
        return _emit_outcome(strategy, limits, notes, None, True, detail)
    if strategy.name == ts.ROUND_ONLY:
        return _emit_outcome(strategy, limits, notes, {"fallback_reason": "round-only は JUnit を読まない"}, False, detail + " が落ちた")
    return _emit_outcome(strategy, limits, notes, _classify(root, strategy, limits, a.base), False, detail + " が落ちた")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    scope = sub.add_parser("scope", help="範囲テスト（雛形の {paths} を置き換えて走らせる）")
    scope.add_argument("--paths", nargs="+", required=True)
    whole = sub.add_parser("whole", help="全体テスト（手元か、CI に任せる戦略なら Pull Request のチェックを待つ）")
    whole.add_argument("--pr", type=int, default=None)
    for p in (scope, whole):
        p.add_argument("--root", default=".")
        p.add_argument("--base", default=None, help="着手前の HEAD とみなす起点のブランチ（既定は .ndf/worktree.json の base_branch）")
        p.add_argument("--template", default=None, help="範囲テストの雛形（{paths} を含むコマンド。宣言より先に効く）")
    a = ap.parse_args(argv)
    try:
        return cmd_scope(a) if a.cmd == "scope" else cmd_whole(a)
    except ts.StrategyError as e:
        emit(result(TOOL, "stopped", str(e), [], {}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
