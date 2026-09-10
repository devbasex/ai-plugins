"""GitHub API の PR files を TSV から分類用構造へ変換する境界（`_parse_pr_files_api_lines`）。

正常系の TSV は `test_state_auto_review_templates.py` 系で通っているが、ページを跨いだ
API 出力で欠けやすい形（空文字列・空行のみ・列が欠けた行）は固定されていない。件数が
崩れない現状の振る舞いを記録する（現状固定テスト。正しさを主張しない）。
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
