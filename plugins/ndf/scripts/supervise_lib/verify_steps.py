"""雛形のテストとマージのステップ（#1334 AC7）。

範囲テストと全体テストは `test-run.py` が宣言の戦略で走らせる。ステップの `timeout` と `merge-when-green` の
`--timeout` は、`apply_decls` が宣言から出した上限（`test_limits`。`lib/test_strategy.limits`）に余裕を足したもので、
固定の秒は持たない。所要が宣言に無いときだけ「所要が不明のときの値」を使い、計画の `テストの時間` にそう書く。
"""

from __future__ import annotations

import math
import shlex

import test_strategy as ts
from supervise_lib.paths import MERGE_CMD, MERGE_PROBE, SPRINT_STATE_PY, TEST_RUN_PY

# ステップの `timeout` に足す余裕（上限の 1 割。下限は監視の 1 周期の 2 倍）
MARGIN_SHARE = 0.1
MARGIN_FLOOR = 30


def plan_strategy(a) -> dict | None:
    """宣言から解いた戦略（`apply_decls` が載せる）。無ければ `--test-cmd` の雛形から解く。"""
    found = getattr(a, "strategy", None)
    if isinstance(found, dict):
        return found
    template = getattr(a, "test_cmd", None)
    return ts.resolve({}, baseline_test=template).as_state() if template else None


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


def _template_arg(a) -> str:
    template = getattr(a, "test_cmd", None)
    return f" --template {shlex.quote(template)}" if template else ""


def scope_cmd(a, tests: str) -> str:
    """範囲テストのステップのコマンド（`test-run.py scope`）。"""
    return f"{TEST_RUN_PY} scope --paths {tests}{_template_arg(a)}"


def whole_cmd(a) -> str:
    """全体テストのステップのコマンド（`test-run.py whole`。CI に任せる戦略は Pull Request のチェックを待つ）。"""
    return f"{TEST_RUN_PY} whole --pr {{pr}}{_template_arg(a)}"


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
    return f" --baseline-test {shlex.quote(template)}" if template else ""


def merge_step(a, **extra) -> dict:
    """マージのステップ。CI の待ちの上限を `--timeout` で渡し、ステップの `timeout` はそれに余裕を足す。"""
    ci_wait = int(plan_limits(a)["ci_wait_timeout"])
    return {
        "id": "merge",
        "type": "run",
        "timeout": with_margin(ci_wait),
        "cmd": f"{MERGE_CMD} --timeout {ci_wait}",
        "probe": MERGE_PROBE,
        **extra,
    }


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


def test_meta(plan: dict, a) -> dict:
    """計画の最上位に `テストの戦略` と `テストの時間` を書く。"""
    strategy = plan_strategy(a)
    if strategy:
        plan["テストの戦略"] = {"name": strategy["name"], "source": strategy["source"], "note": getattr(a, "test_note", None)}
    plan["テストの時間"] = dict(plan_limits(a))
    return plan
