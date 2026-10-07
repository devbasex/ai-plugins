"""兆候・手法・観点・重要度の呼び名と、判断の基準になる定数。

見送りの理由・トレーラー・想定最大時間の既定など、工程の判断に使う値も持つ。
"""

from __future__ import annotations

import pathlib
from typing import Any

import md


# 兆候と手法の呼び名は `refactoring` が持つ（#444）。**ここでは読むだけで、自分では
# 持たない。** 2 か所にあると片方だけが更新され、枠組みの出力と方法論の説明が食い違う。
#
# **読めなければ止める。** 呼び名が揃わないと重複排除が効かず、同じ提案が別物として残る。
# 確認は `init` の時点で行う（`commands/setup.py`）。


class VocabularyUnavailable(RuntimeError):
    """呼び名の表を読めないことを表す。**握りつぶさない。**"""


# 呼び名の表の位置。**現在地には依存させない。** `uv run --script` で起動したときの
# 現在地は、スクリプトの位置と揃わない。
VOCABULARY_TABLE = pathlib.Path(__file__).resolve().parents[3] / "refactoring" / "references" / "vocabulary.md"


def _read_table(heading: str, value_column: str) -> dict[str, str]:
    """呼び名の表の 1 節（深さ 2 の見出し `heading`）の最初の表を、識別子 → 値で返す。

    **列は見出しの名前で決める。** 並びが変わっても読み取りが壊れない。節と表の読み取りは
    ライブラリの `md` が持つ（コードの囲みの中の `## ` や `|` の行を節や表と取り違えない）。
    """
    try:
        text = VOCABULARY_TABLE.read_text(encoding="utf-8")
    except OSError as exc:  # 読めない = 呼び名が無い
        raise VocabularyUnavailable(f"呼び名の表を読めません: {VOCABULARY_TABLE} ({exc})") from exc
    section = md.section_named(text, heading, level=2)
    if section is None:
        raise VocabularyUnavailable(f"呼び名の表に「{heading}」の節がありません")
    table = next((t for t in md.tables(text) if section.start <= t.start < section.end), None)
    if table is None or not table.rows:
        raise VocabularyUnavailable(f"呼び名の表の「{heading}」が空です")
    try:
        i_id, i_value = table.header.index("識別子"), table.header.index(value_column)
    except ValueError as exc:
        raise VocabularyUnavailable(f"呼び名の表の「{heading}」に列がありません: {exc}") from exc
    return {row[i_id].strip("`"): row[i_value] for row in table.rows}


SMELLS: dict[str, str] = _read_table("兆候", "日本語の名前")

TECHNIQUES: dict[str, str] = _read_table("手法", "日本語の名前")

# 提案の観点（#933 の AC5）。**「多面的に」を観点の一覧として示す。** 一覧が無いと、
# 参加者は目についた兆候に偏る（#917 では `long_method` が 10 件）。観点は兆候の語彙を
# 探す入口であって、語彙そのものではない。呼び名と代表の兆候は表が持つ（#1814 決定 9）。
VIEWPOINTS: dict[str, str] = _read_table("観点", "説明")

# 観点 → 代表の兆候。提案の `smell` に観点の識別子が書かれたら、この兆候へ写す（降格しない）。
VIEWPOINT_SMELLS: dict[str, str] = {k: v.strip("`") for k, v in _read_table("観点", "代表の兆候").items()}


def _check_disjoint() -> None:
    """兆候・手法・観点の識別子が重ならず、代表の兆候が兆候の表にあること（I11・I12）。破れていれば止める。"""
    for a, b, names in (
        ("兆候", "手法", set(SMELLS) & set(TECHNIQUES)),
        ("兆候", "観点", set(SMELLS) & set(VIEWPOINTS)),
        ("手法", "観点", set(TECHNIQUES) & set(VIEWPOINTS)),
    ):
        if names:
            raise VocabularyUnavailable(f"呼び名の表の{a}と{b}の識別子が重なっています: {', '.join(sorted(names))}")
    missing = sorted(f"{k} → {v}" for k, v in VIEWPOINT_SMELLS.items() if v not in SMELLS)
    if missing:
        raise VocabularyUnavailable(f"観点の代表の兆候が兆候の表にありません: {', '.join(missing)}")


_check_disjoint()

# 重要度。語彙外の提案は `unknown` へ降格し、しきい値で自動的に落ちるようにする。
SEVERITY_ORDER = {"unknown": 0, "minor": 1, "major": 2, "critical": 3}
DEFAULT_SEVERITY_THRESHOLD = "minor"

# 提案が名乗ってよい重要度。`unknown` は降格先なので含めない。
SEVERITIES: tuple[str, ...] = tuple(s for s in SEVERITY_ORDER if s != "unknown")


def vocabulary() -> dict[str, Any]:
    """提案プロンプトへ**そのまま列挙する**ための語彙集合。

    手順書の見出しは日本語なので、「語彙に限定する」とだけ書くと読んだ側が
    日本語を語彙と解釈する（実測では提案 4 件が全て日本語で返り、
    語彙外の降格規則により全件見送りになった）。**検証側が持つ集合をそのまま
    渡す**ことで、許容値の定義を 1 箇所に保ったまま列挙できる。
    """
    return {
        "smells": dict(SMELLS),
        "techniques": dict(TECHNIQUES),
        "severities": list(SEVERITIES),
        "viewpoints": dict(VIEWPOINTS),
    }


# 想定最大時間の既定（分）。2026-09-24 利用者の指示で 30 分（#754 の指針「60 分以内」の中に収める）。
DEFAULT_BUDGET_MINUTES = 30

# 改修計画へ渡す候補の上限（決定 17）。`path` + `symbol` の組を上位から 30 組、組の中は
# 上位 3 件まで。改修計画の入力と Jev の「同じ変更か」の問いの数（最大 90 回）を抑える。
CANDIDATE_GROUPS = 30
CANDIDATES_PER_GROUP = 3

# Jev の答えを使う確信度の下限（設計の「Jev の問い」の表）。下回ったら実装担当の答えを使う。
JEV_TIER_CONFIDENCE = 0.6
JEV_DUPLICATE_CONFIDENCE = 0.8
JEV_RISK_CONFIDENCE = 0.7

# 見送りの理由（#933 の AC9）。**この 8 つに限る。** 検証で取り消した項目は見送りではなく
# 項目の状態（`reverted`）で表す。
DEFER_BUDGET = "budget"
DEFER_RANK = "rank"
DEFER_DUPLICATE = "duplicate"
DEFER_VOCABULARY = "vocabulary"
DEFER_THRESHOLD = "threshold"
DEFER_NO_TARGET = "no_target"
DEFER_TEST_FAILED = "test_failed"
DEFER_NOT_DONE = "not_done"
DEFER_REASONS = (
    DEFER_BUDGET,
    DEFER_RANK,
    DEFER_DUPLICATE,
    DEFER_VOCABULARY,
    DEFER_THRESHOLD,
    DEFER_NO_TARGET,
    DEFER_TEST_FAILED,
    DEFER_NOT_DONE,
)

# 手順の名前（#933）。**状態・履歴・`launch-cli.sh`・`limits.py`・雛形で同じ語を使う。**
# `readopt` は検証の後・最終ゲートの前の採り直しの判定（#1743 決定 10）
PHASES = ("propose", "plan", "add-tests", "implement", "verify", "readopt", "final", "done")

# テストの追加・実装・修正のコミットに必須のトレーラー。1 つでも欠けたら当該項目を
# 失敗にする。自由文で「codex が実装」と書かせると集計に使えないため、必ずトレーラー
# 形式にする。**項目とコミットの対応は `Item-Id` だけで決める**（#933 の実装計画 I4）。
# ラウンドが無くなったため `Round` は求めない。
REQUIRED_TRAILERS = ("Item-Id", "Impl-Runtime", "Impl-Model")

# 最終ゲートの修正コミットに必須のトレーラー。**`Item-Id` は求めない。** 最終ゲートが
# 直すのは全体のテストの失敗であって、改善項目に属さない。書かせると、実在しない
# 項目番号を実装担当が作ることになる。
FINAL_FIX_TRAILERS = ("Impl-Runtime", "Impl-Model")

# 適用で必ず配置する Skill。ここに無いものは配らない。
REQUIRED_SKILLS = ("refactoring", "tdd-cycle", "quality-gates")

# 生成物を同期したコミットのメッセージ。**どの改善項目にも属さない**ことが分かる形にする。
SYNC_COMMIT_MESSAGE = (
    "Chore: 生成物を同期する（cross-refactoring 進行側）\n\n"
    "実装担当は対象範囲だけを変更するため、生成物が同期されない。\n"
    "同期をチェックする pre-push を持つリポジトリでも push できるよう、\n"
    "公開の直前に進行側がまとめて生成する。"
)

# 改修計画と生成物を 1 つのコミットへまとめたときのメッセージ。
SYNC_AND_PLAN_COMMIT_MESSAGE = (
    "Chore: 生成物と改修計画を同期する（cross-refactoring 進行側）\n\n"
    "実装担当は対象範囲だけを変更するため、生成物が同期されない。\n"
    "改修計画は提案の時点でしか残らないため、公開の直前に書き出す。\n"
    "どちらも進行側の責務なので、1 つのコミットにまとめる。"
)

# 改修計画だけを記録したコミットのメッセージ。
PLAN_COMMIT_MESSAGE = (
    "Docs: 改修計画を記録する（cross-refactoring 進行側）\n\n"
    "なぜ直すのか（理由）とどう直すのか（手順）は提案の時点でしか残らない。\n"
    "状態ファイルは差分から除外されるため、Pull Request から読める場所へ置く。"
)

# 改善項目の表示の状態（`ledger.display_status`）を、Pull Request を読む側に通じる語へ置き換える。
# `verified` は最終ゲートの結論で「採用」か「未確認」に分かれるため、呼び名を持たない（#1692 の I2）。
ITEM_STATUS_LABELS = {
    "planned": "未着手",
    "tested": "テストを追加済み",
    "implemented": "検証中",
    "failing": "修正中",
    "adopted": "採用",
    "unconfirmed": "未確認",
    "reverted": "取り消し",
    "deferred": "見送り",
}
