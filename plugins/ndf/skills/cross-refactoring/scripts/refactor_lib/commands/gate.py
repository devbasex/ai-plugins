"""最終ゲート（Step 7）。**起動のされ方で判定の相手が変わる**（#436 決定 7）。

| 起動のされ方 | 最終ゲート | 見分け方 |
| --- | --- | --- |
| `development-workflow` の 1 工程 | `cross-review` を省き、**全体テスト**で判定 | `--workflow-step` |
| 単独 | `cross-review` を実行 | 既定 |

**引数で受け取る。** 環境変数や記録の読み取りは、起動元が違っても同じ値になりうる。
呼ぶ側が明示する形にすれば、判定が 1 か所で済む。

全体テストの置き場は戦略で決まる（#1334）。`local-full` / `round-only` は手元で走らせ、`local-scoped-ci-whole`（か
`--ci-check`）は push 済みの HEAD のチェックを上限まで待つ。落ちたら JUnit から落ちたテストを取り、フレーキー・既存失敗・
変更起因に分け、変更起因が無ければ通す（I5）。修正を打ち切ったら、原因の項目から順に取り消す（単独は寄せた危険フラグの
全体テストのときだけ。工程の 1 つとして起動したときは打ち切りの後の取り消し。#1649 #1669）。

**テストで見つからない誤りを拾う工程は消えない。** 工程として起動したときに省いた
分は、工程表の「実装レビュー」（`pr` → `cross-review`）が持つ。ここへ軽量なレビューを
足すと、同じ差分を 2 度レビューすることになる。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from typing import Any, Optional

import statefile
import test_strategy as ts
import test_triage

from .. import clock, die, gate_lint, info, launch, publish, stop_revert, timeline, triage
from ..gitfacts import (
    discard_impl_leftovers,
    flush_pending_push,
    push_with_retry_marker,
    read_result,
    reported_shas,
    run_with_timeout,
    safe_int,
    collect_commit_facts,
    commits_in_range,
)
from ..intake import (
    IntakeScope,
    already_closed,
    close_without_result,
    discard_unverified,
)
from ..paths import git_out, load_state, work_dir
from ..verify import verify_final_fix_commit
from ..verify import unassigned_fix_commits
from ..gate_ci import ci_checks, ci_gate, ci_mode, revert_deferred


def cmd_final_gate(args: argparse.Namespace) -> None:
    """最終ゲートを通す（#933 の AC16b・決定 15・決定 16、#1334 F4〜F6）。

    終了コード: 0 = 通過（または `cross-review` を実行する） / 2 = 落ちた（修正ラウンドへ）・取り消した後の確かめ直し /
    1 = 単独で起動して修正を打ち切った（**取り消さず**報告へ抜ける）/ 4 = 判断できない（CI が終わらない・案 B の後でも落ちた）。
    打ち切ったとき、単独の起動は取り消さず判断を Pull Request の読み手へ渡し、工程の 1 つとして起動したときは
    打ち切りの後の取り消し（`stop_revert`。#1669）で全体テストが通る状態へ戻してから確かめ直す。

    | 全体テストの置き場 | 見るもの |
    | --- | --- |
    | CI（`local-scoped-ci-whole` か `--ci-check`） | 継続的統合の結果。`pending` の間は `limits.ci_wait_timeout` まで待つ |
    | 手元 | HEAD で全体テストを 1 度。検証の中で通り、取り消しが無く、HEAD が進んでいなければ使い回す |

    単独起動は、通れば `cross-review` へ渡す（`FINAL_GATE=cross-review`）。
    """
    path, state = load_state(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    standalone = not state.get("workflow_step")
    state["phase"] = "final"
    statefile.save(path, state)
    # 検証を通らずに来た実行（残る項目が 0 件）でも、判定の前に 1 度だけ公開する。HEAD が公開した地点と
    # 同じなら何もしない。CI で見る戦略では、この push が無いと読むチェックが動かない
    publish.enter_final_gate(path, state)

    state.pop("launch_failure", None)
    # 手元でテストの全体テストを走らせたときは、静的解析もテストの実行秒数を差し引いた上限で数える（1 回の全体検証を
    # 1 つの `whole_timeout` に収める。test-run.py の whole と同じ）。落ちたテストの見分けの時間は上限の外に置く。
    # 使い回し・CI で見るときは手元で走らないので渡さない。
    whole_started: Optional[float] = None
    if _reusable_whole_test(state):
        gate["whole_test_reused"] = True
        passed, detail = True, "検証の中で通った全体テストを使い回しました（HEAD は進んでいません）"
        gate["mode"] = "test"
    else:
        passed, detail, test_seconds = _run_and_record_gate_check(path, state, gate)
        if gate.get("mode") == "test":
            whole_started = time.monotonic() - test_seconds
    lint_passed, lint_detail = gate_lint.lint_gate(path, state, gate, started=whole_started)
    if lint_detail:
        detail = f"{detail} / {lint_detail}"
    passed = passed and lint_passed

    if passed and standalone:
        _emit_cross_review(
            path,
            state,
            gate,
            f"✅ 最終ゲートのチェックが通りました（{detail}）。続けて /ndf:cross-review を実行します",
        )
        return
    if passed:
        _gate_passed(path, state, gate, detail)
        return

    stop = _final_fix_stop(state, gate)
    if stop and (revert_deferred(path, state, gate) if standalone else stop_revert.run(path, state, gate)):
        # 原因の項目を取り消した（単独は寄せた危険フラグの全体テスト、工程の 1 つは打ち切りの後の取り消し）。公開して確かめ直す。
        # **修正の依頼ではない（`recheck`）。** 駆動は修正の CLI を起動せずに `final-gate` を打ち直す。
        # 起点を取り消し後の HEAD へ置き直すのは、取り消しのコミットを後の `merge-final-fix` の範囲へ
        # 入れないためである。入れると未申告として取り消され、取り消した項目が PR へ戻る。
        gate["status"] = "recheck"
        gate["fix_base_sha"] = git_out(work_dir(state), ["rev-parse", "HEAD"])
        statefile.save(path, state)
        push_with_retry_marker(path, state, gate)
        info(f"↩ 最終ゲートを落とした原因の項目を取り消しました（{detail}）。次の最終ゲートが確かめます")
        statefile.emit(FINAL_GATE="recheck")
        sys.exit(2)
    if stop:
        _gate_limit_reached(path, state, gate, detail, stop)
        return
    _gate_failing(path, state, gate, detail)


def _final_fix_stop(state: dict[str, Any], gate: dict[str, Any]) -> Optional[str]:
    """最終ゲートの修正を打ち切る理由。続けられれば `None`（決定 23）。

    **回数ではなく時計で決める。** 終わり（`limits.final_end_at` = 開始 + 想定最大時間）を
    過ぎていれば打ち切る。起動し直しても解けない結末（利用上限）も打ち切る。

    **ただし 1 度も試みていなければ時計で打ち切らない**（決定 26）。修正 1 回分の予備時間
    （`plan.reserve.final_fix`）を改修計画の時点で予算から差し引いてあり、予算を使い切った後に
    落ちても必ず 1 度は直しを試みる。
    """
    if gate.get("no_relaunch"):
        return "修正担当を起動し直しても解けない結末だった"
    if safe_int(gate.get("fix_rounds")) == 0:
        return None
    end = clock.parse(timeline.limits_of(state).get("final_end_at"))
    if end is not None and clock.now() >= end:
        return "想定最大時間の終わりを過ぎた"
    return None


def _reusable_whole_test(state: dict[str, Any]) -> bool:
    """検証の中の全体テストを使い回せるか（決定 16）。

    CI で見るなら使い回さない。検証の中で走って通り、取り消しが無く（`whole_test.reverted` が偽）、
    その後に HEAD が 1 つも進んでいないときだけ真。生成物の同期のコミットが積まれていれば HEAD が進んでいるので走らせる。
    """
    if ci_mode(state):
        return False
    record = state.get("whole_test") or {}
    if not (record.get("ran") and record.get("status") == "pass") or record.get("reverted"):
        return False
    head = git_out(work_dir(state), ["rev-parse", "HEAD"])
    return bool(head) and head == record.get("head")


def _run_and_record_gate_check(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, str, float]:
    """最終ゲートのチェックを 1 回走らせ、`checks` へ記録して結果を返す。落ちたら見分け、変更起因が無ければ通す。

    3 つ目はチェックそのものの実行秒数（見分けの時間を含まない）。静的解析の上限の起点に使う。
    """
    # **排他である。** CI で見るなら手元のテストを実行せず継続的統合の結論だけで判定し、無ければ
    # 手元のテストだけで判定する。「どちらか一方が通れば通過」とはしない。
    ci = ci_mode(state)
    gate["mode"] = "ci" if ci else "test"
    started = time.monotonic()
    passed, detail, verdict = ci_gate(state) if ci else _local_gate(path, state)
    seconds = round(time.monotonic() - started, 1)
    if not passed and verdict is not None:
        # 落ちたテストを見分ける。変更起因が無ければ通す（I5・決定 11）。
        classified = triage.classify(state, verdict.get("timed_out", False), verdict.get("ci_xmls"))
        gate["triage"] = {
            k: classified.get(k)
            for k in ("failed_tests", "flaky", "preexisting", "caused", "caused_output", "fallback_reason", "rerun_command")
        }
        if classified.get("fallback_reason"):
            detail += f" / 見分けを全体の走らせ直しに落とした（{classified['fallback_reason']}）"
        else:
            detail += (
                f" / フレーキー {len(classified['flaky'])}・既存失敗 {len(classified['preexisting'])}・変更起因 {len(classified['caused'])}"
            )
            if not classified["caused"]:
                passed = True
                detail += "（変更起因の失敗は無い）"
    _record_gate_check(gate, _gate_command(state), passed, detail, seconds)
    if not ci:
        # 履歴の `whole_test.final`（AC17）。修正の後に走らせ直したときは足し込む。
        gate["whole_test_seconds"] = round(float(gate.get("whole_test_seconds") or 0.0) + seconds, 1)
    statefile.save(path, state)
    return passed, detail, seconds


def _gate_command(state: dict[str, Any]) -> str:
    """記録に残す最終ゲートの相手（チェックの名前か全体テストのコマンド）。"""
    if ci_mode(state):
        return " / ".join(ci_checks(state))
    return " && ".join(timeline.strategy_of(state).whole_commands(ts.TEST))


def _emit_cross_review(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], message: str) -> None:
    """Step 7 を `cross-review` へ委譲する結末。"""
    # **チェックが通ったことを残す。** 履歴へ追記するかは、この目印と `cross-review` の
    # 最終ステータスの両方で決まる（`finalize`）。
    gate["checked_mode"] = gate.get("mode")
    gate["mode"] = "cross-review"
    gate["status"] = "passed"
    statefile.save(path, state)
    info(message)
    statefile.emit(FINAL_GATE="cross-review")


def _record_gate_check(gate: dict[str, Any], command: str, passed: bool, detail: str, seconds: float) -> None:
    """最終ゲートのチェック 1 件を `checks` へ追記する。"""
    gate.setdefault("checks", []).append(
        {
            "at": statefile.now(),
            "mode": gate["mode"],
            "command": command,
            "status": "pass" if passed else "fail",
            "detail": detail,
            "seconds": seconds,
        }
    )


def _gate_passed(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str) -> None:
    gate["status"] = "passed"
    statefile.save(path, state)
    info(f"✅ 最終ゲートを通過しました（{detail}）")
    statefile.emit(FINAL_GATE="passed")


def _gate_limit_reached(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str, why: str) -> None:
    gate["status"] = "failed"
    statefile.save(path, state)
    info(
        f"❌ 最終ゲートが通らないまま修正を打ち切りました（{why} / {detail}）。"
        "**既に push してあるため取り消しません。** 失敗として報告します"
    )
    statefile.emit(FINAL_GATE="failed")
    sys.exit(1)


def _gate_failing(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    detail: str,
) -> None:
    gate["fix_rounds"] = safe_int(gate.get("fix_rounds")) + 1
    gate["status"] = "failing"
    # **修正の起点と担当をここで記録する。** 記録しないと `merge-final-fix` が範囲を
    # 確定できず、修正コミットを 1 件も取り込めない。適用ラウンドの記録にある
    # `fix_base_sha` を流用することもできない。あれは最後の群の検証が落ちた地点で
    # あり、そこから HEAD までには**検証を通った正常なコミット**が並ぶ。範囲に含めると
    # 未申告として扱われ、その全部が取り消される。
    gate["fix_base_sha"] = git_out(work_dir(state), ["rev-parse", "HEAD"])
    impl = _final_fix_impl(state, gate)
    statefile.save(path, state)
    info(f"❌ 最終ゲートが落ちました（{detail}）。修正ラウンド {gate['fix_rounds']} — 修正担当は {impl} です")
    statefile.emit(FINAL_GATE="failing", FINAL_FIX_IMPL=impl, FINAL_FIX_ROUND=gate["fix_rounds"])
    sys.exit(2)


def _final_fix_impl(state: dict[str, Any], gate: dict[str, Any]) -> str:
    """最終ゲートの修正担当。**実装担当が担う**（#933 の決定 1。輪番は無い）。"""
    impl = str(gate.get("impl") or state.get("implementer") or "")
    gate["impl"] = impl
    return impl


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
    head_now = git_out(work, ["rev-parse", "HEAD"]) or ""
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
    # **テストコマンドは渡さない。** 合否は `final-gate` が採った側で 1 度だけ見る
    # （CI で見る実行で手元のテストを走らせないため）。
    facts = collect_commit_facts(
        work,
        claimed_shas,
        set(ordered_range),
        "",
        state["head_branch"],
    )
    problems = [p for p in (verify_final_fix_commit(c, state.get("target_scope") or []) for c in facts) if p]
    return unassigned, problems


def _apply_final_fix_verdict(
    path: pathlib.Path,
    state: dict[str, Any],
    gate: dict[str, Any],
    scope: IntakeScope,
    head_now: str,
    ordered_range: list[str],
    unassigned: list[str],
    problems: list[str],
) -> None:
    """検証の結果に応じて、修正を取り消すか最終ゲートの記録へ取り込む。"""
    if unassigned:
        info(f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります（{', '.join(s[:7] for s in unassigned[:5])}）")
    for problem in problems:
        info(f"❌ {problem}")

    if unassigned or problems:
        # **ここは取り消す。** 「上限に達しても取り消さない」のは*採用した改善項目*
        # の話で、検証を受けていない修正コミットは別である。取り消せば HEAD は
        # 最終ゲートが見た地点へ戻り、公開済みの内容と食い違わない。
        discard_unverified(path, state, scope, ordered_range)
    else:
        gate["fix_base_sha"] = head_now
        gate.setdefault("fix_commits", []).extend(ordered_range)
        info(f"修正を取り込みました（{len(ordered_range)} コミット）")


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

    終了コード: 0 = 取り込んだ / 2 = 取り込めなかった（範囲を確定できない、または
    担当が結果を残さなかった）。合否そのものは判定せず、**次の `final-gate` が
    採った側で 1 度だけ見る**。

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

    payload, head_now, ordered_range = _collect_final_fix_range(
        path,
        state,
        gate,
        scope,
        impl,
        work,
    )
    unassigned, problems = _verify_final_fix_commits(
        state,
        work,
        payload,
        ordered_range,
    )
    _apply_final_fix_verdict(
        path,
        state,
        gate,
        scope,
        head_now,
        ordered_range,
        unassigned,
        problems,
    )

    gate.setdefault("durations", {})["fix"] = gate.get("durations", {}).get("fix", 0) + safe_int(payload.get("elapsed_seconds"))
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 最終ゲートは push 済みの地点なので、
    # 公開しないと Pull Request の内容と手元の HEAD が食い違ったまま次の判定へ入る。
    # CI で見る実行では、push しないと読む対象のチェックそのものが動かない。
    push_with_retry_marker(path, state, gate)


def _local_gate(path: pathlib.Path, state: dict[str, Any]) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """テストの種別の全体テストを手元で実行する（I4: `local-scoped-ci-whole` では呼ばない）。3 つ目は落ちたときの見分けの材料。

    起動の失敗なら止める（#1483 E7）。静的解析は `_lint_gate` が走らせる。
    """
    commands = timeline.strategy_of(state).whole_commands(ts.TEST)
    if not commands:
        return True, "テストの種別の全体テストが無い", None
    work = work_dir(state)
    timeout = timeline.state_whole_timeout(state)
    triage.clear_junit(state)
    started = time.monotonic()
    for command in commands:
        # 上限は suite 群全体で 1 つ（test-run.py の whole と同じ）
        code, timed_out = test_triage.run_within(timeout, started, lambda left, command=command: run_with_timeout(command, work, left))
        result = ts.outcome(code, timed_out)
        if result.launch_failed:
            launch.stop(path, state, "gate", command, result)
        if timed_out:
            return False, f"{command} が {timeout} 秒で終わりませんでした", {"timed_out": True}
        if code != 0:
            return False, f"{command} / 終了コード {code}", {"timed_out": False}
    return True, f"{' && '.join(commands)} / 終了コード 0", None
