"""並行の本数と重なりの下限（`parallel-work.md`、#621 / #540）。

**この文書は本数の初期値を持たない。** 予備・1 本の見込み・上限・スワップの閾値を
決めるのは `plugins/ndf/scripts/parallel-measure.py` の定数だけで、文書が写すと
2 か所が同じ事実を持つ（設計の決定 7）。

`test_approval_gates.py` は下限の**存在**（F5〜F10）を見る。こちらは下限 4〜6 の
**理由と目安**を見る。同じファイルの別の節に当たるため、束が分かれても衝突しない。
"""
from __future__ import annotations

import re

from workflow_helpers import SKILL_DIR

PARALLEL = SKILL_DIR / "references" / "parallel-work.md"
MEASURE = SKILL_DIR.parents[1] / "scripts" / "parallel-measure.py"


def parallel() -> str:
    return PARALLEL.read_text(encoding="utf-8")


# --- 下限 6: ホストのメモリ（AC30 / AC36） ----------------------------------


def test_the_sixth_bound_names_the_host_memory() -> None:
    """CLI の共有だけを理由にすると、1 本ずつ回しても落ちる事故を説明できない。"""
    body = parallel()
    assert "ホストのメモリ" in body
    assert "並行の本数は、測った本数と収束レビューが回る範囲の小さい方に抑える" in body


def test_the_sixth_bound_keeps_the_measured_incident() -> None:
    """21GiB のホストで 5〜6 本のとき本体が 2 回落ち、3 本では落ちなかった（#621）。"""
    body = parallel()
    assert "21GiB" in body
    assert "5〜6 本" in body
    assert "3 本では落ちなかった" in body
    assert "#621" in body


def test_the_bounds_name_both_releases_they_came_from() -> None:
    """下限 6 の理由は v10.11.0 の事故（5〜6 本で本体が落ちた）から来ている。"""
    body = parallel()
    assert "いずれも v10.5.0 と v10.11.0 の事故か、その予防に当たる" in body


def test_a_lane_is_one_worktree() -> None:
    """数えるのは作業ツリー 1 つ分の担当で、その中の補助の実行主体は数えない（決定 26）。"""
    body = parallel()
    assert "作業ツリー 1 つ分の担当" in body


def test_only_one_lane_runs_container_checks() -> None:
    """コンテナは担当の cgroup の外で動き、1 本の見込みの外側で空きを食う（決定 9）。"""
    assert "コンテナを起動する検査を持つ担当は同時に 1 本にする" in parallel()


# --- 振り分け: 測定は機械、判断は手順（決定 8） ------------------------------


def test_the_measurement_is_machine_and_the_judgement_is_procedure() -> None:
    body = parallel()
    assert "測定は機械、判断は手順" in body
    assert "parallel-measure.py capacity" in body


def test_the_measurement_does_not_refuse_a_launch() -> None:
    """担当は Agent ツールで起動され、フックで数えられない（決定 8）。"""
    assert "起動は拒否しない" in parallel()


# --- 初期値を持つのは 1 か所だけ（AC33 / 決定 7） ---------------------------


def test_the_document_does_not_copy_the_defaults() -> None:
    body = parallel()
    for value in ("2048", "25%"):
        assert value not in body, f"初期値 {value} は parallel-measure.py だけが持つ"


def test_the_script_holds_the_defaults() -> None:
    source = MEASURE.read_text(encoding="utf-8")
    for name in ("DEFAULT_RESERVE_MIB", "DEFAULT_PER_LANE_MIB", "DEFAULT_MAX_LANES",
                 "DEFAULT_SWAP_FREE_MIN_PCT"):
        assert re.search(rf"^{name} = \d+$", source, re.MULTILINE), name
