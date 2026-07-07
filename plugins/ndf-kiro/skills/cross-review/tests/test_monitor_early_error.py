"""monitor.py `_scan_early_fatal()` の誤検知防止テスト。

回帰: SKILL.md / docs/*.md 内の以下のような行は **fatal 検知してはいけない**。
- Markdown 表セル (`| ... | quota exceeded ... |`)
- backtick で囲まれた quote (`` `quota exceeded` ``)
- 日本語クォートで囲まれた quote (`「quota exceeded」`)
- ダブル/シングルクォートで囲まれた **文字列リテラル** (`"quota exceeded: ..."`)
- grep / ripgrep 形式の **ソース引用行** (`path/to/file.py:22:    "quota exceeded..."`)
codex がレビュー対象の doc / テストコードを echo した際に上記が err.log に書かれることで、
従来の monitor.py は無関係なドキュメント・コード記述を fatal とみなしてプロセスを kill していた。
"""
from __future__ import annotations

import pathlib


def _write(p: pathlib.Path, content: str) -> pathlib.Path:
    p.write_text(content, encoding="utf-8")
    return p


def test_real_quota_exceeded_is_detected(tmp_path, monitor_mod):
    """行頭の本物 `quota exceeded` は依然として fatal 扱い。"""
    log = _write(tmp_path / "err.log", "quota exceeded: please upgrade\n")
    assert monitor_mod._scan_early_fatal(log) is not None


def test_markdown_table_row_is_benign(tmp_path, monitor_mod):
    """Markdown 表セル行内のキーワードは doc 引用として無視。"""
    log = _write(
        tmp_path / "err.log",
        "| early-error | `^Error:` / 「quota exceeded」「sandbox error」を含む行 |\n",
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_backtick_quoted_keyword_is_benign(tmp_path, monitor_mod):
    """backtick 引用内のキーワードは doc 引用として無視。"""
    log = _write(
        tmp_path / "err.log",
        "explanation of `quota exceeded` pattern handling here\n",
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_japanese_quote_wrapped_keyword_is_benign(tmp_path, monitor_mod):
    """日本語「」内のキーワードは doc 引用として無視。"""
    log = _write(
        tmp_path / "err.log",
        "「quota exceeded」を含む行を検出 (diff/doc 引用文中の同語句は誤検知しない)\n",
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_sandbox_error_in_table_is_benign(tmp_path, monitor_mod):
    """表セル内の `sandbox error` も無視。"""
    log = _write(
        tmp_path / "err.log",
        "| pattern | codex 固有: 「sandbox error」を含む行を検出 |\n",
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_real_sandbox_error_still_detected(tmp_path, monitor_mod):
    """素の `sandbox error` は依然として fatal 扱い。"""
    log = _write(tmp_path / "err.log", "Internal sandbox error: cannot start\n")
    assert monitor_mod._scan_early_fatal(log) is not None


def test_grep_style_source_citation_is_benign(tmp_path, monitor_mod):
    """codex がレビュー対象のテストコードを grep 形式 (`path.py:22:    code`) で
    echo した行は fatal 扱いしない。

    回帰: 本 skill 自身の tests/test_monitor_early_error.py を cross-review した際、
    `quota exceeded` を含むテスト用文字列リテラル行が echo され、誤って
    EARLY_ERROR で codex が kill された (PR #23 round 2 で実際に発生)。
    """
    log = _write(
        tmp_path / "err.log",
        '/work/worktrees/pr23/plugins/ndf-shared/skills/cross-review/tests/'
        'test_monitor_early_error.py:22:    log = _write(tmp_path / "err.log", '
        '"quota exceeded: please upgrade\\n")\n',
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_double_quoted_string_literal_is_benign(tmp_path, monitor_mod):
    """ダブルクォート文字列リテラル内のキーワードは benign（コード片の echo）。"""
    log = _write(
        tmp_path / "err.log",
        '    raise RuntimeError("quota exceeded: please upgrade")\n',
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_single_quoted_string_literal_is_benign(tmp_path, monitor_mod):
    """シングルクォート文字列リテラル内のキーワードも benign。"""
    log = _write(
        tmp_path / "err.log",
        "    msg = 'sandbox error occurred in test fixture'\n",
    )
    assert monitor_mod._scan_early_fatal(log) is None


def test_match_is_quoted_double_quote(monitor_mod):
    """`_match_is_quoted()` がダブルクォート文字列リテラルを検出する。"""
    line = 'raise RuntimeError("quota exceeded: please upgrade")'
    start = line.index("quota")
    end = start + len("quota exceeded")
    assert monitor_mod._match_is_quoted(line, start, end)


def test_match_is_quoted_helper(monitor_mod):
    """`_match_is_quoted()` 単体テスト。"""
    line_backtick = "see `quota exceeded` doc"
    start = line_backtick.index("quota")
    end = start + len("quota exceeded")
    assert monitor_mod._match_is_quoted(line_backtick, start, end)

    line_jp = "「quota exceeded」と書かれた行"
    start = line_jp.index("quota")
    end = start + len("quota exceeded")
    assert monitor_mod._match_is_quoted(line_jp, start, end)

    line_raw = "quota exceeded happened"
    assert not monitor_mod._match_is_quoted(line_raw, 0, len("quota exceeded"))
