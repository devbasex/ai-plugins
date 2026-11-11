"""雛形のテストとマージのステップ（#1334 AC7）。

範囲テストと全体テストは `test-run.py` が宣言の戦略で走らせる。ステップの `timeout` と `merge-when-green` の
`--timeout` は、`apply_decls` が宣言から出した上限（`test_limits`。`lib/test_strategy.limits`）に余裕を足したもので、
固定の秒は持たない。所要が宣言に無いときだけ「所要が不明のときの値」を使い、計画の `テストの時間` にそう書く。
"""

from __future__ import annotations

import math
import shlex

import delivery
import test_strategy as ts
import limits
from step_result import EXIT_BASE_CONFLICT
from supervise_lib.decl import scope_paths, test_kind
from supervise_lib.paths import MERGE_CMD, MERGE_GATE_CMD, MERGE_PROBE, SPRINT_STATE_PY, STEPS_PY, TEST_RUN_PY

# merge-when-green と release が CI の基盤待ち（75。lib/step_result.py の EXIT_INFRA_WAIT）で止まったら、judge を通らず
# プランを止める（修正へ回さない。#1645 の決定 6）
INFRA_WAIT_STOP = {"75": "stop"}

# ステップの `timeout` に足す余裕（上限の 1 割。下限は監視の 1 周期の 2 倍）
MARGIN_SHARE = 0.1
MARGIN_FLOOR = limits.seconds("supervise.verify-margin-floor")

# マージに続く後片付けのテスト環境の回収の上限（秒）。`lib/container_reach.py` の RECLAIM_LIMIT と同じ値である。
# `lib/container_reach.py` はプランを作る側が読まない環境変数と設定を読むため、ここへは import しない（#2660 の決定 8）
RECLAIM_LIMIT = 330


def plan_strategy(a) -> dict | None:
    """宣言から解いた戦略（`apply_decls` が載せる）。無ければ `--test-cmd` の雛形から解く。"""
    found = getattr(a, "strategy", None)
    if isinstance(found, dict):
        return found
    template = getattr(a, "test_cmd", None)
    if not template:
        return None
    return ts.resolve({}, baseline_test=template, template_kind=test_kind(a), scope_paths=scope_paths(a)).as_state()


def plan_limits(a) -> dict:
    """テストと CI の待ちの上限（`apply_decls` が載せる `test_limits`。無ければ所要が不明のときの値）。"""
    found = getattr(a, "test_limits", None)
    if isinstance(found, dict):
        return found
    strategy = plan_strategy(a)
    return ts.limits(ts.Strategy.from_state(strategy) if strategy else ts.Strategy(ts.LOCAL_FULL, "args"), None)


def with_margin(seconds: int) -> int:
    """ステップの `timeout` = 上限 + 余裕。"""
    return int(seconds) + max(MARGIN_FLOOR, math.ceil(int(seconds) * MARGIN_SHARE))


CD_RESULT_FILE = "{state_dir}/work/cd-result.json"  # cd-result が書き record が読む結果のファイル（#2623）
CD_FAILED_STOP = {"5": "stop"}  # CD の結果が失敗（record が記録を書いた後の 5）なら judge-record を通らずに止める


def cd_result_step(repo: str | None, base: str, head: str | None, prs: str | None = None) -> dict:
    """本番チャネルへのマージの後に CD の結果を読むステップ。宣言に依らず常に置き、CD の行が無ければ `none` を書く（#2623 の決定 8）。
    結果は 0 で返り、record が書いた後に失敗を止める。`prs` があれば最後の PR のマージのコミットを読む（record --merged と同じリビジョン）。"""
    head_arg = (f" --head {shlex.quote(head)}" if head else "") + (f" --prs {prs}" if prs else "")
    return {
        "id": "cd-result",
        "type": "run",
        "stage": "デプロイ",
        "timeout": with_margin(delivery.RESULT_TOTAL_LIMIT),
        **({"cwd": repo} if repo else {}),
        "cmd": f"{STEPS_PY} cd-result --base {shlex.quote(base)}{head_arg} --out {CD_RESULT_FILE}",
        "next": "record",
    }


IMPL_STALL_DEFAULT = limits.seconds("supervise.impl-stall")


def _template_arg(a) -> str:
    """`--template` と、静的解析の雛形なら `--test-kind lint`（種別は `test-run.py` へそのまま渡す）。"""
    template = getattr(a, "test_cmd", None)
    if not template:
        return ""
    kind = test_kind(a)
    return f" --template {shlex.quote(template)}" + (f" --test-kind {kind}" if kind != ts.TEST else "")


def limit_arg(key: str, seconds: int) -> str:
    """テストのステップの `--limit`。run のステップが開始の時点で上限を決め直して置き換える（`steps.fill_test_limit`。#1631）。

    `{test_limit:<上限の表の鍵>:<プランの作成の時点の秒>}` の形で、決め直せなければ作成の時点の秒になる。"""
    return f" --limit {{test_limit:{key}:{int(seconds)}}}"


def scope_cmd(a, tests: str) -> str:
    """範囲テストのステップのコマンド（`test-run.py scope`）。"""
    limit = limit_arg("test_timeout", plan_limits(a)["test_timeout"])
    return f"{TEST_RUN_PY} scope --paths {tests}{_template_arg(a)}{limit}"


def whole_cmd(a) -> str:
    """全体テストのステップのコマンド（`test-run.py whole`。CI に任せる戦略は Pull Request のチェックを待つ）。"""
    paths = (getattr(a, "test_paths", None) or scope_paths(a)) if getattr(a, "test_cmd", None) and test_kind(a) == ts.LINT else []
    extra = " --paths " + " ".join(shlex.quote(p) for p in paths) if paths else ""
    strategy = plan_strategy(a)
    # CI に任せる戦略のステップの timeout は CI の待ちの上限で決まるため、手元の上限を渡さない
    if not (strategy and strategy.get("name") == ts.LOCAL_SCOPED_CI_WHOLE):
        extra += limit_arg("whole_timeout", plan_limits(a)["whole_timeout"])
    return f"{TEST_RUN_PY} whole --pr {{pr}}{_template_arg(a)}{extra}"


def scope_timeout(a) -> int:
    return with_margin(plan_limits(a)["test_timeout"])


def whole_on_ci(a) -> bool:
    """全体テストを CI に任せる戦略（`local-scoped-ci-whole`）か。"""
    strategy = plan_strategy(a)
    return bool(strategy and strategy.get("name") == ts.LOCAL_SCOPED_CI_WHOLE)


def whole_timeout(a) -> int:
    """全体テストのステップの `timeout`。手元なら `whole_timeout`、CI なら `ci_wait_timeout` に余裕を足す。"""
    limits = plan_limits(a)
    return with_margin(limits["ci_wait_timeout"] if whole_on_ci(a) else limits["whole_timeout"])


def without_ci_wait(a, steps: list[dict], after_whole: str, drop: tuple[str, ...] = ()) -> list[dict]:
    """全体テストを CI に任せる戦略なら、全体テストのステップ（test-all。PR の CI を待つ）と `drop` を外し、test-all へ
    向かう遷移を `after_whole` へ、入力から test-all を外す。CI の結果を待つのはマージのステップ（merge-when-green）だけで、
    落ちればマージしない（2026-11-12、利用者の指示）。手元で走らせる戦略ならそのまま返す。"""
    if not whole_on_ci(a):
        return steps
    gone = {"test-all", *drop}
    out = []
    for step in (s for s in steps if s["id"] not in gone):
        step = {k: (after_whole if k in ("next", "on_fail", "skip_to") and val == "test-all" else val) for k, val in step.items()}
        if "inputs" in step:
            step["inputs"] = [i for i in step["inputs"] if i not in gone]
        out.append(step)
    return out


def trim_check_steps(a, steps: list[dict], after_whole: str, refactor_off: bool) -> list[dict]:
    """検査のプランのステップを外す。`refactor_off` なら構造改善（assess・refactor）を外す。全体テストを CI に任せる戦略なら、
    レビューの後の全体テスト（test-all と、それだけを入力に持つ judge・fix）を外す（`without_ci_wait`）。"""
    kept = [s for s in steps if not (refactor_off and s["id"] in ("assess", "refactor"))]
    return without_ci_wait(a, kept, after_whole, drop=("judge", "fix"))


def refactor_template_arg(a) -> str:
    """`--test-cmd` を渡したときだけ、cross-refactoring へ同じ雛形を `--baseline-test` で渡す（宣言があれば渡さない）。"""
    template = getattr(a, "test_cmd", None)
    if not template:
        return ""
    kind = test_kind(a)
    return f" --baseline-test {shlex.quote(template)}" + (f" --test-kind {kind}" if kind != ts.TEST else "")


def merge_steps(a, on_conflict: str | None = None, **extra) -> list[dict]:
    """マージの 3 ステップ（#1336 の決定 1）。

    - `merge-gate`: 宛先がリリース承認ゲート（自動反映の本番チャネル）に当たるかだけを判定する。当たればプランを終える（`gate_next: end`）
    - `merge`: CI を待ってマージする。CI の待ちの上限を `--timeout` で渡し、ステップの `timeout` はそれに余裕と、
      マージに続く後片付けのテスト環境の回収の上限を足す
    - `merge-approved`: `merge` に `--gate-approved user` を足したもの。通常の流れからは入らず、承認の後に `--from merge-approved` でだけ入る

    `merge` と `merge-approved` は CI の基盤待ち（75）で judge を通らずに止まる（`on_exit`）。`on_conflict` を渡すと、
    PR がベースブランチと衝突したとき（4。`EXIT_BASE_CONFLICT`）にそのステップへ進む（#1917）。渡さなければ今の失敗と同じ。

    `extra` の `next`（と `on_fail`）は `merge` と `merge-approved` に付ける。`next` が無いと `merge` が並びの次の
    `merge-approved` へ流れるため、必ず渡す。
    """
    if "next" not in extra:
        raise ValueError("merge_steps には next が要る（merge が merge-approved へ流れないように）")
    ci_wait = int(plan_limits(a)["ci_wait_timeout"])
    merge = {
        "id": "merge",
        "type": "run",
        "timeout": with_margin(ci_wait) + RECLAIM_LIMIT,
        "cmd": f"{MERGE_CMD} --timeout {ci_wait}",
        "probe": MERGE_PROBE,
        "on_exit": {**INFRA_WAIT_STOP, **({str(EXIT_BASE_CONFLICT): on_conflict} if on_conflict else {})},
        **extra,
    }
    gate = {"id": "merge-gate", "type": "run", "timeout": 120, "cmd": MERGE_GATE_CMD, "gate_next": "end", "next": "merge"}
    if extra.get("on_fail"):
        gate["on_fail"] = extra["on_fail"]
    approved = {**merge, "id": "merge-approved", "cmd": f"{MERGE_CMD} --timeout {ci_wait} --gate-approved user"}
    return [gate, merge, approved]


def handoff_step(state: str, gate: str, what: str) -> dict:
    """MVV 判定で通した後のステップ（ラベル・マージ・判定のコメント）が落ちたときに承認ゲートへ落とすステップ（#1370 の I8）。
    `mvv` のステップが書いた `by: mvv` の記録を外し、理由を 1 行出して終了コード 10（承認ゲート）を返す。外せなくても 10 を返す。"""
    st, g = shlex.quote(state), shlex.quote(gate)
    why = shlex.quote(f"{what}が失敗した。{gate}は自動で通さず、利用者の承認を求める")
    cmd = (
        f'sh -c \'{SPRINT_STATE_PY} gate "$1" "$2" --withdraw >/dev/null || echo "$2 の MVV 判定の記録を外せなかった"; '
        f'echo "$3"; exit 10\' handoff {st} {g} {why}'
    )
    return {"id": "handoff", "type": "run", "timeout": 120, "cmd": cmd, "gate_next": "end", "next": "end"}


REJECT_STEP = "reject"


def reject_step(state: str, gate: str, next_id: str, root: str = ".", pr: str | None = None) -> dict:
    """承認ゲートで差し戻された後の続き（#2183）。直した後に `run <計画> --from reject` で流すと、差し戻しを差し戻しの記録
    （`gate-rejections.jsonl`）へ 1 行書いてから `next_id`（再提示のステップ）へ進む。記録を書けなければ止まる。
    conductor は `sprint-state.py gate ... --outcome rejected` を手で打たない。"""
    what = shlex.quote(f"{gate}で差し戻された（直して再提示する）")
    cmd = (
        f"{SPRINT_STATE_PY} gate {shlex.quote(state)} {shlex.quote(gate)} --what {what} --by user --outcome rejected "
        f"--root {shlex.quote(root)}" + (f" --pr {pr}" if pr else "")
    )
    return {"id": REJECT_STEP, "type": "run", "timeout": 120, "cmd": cmd, "next": next_id, "from_only": True}


def test_meta(plan: dict, a) -> dict:
    """計画の最上位に `テストの戦略` と `テストの時間` を書く。"""
    strategy = plan_strategy(a)
    if strategy:
        plan["テストの戦略"] = {"name": strategy["name"], "source": strategy["source"], "note": getattr(a, "test_note", None)}
    plan["テストの時間"] = dict(plan_limits(a))
    return plan


def pre_test_steps(a, fix_prompt: str) -> list[dict]:
    """範囲テストの前のステップ: 整形（format）→ 同期とチェック（sync）→ 範囲の静的解析（lint-limited）。宣言の無いものは
    置かない（format は supervise.json の format、sync は sync_checks、lint-limited は範囲の雛形を持つ kind: lint の suite が
    あり戦略が round-only でないとき。#1651 の決定 5・6）。fix-format・fix-sync・fix-lint（修正の worker。`fix_prompt`）は並びの先頭へ戻る
    （sync は静的解析も打つため、fix-sync の直しも整形を通し直す）。
    実装のプランの雛形（`templates.plan_to_merge`）が起点の取り込みの後に置く。"""
    st = plan_strategy(a) or {}
    lint = st.get("name") != ts.ROUND_ONLY and any(s.get("scope_command") and s.get("kind") == ts.LINT for s in st.get("suites") or [])
    lint_cmd = scope_cmd(a, " ".join(a.tests)) + " --only-kind lint"
    wanted = [
        (getattr(a, "format", None), {"id": "format", "type": "run", "preset": "format", "stage": "実装", "on_exit": {"2": "stop"}}),
        (getattr(a, "sync_checks", None), {"id": "sync", "type": "run", "preset": "sync-check", "stage": "実装"}),
        (
            lint,
            {
                "id": "lint-limited",
                "type": "run",
                "stage": "完了判定",
                "timeout": scope_timeout(a),
                "cmd": lint_cmd,
                "on_exit": {"2": "stop"},
            },
        ),
    ]
    found = [step for want, step in wanted if want]
    out: list[dict] = []
    for i, step in enumerate(found):
        fix = "fix-" + step["id"].split("-")[0]
        nxt = found[i + 1]["id"] if i + 1 < len(found) else "test-limited"
        out += [
            {**step, "on_fail": fix, "next": nxt},
            {"id": fix, "type": "work", "kind": "修正", "inputs": [step["id"]], "prompt": fix_prompt, "next": found[0]["id"]},
        ]
    return out
