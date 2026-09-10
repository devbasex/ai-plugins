"""PR files を分類用構造へ変換する 2 つの入口の境界。

TSV 側（`_parse_pr_files_api_lines`）と JSON 側（`_parse_pr_files_payload`）の、
どちらも正常系は `test_state_auto_review_templates.py` 系で通っている。ここで固定
するのは項目ごとの端の形で、TSV はページを跨いだ API 出力で欠けやすい形（空文字列・
空行のみ・列が欠けた行）、JSON は dict でない項目・パスの欠落・未知の changeType・
旧パスの別名キーである。件数と status が崩れない現状の振る舞いを記録する
（現状固定テスト。正しさを主張しない）。
"""
from __future__ import annotations


def test_an_empty_string_gives_no_entries(state_mod):
    """空文字列は 1 件も返さない。"""
    assert state_mod._parse_pr_files_api_lines("") == []


def test_blank_lines_only_give_no_entries(state_mod):
    """空行だけの入力は、空行が読み飛ばされ 1 件も残らない。"""
    assert state_mod._parse_pr_files_api_lines("\n  \n\t\n") == []


def test_a_status_only_line_is_dropped(state_mod):
    """status 列だけ（path が無い）の行は除かれる。"""
    assert state_mod._parse_pr_files_api_lines("modified") == []


def test_a_line_with_an_empty_path_is_dropped(state_mod):
    """path 列が空の行は除かれる。"""
    assert state_mod._parse_pr_files_api_lines("modified\t\t") == []


def test_a_line_without_a_previous_column_yields_one_path(state_mod):
    """previous 列が無い行は、paths が path 1 件になる。"""
    assert state_mod._parse_pr_files_api_lines("modified\tsrc/app.py") == [
        {"status": "M", "paths": ["src/app.py"]},
    ]


def test_a_previous_equal_to_path_yields_one_path(state_mod):
    """previous と path が同じ行は、paths が path 1 件になる。"""
    assert state_mod._parse_pr_files_api_lines(
        "renamed\tsrc/app.py\tsrc/app.py"
    ) == [
        {"status": "R", "paths": ["src/app.py"]},
    ]


def test_a_distinct_previous_yields_two_paths(state_mod):
    """previous が path と異なる行は、previous と path の 2 件になる。"""
    assert state_mod._parse_pr_files_api_lines(
        "renamed\tsrc/new.py\tsrc/old.py"
    ) == [
        {"status": "R", "paths": ["src/old.py", "src/new.py"]},
    ]


def test_blank_lines_between_entries_are_skipped(state_mod):
    """行の間に空行が挟まっても、実データの行だけが残る。"""
    assert state_mod._parse_pr_files_api_lines(
        "added\ta.py\n\nremoved\tb.py\n"
    ) == [
        {"status": "A", "paths": ["a.py"]},
        {"status": "D", "paths": ["b.py"]},
    ]


# ---- JSON 側（`gh pr view --json files` の fallback）の項目ごとの端 ----


def test_a_non_dict_entry_is_dropped_and_the_valid_one_remains(state_mod):
    """dict でない項目は除かれ、同じ一覧の正しい項目だけが残る。"""
    assert state_mod._parse_pr_files_payload(
        '{"files":["src/app.py",{"path":"src/app.py","changeType":"MODIFIED"}]}'
    ) == [
        {"status": "M", "paths": ["src/app.py"]},
    ]


def test_entries_without_a_usable_path_are_dropped(state_mod):
    """`path` キーが無い項目と、`path` が空文字列の項目はどちらも除かれる。"""
    assert state_mod._parse_pr_files_payload(
        '{"files":[{"changeType":"MODIFIED"},{"path":"","changeType":"ADDED"}]}'
    ) == []


def test_an_unknown_change_type_keeps_its_first_letter(state_mod):
    """対応表に無い `changeType` は、先頭 1 文字を大文字のまま status にする。"""
    assert state_mod._parse_pr_files_payload(
        '{"files":[{"path":"src/app.py","changeType":"WEIRD"}]}'
    ) == [
        {"status": "W", "paths": ["src/app.py"]},
    ]


def test_an_empty_or_missing_change_type_falls_back_to_modified(state_mod):
    """`changeType` が空文字列でも欠けていても、`MODIFIED` として `M` になる。"""
    assert state_mod._parse_pr_files_payload(
        '{"files":[{"path":"a.py","changeType":""},{"path":"b.py"}]}'
    ) == [
        {"status": "M", "paths": ["a.py"]},
        {"status": "M", "paths": ["b.py"]},
    ]


def test_the_snake_case_previous_filename_is_accepted(state_mod):
    """旧パスは `previousPath` の代わりに `previous_filename` でも受け取る。"""
    assert state_mod._parse_pr_files_payload(
        '{"files":[{"path":"new.py","previous_filename":"old.py","changeType":"RENAMED"}]}'
    ) == [
        {"status": "R", "paths": ["old.py", "new.py"]},
    ]
