"""最終ゲート修正の取り込み（`merge-final-fix`。#436 B5・#1693 F1 F2）。

順序: 範囲の確定 → 手順の検証（未申告・トレーラー・範囲） → **公開前の静的解析** → 取り込みか取り消し → push。

| 結末 | 終了コード | push |
| --- | --- | --- |
| 取り込んだ（公開前の静的解析を通った） | 0 | する |
| 手順の検証で取り消した | 0 | する（取り消した HEAD を公開する） |
| 公開前の静的解析で落ちて取り消した（`FINAL_FIX=lint_rejected`） | 2 | **しない**（HEAD は公開済みの `fix_base_sha` へ戻る。I1） |
| 範囲を確定できない・担当が結果を残さなかった | 2 | 取り消したときだけ |

差し戻した違反は `final_gate.lint_rejections` に残り、次の `final-gate` が修正の依頼（`fix_request`）へ載せる。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

import statefile

from .. import die, info, launch, prepush_lint
from ..gitfacts import (
    changed_files,
    collect_commit_facts,
    commits_in_range,
    discard_impl_leftovers,
    flush_pending_push,
    push_with_retry_marker,
    read_result,
    reported_shas,
    safe_int,
)
from ..intake import IntakeScope, already_closed, close_without_result, discard_unverified
from ..paths import head_sha, load_state, work_dir
from ..verify import unassigned_fix_commits, verify_final_fix_commit


def _final_fix_scope(gate: dict[str, Any], impl: str) -> IntakeScope:
    """最終ゲートの修正の取り込み 1 回分の範囲の値。

    起点も結末の記録も最終ゲートの記録が持つ。改善項目にも提案ラウンドにも
    属さないため、群は関わらない。
    """
    rounds = safe_int(gate.get("fix_rounds"))
    return IntakeScope(
        holder=gate,
        base_key="fix_base_sha",
        records=gate,
        phase="final-fix",
        attempt=rounds,
        impl=impl,
        label=f"final-gate-fix{rounds}",
    )


def _close_failed_final_fix(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    scope: IntakeScope,
    outcome: Any,
) -> None:
    """最終ゲートの修正担当が結果を残さなかったときに、取り消して判定へ戻す。

    **修正ラウンドは進めない。** 進めるのは次の最終ゲートで、そこが打ち切りを見る。
    起動し直しても解けない結末（利用上限）だけはフラグ（`no_relaunch`）を立て、次の最終
    ゲートを「取り消さず報告」で終わらせる（#728 の決定 11）。
    """
    closed = close_without_result(path, state, scope, outcome)
    if closed.range_unknown:
        statefile.save(path, state)
        die(
            f"最終ゲートの修正の範囲を確定できませんでした（起点 {gate.get('fix_base_sha')}）。検証できない修正は採りません",
            code=2,
        )
    if not closed.relaunch_same_agent:
        gate["no_relaunch"] = True
    statefile.save(path, state)
    if closed.reverted:
        # 最終ゲートは push 済みの地点を判定する。取り消した後の HEAD を公開してから判定へ戻す
        push_with_retry_marker(path, state, gate)
    sys.exit(2)


def _collect_final_fix_range(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    scope: IntakeScope,
    impl: str,
    work: str,
) -> tuple[dict[str, Any], str, list[str]]:
    """修正担当の結果と、取り込む範囲（HEAD と起点からのコミット）を確定する。

    結果が無いとき・範囲を確定できないときは、ここで終了する。
    """
    outcome = read_result(state, impl, "final-fix")
    if outcome.payload is None:
        _close_failed_final_fix(path, state, gate, scope, outcome)
    payload = outcome.payload
    head_now = head_sha(work) or ""
    ordered_range = commits_in_range(work, gate.get("fix_base_sha"), head_now)
    if ordered_range is None:
        statefile.save(path, state)
        die(
            "最終ゲートの修正の範囲を確定できませんでした"
            f"（起点 {gate.get('fix_base_sha')} / HEAD {head_now}）。"
            "検証できない修正は採りません",
            code=2,
        )
    return payload, head_now, ordered_range


def _verify_final_fix_commits(
    state: dict[str, Any],
    work: str,
    payload: dict[str, Any],
    ordered_range: list[str],
) -> tuple[list[str], list[str]]:
    """申告されたコミットを検証し、未申告のコミットと問題の一覧を返す。"""
    claimed_shas = reported_shas(payload)
    unassigned = unassigned_fix_commits(work, claimed_shas, ordered_range)
    # **テストコマンドは渡さない。** テストの合否は `final-gate` が採った側で 1 度だけ見る
    # （CI で見る実行で手元のテストを走らせないため）。静的解析は公開前に `prepush_lint` が見る。
    facts = collect_commit_facts(
        work,
        claimed_shas,
        set(ordered_range),
        "",
        state["head_branch"],
    )
    problems = [p for p in (verify_final_fix_commit(c, state.get("target_scope") or []) for c in facts) if p]
    return unassigned, problems


def _report_problems(unassigned: list[str], problems: list[str]) -> None:
    if unassigned:
        info(f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります（{', '.join(s[:7] for s in unassigned[:5])}）")
    for problem in problems:
        info(f"❌ {problem}")


def _reject_by_lint(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    scope: IntakeScope,
    ordered_range: list[str],
    result: prepush_lint.PrepushResult,
) -> None:
    """公開前の静的解析で落ちた修正を取り消し、違反を `lint_rejections` へ残す。**push しない**（I1）。"""
    rejections = [r.as_dict() for r in result.rejections]
    gate.setdefault("lint_rejections", []).append(
        {
            "round": safe_int(gate.get("fix_rounds")),
            "at": statefile.now(),
            "commits": list(ordered_range),
            "rejections": rejections,
        }
    )
    # 取り消せば HEAD は最終ゲートが判定した公開済みの地点（`fix_base_sha`）へ戻る。公開しなくても食い違わない
    discard_unverified(path, state, scope, ordered_range)
    info(f"↩ 最終ゲート修正が公開前の静的解析で落ちたため取り消しました（{prepush_lint.summary(rejections)}）。push しません")


def _already_rejected(gate: dict[str, Any]) -> bool:
    """この修正ラウンドを公開前の静的解析で差し戻し済みか（打ち直しで同じ結果ファイルを読み直さない）。"""
    last = (gate.get("lint_rejections") or [{}])[-1]
    return bool(last) and safe_int(last.get("round")) == safe_int(gate.get("fix_rounds"))


def _add_fix_seconds(gate: dict[str, Any], payload: dict[str, Any]) -> None:
    gate.setdefault("durations", {})["fix"] = gate.get("durations", {}).get("fix", 0) + safe_int(payload.get("elapsed_seconds"))


def cmd_merge_final_fix(args: argparse.Namespace) -> None:
    """Step 7 — 最終ゲートの修正結果を取り込む。

    **`merge-fix` では代用できない。** あちらは適用ラウンド（群）の記録を読み、
    範囲の起点・担当・改善項目の 3 つをそこから取る。最終ゲートにはそのどれも無い。
    実際に流用すると次の 3 つが起きる。

    | 流用したときに起きること | なぜ |
    | --- | --- |
    | 「起点 None」で止まり修正を取り込めない | 最後の群が検証を通っていれば `fix_base_sha` が無い |
    | 正常なコミットまで取り消される | 古い起点が残っていると、そこから HEAD までが範囲になる |
    | トレーラーが揃わず全件が不正になる | `Item-Id` を要求するが、最終ゲートの修正は項目に属さない |

    終了コード: 0 = 取り込んだ・手順の検証で取り消した / 2 = 取り込めなかった（範囲を確定できない、
    担当が結果を残さなかった、公開前の静的解析で落ちて取り消した）/ 4 = 止まった（静的解析の起動の失敗など）。
    テストの合否は判定せず、**次の `final-gate` が採った側で 1 度だけ見る**。

    **結果を残さなかったときも、作られたコミットは取り消す。** 取り消さずに抜けると、
    次の最終ゲートがそのコミットを含む先端でテストし、落ちれば起点をそこへ置き直す。
    未検証の差分が Pull Request に残る（#674）。
    """
    path, state = load_state(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    impl = str(gate.get("impl") or "")
    if not impl:
        die(
            "最終ゲートの修正担当が記録されていません。先に `final-gate` を実行してください",
            code=4,
        )

    work = work_dir(state)
    discard_impl_leftovers(state, work)
    flush_pending_push(path, state, gate)

    scope = _final_fix_scope(gate, impl)
    if already_closed(scope):
        info("↻ この最終ゲートの修正の試行は結果なしとして記録済みです")
        sys.exit(2)
    if _already_rejected(gate):
        info("↻ この最終ゲートの修正の試行は公開前の静的解析で差し戻し済みです")
        statefile.emit(FINAL_FIX="lint_rejected")
        sys.exit(2)

    payload, head_now, ordered_range = _collect_final_fix_range(path, state, gate, scope, impl, work)
    unassigned, problems = _verify_final_fix_commits(state, work, payload, ordered_range)
    _report_problems(unassigned, problems)
    _add_fix_seconds(gate, payload)

    if unassigned or problems:
        # **ここは取り消す。** 「上限に達しても取り消さない」のは*採用した改善項目*
        # の話で、検証を受けていない修正コミットは別である。取り消せば HEAD は
        # 最終ゲートが見た地点へ戻り、公開済みの内容と食い違わない。手順の検証で取り消したときは静的解析を走らせない。
        discard_unverified(path, state, scope, ordered_range)
    else:
        lint = prepush_lint.check_files(state, changed_files(work, ordered_range), f"final-fix{safe_int(gate.get('fix_rounds'))}-lint")
        if lint.launch_failure is not None:
            failure = lint.launch_failure
            launch.stop(path, state, "prepush", failure.command, failure.outcome, failure.log)
        if not lint.passed:
            _reject_by_lint(path, state, gate, scope, ordered_range, lint)
            statefile.save(path, state)
            statefile.emit(FINAL_FIX="lint_rejected")
            sys.exit(2)
        gate["fix_base_sha"] = head_now
        gate.setdefault("fix_commits", []).extend(ordered_range)
        info(f"修正を取り込みました（{len(ordered_range)} コミット）")

    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する**（公開前の静的解析で落ちたときを除く）。最終ゲートは push 済みの地点なので、
    # 公開しないと Pull Request の内容と手元の HEAD が食い違ったまま次の判定へ入る。
    # CI で見る実行では、push しないと読む対象のチェックそのものが動かない。
    push_with_retry_marker(path, state, gate)
