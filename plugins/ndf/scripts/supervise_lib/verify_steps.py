"""雛形のテストとマージのステップ（#1334 AC7）。

範囲テストと全体テストは `test-run.py` が宣言の戦略で走らせる。ステップの `timeout` と `merge-when-green` の
`--timeout` は、`apply_decls` が宣言から出した上限（`test_limits`。`lib/test_strategy.limits`）に余裕を足したもので、
固定の秒は持たない。所要が宣言に無いときだけ「所要が不明のときの値」を使い、計画の `テストの時間` にそう書く。
"""

from __future__ import annotations

import math
import shlex

import test_strategy as ts
from step_result import EXIT_BASE_CONFLICT
from supervise_lib.decl import scope_paths, test_kind
from supervise_lib.paths import MERGE_CMD, MERGE_GATE_CMD, MERGE_PROBE, SPRINT_STATE_PY, TEST_RUN_PY

# merge-when-green と release が CI の基盤待ち（75。lib/step_result.py の EXIT_INFRA_WAIT）で止まったら、judge を通らず
# プランを止める（修正へ回さない。#1645 の決定 6）
INFRA_WAIT_STOP = {"75": "stop"}

# ステップの `timeout` に足す余裕（上限の 1 割。下限は監視の 1 周期の 2 倍）
MARGIN_SHARE = 0.1
MARGIN_FLOOR = 30


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


IMPL_TIMEOUT_DEFAULT = 3600  # 実装の上限の既定（引数・宣言・実績のどれも無いとき）


def impl_timeout_estimate(a) -> tuple[int, dict]:
    """実装のステップの (timeout, 根拠)。引数 → 宣言 → 実績の見込み（同じ構成の直近の最大 + 余裕。最大が打ち切りなら余裕を足さない。
    3600 を下限にする）→ 3600（#1856 の決定 10）。

    根拠はステップの `estimate` の欄の形 `{source: arg|decl|history|uncalibrated, samples, high, basis}`。"""
    if getattr(a, "impl_timeout", None):
        source = getattr(a, "impl_timeout_source", None) or "arg"
        return int(a.impl_timeout), {"source": source, "samples": 0, "high": None, "basis": []}
    import estimate_predict as ep
    import estimate_records as er

    root = getattr(a, "repo", None) or getattr(a, "worktree", None) or "."
    try:
        decl = er.load_estimate_decl(root)
        window, least = decl.get("window") or ep.stats.WINDOW, decl.get("min_samples") or ep.IMPL_MIN_SAMPLES
        got = ep.impl_limit(er.read_actuals(er.estimate_dir(root)), window, least)
    except (er.DeclError, OSError) as e:
        got = {"source": "uncalibrated", "samples": 0, "high": None, "basis": [], "note": str(e)[:200]}
    timed_out = got.pop("timed_out", False)  # ステップの `estimate` の欄の形には載せない
    if got["source"] == "history" and got["high"]:
        high = math.ceil(got["high"])
        # 最大が打ち切りの標本（秒は前の上限）なら余裕を足さない。足すと打ち切りのたびに上限が伸び続ける。
        # 既定より下げない: 短い実績で上限が下がると、それより長い実装が同じ秒で打ち切られ続け、上限が戻らない
        return max(IMPL_TIMEOUT_DEFAULT, high if timed_out else with_margin(high)), got
    return IMPL_TIMEOUT_DEFAULT, got


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


def whole_timeout(a) -> int:
    """全体テストのステップの `timeout`。手元なら `whole_timeout`、CI なら `ci_wait_timeout` に余裕を足す。"""
    limits, strategy = plan_limits(a), plan_strategy(a)
    on_ci = bool(strategy and strategy.get("name") == ts.LOCAL_SCOPED_CI_WHOLE)
    return with_margin(limits["ci_wait_timeout"] if on_ci else limits["whole_timeout"])


def refactor_template_arg(a) -> str:
    """`--test-cmd` を渡したときだけ、cross-refactoring へ同じ雛形を `--baseline-test` で渡す（宣言があれば渡さない）。"""
    template = getattr(a, "test_cmd", None)
    if not template:
        return ""
    kind = test_kind(a)
    return f" --baseline-test {shlex.quote(template)}" + (f" --test-kind {kind}" if kind != ts.TEST else "")


def merge_steps(a, on_conflict: str | None = None, **extra) -> list[dict]:
    """マージの 3 ステップ（#1336 の決定 1）。

    - `merge-gate`: 宛先が承認ゲート 2（自動反映の本番チャネル）に当たるかだけを判定する。当たればプランを終える（`gate_next: end`）
    - `merge`: CI を待ってマージする。CI の待ちの上限を `--timeout` で渡し、ステップの `timeout` はそれに余裕を足す
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
        "timeout": with_margin(ci_wait),
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
    why = shlex.quote(f"{what}が失敗した。{gate} は自動で通さず、利用者の承認を求める")
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
    what = shlex.quote(f"{gate} で差し戻された（直して再提示する）")
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
