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

# テスト整備ラウンドの語彙。**新しい語彙は作らない**（#436 決定 9）。値は既存の
# 3 本の参照が持つ分類をそのまま使う。閉じるのは 2 つだけで、17 種の兆候に当たる
# ものは要らない。テスト整備の提案の中身は「どの経路が固定されていないか」で
# あって、悪さの分類ではない。

# 固定する経路の種類。出所は `refactoring/references/characterization-tests.md` の
# 「分岐を洗い出して固定する」の表である。
TEST_CASES: dict[str, str] = {
    "normal": "代表的な正常系",
    "branch": "各分岐に入る入力",
    "boundary": "境界値（0 件・空・上限）",
    "error": "例外・エラーになる入力",
}

# どの階層で固定するか。出所は `tdd-cycle/references/testing-levels.md` である。
# **並びは階層の低い順**で、同じ経路に複数の階層が挙がったときは低い方を採る
# （「上の階層へ持ち上げない」）。
TEST_LEVELS: dict[str, str] = {
    "unit": "単体",
    "integration": "結合",
    "contract": "契約",
    "e2e": "端から端まで",
}


def test_vocabulary() -> dict[str, Any]:
    """テスト整備の提案プロンプトへ**そのまま列挙する**ための語彙集合。

    構造改善の `vocabulary()` と同じ形にする。手順書を読ませるだけでは足りず、
    許容値を列挙しないと語彙外の値が返って全件降格する（実測）。
    """
    return {
        "cases": dict(TEST_CASES),
        "levels": dict(TEST_LEVELS),
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
    }


# 適用と修正のコミットに必須のトレーラー。1 つでも欠けたら当該項目を失敗にする。
# 自由文で「codex が実装」と書かせると集計に使えないため、必ずトレーラー形式にする。
REQUIRED_TRAILERS = ("Item-Id", "Round", "Impl-Runtime", "Impl-Model")

# 最終ゲート（Step 7）の修正コミットに必須のトレーラー。**`Item-Id` と `Round` は
# 求めない。** 最終ゲートが直すのは全体のテストの失敗であって、改善項目にも提案
# ラウンドにも属さない。書かせると、実在しない項目番号を実装担当が作ることになる。
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
    "pending": "未着手",
    "applied": "検証中",
    "done": "採用",
    "abandoned": "取り消し",
    "blocked": "着手せず",
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

# 1 改善項目が履歴に残せるコミット数。
#
# **手順を 1 手ずつ進めることと、その途中経過を履歴に残すことは別である。**
# 手ごとにテストを回して安全に進めるのは変わらないが、残すのは項目単位の
# 1 コミットだけにする。刻んだままだと Pull Request を読む側が改善項目と履歴を
# 1 対 1 で辿れず、取り消しと積み直しのコミットも件数に比例して増える
# （実測: 採用 12 件に対して適用 34 コミット、取り消しと積み直しで 25 コミット）。
MAX_COMMITS_PER_ITEM = 1

# 現状固定テストが要る項目だけは 2 コミットを許す。テストと実装を 1 コミットへ
# 混ぜると、「テストを先に足した」ことを履歴から確かめられなくなる。
MAX_COMMITS_PER_ITEM_WITH_TEST_GAP = 2

# テスト 1 回あたりの上限（秒）。生成されたコードやテストが無限ループに入ると、
# 待ち続けて**進行全体が止まる**。打ち切って失敗として扱う。
DEFAULT_TEST_TIMEOUT = 900

# 提案の重複率がこの割合を超えたら、提案ラウンドの繰り返しを収束とみなす。
DUPLICATE_RATE_THRESHOLD = 0.7

# テスト整備ラウンドの既定の上限。**構造改善より少ない。** 母集合の増え方が違う
# ためである。構造を変えると新しい兆候が見えて提案の母集合は増えるが、テストが
# 薄い経路の集合は対象のコードを変えないため最初から確定している。2 回目に出るのは
# 1 回目の挙げ漏らしだけである。**上限は歯止めであって、回数の指定ではない。**
DEFAULT_MAX_TEST_ROUNDS = 2

# レビュー結果の形式不正で差し戻せる回数。超えたら変更要求として扱う。
# 差し戻しを無限に繰り返すと、形式を満たせないランタイムでループが止まらなくなる。
MAX_INVALID_REVIEWS = 1
