"""検証の中の全体テスト（#933 決定 22・#1334 F5・F6）。`commands/converge.py` が呼ぶ。

危険フラグが立ったとき、手元の戦略は全体テストを 1 度走らせ、全体テストを CI に任せる戦略は最終ゲートへ寄せる。
落ちて JUnit で見分けられないときは全体を 1 度走らせ直し、落としたことと理由を `whole_test` に残す。
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Callable

import statefile
import test_strategy as ts
import test_triage

from . import info, launch, timeline, triage
from .gitfacts import run_with_timeout
from .outbound import dropped_line
from .paths import work_dir
from .undo import drop


def defer_to_final_gate(path: pathlib.Path, state: dict[str, Any], record: dict[str, Any], flags: list[str], flagged: list[str]) -> None:
    """全体テストを CI に任せる戦略では、危険フラグの全体テストを最終ゲートの 1 回へ寄せる（決定 7・I8）。"""
    deferred = record.setdefault("deferred", {"flags": [], "items": []})
    deferred["flags"] = sorted(set(deferred.get("flags") or []) | set(flags))
    deferred["items"] = list(dict.fromkeys(list(deferred.get("items") or []) + flagged))
    record["resolution"] = "deferred"
    statefile.save(path, state)
    info(
        f"⏭ 危険フラグ（{', '.join(flags)}）が立ちましたが、全体テストは CI に任せる戦略のため最終ゲートへ寄せます（項目 {', '.join(flagged)}）"
    )


def run_locally(state: dict[str, Any], log: pathlib.Path, path: Any = None) -> tuple[bool, bool, list[str]]:
    """手元でテストの種別の全体テストを 1 度走らせる。`(通ったか, 打ち切ったか, 走らせたコマンド)`。

    静的解析は項目ごとに変更したファイルで走らせ済みのため入れない（#1483 決定 7）。起動の失敗なら止める。
    """
    work = work_dir(state)
    commands = timeline.strategy_of(state).whole_commands(ts.TEST)
    triage.clear_junit(state)
    passed, timed_out = True, False
    with open(log, "wb"):
        pass
    limit, started = timeline.state_whole_timeout(state), time.monotonic()
    for i, command in enumerate(commands):
        part = log.with_name(f"{log.stem}-{i}.log") if len(commands) > 1 else log
        # 上限は suite 群全体で 1 つ（test-run.py の whole と同じ）
        code, timed_out = test_triage.run_within(
            limit, started, lambda left, command=command, part=part: run_with_timeout(command, work, left, output=part)
        )
        result = ts.outcome(code, timed_out)
        if result.launch_failed:
            launch.stop(path, state, "whole", command, result, part)
        if result.status != ts.PASSED:
            passed = False
        if timed_out:
            break
    return passed, timed_out, commands


def whole_fallback_command(state: dict[str, Any]) -> Any:
    """変更起因のファイルを挙げられないときに走らせ直す全体テスト（シェルで走らせる文字列）。

    **suite が複数なら全部を走らせる。** 先頭だけにすると、2 本目以降だけが落ちたときに先頭が通って
    直ったと誤り、最終ゲートで同じ失敗を見つけ直して修正の起動が増える。各 suite は括弧で分けて
    作業ディレクトリの移動を持ち越さず、`&&` で最初に落ちた suite で止める。
    """
    commands = timeline.strategy_of(state).whole_commands(ts.TEST)
    if len(commands) <= 1:
        return commands[0] if commands else []
    return " && ".join(f"( {c} )" for c in commands)


def fallback(
    path: pathlib.Path,
    state: dict[str, Any],
    record: dict[str, Any],
    flags: list[str],
    flagged: list[dict[str, Any]],
    log: pathlib.Path,
    fix_or_narrow: Callable[[pathlib.Path, dict[str, Any], dict[str, Any], pathlib.Path], bool],
) -> bool:
    """JUnit で見分けられないときは全体を 1 度走らせ直す（#1334 前提 4・見分けの 4）。

    通ればフレーキー（残す）。落ちれば、着手前が green なら変更起因として危険フラグの項目を修正へ回し、
    着手前も red なら見分けられないので危険フラグの項目をまとめて取り消して理由を残す。
    """
    info(f"⚠ {record['fallback_reason']}ため、全体を 1 度走らせ直して見分けます")
    rerun_log = log.with_name("verify-whole-rerun.log")
    passed, timed_out, _ = run_locally(state, rerun_log, path)
    record["fallback_rerun"] = "pass" if passed else ("timeout" if timed_out else "fail")
    if passed:
        record["resolution"] = "kept"
        info("✅ 走らせ直しで通りました（フレーキー）。危険フラグの項目は取り消しません")
        return False
    baseline_green = (state.get("baseline_test") or {}).get("status") == "green"
    if baseline_green and not timed_out:
        record["caused"] = ["<全体>"]
        record["resolution"] = "fixing"
        record["rerun_command"] = None
        info("🔧 走らせ直しでも落ち、着手前は通っていたため変更起因とみなします")
        return fix_or_narrow(path, state, record, rerun_log)
    reason = f"危険フラグ（{', '.join(flags)}）で走らせた全体テストが落ち、見分けられなかった（{record['fallback_reason']}）"
    for item in flagged:
        item["failure_reason"] = reason
    drop(path, state, [i["id"] for i in flagged], reason)
    record["reverted"] = True
    record["resolution"] = "reverted_all"
    info(f"↩ {dropped_line(state, len(flagged))}。取り消した後の HEAD は最終ゲートが全体テストで確かめます")
    return False
