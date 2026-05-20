"""Phase 3 unit: ``playwright_kit.pytest_report`` の純粋関数テスト。

pytest_terminal_summary 経由の集約は Playwright を要するため smoke では扱わず、
``render_markdown`` と ``write_report`` を直接呼んで Markdown 出力を検証する。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from playwright_kit.pytest_report import PwkTestEntry, render_markdown, write_report


def _entry(**overrides) -> PwkTestEntry:
    base = dict(
        nodeid="tests/test_x.py::test_y",
        name="test_y",
        outcome="passed",
        duration_s=0.42,
    )
    base.update(overrides)
    return PwkTestEntry(**base)  # type: ignore[arg-type]


def test_status_label_mapping():
    assert _entry(outcome="passed").status_label == "OK"
    assert _entry(outcome="failed").status_label == "FAIL"
    assert _entry(outcome="skipped").status_label == "SKIP"
    assert _entry(outcome="xfailed").status_label == "XFAIL"
    assert _entry(outcome="xpassed").status_label == "XPASS"
    assert _entry(outcome="error").status_label == "ERROR"


def test_render_markdown_all_pass():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 5)
    md = render_markdown(
        [
            _entry(nodeid="t::a"),
            _entry(nodeid="t::b", outcome="passed", duration_s=0.1),
        ],
        started_at=started,
        finished_at=finished,
        title="My Report",
        base_url="https://example.com",
    )
    assert "# My Report" in md
    assert "https://example.com" in md
    assert "2/2 test PASS" in md
    assert "全PASS" in md
    assert "`t::a`" in md
    assert "`t::b`" in md
    # FAIL section は出ない
    assert "FAIL / ERROR の詳細" not in md


def test_render_markdown_with_failure_includes_details():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 10)
    md = render_markdown(
        [
            _entry(nodeid="t::ok"),
            _entry(
                nodeid="t::fail",
                outcome="failed",
                error_message="AssertionError: expected 1, got 2",
                trace_path="/tmp/runs/x/trace.zip",
                har_path="/tmp/runs/x/request.har",
            ),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "1/2 test PASS" in md
    assert "FAIL 1" in md
    assert "FAIL / ERROR の詳細" in md
    assert "AssertionError: expected 1, got 2" in md
    assert "trace.zip" in md
    assert "request.har" in md


def test_render_markdown_sorts_by_phase_then_priority_then_nodeid():
    """phase / priority / nodeid 昇順で並ぶ。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [
            _entry(nodeid="t::z", phase=2, priority="low"),
            _entry(nodeid="t::a", phase=1, priority="high"),
            _entry(nodeid="t::b", phase=1, priority="high"),
        ],
        started_at=started,
        finished_at=finished,
    )
    # `t::a` が `t::b` より先に出る
    pos_a = md.index("`t::a`")
    pos_b = md.index("`t::b`")
    pos_z = md.index("`t::z`")
    assert pos_a < pos_b < pos_z


def test_render_markdown_page_role_and_role_columns():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [_entry(nodeid="t::x", role="admin", page_role=["form", "list"])],
        started_at=started,
        finished_at=finished,
    )
    assert "admin" in md
    assert "form,list" in md


def test_render_markdown_xfailed_counted_in_header():
    """xfailed / xpassed がヘッダ集計に出ること (Codex Major 3)。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 5)
    md = render_markdown(
        [
            _entry(nodeid="t::ok", outcome="passed"),
            _entry(nodeid="t::xf", outcome="xfailed"),
            _entry(nodeid="t::xp", outcome="xpassed"),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "XFAIL 1" in md
    assert "XPASS 1" in md
    # xpassed がある場合は全PASS にならない
    assert "全PASS" not in md


def test_render_markdown_xfailed_only_is_all_pass():
    """xfailed のみ (xpassed なし) は全PASS 扱い。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 5)
    md = render_markdown(
        [
            _entry(nodeid="t::ok", outcome="passed"),
            _entry(nodeid="t::xf", outcome="xfailed"),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "全PASS" in md
    assert "XFAIL 1" in md


def test_render_markdown_xfailed_not_in_failure_section():
    """xfailed は FAIL / ERROR の詳細セクションに出ない。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 5)
    md = render_markdown(
        [_entry(nodeid="t::xf", outcome="xfailed")],
        started_at=started,
        finished_at=finished,
    )
    assert "FAIL / ERROR の詳細" not in md


def test_write_report_produces_file(tmp_path: Path):
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    out = tmp_path / "out"
    path = write_report(
        [_entry()], out_dir=out, started_at=started, finished_at=finished
    )
    assert path == out / "report.md"
    assert path.exists()
    txt = path.read_text(encoding="utf-8")
    assert "1/1 test PASS" in txt


def test_render_markdown_body_check_column_present_with_zero():
    """body_check カラムは違反 0 件でもサマリ表に出る。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [_entry(nodeid="t::x")],
        started_at=started,
        finished_at=finished,
    )
    # 表ヘッダに body_check が含まれる
    assert "body_check" in md
    # サマリ表の row には末尾に "| 0 |" (body_check_violations) が出る
    assert "| 0 |" in md


def test_render_markdown_body_check_detail_section_for_violations():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [
            _entry(
                nodeid="t::pass_with_violations",
                outcome="passed",
                body_check_violations=2,
                body_check_detail=[
                    {
                        "url": "https://e/u.php",
                        "category": "warning",
                        "pattern": "STRICT:",
                        "snippet": "STRICT: page leak",
                    },
                    {
                        "url": "https://e/v.php",
                        "category": "fatal",
                        "pattern": "Fatal error",
                        "snippet": "Fatal error: oops",
                    },
                ],
            ),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "body_check 違反の詳細" in md
    assert "STRICT:" in md
    assert "Fatal error" in md
    assert "https://e/u.php" in md


def test_render_markdown_no_body_check_section_when_clean():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [_entry(nodeid="t::x")],
        started_at=started,
        finished_at=finished,
    )
    assert "body_check 違反の詳細" not in md


def test_render_markdown_body_check_escapes_newlines_in_snippet():
    """snippet に改行が混入しても表が崩れない (1 violation = 1 row)。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [
            _entry(
                nodeid="t::nl",
                outcome="failed",
                body_check_violations=1,
                body_check_detail=[
                    {
                        "url": "https://e/u.php",
                        "category": "fatal",
                        "pattern": "Fatal error",
                        "snippet": "line1\nline2\twith pipe | and `code`",
                    }
                ],
            ),
        ],
        started_at=started,
        finished_at=finished,
    )
    # detail セクションは存在する
    assert "body_check 違反の詳細" in md
    # 表の row には改行が混ざっていない (cell が複数行に割れない)
    detail_section = md.split("body_check 違反の詳細", 1)[1]
    table_rows = [
        line for line in detail_section.splitlines()
        if line.startswith("| 1 ")
    ]
    assert len(table_rows) == 1
    row = table_rows[0]
    assert "line1 line2 with pipe \\| and \\`code\\`" in row
    # row 内に改行や生 backtick が残らない
    assert "\n" not in row
    assert "`code`" not in row  # backtick is escaped


def test_render_markdown_body_check_escapes_pipe_in_pattern():
    """pattern に ``|`` が含まれても表が崩れない。"""
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    md = render_markdown(
        [
            _entry(
                nodeid="t::pipe",
                outcome="failed",
                body_check_violations=1,
                body_check_detail=[
                    {
                        "url": "https://e/x",
                        "category": "fatal",
                        "pattern": "Fatal | error",
                        "snippet": "ok",
                    }
                ],
            ),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "Fatal \\| error" in md


def test_render_markdown_body_check_truncates_at_20():
    started = _dt.datetime(2026, 4, 26, 12, 0, 0)
    finished = _dt.datetime(2026, 4, 26, 12, 0, 1)
    detail = [
        {
            "url": f"https://e/u{i}.php",
            "category": "warning",
            "pattern": "STRICT:",
            "snippet": f"hit {i}",
        }
        for i in range(25)
    ]
    md = render_markdown(
        [
            _entry(
                nodeid="t::many",
                outcome="failed",
                body_check_violations=25,
                body_check_detail=detail,
            ),
        ],
        started_at=started,
        finished_at=finished,
    )
    assert "body_check.jsonl" in md
    # 表示されない 21 件目以降のひとつ (u24) が出ていないこと
    assert "u24.php" not in md
    # 表示される 1 件目は出る
    assert "u0.php" in md
