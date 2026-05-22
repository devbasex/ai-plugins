"""monitor.py `_scan_early_fatal()` の誤検知防止テスト。

回帰: SKILL.md / docs/*.md 内の以下のような行は **fatal 検知してはいけない**。
- Markdown 表セル (`| ... | quota exceeded ... |`)
- backtick で囲まれた quote (`` `quota exceeded` ``)
- 日本語クォートで囲まれた quote (`「quota exceeded」`)
codex がレビュー対象の doc を echo した際に上記が err.log に書かれることで、
従来の monitor.py は無関係なドキュメント記述を fatal とみなしてプロセスを kill していた。
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
