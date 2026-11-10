"""全体テストの失敗の見分け（#933 決定 22・#1334 F5）の薄い包み。

状態ファイルから共通層 `test_triage.classify` の入力（戦略・着手前の HEAD・既存失敗・上限）を組み、結果を
`whole_test` / `final_gate` へ写す形に直す。見分けそのものは共通層が持ち、supervise の `test-run.py` と同じである。

| 分類 | 見分け方 | 扱い |
| --- | --- | --- |
| フレーキー（`flaky`） | 今の HEAD で走らせ直すと通る | 取り消さない |
| 既存失敗（`preexisting`） | 着手前の HEAD でも落ちる（`init` の既存失敗に載る ID も同じ） | 取り消さない |
| 変更起因（`caused`） | 今の HEAD で再び落ち、着手前の HEAD では通る | 直しを試みる |

落ちた ID は JUnit からだけ読む（I6）。読めなければ `fallback_reason` を書き、呼ぶ側が全体の走らせ直しへ落とす。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import test_strategy as ts
import test_triage

from . import timeline
from .gitfacts import run_with_timeout
from .paths import work_dir


def run_test(command: Any, cwd: str, timeout: int, log: Optional[pathlib.Path]) -> tuple[Optional[int], bool]:
    return run_with_timeout(command, cwd, timeout, output=log)


def baseline_head(state: dict[str, Any]) -> Optional[str]:
    """着手前の HEAD。`init` が残した SHA、無ければ（旧い状態ファイル）改修計画の起点。"""
    return (state.get("baseline_test") or {}).get("head") or (state.get("plan") or {}).get("base_sha") or None


def existing_failures(state: dict[str, Any]) -> list[str]:
    return list((state.get("baseline_test") or {}).get("existing_failures") or [])


def failed_ids_of(state: dict[str, Any], ci_xmls: Optional[list[bytes]] = None) -> tuple[Optional[list[str]], Optional[str]]:
    """落ちた ID。CI の JUnit（`ci_xmls`）があればそこから、無ければ手元の suite の JUnit の置き場から読む。"""
    work = work_dir(state)
    if ci_xmls is not None:
        return test_triage.merged_failed_ids(ci_xmls, test_triage.tracked_files(work))
    return test_triage.read_junit(work, timeline.strategy_of(state))


def classify(state: dict[str, Any], timed_out: bool, ci_xmls: Optional[list[bytes]] = None) -> dict[str, Any]:
    """全体テストの失敗を分ける。JUnit を読めなければ `fallback_reason` だけを返す。"""
    if timed_out:
        return {
            "failed_tests": None,
            "flaky": [],
            "preexisting": [],
            "caused": [],
            "fallback_reason": "全体テストが打ち切られ、落ちたテストを取り出せなかった",
        }
    ids, reason = failed_ids_of(state, ci_xmls)
    if ids is not None and not ids:
        return {
            "failed_tests": None,
            "flaky": [],
            "preexisting": [],
            "caused": [],
            "fallback_reason": "JUnit に落ちたテストが無かった（走らせ直して見分ける）",
        }
    result = test_triage.classify(
        work=work_dir(state),
        strategy=timeline.strategy_of(state),
        failed=ids,
        fallback_reason=reason,
        base_sha=baseline_head(state),
        timeout=timeline.state_test_timeout(state),
        log_dir=pathlib.Path(state["tmp_dir"]),
        existing_failures=existing_failures(state),
        run=run_test,
    )
    commands = list(result.get("rerun_commands") or [])
    # 変更起因のファイルだけを走らせ直すコマンド（シェルで走らせる文字列）。修正担当へ渡し、取り消しの判定にも使う。
    result["rerun_commands"] = commands
    result["rerun_command"] = commands[0] if commands else None
    result["rerun_log"] = str(pathlib.Path(state["tmp_dir"]) / "rerun-*.log")
    return result


def strategy(state: dict[str, Any]) -> ts.Strategy:
    return timeline.strategy_of(state)


def clear_junit(state: dict[str, Any]) -> None:
    """全体テストを走らせる前に suite の JUnit の置き場を消す（前の実行の結果を読まない）。"""
    test_triage.clear_junit(work_dir(state), timeline.strategy_of(state))
