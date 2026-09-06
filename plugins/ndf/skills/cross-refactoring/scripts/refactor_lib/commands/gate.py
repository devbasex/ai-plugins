"""最終ゲート（Step 7）。**起動のされ方で判定の相手が変わる**（#436 決定 7）。

| 起動のされ方 | 最終ゲート | 見分け方 |
| --- | --- | --- |
| `development-workflow` の 1 工程 | `cross-review` を省き、**全体のテスト**で判定 | `--workflow-step` |
| 単独 | `cross-review` を実行 | 既定 |

**引数で受け取る。** 環境変数や控えの読み取りは、起動元が違っても同じ値になりうる。
呼ぶ側が明示する形にすれば、判定が 1 か所で済む。

**テストで見つからない誤りを拾う工程は消えない。** 工程として起動したときに省いた
分は、工程表の「レビュー」（`pr` → `cross-review`）が持つ。ここへ軽量なレビューを
足すと、同じ差分を 2 度レビューすることになる。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

import statefile

from .. import info
from ..gitfacts import _git_out, _run_with_timeout, _safe_int, check_run_result
from ..paths import _load
from ..vocabulary import DEFAULT_TEST_TIMEOUT


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
    statefile.save(path, state)
    info(f"❌ 最終ゲートが落ちました（{detail}）。修正ラウンド {gate['fix_rounds']} / {limit}")
    statefile.emit(FINAL_GATE="failing")
    sys.exit(2)


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
