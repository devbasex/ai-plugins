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


def flat(text: str) -> str:
    """折り返しの改行を除く。日本語の文は改行の位置で語が割れる。"""
    return text.replace("\n", "").replace("**", "")


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


# --- 工程が動く単位 ----------------------------------------------------------


def test_stage_units_have_the_three_current_values() -> None:
    rows = _stage_unit_rows()
    assert {row[1] for row in rows} == {"課題", "Pull Request", "まとまり"}


def test_the_release_group_stages_move_as_one_group() -> None:
    rows = _stage_unit_rows()
    assert {row[0] for row in rows if row[1] == "まとまり"} == {
        "確定仕様化", "配布", "体裁レビュー", "リリース後テスト", "振り返り",
    }


def test_requirements_and_acceptance_criteria_move_per_issue() -> None:
    rows = _stage_unit_rows()
    assert {row[0] for row in rows if row[1] == "課題"} == {"要求と受け入れ条件"}


def _stage_unit_rows() -> list[list[str]]:
    lines = parallel().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "## 工程が動く単位")
    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells == ["工程", "単位", "理由"] or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


# --- 振り分け: 測定は機械、判断は手順（決定 8） ------------------------------


def test_the_measurement_is_machine_and_the_judgement_is_procedure() -> None:
    body = parallel()
    assert "測定は機械、判断は手順" in body
    assert "parallel-measure.py capacity" in body


def test_the_measurement_does_not_refuse_a_launch() -> None:
    """担当は Agent ツールで起動され、フックで数えられない（決定 8）。"""
    assert "起動は拒否しない" in parallel()


def test_bounds_1_to_3_are_watched_by_machine() -> None:
    """下限 1〜3 は Pull Request 作成時に機械が案内する。"""
    by_bound = {row[0]: row[2] for row in _watcher_rows()}
    for bound in ("1", "2", "3"):
        assert "機械" in by_bound[bound]


def test_bounds_4_and_5_are_watched_by_procedure() -> None:
    """下限 4・5 は手順（実行計画・後からマージする側の実装レビュー）が見る。"""
    by_bound = {row[0]: row[2] for row in _watcher_rows()}
    for bound in ("4", "5"):
        assert "手順" in by_bound[bound]


def test_bound_6_splits_measurement_and_judgement() -> None:
    """下限 6 は測定をスクリプトが行い、判断を担当自身が下す。"""
    by_bound = {row[0]: row[2] for row in _watcher_rows()}
    assert "測定は機械、判断は手順" in by_bound["6"]


def _watcher_rows() -> list[list[str]]:
    lines = parallel().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "## 機械が見るものと、手順として書くもの")
    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells == ["#", "下限", "誰が見るか", "どこで"] or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


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


# --- 下限 4: 依存は工程の対で見る（AC5） -----------------------------------


def test_the_fourth_bound_is_a_pair_of_stages() -> None:
    """依存は「B のどの工程が、A のどの時点を入力にするか」で決まる（決定 2）。"""
    body = flat(parallel())
    assert "依存する工程が終わる前に、それを入力にする工程を始めない" in body
    assert "B の設計が A の決定を読むなら A の設計の収束を" in body
    assert "B の実装が A の実装を前提にするなら A の実装のマージを待つ" in body


def test_an_implementation_order_does_not_block_the_next_design() -> None:
    """v10.11.0 の後半 3 回は、この 2 つを区別しなかったために設計が実装を待った。"""
    assert "実装の順序の依存は、B の設計を止めない" in flat(parallel())


# --- 下限 5 と重なりの目安（AC6 / AC7 / AC8） -------------------------------


def test_the_fifth_bound_is_about_the_resolved_diff() -> None:
    """守るものは「レビューした差分と入る差分が食い違わない」ことである（決定 3）。"""
    body = flat(parallel())
    assert "競合を解いた差分を、レビューを通さずにマージしない" in body
    assert "同じファイルを触るものを並行させない" not in body


def test_the_later_merge_resolves_the_conflict() -> None:
    """先にマージした側は、解く時点で既に閉じている（決定 3）。"""
    body = flat(parallel())
    assert "後からマージする側が解き、解いた後の head でレビューを収束させる" in body


def test_there_is_a_section_for_the_overlap_guide() -> None:
    assert "## 重なりの目安" in parallel()


def test_the_overlap_guide_has_three_kinds() -> None:
    """区分は設計の時点で分かる最も細かい単位（節）で切る（決定 4）。"""
    table = _overlap_table()
    assert [row[0] for row in table] == ["別の節", "足すだけ", "書き換え"], table


def test_the_overlap_guide_says_whether_each_kind_runs_in_parallel() -> None:
    """区分ごとに、並行してよいかと、並行しないときに何を待つかが一意に決まる。"""
    header, *rows = _overlap_rows()
    assert header == ["区分", "何が当たるか", "並行", "待つもの"], header
    verdicts = {row[0]: (row[2], row[3]) for row in rows}
    assert verdicts["別の節"] == ("してよい", "なし")
    assert verdicts["足すだけ"][0] == "してよい"
    assert "実装はしない" in verdicts["書き換え"][0]
    assert "先の Pull Request のマージ" in verdicts["書き換え"][1]


def test_designs_run_in_parallel_whatever_the_kind() -> None:
    """設計文書は束ごとに別のファイルへ書く（AC8）。"""
    body = flat(parallel())
    assert "設計の工程どうしは、どの区分でも並行してよい" in body


def test_the_kind_is_fixed_after_the_design() -> None:
    """着手の時点の触る場所は見込みで、確定の値は実行計画が持つ（決定 4）。"""
    body = flat(parallel())
    assert "区分は設計の後に確定する" in body
    assert "実行計画" in body


def _overlap_rows() -> list[list[str]]:
    lines = parallel().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "## 重なりの目安")
    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def _overlap_table() -> list[list[str]]:
    return _overlap_rows()[1:]


# --- `issue-plan-strategy` との境界（決定 5） -------------------------------


def test_the_execution_plan_belongs_to_issue_plan_strategy() -> None:
    """置き場所・形・見直す時点・閉じ方は `issue-plan-strategy` が持つ。"""
    body = flat(parallel())
    assert "実行計画" in body
    assert "issue-plan-strategy" in body
