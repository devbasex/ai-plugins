"""公開前の静的解析（#1693 F1・I1・I2）。

最終ゲート修正の取り込み（`merge-final-fix`）が push の前に、修正のコミットが変えたファイルへ静的解析の範囲テストを当てる。
組み方は項目の検証と同じ `targets.lint_runs_for` で、宣言に静的解析の suite が無ければ何も走らせない（I7）。

**判定は値を返すだけで止めない。** 落ちた suite・コマンド・ファイルを `LintRejection` に載せ、起動の失敗も値
（`PrepushResult.launch_failure`）に載せる。止める（`launch.stop`）のも取り消すのも呼ぶ側（`commands/final_fix.py`）である。
次の最終ゲート修正の依頼に載せる内容（`fix_request`）もここで組む（`commands/gate.py` が書く）。
上限はテスト 1 回の上限（`timeline.state_test_timeout`）を suite 群全体で 1 つ使い、超えたら落ちた扱いにする。
"""

from __future__ import annotations

import dataclasses
import pathlib
import time
from typing import Any, Optional

import failure_paths
import test_strategy as ts

from . import targets, timeline
from .paths import work_dir


@dataclasses.dataclass
class LintRejection:
    """落ちた静的解析の suite 1 つ。`files` はログに現れた変えたファイル（無ければ suite に渡したファイル）。"""

    suite: str
    command: str
    files: list[str]
    reason: str
    log: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class LaunchFailure:
    """起動の失敗（`launch.stop` へ渡す材料）。"""

    command: str
    outcome: ts.Outcome
    log: pathlib.Path


@dataclasses.dataclass
class PrepushResult:
    passed: bool
    rejections: list[LintRejection]
    seconds: float
    launch_failure: Optional[LaunchFailure] = None


def _read(log: pathlib.Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _files_of(run: ts.ScopeRun, strategy: ts.Strategy, files: list[str]) -> list[str]:
    suite = next((s for s in strategy.scoped_suites(ts.LINT) if s.name == run.suite), None)
    return [f for f in files if suite is None or suite.covers(f)]


def check(state: dict[str, Any], files: list[str], label: str) -> PrepushResult:
    """`files` へ静的解析の範囲テストを当てる。通れば `passed`。ログは `tmp_dir/<label>-<suite>.log`。"""
    runs = targets.lint_runs_for(state, files)
    started = time.monotonic()
    if not runs:
        return PrepushResult(True, [], 0.0)
    strategy, work = timeline.strategy_of(state), work_dir(state)
    limit = timeline.state_test_timeout(state)
    rejections: list[LintRejection] = []
    for run in runs:
        log = pathlib.Path(state["tmp_dir"]) / f"{label}-{run.suite}.log"
        left = int(limit - (time.monotonic() - started))
        if left < 1:
            outcome = ts.outcome(None, True)
        else:
            outcome, _ = targets.run_commands([run.command], work, left, log)
        if outcome.launch_failed:
            return PrepushResult(False, rejections, round(time.monotonic() - started, 1), LaunchFailure(run.command, outcome, log))
        if outcome.status == ts.PASSED:
            continue
        given = _files_of(run, strategy, files)
        named = failure_paths.mentioned(_read(log), given)
        reason = f"{limit} 秒の上限で打ち切った" if outcome.status == ts.TIMED_OUT else outcome.reason
        rejections.append(LintRejection(run.suite, run.command, named or given, reason, str(log)))
    return PrepushResult(not rejections, rejections, round(time.monotonic() - started, 1))


def summary(rejections: list[dict[str, Any]]) -> str:
    """差し戻しの 1 行（suite とファイル）。報告と修正の依頼が使う。"""
    parts = [f"静的解析 {r.get('suite')} が {', '.join(r.get('files') or []) or '（ファイル不明）'} で落ちた" for r in rejections]
    return " / ".join(parts)


def fix_request(gate: dict[str, Any]) -> str:
    """次の最終ゲート修正の依頼に載せる内容（Markdown）。載せるものが無ければ空（#1693 F2）。

    直前の最終ゲートで落ちた検査（静的解析の変更起因の suite とコマンド・テストの見分け）と、
    直前の差し戻し（`lint_rejections` の最後の 1 件）を並べる。
    """
    lines: list[str] = []
    for verdict in gate.get("lint") or []:
        if verdict.get("verdict") == "caused":
            lines.append(f"- 静的解析 `{verdict.get('suite')}`（変更起因）: `{verdict.get('command')}` — {verdict.get('reason')}")
    triage = gate.get("triage") or {}
    if triage.get("caused"):
        rerun = triage.get("rerun_commands") or ([triage["rerun_command"]] if triage.get("rerun_command") else [])
        commands = " / ".join(f"`{c}`" for c in rerun) or "（走らせ直すコマンド無し）"
        lines.append(f"- テスト（変更起因 {len(triage['caused'])} 件）: {', '.join(triage['caused'][:10])} — 走らせ直し: {commands}")
    last = (gate.get("lint_rejections") or [None])[-1]
    # 直前のラウンドの差し戻しだけを載せる（`_gate_failing` はラウンドを進めてから呼ぶ）
    if last and int(last.get("round") or 0) >= int(gate.get("fix_rounds") or 0) - 1:
        lines.append("")
        lines.append(f"直前の最終ゲート修正（ラウンド {last.get('round')}）は公開前の静的解析で落ち、取り消しました。同じ違反を入れないこと:")
        for r in last.get("rejections") or []:
            files = ", ".join(f"`{f}`" for f in r.get("files") or []) or "（ファイル不明）"
            lines.append(f"- 静的解析 `{r.get('suite')}`: {files} — `{r.get('command')}`（{r.get('reason')}。ログ: {r.get('log')}）")
    return "\n".join(lines).strip()
