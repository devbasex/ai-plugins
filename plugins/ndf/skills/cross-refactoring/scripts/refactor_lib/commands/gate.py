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
import sys
from typing import Any, Optional

import assignment
import statefile

from .. import die, info
from ..gitfacts import (
    _discard_impl_leftovers,
    _flush_pending_push,
    _git_out,
    _push_with_retry_marker,
    _read_result,
    _reported_shas,
    _run_with_timeout,
    _safe_int,
    check_run_result,
    collect_commit_facts,
    commits_in_range,
    revert_unverified_range,
)
from ..paths import _load, _result_path, stem_for
from ..verify import verify_final_fix_commit
from ..vocabulary import DEFAULT_TEST_TIMEOUT
from .review import _unassigned_fix_commits


def cmd_final_gate(args: argparse.Namespace) -> None:
    """Step 7 — 最終ゲートを通す。

    終了コード: 0 = 通過（または `cross-review` を実行する） / 2 = 落ちた
    （修正ラウンドへ） / 1 = 修正の上限に達した（**取り消さず**報告へ抜ける）。

    **Step 7 は push 済みの地点である。** 上限に達しても取り消さない。取り消しの
    判断は Pull Request の読み手が持つため、失敗として報告に書く。
    """
    path, state = _load(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})

    if not state.get("workflow_step"):
        gate["mode"] = "cross-review"
        statefile.save(path, state)
        info("単独起動のため、Step 7 は /ndf:cross-review を実行します")
        statefile.emit(FINAL_GATE="cross-review")
        return

    # **排他である。** `--ci-check` があれば手元のテストを実行せず継続的統合の成功
    # だけで判定し、無ければ手元のテストだけで判定する。「どちらか一方が通れば通過」
    # とはしない（OR で採ると、手元のテストの失敗を継続的統合の成功が覆す）。
    ci_check = str(state.get("ci_check") or "").strip()
    gate["mode"] = "ci" if ci_check else "test"
    passed, detail = (
        _ci_gate(state, ci_check) if ci_check else _local_gate(state)
    )
    gate.setdefault("checks", []).append({
        "at": statefile.now(),
        "mode": gate["mode"],
        "status": "pass" if passed else "fail",
        "detail": detail,
    })

    if passed:
        gate["status"] = "passed"
        statefile.save(path, state)
        info(f"✅ 最終ゲートを通過しました（{detail}）")
        statefile.emit(FINAL_GATE="passed")
        return

    limit = _safe_int(state.get("max_fix_rounds"), 3)
    if _safe_int(gate.get("fix_rounds")) >= limit:
        gate["status"] = "failed"
        statefile.save(path, state)
        info(
            f"❌ 最終ゲートが通らないまま修正の上限 {limit} に達しました（{detail}）。"
            "**既に push してあるため取り消しません。** 失敗として報告します"
        )
        statefile.emit(FINAL_GATE="failed")
        sys.exit(1)

    gate["fix_rounds"] = _safe_int(gate.get("fix_rounds")) + 1
    gate["status"] = "failing"
    # **修正の起点と担当をここで記録する。** 記録しないと `merge-final-fix` が範囲を
    # 確定できず、修正コミットを 1 件も取り込めない。適用ラウンドの控えにある
    # `fix_base_sha` を流用することもできない。あれは最後の群の検証が落ちた地点で
    # あり、そこから HEAD までには**検証を通った正常なコミット**が並ぶ。範囲に含めると
    # 未申告として扱われ、その全部が取り消される。
    gate["fix_base_sha"] = _git_out(str(state["worktrees"]["work"]), ["rev-parse", "HEAD"])
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
    seq = _safe_int(state.get("apply_seq")) + 1
    impl, _ = assignment.assign(seq, state["host"])
    state["apply_seq"] = seq
    gate["impl"] = impl
    return impl


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

    終了コード: 0 = 取り込んだ / 2 = 取り込めなかった（範囲を確定できない）。
    合否そのものは判定せず、**次の `final-gate` が採った側で 1 度だけ見る**。
    """
    path, state = _load(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    impl = str(gate.get("impl") or "")
    if not impl:
        die(
            "最終ゲートの修正担当が記録されていません。"
            "先に `final-gate` を実行してください",
            code=4,
        )

    work = str(state["worktrees"]["work"])
    _discard_impl_leftovers(state, work)
    _flush_pending_push(path, state, gate)

    result = _result_path(state, impl, stem_for(impl, "final-fix", state["id"]))
    payload = _read_result(result, impl)
    head_now = _git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered_range = commits_in_range(work, gate.get("fix_base_sha"), head_now)
    if ordered_range is None:
        statefile.save(path, state)
        die(
            "最終ゲートの修正の範囲を確定できませんでした"
            f"（起点 {gate.get('fix_base_sha')} / HEAD {head_now}）。"
            "検証できない修正は採りません",
            code=2,
        )

    reported_shas = _reported_shas(payload)
    unassigned = _unassigned_fix_commits(work, reported_shas, ordered_range)
    # **テストコマンドは渡さない。** 合否は `final-gate` が採った側で 1 度だけ見る
    # （`--ci-check` を指定した実行で手元のテストを走らせないため）。
    facts = collect_commit_facts(
        work, reported_shas, set(ordered_range), "", state["head_branch"],
    )
    problems = [
        p for p in (
            verify_final_fix_commit(c, state.get("target_scope") or [])
            for c in facts
        ) if p
    ]

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
        revert_unverified_range(
            path, state, gate, ordered_range,
            f"final-gate-fix{_safe_int(gate.get('fix_rounds'))}",
        )
    else:
        gate["fix_base_sha"] = head_now
        gate.setdefault("fix_commits", []).extend(ordered_range)
        info(f"修正を取り込みました（{len(ordered_range)} コミット）")

    gate.setdefault("durations", {})["fix"] = (
        gate.get("durations", {}).get("fix", 0)
        + _safe_int(payload.get("elapsed_seconds"))
    )
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 最終ゲートは push 済みの地点なので、
    # 公開しないと Pull Request の内容と手元の HEAD が食い違ったまま次の判定へ入る。
    # `--ci-check` の実行では、push しないと読む対象の検査そのものが動かない。
    _push_with_retry_marker(path, state, gate)


def _local_gate(state: dict[str, Any]) -> tuple[bool, str]:
    """全体のテストを手元で実行する。"""
    command = (state.get("baseline_test") or {}).get("command") or ""
    work = str(state["worktrees"]["work"])
    timeout = _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    code, timed_out = _run_with_timeout(command, work, timeout)
    if timed_out:
        return False, f"{command} が {timeout} 秒で終わりませんでした"
    return code == 0, f"{command} / 終了コード {code}"


def _ci_gate(state: dict[str, Any], name: str) -> tuple[bool, str]:
    """継続的統合の結果で判定する。**読むのは `check-runs` の 1 回だけ。**

    **結果を得られないときは通過させない**（fail-closed）。照会できなかったことと、
    検査が成功したことは別である。
    """
    sha = _git_out(str(state["worktrees"]["work"]), ["rev-parse", "HEAD"]) or ""
    result: Optional[str] = check_run_result(str(state.get("repo") or ""), sha, name)
    if result is None:
        return False, f"検査 {name} の結果を得られませんでした（{sha[:7]}）"
    return result == "success", f"検査 {name} の結論は {result} でした（{sha[:7]}）"
