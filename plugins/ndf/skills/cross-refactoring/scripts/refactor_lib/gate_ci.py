"""最終ゲートの CI の側（#1334 F4・決定 7・9）。`commands/gate.py` が呼ぶ。

全体テストを CI に任せる戦略（`local-scoped-ci-whole`）か `--ci-check` のとき、push 済みの HEAD のチェックを
`limits.ci_wait_timeout` まで待ち、落ちたら run の成果物の JUnit を落として見分けの材料にする。検証で最終ゲートへ
寄せた危険フラグの全体テストで変更起因が残って締め切りを過ぎたときは、原因の項目から順に取り消す（#1649）。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import gh_checks
import test_triage

from . import culprit, die, info, rounds, timeline
from .github import gh_api_get
from .items import live_items
from .outbound import plan_line
from .paths import git_out, work_dir


def ci_mode(state: dict[str, Any]) -> bool:
    """最終ゲートを CI で見るか（戦略が CI に任せる、または `--ci-check`）。"""
    return timeline.strategy_of(state).whole_on_ci or bool(str(state.get("ci_check") or "").strip())


def ci_checks(state: dict[str, Any]) -> list[str]:
    """待つチェックの名前。`--ci-check` → 戦略の `ci.checks`（宣言の `test.ci.check` か必須のチェック）。"""
    named = str(state.get("ci_check") or "").strip()
    if named:
        return [named]
    return [str(c) for c in (timeline.strategy_of(state).ci or {}).get("checks") or []]


def ci_junit(state: dict[str, Any], sha: str, checks: list[str], runs: Optional[list[dict[str, Any]]] = None) -> list[bytes]:
    """落ちたチェックの run の成果物から JUnit を落とす。取れなければ空（見分けは走らせ直しへ落ちる）。

    `runs` は判定に使ったチェックジョブの一覧。渡せば照会し直さない。
    """
    repo = str(state.get("repo") or "")
    glob = (timeline.strategy_of(state).ci or {}).get("junit_artifacts")
    return test_triage.ci_junit_xmls(
        repo, sha, checks, glob, fetch_runs=lambda: runs if runs is not None else gh_checks.fetch_check_runs(repo, sha, rest_get=gh_api_get)
    )


def ci_gate(state: dict[str, Any]) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """継続的統合の結果で判定する。`pending` の間は上限（`limits.ci_wait_timeout`）まで待つ（決定 9）。

    **結果を得られないときは通過させない**（fail-closed）。上限までに終わらない・照会できないときは、待った秒と
    理由を出して「判断が要る」（終了コード 4）で終える。
    """
    sha = git_out(work_dir(state), ["rev-parse", "HEAD"]) or ""
    repo = str(state.get("repo") or "")
    checks = ci_checks(state)
    if not checks:
        die("最終ゲートで待つチェックの名前がありません（--ci-check か宣言の test.ci.check・ci.required_checks）")
    max_wait = float(timeline.state_ci_wait_timeout(state))
    last: dict[str, Any] = {}

    def fetch() -> Optional[str]:
        # 1 回の照会で全チェックの run を読み、名前ごとの結果はその一覧から出す（照会はチェックの本数によらず 1 回）
        # 照会の失敗は `None`、チェックの未登録は `pending`（`checks_outcome`）で、未登録の間は上限まで待つ
        runs = gh_checks.fetch_check_runs(repo, sha, rest_get=gh_api_get, empty_ok=True) if repo and sha else None
        last["runs"] = runs
        return gh_checks.checks_outcome(runs, checks)

    outcome, waited, attempts = test_triage.wait_check(
        fetch, max_wait, on_wait=lambda gap, n: info(f"⏳ CI を待っています（{n} 回目 / 次は {gap:.0f} 秒後）")
    )
    label = f"チェック {', '.join(checks)}（{sha[:7]} / 待ち {waited:.0f} 秒・照会 {attempts} 回 / 上限 {max_wait:.0f} 秒）"
    if outcome is None:
        die(f"{label} の結論を得られませんでした（上限までに終わらない、または照会に失敗）。判断が要ります")
    if outcome == "success":
        return True, f"{label} の結論は success でした", None
    return False, f"{label} の結論は {outcome} でした", {"timed_out": False, "ci_xmls": ci_junit(state, sha, checks, last.get("runs"))}


def revert_deferred(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> bool:
    """検証で最終ゲートへ寄せた危険フラグの全体テストで変更起因の失敗が出て締め切りを過ぎたとき、原因の項目から取り消す（#1649）。

    取り消す対象は寄せた危険フラグの項目ではなく、原因の判定（`culprit.judge`。締め切りは打ち切りの後の取り消しと同じ
    `limits.stop_revert_end_at`）の順である。取り消すたびに変更起因のファイルを手元で走らせ直し、通った時点か締め切りで止める。
    取り消したら真。寄せた項目が無い・変更起因でない・走らせ直す語が無い・残る項目が無いときは何もせず偽。
    """
    deferred = rounds.deferred_union(state)
    verdict = gate.get("triage") or {}
    rerun = culprit.rerun_of(verdict)
    if not deferred.get("items") or not verdict.get("caused") or not rerun or not live_items(state):
        return False
    end = timeline.stop_revert_end(state)
    found = culprit.judge(state, gate, verdict, end)
    reason = "最終ゲートへ寄せた危険フラグの全体テストで変更起因の失敗が出て、締め切りを過ぎた"
    passes = culprit.rerun_passes(state, rerun, end)
    reverted = culprit.revert_in_order(path, state, found.order, reason, passes, deadline=end).reverted
    gate["reverted_deferred"] = list(gate.get("reverted_deferred") or []) + reverted
    if reverted:
        info(f"↩ 原因の項目から順に取り消しました（{', '.join(reverted)}）。{plan_line(state)}")
    return bool(reverted)
