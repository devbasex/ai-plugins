"""現状固定（I-002: check_rows の見出しの有無と列の対応を決める処理を切り出す前に、表ごとの読み方を記録する）。"""

from __future__ import annotations

from ndf.core.plan.inputs_decide import CHECKS_HEAD, check_rows

HEAD = ("条件", "区分", "手段")


def _body(*tables: str) -> str:
    return "## 受け入れ条件の確認\n\n" + "\n\n".join(tables) + "\n"


def test_current_no_section_no_table_or_short_first_table_is_empty():
    assert check_rows("本文だけ", CHECKS_HEAD) == []
    assert check_rows("## 受け入れ条件の確認\n\n説明だけ\n", CHECKS_HEAD) == []
    # 最初の表が 1 行だけ（見出しのみ）
    assert check_rows(_body("| 条件 | 区分 |"), CHECKS_HEAD) == []
    # 囲みの中の表は表として数えない
    fenced = "## 受け入れ条件の確認\n\n```md\n| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |\n```\n"
    assert check_rows(fenced, CHECKS_HEAD) == []


def test_current_first_table_missing_a_head_column_is_empty_even_with_good_later_tables():
    first = "| 条件 | 手段 |\n| --- | --- |\n| 1 | a |"
    later = "| 条件 | 区分 |\n| --- | --- |\n| 2 | マージ前 |"
    assert check_rows(_body(first, later), CHECKS_HEAD) == []


def test_current_first_table_reorders_columns_and_skips_unreadable_rows():
    first = "\n".join(
        [
            "| 手段 | 区分 | 条件 |",
            "| --- | --- | --- |",
            "| a | マージ前 | 1 |",
            "| b | リリース後 | 2. 補足 |",
            "| c | マージ前 | 番号なし |",
            "| 短い |",
            "| d | 未定 | 3a |",
            "| e | マージ前 | 1 |",
        ]
    )
    assert check_rows(_body(first), HEAD) == [
        (1, ["1", "マージ前", "a"]),
        (2, ["2. 補足", "リリース後", "b"]),
        (3, ["3a", "未定", "d"]),
        # 最初の表の中の重複はそのまま返す
        (1, ["1", "マージ前", "e"]),
    ]


def test_current_first_table_second_row_is_dropped_even_if_not_a_rule():
    # 最初の表は区切りの有無を見ずに 2 行目を区切りとして飛ばす
    first = "| 条件 | 区分 |\n| 1 | マージ前 |\n| 2 | リリース後 |"
    assert check_rows(_body(first), CHECKS_HEAD) == [(2, ["2", "リリース後"])]


def test_current_later_headed_table_uses_its_own_column_order():
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |"
    later = "| 区分 | メモ | 条件 |\n| :--- | --- | ---: |\n| リリース後 | m | 2 |\n| マージ前 | n | 1 |"
    # 番号 1 は先頭の表で有効な記録があるので後続の表から除く
    assert check_rows(_body(first, later), CHECKS_HEAD) == [(1, ["1", "マージ前"]), (2, ["2", "リリース後"])]


def test_current_later_headed_table_missing_a_head_column_is_skipped():
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |"
    other = "| 項目 | 値 |\n| --- | --- |\n| 2 | x |"
    last = "| 3 | リリース後 |"
    assert check_rows(_body(first, other, last), CHECKS_HEAD) == [(1, ["1", "マージ前"]), (3, ["3", "リリース後"])]


def test_current_later_headless_table_uses_first_table_columns():
    first = "| 区分 | 条件 |\n| --- | --- |\n| マージ前 | 1 |"
    headless = "| リリース後 | 2 |\n| マージ前 | 1 |\n| 短 |\n| マージ前 | x |"
    assert check_rows(_body(first, headless), CHECKS_HEAD) == [(1, ["1", "マージ前"]), (2, ["2", "リリース後"])]


def test_current_rule_without_leading_pipe_stays_in_the_same_table():
    # 区切りの行が先頭の `|` を省いても、見出しとデータ行は同じ表（#3886 の r7-1）
    body = "## 受け入れ条件の確認\n\n| 区分 | 条件 |\n--- | ---\n| リリース後 | 1 |\n"
    assert check_rows(body, CHECKS_HEAD) == [(1, ["1", "リリース後"])]
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |"
    later = "| 区分 | 条件 |\n--- | ---\n| リリース後 | 2 |"
    assert check_rows(_body(first, later), CHECKS_HEAD) == [(1, ["1", "マージ前"]), (2, ["2", "リリース後"])]


def test_current_headless_table_with_rule_of_different_width_is_not_headed():
    # 見出しの無い表のデータ行の直後の区切り（列数が違う）は見出しとみなさない（#3886 の r8-1）
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |"
    later = "| 2 | リリース後 |\n| --- | --- | --- |\n| 3 | マージ前 |"
    assert check_rows(_body(first, later), CHECKS_HEAD) == [
        (1, ["1", "マージ前"]),
        (2, ["2", "リリース後"]),
        (3, ["3", "マージ前"]),
    ]


def test_current_headless_table_with_rule_of_same_width_is_read_as_headed_and_skipped():
    # 列数が同じ区切りが 2 行目にあると、1 行目を見出しとみなし、見出しに head が無いので表ごと飛ばす
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |"
    later = "| 2 | リリース後 |\n| --- | --- |\n| 3 | マージ前 |"
    assert check_rows(_body(first, later), CHECKS_HEAD) == [(1, ["1", "マージ前"])]


def test_current_later_tables_skip_only_numbers_with_a_valid_earlier_record():
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | 未定 |\n| 2 | マージ前 |"
    second = "| 1 | リリース後 |\n| 2 | リリース後 |\n| 3 | 未定 |"
    third = "| 1 | マージ前 |\n| 3 | マージ前 |\n| 4 | マージ前 |"
    valid = lambda row: row[1] in ("マージ前", "リリース後")  # noqa: E731
    assert check_rows(_body(first, second, third), CHECKS_HEAD, valid) == [
        (1, ["1", "未定"]),
        (2, ["2", "マージ前"]),
        # 1 は先頭の表の行が無効なので引き継ぐ。2 は有効な記録があるので除く
        (1, ["1", "リリース後"]),
        (3, ["3", "未定"]),
        # 1 は 2 つ目の表で有効になったので除く。3 は無効のままなので引き継ぐ
        (3, ["3", "マージ前"]),
        (4, ["4", "マージ前"]),
    ]


def test_current_default_valid_treats_every_earlier_row_as_a_record():
    first = "| 条件 | 区分 |\n| --- | --- |\n| 1 | 未定 |"
    later = "| 1 | マージ前 |\n| 2 | マージ前 |\n| 2 | リリース後 |"
    # 同じ後続の表の中の重複は、その表を読み終えるまで seen に入らないので両方残る
    assert check_rows(_body(first, later), CHECKS_HEAD) == [
        (1, ["1", "未定"]),
        (2, ["2", "マージ前"]),
        (2, ["2", "リリース後"]),
    ]


def test_current_tables_after_the_next_section_are_not_read():
    body = _body("| 条件 | 区分 |\n| --- | --- |\n| 1 | マージ前 |") + "\n## 次\n\n| 2 | リリース後 |\n"
    assert check_rows(body, CHECKS_HEAD) == [(1, ["1", "マージ前"])]
