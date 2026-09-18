"""3 層（conductor / supervisor / worker）の規約の文書を固定する（#550 の 2 本目）。

要求は `issues/issue-550-657-requirements.md` の AC3・AC4・AC4b・AC9〜AC19・AC64、
形は `issues/issue-550-657-design-contracts.md` が持つ。

**規約は文書にしか無い。** 運転そのものは Claude Code の `/goal` と `Agent` が行うため、
このテストが確かめられるのは「規約が形を持って書かれていること」だけである。実機での
無人の通過（AC1・AC2・AC60・AC65）はリリース後テストが見る。

**値ではなく形を見る。** 固定費の実測値のようにリポジトリとモデルで変わる値は規約へ
書かない（AC12）ため、そこだけは「無いこと」を確かめる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from workflow_helpers import SKILL_DIR

PLUGIN_ROOT = SKILL_DIR.parents[1]
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
LAYERS_REF = REFERENCES / "agent-layers.md"
WINDOW_REF = REFERENCES / "context-window.md"
CROSS_REVIEW = PLUGIN_ROOT / "skills" / "cross-review" / "SKILL.md"
CROSS_REVIEW_BUDGET = (PLUGIN_ROOT / "skills" / "cross-review" / "references"
                      / "context-budget.md")

# 語を揃える対象（AC16）。ファイル名は変えない。
TERM_TARGETS = (SKILL, WINDOW_REF, LAYERS_REF)

# 分割の基準（`scripts/check-doc-line-limit.py` の LIMIT と同じ）。
LINE_LIMIT = 500


@pytest.fixture(scope="module")
def layers() -> str:
    return LAYERS_REF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def window() -> str:
    return WINDOW_REF.read_text(encoding="utf-8")


# ---------- AC16: 語が 3 層と context window に揃っている ----------

@pytest.mark.parametrize("path", TERM_TARGETS, ids=lambda p: p.name)
def test_the_three_layer_docs_drop_the_old_words(path: Path) -> None:
    """「窓」と「親」（実行の主体の意味）が残っていない。"""
    body = path.read_text(encoding="utf-8")
    assert "窓" not in body, f"{path.name} に「窓」が残っている"
    assert "親" not in body, f"{path.name} に「親」が残っている"


@pytest.mark.parametrize("path", TERM_TARGETS, ids=lambda p: p.name)
def test_the_three_layer_docs_use_the_layer_words(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "context window" in body
    assert "conductor" in body and "supervisor" in body and "worker" in body


@pytest.mark.parametrize("path", (SKILL, *sorted(REFERENCES.glob("*.md"))), ids=lambda p: p.name)
def test_the_workflow_docs_carry_no_measured_fixed_cost(path: Path) -> None:
    """規約の文書に固定費の実測値が書かれていない（AC12）。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        if "固定費" not in line:
            continue
        assert not re.search(r"[0-9０-９][0-9０-９,]*\s*(万|k|K)?\s*トークン", line), line


def test_the_skill_stays_within_the_split_limit() -> None:
    """`/goal` の節を移した後も分割の基準を超えない（決定 23）。"""
    assert len(SKILL.read_text(encoding="utf-8").splitlines()) <= LINE_LIMIT


def test_the_layers_doc_stays_within_the_split_limit() -> None:
    """3 本目（中断と再開）が節を足せる余りを残す。"""
    assert len(LAYERS_REF.read_text(encoding="utf-8").splitlines()) <= LINE_LIMIT


# ---------- AC9: 3 層の責務と持ち場の表 ----------

def test_the_layers_doc_tells_who_may_ask_the_human(layers: str) -> None:
    """人間へ問えるのは conductor だけである（AC2 の前提・AC17）。"""
    assert "AskUserQuestion" in layers
    row = next(line for line in layers.splitlines()
               if line.startswith("| conductor ") and "AskUserQuestion" in line)
    assert "問える" in row
    for layer in ("supervisor", "worker"):
        other = next(line for line in layers.splitlines() if line.startswith(f"| {layer} "))
        assert "問えない" in other


@pytest.mark.parametrize("post", ("設計", "実装", "検査", "取り込み", "仕上げ"))
def test_the_post_table_lists_the_five_posts(layers: str, post: str) -> None:
    assert re.search(rf"^\| {post} \|", layers, re.MULTILINE), post


def test_the_post_table_has_a_gate_column(layers: str) -> None:
    header = next(line for line in layers.splitlines()
                  if line.startswith("| 持ち場 |") and "返す関門" in line)
    for column in ("通す工程", "単位", "始まりの条件", "終わりの条件", "返す関門"):
        assert column in header, column


def test_the_post_table_maps_onto_the_four_cut_points(layers: str) -> None:
    """切れ目 4 点（`context-window.md`）と関門 2 つとの対応が読み取れる（AC9）。"""
    assert "context-window.md" in layers
    for cut in ("ドキュメントレビューのマージの後", "構造改善と実装レビューの前後",
                "Pull Request を出した後", "配布の後"):
        assert cut in layers, cut
    assert "設計 Pull Request のマージ" in layers
    assert "本番の系へ届く操作" in layers


def test_the_layers_doc_keeps_the_gate_count_at_two(layers: str) -> None:
    """関門を増やさない（AC62）。"""
    assert "関門は 2 つ" in layers


# ---------- モードごとの持ち場の組み方 ----------

MODE_POST_COMPOSITIONS = (
    ("standard", "上の表のまま"),
    ("documentation", "設計に素材の収集と出典の確定、仕上げに体裁レビューが入る。"
     "実装は執筆、検査は構造改善を含まない"),
    ("legacy-refactor", "設計 Pull Request を分けないときは設計の持ち場を作らない。"
     "実装は現状固定テストと段階的改善、実装レビューは `pr-review`"),
    ("light", "設計の持ち場を作らない。実装は計画を含まず、検査は構造改善を含まない。"
     "本番の承認が要らなければ仕上げも作らない"),
    ("operation", "設計が**本番の系へ届く実行の前で関門を返す**。"
     "実装は実行（[operation-run.md](operation-run.md)）"),
)


@pytest.mark.parametrize(("mode", "composition"), MODE_POST_COMPOSITIONS)
def test_each_mode_keeps_its_current_post_composition(
        layers: str, mode: str, composition: str) -> None:
    """各モードで省略・追加する持ち場、工程、関門の現状を固定する。"""
    assert f"| `{mode}` | {composition} |" in layers


def test_omitted_post_work_and_start_condition_move_together(layers: str) -> None:
    """作らない持ち場の工程と開始条件は、次の持ち場へ一緒に移る。"""
    assert ("作らない持ち場の工程は、次の持ち場の先頭へ入る。"
            "始まりの条件も一緒に移る。") in layers
    assert "関門を返す\n持ち場は、中身が少なくても作る。" in layers


# ---------- AC4: supervisor の報告の 10 項目 ----------

SUPERVISOR_REPORT = (
    "持ち場", "課題", "結果", "関門", "次の持ち場", "Pull Request",
    "最後に記録した工程", "使った worker", "提示物", "理由",
)


@pytest.mark.parametrize("item", SUPERVISOR_REPORT)
def test_the_supervisor_report_carries_all_ten_items(layers: str, item: str) -> None:
    block = layers.split("## 持ち場の報告", 1)
    assert len(block) > 1, "`## 持ち場の報告` の見出しが無い"
    assert f"- {item}: " in block[1], item


def test_the_supervisor_report_names_the_next_post(layers: str) -> None:
    """報告の中に次に起動する持ち場の名前が入る（AC4）。"""
    assert "次の持ち場" in layers
    assert "工程表を読まずに" in layers


# ---------- AC17: worker の報告の 5 項目 ----------

WORKER_REPORT = ("作業", "結果", "見つけたもの", "置き場所", "次にすること")


@pytest.mark.parametrize("item", WORKER_REPORT)
def test_the_worker_report_carries_all_five_items(layers: str, item: str) -> None:
    block = layers.split("## 作業の報告", 1)
    assert len(block) > 1, "`## 作業の報告` の見出しが無い"
    assert f"- {item}: " in block[1], item


def test_the_worker_is_a_leaf(layers: str) -> None:
    """worker は人間へ問わず、別のサブエージェントを起動しない（AC17）。"""
    assert "別のサブエージェントを起動しない" in layers


# ---------- AC18: worker の報告を conductor へ転送しない ----------

def test_the_worker_report_is_not_forwarded(layers: str) -> None:
    assert "worker の報告をそのまま conductor へ渡さない" in layers
    assert "conductor が 1 つの持ち場について読む報告は 1 件である" in layers


# ---------- AC3: supervisor は承認を求めず、取り消せない操作をしない ----------

def test_the_supervisor_returns_the_gate_without_asking(layers: str) -> None:
    assert "承認を求めずに" in layers
    assert "結果: 関門" in layers


def test_the_supervisor_never_merges_the_design_pull_request(layers: str) -> None:
    assert "設計 Pull Request をマージしない" in layers
    assert re.search(r"承認[^\n]*無いまま行わない", layers)


# ---------- AC15: 関門以外の確認は提示して進める ----------

def test_the_lower_layers_present_and_proceed(layers: str) -> None:
    assert "関門以外の Skill の確認は、提示して進める" in layers
    assert "確認待ちで止まらない" in layers


# ---------- AC5: 報告の形を持たない終わりは 3 回まで続けさせる ----------

def test_the_upper_layer_resends_three_times_at_most(layers: str) -> None:
    assert "SendMessage" in layers
    assert "3 回" in layers
    assert "4 回目は送らず" in layers


def test_the_conductor_counts_the_resends_per_post(layers: str) -> None:
    assert "報告なしで続けさせた回数" in layers


# ---------- AC4b: 止まるときに持ち場の一覧を 1 回出す ----------

def test_the_conductor_prints_the_post_list_when_it_stops(layers: str) -> None:
    assert "## 持ち場の一覧" in layers
    for moment in ("`AskUserQuestion` を出す前", "失敗を報告する前",
                   "背景の待ちを起動して応答を終える前"):
        assert moment in layers, moment


# ---------- AC10: モデルの既定と落とす基準 2 つ ----------

def test_the_model_default_follows_the_upper_layer(layers: str) -> None:
    assert re.search(r"`model`[^\n]*省", layers)


def test_the_model_may_be_lowered_only_on_two_conditions(layers: str) -> None:
    block = layers.split("軽いモデルへ落として", 1)
    assert len(block) > 1, "軽いモデルへ落とす基準が無い"
    tail = block[1][:800]
    assert "1." in tail and "2." in tail, tail
    assert "調査" in tail and "集計" in tail


# ---------- AC19: 委譲しない 5 つ ----------

NOT_DELEGATED = (
    "モード判定", "関門の判断", "収束の判定",
    "設計の決定と理由の記録", "受け入れ条件の書き換え",
)


@pytest.mark.parametrize("item", NOT_DELEGATED)
def test_the_five_undelegated_judgements_stay_above_the_worker(layers: str, item: str) -> None:
    assert item in layers, item


def test_the_undelegated_judgements_belong_to_the_upper_layers(layers: str) -> None:
    assert re.search(r"(supervisor か conductor|supervisor（または conductor）)", layers)


# ---------- 起動の指示の description の形（測定が持ち場を読めるようにする） ----------

def test_the_launch_instruction_fixes_the_description_head(layers: str) -> None:
    """測定は `description` の先頭語から持ち場と作業の種類を読む（決定 14）。"""
    assert "<持ち場>: <課題番号" in layers
    assert "<作業の種類>: <一言>" in layers
    assert "その他" in layers


@pytest.mark.parametrize("word", ("設計", "実装", "検査", "取り込み", "仕上げ",
                                  "調査", "修正", "検証", "集計"))
def test_the_launch_vocabulary_matches_the_measurement(layers: str, word: str) -> None:
    assert word in layers, word


# ---------- AC64: 並行の本数を数える単位 ----------

def test_the_parallel_unit_is_the_supervisor(layers: str) -> None:
    assert "supervisor 1 つ" in layers
    assert "1 つの作業ツリー" in layers
    assert re.search(r"同時に(動かす|走る)\s*worker[^\n]*既定", layers)


# ---------- AC14: `/goal` でない呼び出しでは 3 層へ出さない ----------

def test_the_three_layers_apply_only_to_goal(layers: str, ) -> None:
    assert "`/goal` の引数として呼ばれたときだけ" in layers
    skill = SKILL.read_text(encoding="utf-8")
    assert "agent-layers.md" in skill


def test_the_goal_section_keeps_only_the_entrance() -> None:
    """細部は参照文書が持つ（決定 23）。"""
    skill = SKILL.read_text(encoding="utf-8")
    section = skill.split("## `/goal` の引数として呼ばれたとき", 1)[1].split("\n## ", 1)[0]
    assert "agent-layers.md" in section
    assert len(section.splitlines()) <= 40, "節が長い。細部は参照文書へ移す"
    assert "他者の承認が要るときは、到達点を置き直す" not in section


def test_the_goal_section_keeps_the_two_gates() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    section = skill.split("## `/goal` の引数として呼ばれたとき", 1)[1].split("\n## ", 1)[0]
    assert "AskUserQuestion" in section
    assert "設計 Pull Request のマージ" in section
    assert "本番の系へ届く操作" in section


def test_the_goal_target_reset_moved_to_the_layers_doc(layers: str) -> None:
    """到達点の置き直しは参照文書が持つ。"""
    assert "到達点を置き直す" in layers
    assert "gh api" in layers


def test_the_target_reset_table_maps_results_to_targets(layers: str) -> None:
    """他者承認が要る・要らない・判定できないの 3 結果と到達点の対応を固定する。"""
    section = layers.split("## 到達点を置き直す", 1)[1].split("\n## ", 1)[0]
    # 1. 到達点の判定表を行単位で読み取る
    rows = {
        parts[0].strip(): parts[1].strip()
        for line in section.splitlines()
        if line.startswith("|")
        for parts in [line.split("|")[1:-1]]
        if len(parts) == 2 and parts[0].strip() not in ("結果", "---")
    }
    assert set(rows.keys()) == {"承認が要る", "要らない", "判定できない"}

    # 2. 承認が要る場合は Pull Request の提出とレビュー収束へ置き直す現状を比較する
    assert rows["承認が要る"] == "**Pull Request の提出とレビューの収束**へ置き直す"

    # 3. 承認不要の場合と判定不能の場合は元のゴールまで進み、判定不能ではマージ拒否時に置き直す現状を比較する
    assert rows["要らない"] == "ゴール条件が指す工程まで"
    assert rows["判定できない"] == "ゴール条件が指す工程まで進み、マージが拒否された時点で置き直す"


# ---------- AC13: モデルに依る目安とリポジトリに依る固定費 ----------

def test_the_window_doc_separates_the_model_guideline_from_the_repository_cost(window: str) -> None:
    assert "モデルに依る" in window
    assert "リポジトリに依る" in window


def test_the_window_doc_ties_its_guideline_to_the_measurement_default(window: str) -> None:
    """`skill-stats --window-limit` の既定（200000）と「遅くとも N 万で切る」が一致する。"""
    match = re.search(r"遅くとも\s*([0-9]+)\s*万で切る", window)
    assert match, "「遅くとも N 万で切る」が無い"
    assert int(match.group(1)) * 10000 == 200000


# ---------- AC12: 粒度の基準は比で、supervisor に当てる ----------

def test_the_grain_rule_is_a_ratio_applied_to_the_supervisor(window: str) -> None:
    assert "実作業が固定費を上回る" in window
    assert "束ね" in window
    block = window.split("実作業が固定費を上回る", 1)[0][-600:]
    assert "supervisor" in block, "比の基準を当てる層が supervisor だと書かれていない"


def test_the_window_doc_points_at_the_three_layer_operation(window: str) -> None:
    assert "agent-layers.md" in window


# ---------- AC11: cross-review の「メイン」の定義 ----------

def test_cross_review_defines_main_as_the_driving_supervisor() -> None:
    body = CROSS_REVIEW_BUDGET.read_text(encoding="utf-8")
    block = body.split("「メイン」", 1)
    assert len(block) > 1, "「メイン」の定義が無い"
    tail = block[1][:500]
    assert "収束ループを駆動している supervisor" in tail
    assert "conductor" in tail


def test_cross_review_keeps_the_diff_out_of_the_supervisor_window() -> None:
    body = CROSS_REVIEW_BUDGET.read_text(encoding="utf-8")
    assert "worker" in body
    assert re.search(r"supervisor の context window に diff を載せない", body)


def test_cross_review_points_at_the_definition() -> None:
    """手順書は 420 行の余白を保つため、定義は参照文書が持つ。"""
    skill = CROSS_REVIEW.read_text(encoding="utf-8")
    assert "「メイン」が何を指すか" in skill
    assert "references/context-budget.md" in skill
