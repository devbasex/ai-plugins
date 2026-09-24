"""兆候・手法・重要度の呼び名と、判断の基準になる定数。

差分予算・コミット数の上限・テストの上限秒数など、工程の判断に使う値も持つ。
"""
from __future__ import annotations

import pathlib
import re

from typing import Any


# 兆候と手法の呼び名は `refactoring` が持つ（#444）。**ここでは読むだけで、自分では
# 持たない。** 2 か所にあると片方だけが更新され、枠組みの出力と方法論の説明が食い違う。
#
# **読めなければ止める。** 呼び名が揃わないと重複排除が効かず、同じ提案が別物として残る。
# 確認は `init` の時点で行う（`commands/setup.py`）。

class VocabularyUnavailable(RuntimeError):
    """呼び名の表を読めないことを表す。**握りつぶさない。**"""


# 呼び名の表の位置。**現在地には依存させない。** `uv run --script` で起動したときの
# 現在地は、スクリプトの位置と揃わない。
VOCABULARY_TABLE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "refactoring" / "references" / "vocabulary.md"
)


def _read_table(heading: str, value_column: str) -> dict[str, str]:
    """呼び名の表の 1 節を、識別子 → 値で返す。

    **列は見出しの名前で決める。** 並びが変わっても読み取りが壊れない。
    """
    try:
        text = VOCABULARY_TABLE.read_text(encoding="utf-8")
    except OSError as exc:                       # 読めない = 呼び名が無い
        raise VocabularyUnavailable(
            f"呼び名の表を読めません: {VOCABULARY_TABLE} ({exc})"
        ) from exc
    body = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not body:
        raise VocabularyUnavailable(f"呼び名の表に「{heading}」の節がありません")
    rows = [ln for ln in body.group(1).splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        raise VocabularyUnavailable(f"呼び名の表の「{heading}」が空です")
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    try:
        i_id, i_value = header.index("識別子"), header.index(value_column)
    except ValueError as exc:
        raise VocabularyUnavailable(
            f"呼び名の表の「{heading}」に列がありません: {exc}"
        ) from exc
    out: dict[str, str] = {}
    for line in rows[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        out[cells[i_id].strip("`")] = cells[i_value]
    return out


SMELLS: dict[str, str] = _read_table("兆候", "日本語の名前")

TECHNIQUES: dict[str, str] = _read_table("手法", "日本語の名前")

# 提案の観点（#933 の AC5）。**「多面的に」を観点の一覧として示す。** 一覧が無いと、
# 参加者は目についた兆候に偏る（#917 では `long_method` が 10 件）。観点は兆候の語彙を
# 探す入口であって、語彙そのものではない（提案の `smell` は兆候の語彙から選ぶ）。
VIEWPOINTS: dict[str, str] = {
    "duplication": "重複 — 同じ知識・同じ手順が 2 か所以上にある",
    "mixed_responsibility": "責務の混在 — 1 つの関数・クラスが別々の理由で変わる",
    "branching": "分岐の表し方 — 同じ条件の分岐が散らばる・種類ごとの分岐が伸び続ける",
    "naming": "名前 — 名前が中身と食い違う・同じものを別の名前で呼ぶ",
    "dependency_direction": "依存の向き — 下の層が上の層を読む・循環する・知りすぎる",
    "testability": "テストの書きにくさ — 外部への依存や隠れた状態のせいで単体で試せない",
    "data_shape": "データの形 — 基本型の羅列・いつも一緒に渡る引数の組",
    "size": "大きさ — 長すぎる関数・大きすぎるクラス・長い引数の列",
}

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

# 計画へ渡す候補の上限（決定 17）。`path` + `symbol` の組を上位から 30 組、組の中は
# 上位 3 件まで。計画の入力と Jev の「同じ変更か」の問いの数（最大 90 回）を抑える。
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
    DEFER_BUDGET, DEFER_RANK, DEFER_DUPLICATE, DEFER_VOCABULARY, DEFER_THRESHOLD,
    DEFER_NO_TARGET, DEFER_TEST_FAILED, DEFER_NOT_DONE,
)

# フェーズの名前（#933）。**状態・履歴・`launch-cli.sh`・`limits.py`・雛形で同じ語を使う。**
PHASES = ("propose", "plan", "add-tests", "implement", "verify", "final", "done")

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
    "同期を検査する pre-push を持つリポジトリでも push できるよう、\n"
    "公開の直前に進行側がまとめて生成する。"
)

# 計画と生成物を 1 つのコミットへまとめたときのメッセージ。
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

# 改善項目の状態を、Pull Request を読む側に通じる語へ置き換える。
ITEM_STATUS_LABELS = {
    "planned": "未着手",
    "tested": "テストを追加済み",
    "implemented": "検証中",
    "failing": "修正中",
    "verified": "採用",
    "reverted": "取り消し",
    "deferred": "見送り",
}

# 実差分行数が見積りのこの倍数を超えたら範囲の逸脱とみなす。
DIFF_BUDGET_FACTOR = 2

# 新しい定義を作って呼び出し側を書き換える手法は、**見積より実差分が膨らむ**。
# 抽出した本体に加えて、呼び出し側の書き換え・import の追加・引数の受け渡しが
# 固定費として乗るためで、提案の時点では見えにくい。
#
# 実測で予算超過として落ちた 4 件はいずれも `long_method` の抽出で、見積の
# 2.03〜2.31 倍に収まっていた（4 回目: 265/120 行・183/90 行、5 回目: 277/120 行・
# 113/50 行）。範囲の逸脱ではなく、倍率 2 の予算をわずかに超えただけである。
# 一方、範囲外の 3 系統を触った実測例は見積の 4 倍まで膨らんだので、倍率を 3 へ
# 上げても逸脱は取り逃がさない。
# 倍率 3 の手法は呼び名の表が持つ（#444）。**ここでは読むだけである。**
EXTRACTION_DIFF_BUDGET_FACTOR = 3

EXTRACTION_TECHNIQUES: frozenset[str] = frozenset(
    name
    for name, factor in _read_table("手法ごとの差分予算の倍率", "倍率").items()
    if factor == str(EXTRACTION_DIFF_BUDGET_FACTOR)
)

