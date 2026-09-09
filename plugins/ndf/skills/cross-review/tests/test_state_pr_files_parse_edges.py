"""GitHub API の PR files を TSV から分類用構造へ変換する境界（`_parse_pr_files_api_lines`）。

正常系の TSV は `test_state_auto_review_templates.py` 系で通っているが、ページを跨いだ
API 出力で欠けやすい形（空文字列・空行のみ・列が欠けた行）は固定されていない。件数が
崩れない現状の振る舞いを記録する（現状固定テスト。正しさを主張しない）。

`gh pr view --json files` の JSON を変換する境界（`_parse_pr_files_payload`）も同じ
ファイルで扱う。こちらは正常系だけが固定されており、`gh` の出力が想定外のとき
（壊れた JSON・`files` が list でない・要素が dict でない）に一覧を空にして進行を
止めない経路が残っている。
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


# ---- `gh pr view --json files` の JSON を変換する境界 ----


def test_a_broken_json_gives_no_entries(state_mod):
    """JSON として閉じていない文字列は、例外を投げず 1 件も返さない。"""
    assert state_mod._parse_pr_files_payload('{"files": [{"path": "src/app.py"') == []


def test_an_empty_payload_gives_no_entries(state_mod):
    """空文字列も同じ経路（JSONDecodeError）を通り、1 件も返さない。"""
    assert state_mod._parse_pr_files_payload("") == []


def test_a_payload_without_a_files_key_gives_no_entries(state_mod):
    """`files` キーを持たない JSON は 1 件も返さない。"""
    assert state_mod._parse_pr_files_payload('{"pullRequest": {"number": 1}}') == []


def test_a_non_list_files_value_gives_no_entries(state_mod):
    """`files` が list でない JSON は 1 件も返さない。"""
    assert state_mod._parse_pr_files_payload('{"files": {"path": "src/app.py"}}') == []


def test_non_dict_file_entries_are_dropped(state_mod):
    """`files` の要素のうち dict でないものだけが除かれる。"""
    assert state_mod._parse_pr_files_payload(
        '{"files": ["src/app.py", null, 42,'
        ' {"path": "src/app.py", "changeType": "MODIFIED"}]}'
    ) == [
        {"status": "M", "paths": ["src/app.py"]},
    ]


def test_a_file_entry_without_a_path_is_dropped(state_mod):
    """`path` を持たない要素だけが除かれ、残りはそのまま返る。"""
    assert state_mod._parse_pr_files_payload(
        '{"files": [{"changeType": "MODIFIED"},'
        ' {"path": "src/app.py", "changeType": "ADDED"}]}'
    ) == [
        {"status": "A", "paths": ["src/app.py"]},
    ]
