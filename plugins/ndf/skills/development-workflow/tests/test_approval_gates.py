"""承認の関門と、並行開発の下限が書かれていることを固定する（#424 の E / F）。

**関門は 2 つで、増やさない。** 増やすほど「承認したこと」の意味が薄れる。通過の
回数が増えると、内容を読まずに通す動きが入る。

**2 つは要否の決まり方が違う。** 設計 Pull Request のマージはマージ先のチャネルに
よらず要り、本番の系へ届く操作だけが**届く先が本番の系かどうか**で決まる。表と規則を
並べて書くと片方に掛かる規則が両方に掛かって読めるため、**関門ごとに節を分ける**。

**2 つ目の関門は配布と運用モードの実行の両方を含む（#423）。** 性質が同じもの
（反映した瞬間に効き、取り消しても「その状態を見た人」は戻らない）を別の関門として
数えると、数だけが増える。
"""
from __future__ import annotations

import re

from workflow_helpers import SKILL_DIR

SKILL = SKILL_DIR / "SKILL.md"
APPROVAL = SKILL_DIR / "references" / "approval-request.md"
PARALLEL = SKILL_DIR / "references" / "parallel-work.md"
PLUGIN_ROOT = SKILL_DIR.parents[1]
PR_SKILL = PLUGIN_ROOT / "skills" / "pr" / "SKILL.md"
MERGED_SKILL = PLUGIN_ROOT / "skills" / "merged" / "SKILL.md"


def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def approval() -> str:
    return APPROVAL.read_text(encoding="utf-8")


def parallel() -> str:
    return PARALLEL.read_text(encoding="utf-8")


# --- E1 / E2 / E2-b: 関門は 2 つ --------------------------------------------


def test_there_are_exactly_two_gates() -> None:
    body = skill()
    assert re.search(r"関門は\s*\*{0,2}2 つ", body)
    assert "設計 Pull Request のマージ" in body
    assert "本番の系へ届く操作" in body


def test_the_design_gate_does_not_depend_on_the_channel() -> None:
    """承認するのは配布の可否ではなく、この設計で実装へ進んでよいかである。"""
    body = skill()
    assert "マージ先のチャネルによらず" in body


def test_the_release_gate_depends_on_the_destination() -> None:
    """要否は「届く先が本番の系かどうか」で決まる。`operation` はマージ経路を持たない。"""
    body = skill()
    assert re.search(r"届く先が本番の系かどうかで決まる", body)
    assert "チャネルは系の一種である" in body


def test_the_two_gates_have_their_own_sections() -> None:
    """規則を並べて書くと、片方に掛かる規則が両方に掛かって読める。"""
    body = skill() + approval()
    assert body.count("### 設計 Pull Request のマージ") >= 1
    assert body.count("### 本番の系へ届く操作") >= 1


def test_the_implementation_merge_is_not_a_gate() -> None:
    body = skill()
    assert "実装 Pull Request のマージは、それ自体では関門にならない" in body


def test_merged_and_pr_point_at_the_rule() -> None:
    """規則は `release` が持つ。読む側は `merged` と `pr` から辿れる。"""
    for path in (PR_SKILL, MERGED_SKILL):
        body = path.read_text(encoding="utf-8")
        assert "チャネル" in body, path
        assert "release" in body, path


# --- E3 / E4: 本番のチャネルの宣言 ------------------------------------------


def test_the_production_channel_is_declared_by_the_repository() -> None:
    body = skill()
    assert "production_branch" in body
    assert ".ndf/worktree.json" in body


def test_the_base_branch_is_not_borrowed() -> None:
    body = skill()
    assert re.search(r"`base_branch`\s*は.{0,30}流用しない", body, re.DOTALL)


def test_without_a_declaration_the_default_branch_is_used() -> None:
    body = skill()
    assert re.search(r"宣言が無.{0,40}既定ブランチ", body, re.DOTALL)


# --- E5〜E8: 提示物 ---------------------------------------------------------


def test_parallel_pull_requests_are_approved_in_one_go() -> None:
    body = skill() + approval()
    assert re.search(r"まとめて\s*\*{0,2}1 回", body)


def test_every_url_is_listed_raw() -> None:
    body = approval()
    assert "生の URL" in body
    assert re.search(r"すべて|全て", body)


def test_the_order_of_multiple_pull_requests_is_defined() -> None:
    body = approval()
    assert "並べ方" in body


def test_a_dependency_order_is_shown_as_a_merge_order() -> None:
    body = approval()
    assert "マージの順序" in body


# --- E9: `/goal` の止まり方 -------------------------------------------------


def test_the_goal_stop_matches_the_two_gates() -> None:
    body = skill()
    section = body[body.index("## `/goal` の引数として呼ばれたとき") :]
    assert "AskUserQuestion" in section
    assert "本番" in section


# --- E10 / E11 / F: 並行開発 ------------------------------------------------


def test_the_method_is_left_to_the_person_in_charge() -> None:
    body = parallel()
    assert "担当の判断に任せる" in body


def test_multiple_milestones_are_sequential_by_default() -> None:
    body = parallel()
    assert re.search(r"マイルストーン.{0,80}順次", body, re.DOTALL)


def test_more_than_one_issue_can_be_handed_over() -> None:
    body = parallel()
    assert "複数の課題" in body


def test_the_four_shapes_are_listed() -> None:
    body = parallel()
    for shape in ("1 : 1", "1 : N", "N : 1", "N : M"):
        assert shape in body, shape


def test_the_boundary_with_issue_plan_strategy_is_written() -> None:
    body = parallel()
    assert "issue-plan-strategy" in body
    assert "工程の単位" in body


def test_every_stage_says_which_unit_it_moves_in() -> None:
    """工程表の 15 行それぞれが、どの単位で動くかを持つ。"""
    body = parallel()
    stages = [
        "要求と受け入れ条件", "作業場所の用意", "設計", "ドキュメントレビュー", "計画", "実装",
        "構造改善", "実装レビュー", "完了判定", "Pull Request", "確定仕様化", "後片付け",
        "配布", "リリース後テスト", "振り返り",
    ]
    for stage in stages:
        assert re.search(rf"^\|\s*{re.escape(stage)}\s*\|", body, re.MULTILINE), stage


LOWER_BOUNDS = (
    ("F5", "モードの違う課題を 1 本の Pull Request へ混ぜない"),
    ("F6", "Pull Request ごとにモードを判定して記録する"),
    ("F7", "判定したモードの必須の工程を飛ばさない"),
    ("F8", "依存の順序があるものを並行させない"),
    ("F9", "同じファイルを触るものを並行させない"),
    ("F10", "収束レビューが回る範囲"),
)


def test_the_six_lower_bounds_are_written() -> None:
    body = parallel()
    for name, text in LOWER_BOUNDS:
        assert text in body, f"{name}: {text}"


def test_it_says_which_bounds_the_machine_watches() -> None:
    """振り分けを書かないと、手順の側にある下限が「機械が見ているはず」と読まれる。"""
    body = parallel()
    assert "機械" in body
    assert "手順" in body
    assert re.search(r"^\|.*機械.*\|", body, re.MULTILINE)


def test_the_skill_points_at_the_reference() -> None:
    assert "references/parallel-work.md" in skill()
