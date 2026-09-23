"""最終ゲート（Step 7）。**起動のされ方で判定の相手が変わる**（#436 決定 7）。

| 起動のされ方 | 最終ゲート | 見分け方 |
| --- | --- | --- |
| `development-workflow` の 1 工程 | `cross-review` を省き、**全体のテスト**で判定 | `--workflow-step` |
| 単独 | `cross-review` を実行 | 既定 |

**引数で受け取る。** 環境変数や控えの読み取りは、起動元が違っても同じ値になりうる。
呼ぶ側が明示する形にすれば、判定が 1 か所で済む。

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

from .. import die, info
from ..gitfacts import (
    discard_impl_leftovers,
    flush_pending_push,
    push_with_retry_marker,
    read_result,
    reported_shas,
    run_with_timeout,
    safe_int,
    check_run_result,
    collect_commit_facts,
    commits_in_range,
)
from ..intake import (
    IntakeScope,
    already_closed,
    close_without_result,
    discard_unverified,
)
from ..paths import git_out, load_state
from ..rounds import impl_for_seq
from ..scope import round_test_command
from ..verify import verify_final_fix_commit
from ..vocabulary import DEFAULT_TEST_TIMEOUT
from ..verify import unassigned_fix_commits


def cmd_final_gate(args: argparse.Namespace) -> None:
    """Step 7 — 最終ゲートを通す。

    終了コード: 0 = 通過（または `cross-review` を実行する） / 2 = 落ちた
    （修正ラウンドへ） / 1 = 修正の上限に達した（**取り消さず**報告へ抜ける）。

    **Step 7 は push 済みの地点である。** 上限に達しても取り消さない。取り消しの
    判断は Pull Request の読み手が持つため、失敗として報告に書く。

    **単独起動でも、群を範囲のテストで検証してきたなら全体のテストを 1 回通す**
    （#880）。範囲の外への波及を見る機会が、ほかに無いためである。通れば今のとおり
    `cross-review` へ渡し、落ちれば工程として起動したときと同じ修正ラウンドへ入る。
    """
    path, state = load_state(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    standalone = not state.get("workflow_step")

    if standalone and not _round_test_differs(state):
        _emit_cross_review(
            path, state, gate, "単独起動のため、Step 7 は /ndf:cross-review を実行します"
        )
        return

    # **排他である。** `--ci-check` があれば手元のテストを実行せず継続的統合の成功
    # だけで判定し、無ければ手元のテストだけで判定する。「どちらか一方が通れば通過」
    # とはしない（OR で採ると、手元のテストの失敗を継続的統合の成功が覆す）。
    ci_check = str(state.get("ci_check") or "").strip()
    gate["mode"] = "ci" if ci_check else "test"
    started = time.monotonic()
    passed, detail = (
        _ci_gate(state, ci_check) if ci_check else _local_gate(state)
    )
    _record_gate_check(
        gate, ci_check or _baseline_command(state), passed, detail,
        round(time.monotonic() - started, 1),
    )

    if passed and standalone:
        _emit_cross_review(
            path, state, gate,
            f"✅ 全体のテストが通りました（{detail}）。Step 7 は /ndf:cross-review を実行します",
        )
        return
    if passed:
        _gate_passed(path, state, gate, detail)
        return

    limit = safe_int(state.get("max_fix_rounds"), 3)
    if safe_int(gate.get("fix_rounds")) >= limit:
        _gate_limit_reached(path, state, gate, detail, limit)
    _gate_failing(path, state, gate, detail, limit)


def _emit_cross_review(
    path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], message: str
) -> None:
    """Step 7 を `cross-review` へ委譲する結末。"""
    gate["mode"] = "cross-review"
    statefile.save(path, state)
    info(message)
    statefile.emit(FINAL_GATE="cross-review")


def _record_gate_check(
    gate: dict[str, Any], command: str, passed: bool, detail: str, seconds: float
) -> None:
    """最終ゲートの検査 1 件を `checks` へ追記する。"""
    gate.setdefault("checks", []).append({
        "at": statefile.now(),
        "mode": gate["mode"],
        "command": command,
        "status": "pass" if passed else "fail",
        "detail": detail,
        "seconds": seconds,
    })


def _gate_passed(
    path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str
) -> None:
    gate["status"] = "passed"
    statefile.save(path, state)
    info(f"✅ 最終ゲートを通過しました（{detail}）")
    statefile.emit(FINAL_GATE="passed")


def _gate_limit_reached(
    path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str, limit: int
) -> None:
    gate["status"] = "failed"
    statefile.save(path, state)
    info(
        f"❌ 最終ゲートが通らないまま修正の上限 {limit} に達しました（{detail}）。"
        "**既に push してあるため取り消しません。** 失敗として報告します"
    )
    statefile.emit(FINAL_GATE="failed")
    sys.exit(1)


def _gate_failing(
    path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any], detail: str, limit: int
) -> None:
    gate["fix_rounds"] = safe_int(gate.get("fix_rounds")) + 1
    gate["status"] = "failing"
    # **修正の起点と担当をここで記録する。** 記録しないと `merge-final-fix` が範囲を
    # 確定できず、修正コミットを 1 件も取り込めない。適用ラウンドの控えにある
    # `fix_base_sha` を流用することもできない。あれは最後の群の検証が落ちた地点で
    # あり、そこから HEAD までには**検証を通った正常なコミット**が並ぶ。範囲に含めると
    # 未申告として扱われ、その全部が取り消される。
    gate["fix_base_sha"] = git_out(str(state["worktrees"]["work"]), ["rev-parse", "HEAD"])
    impl = _final_fix_impl(state, gate)
    statefile.save(path, state)
    info(
        f"❌ 最終ゲートが落ちました（{detail}）。修正ラウンド {gate['fix_rounds']} / {limit}"
        f" — 修正担当は {impl} です"
    )
    statefile.emit(
        FINAL_GATE="failing", FINAL_FIX_IMPL=impl, FINAL_FIX_ROUND=gate["fix_rounds"]
    )
    sys.exit(2)


def _baseline_command(state: dict[str, Any]) -> str:
    return str((state.get("baseline_test") or {}).get("command") or "")


def _round_test_differs(state: dict[str, Any]) -> bool:
    """群の検証が全体のテストと違うコマンドで行われたか（#880）。"""
    return round_test_command(state) != _baseline_command(state)


def _final_fix_impl(state: dict[str, Any], gate: dict[str, Any]) -> str:
    """最終ゲートの修正担当を決める。**最初に落ちたときだけ輪番を 1 つ進める。**

    修正ラウンドごとに担当を替えない。適用ラウンドの修正が適用した担当に閉じるのと
    同じで、直しかけの文脈を持っている者が続けたほうが速い。輪番の通し番号
    （`apply_seq`）を共有するのは、最終ゲートの修正も**適用と同じ重さの作業**だから
    である。
    """
    impl = str(gate.get("impl") or "")
    if impl:
        return impl
    seq = safe_int(state.get("apply_seq")) + 1
    impl, _ = impl_for_seq(state, seq)
    state["apply_seq"] = seq
    gate["impl"] = impl
    return impl


def _final_fix_scope(gate: dict[str, Any], impl: str) -> IntakeScope:
    """最終ゲートの修正の取り込み 1 回分の範囲の値。

    起点も結末の記録も最終ゲートの控えが持つ。改善項目にも提案ラウンドにも
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

    **修正ラウンドは進めない。** 進めるのは次の最終ゲートで、そこが上限を見る。
    起動し直しても解けない結末（利用上限）だけは上限の値まで進め、次の最終ゲートを
    「取り消さず報告」で終わらせる（#728 の決定 11）。
    """
    closed = close_without_result(path, state, scope, outcome)
    if closed.range_unknown:
        statefile.save(path, state)
        die(
            "最終ゲートの修正の範囲を確定できませんでした"
            f"（起点 {gate.get('fix_base_sha')}）。検証できない修正は採りません",
            code=2,
        )
    if not closed.relaunch_same_agent:
        gate["fix_rounds"] = safe_int(state.get("max_fix_rounds"), 3)
    statefile.save(path, state)
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
    # （`--ci-check` を指定した実行で手元のテストを走らせないため）。
    facts = collect_commit_facts(
        work, claimed_shas, set(ordered_range), "", state["head_branch"],
    )
    problems = [
        p for p in (
            verify_final_fix_commit(c, state.get("target_scope") or [])
            for c in facts
        ) if p
    ]
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
        info(
            f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )
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

    **`merge-fix` では代用できない。** あちらは適用ラウンド（群）の控えを読み、
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
            "最終ゲートの修正担当が記録されていません。"
            "先に `final-gate` を実行してください",
            code=4,
        )

    work = str(state["worktrees"]["work"])
    discard_impl_leftovers(state, work)
    flush_pending_push(path, state, gate)

    scope = _final_fix_scope(gate, impl)
    if already_closed(scope):
        info("↻ この最終ゲートの修正の試行は結果なしとして記録済みです")
        sys.exit(2)

    payload, head_now, ordered_range = _collect_final_fix_range(
        path, state, gate, scope, impl, work,
    )
    unassigned, problems = _verify_final_fix_commits(
        state, work, payload, ordered_range,
    )
    _apply_final_fix_verdict(
        path, state, gate, scope, head_now, ordered_range, unassigned, problems,
    )

    gate.setdefault("durations", {})["fix"] = (
        gate.get("durations", {}).get("fix", 0)
        + safe_int(payload.get("elapsed_seconds"))
    )
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 最終ゲートは push 済みの地点なので、
    # 公開しないと Pull Request の内容と手元の HEAD が食い違ったまま次の判定へ入る。
    # `--ci-check` の実行では、push しないと読む対象の検査そのものが動かない。
    push_with_retry_marker(path, state, gate)


def _local_gate(state: dict[str, Any]) -> tuple[bool, str]:
    """全体のテストを手元で実行する。**全体のテストを呼ぶのは `init` とここだけである。**"""
    command = _baseline_command(state)
    work = str(state["worktrees"]["work"])
    timeout = safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    code, timed_out = run_with_timeout(command, work, timeout)
    if timed_out:
        return False, f"{command} が {timeout} 秒で終わりませんでした"
    return code == 0, f"{command} / 終了コード {code}"


def _ci_gate(state: dict[str, Any], name: str) -> tuple[bool, str]:
    """継続的統合の結果で判定する。**読むのは `check-runs` の 1 回だけ。**

    **結果を得られないときは通過させない**（fail-closed）。照会できなかったことと、
    検査が成功したことは別である。
    """
    sha = git_out(str(state["worktrees"]["work"]), ["rev-parse", "HEAD"]) or ""
    result: Optional[str] = check_run_result(str(state.get("repo") or ""), sha, name)
    if result is None:
        return False, f"検査 {name} の結果を得られませんでした（{sha[:7]}）"
    return result == "success", f"検査 {name} の結論は {result} でした（{sha[:7]}）"
