"""pytest_terminal_summary が report.md を正しく生成することを
terminalreporter mock で検証する (Playwright 実機不要) (Codex Major 5)。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from playwright_kit.pytest_plugin import (
    _collect_entries,
    pytest_sessionfinish,
    pytest_terminal_summary,
)
from playwright_kit.pytest_report import PwkTestEntry


def _make_rep(
    nodeid: str = "tests/test_x.py::test_y",
    outcome: str = "passed",
    when: str = "call",
    duration: float = 0.5,
    user_properties: list | None = None,
    longrepr=None,
) -> Any:
    return SimpleNamespace(
        nodeid=nodeid,
        head_line=nodeid.split("::")[-1],
        outcome=outcome,
        when=when,
        duration=duration,
        user_properties=user_properties or [],
        longrepr=longrepr,
    )


def _make_terminalreporter(stats: dict, out_dir: Path | None = None) -> Any:
    tr = MagicMock()
    tr.stats = stats
    tr._sessionstarttime = _dt.datetime(2026, 4, 26, 12, 0, 0).timestamp()
    return tr


# ---------------------------------------------------------------------------
# _collect_entries のユニットテスト
# ---------------------------------------------------------------------------


def test_collect_entries_passed():
    tr = _make_terminalreporter({
        "passed": [_make_rep(nodeid="t::ok", outcome="passed")],
    })
    entries = _collect_entries(tr)
    assert len(entries) == 1
    assert entries[0].outcome == "passed"
    assert entries[0].nodeid == "t::ok"


def test_collect_entries_skips_teardown():
    """teardown phase の rep は集約しない。"""
    tr = _make_terminalreporter({
        "failed": [
            _make_rep(nodeid="t::fail", outcome="failed", when="call"),
            _make_rep(nodeid="t::teardown", outcome="failed", when="teardown"),
        ],
    })
    entries = _collect_entries(tr)
    assert len(entries) == 1
    assert entries[0].nodeid == "t::fail"


def test_collect_entries_includes_xfailed_xpassed():
    """xfailed / xpassed も集約されること (Codex Major 3)。"""
    tr = _make_terminalreporter({
        "passed": [_make_rep(nodeid="t::ok")],
        "xfailed": [_make_rep(nodeid="t::xf", outcome="xfailed")],
        "xpassed": [_make_rep(nodeid="t::xp", outcome="xpassed")],
    })
    entries = _collect_entries(tr)
    outcomes = {e.outcome for e in entries}
    assert "xfailed" in outcomes
    assert "xpassed" in outcomes


def test_collect_entries_promotes_teardown_failure_on_passed_call():
    """call=passed + teardown=failed (body_check fail) → outcome=failed に昇格。"""
    tr = _make_terminalreporter({
        "passed": [_make_rep(nodeid="t::bc", outcome="passed", when="call")],
        "": [
            _make_rep(
                nodeid="t::bc",
                outcome="failed",
                when="teardown",
                user_properties=[("pwk_body_check_violations", 2)],
                longrepr="body_check teardown failure",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert len(entries) == 1
    assert entries[0].outcome == "failed"
    assert entries[0].body_check_violations == 2
    assert entries[0].error_message == "body_check teardown failure"


def test_collect_entries_promotes_teardown_failure_on_xfailed_call():
    """call=xfailed + teardown=failed → outcome=failed (xfail で隠さない)。

    旧実装では ``entry.outcome == 'passed'`` 限定のため xfail テストの
    teardown failure を拾い損ねていた (codex review Major #2)。
    """
    tr = _make_terminalreporter({
        "xfailed": [_make_rep(nodeid="t::xf", outcome="xfailed", when="call")],
        "": [
            _make_rep(
                nodeid="t::xf",
                outcome="failed",
                when="teardown",
                user_properties=[("pwk_body_check_violations", 1)],
                longrepr="body_check teardown failure on xfail",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert entries[0].outcome == "failed"
    assert entries[0].body_check_violations == 1


def test_collect_entries_promotes_teardown_error_with_error_outcome():
    """teardown=error は outcome=error に昇格 (failed と区別する)。"""
    tr = _make_terminalreporter({
        "passed": [_make_rep(nodeid="t::e", outcome="passed", when="call")],
        "": [
            _make_rep(
                nodeid="t::e",
                outcome="error",
                when="teardown",
                user_properties=[("pwk_body_check_violations", 0)],
                longrepr="teardown error",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert entries[0].outcome == "error"


def test_collect_entries_promotes_teardown_failure_on_xpassed_call():
    """call=xpassed + teardown=failed → outcome=failed に昇格。"""
    tr = _make_terminalreporter({
        "xpassed": [_make_rep(nodeid="t::xp", outcome="xpassed", when="call")],
        "": [
            _make_rep(
                nodeid="t::xp",
                outcome="failed",
                when="teardown",
                user_properties=[("pwk_body_check_violations", 1)],
                longrepr="body_check teardown failure on xpass",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert entries[0].outcome == "failed"
    assert entries[0].body_check_violations == 1


def test_collect_entries_promotes_teardown_failure_on_skipped_call():
    """call=skipped + teardown=failed → outcome=failed に昇格。"""
    tr = _make_terminalreporter({
        "skipped": [_make_rep(nodeid="t::sk", outcome="skipped", when="call")],
        "": [
            _make_rep(
                nodeid="t::sk",
                outcome="failed",
                when="teardown",
                user_properties=[("pwk_body_check_violations", 3)],
                longrepr="body_check teardown failure on skip",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert entries[0].outcome == "failed"
    assert entries[0].body_check_violations == 3


def test_collect_entries_does_not_overwrite_genuine_call_failure():
    """call phase の本物の failure は teardown failure で上書きしない。"""
    tr = _make_terminalreporter({
        "failed": [
            _make_rep(
                nodeid="t::f",
                outcome="failed",
                when="call",
                longrepr="real call failure",
            )
        ],
        "": [
            _make_rep(
                nodeid="t::f",
                outcome="failed",
                when="teardown",
                longrepr="teardown also failed",
            )
        ],
    })
    entries = _collect_entries(tr)
    assert entries[0].outcome == "failed"
    # error_message は call phase のものが残る
    assert entries[0].error_message == "real call failure"


def test_collect_entries_error_message_only_for_failed(tmp_path: Path):
    """failed のみ error_message が設定され、skipped は None になること (Amazon Q Critical-3)。"""
    tr = _make_terminalreporter({
        "failed": [_make_rep(nodeid="t::fail", outcome="failed", longrepr="AssertionError: x")],
        "skipped": [_make_rep(nodeid="t::skip", outcome="skipped", longrepr=("file", 1, "skipped"))],
    })
    entries = _collect_entries(tr)
    failed_entry = next(e for e in entries if e.outcome == "failed")
    skipped_entry = next(e for e in entries if e.outcome == "skipped")
    assert failed_entry.error_message == "AssertionError: x"
    assert skipped_entry.error_message is None


# ---------------------------------------------------------------------------
# pytest_terminal_summary が report.md を生成するテスト
# ---------------------------------------------------------------------------


def test_terminal_summary_generates_report_md(tmp_path: Path):
    """pytest_terminal_summary が report.md を out_dir に生成すること。"""
    tr = _make_terminalreporter({
        "passed": [_make_rep(nodeid="t::ok", outcome="passed", duration=1.0)],
        "failed": [_make_rep(
            nodeid="t::fail",
            outcome="failed",
            duration=0.5,
            longrepr="AssertionError: expected True got False",
        )],
    })

    config = MagicMock()
    config.getoption.return_value = str(tmp_path)
    config._pwk_config = None

    pytest_terminal_summary(tr, exitstatus=1, config=config)

    report_path = tmp_path / "report.md"
    assert report_path.exists(), "report.md が生成されていない"

    content = report_path.read_text(encoding="utf-8")
    assert "t::ok" in content
    assert "t::fail" in content
    assert "AssertionError" in content


def test_terminal_summary_skips_when_no_tests(tmp_path: Path):
    """テストが 1 件も無い場合は report.md を生成しない。"""
    tr = _make_terminalreporter({})
    config = MagicMock()
    config.getoption.return_value = str(tmp_path)
    config._pwk_config = None

    pytest_terminal_summary(tr, exitstatus=0, config=config)

    assert not (tmp_path / "report.md").exists()


# ---------------------------------------------------------------------------
# pytest_sessionfinish の Drive upload 分岐 (codex review2 P3)
# ---------------------------------------------------------------------------


def _make_session(*, drive_folder: str | None, report_path: Path, out_dir: Path):
    """``pytest_sessionfinish`` 用の薄い session mock を作る。"""
    config = MagicMock()
    config.getoption = lambda name, default=None: (
        drive_folder if name == "pwk_drive_folder" else default
    )
    config._pwk_report_path = report_path
    config._pwk_out_dir = out_dir
    session = MagicMock()
    session.config = config
    return session


def test_sessionfinish_uploads_body_check_jsonl_with_any_kind(tmp_path: Path):
    """body_check.jsonl が ``kind="any"`` で upload される (codex review2 P3)。

    .zip / .har / .mp4 / .webm は ``detect_kind`` の結果、.jsonl は固定で ``any``
    として upload されることを mock 経由で検証する。
    """
    out_dir = tmp_path / "out"
    sub = out_dir / "case-x"
    sub.mkdir(parents=True)
    (sub / "trace.zip").write_bytes(b"")
    (sub / "request.har").write_bytes(b"")
    (sub / "video.mp4").write_bytes(b"")
    (sub / "body_check.jsonl").write_text("{}\n", encoding="utf-8")
    # 拾わない拡張子 (sanity)
    (sub / "ignore.txt").write_text("x", encoding="utf-8")

    report = out_dir / "report.md"
    report.write_text("# r", encoding="utf-8")

    uploads: list[tuple[str, str]] = []

    def fake_upload(f, *, kind, parent_folder_id, public):
        uploads.append((Path(f).name, kind))

    def fake_detect_kind(f):
        suffix = Path(f).suffix
        return {".zip": "trace", ".har": "har", ".mp4": "video", ".webm": "video"}.get(
            suffix, "any"
        )

    with patch.dict(
        "sys.modules",
        {
            "playwright_kit.uploaders": SimpleNamespace(
                upload=fake_upload, detect_kind=fake_detect_kind
            )
        },
    ):
        session = _make_session(
            drive_folder="DRIVE_FOLDER", report_path=report, out_dir=out_dir
        )
        pytest_sessionfinish(session, exitstatus=0)

    by_name = dict(uploads)
    # report.md は kind="any" でアップ (実装契約)
    assert by_name.get("report.md") == "any"
    # .jsonl は固定で kind="any"
    assert by_name.get("body_check.jsonl") == "any"
    # 既存の各種 evidence は detect_kind の結果が反映される
    assert by_name.get("trace.zip") == "trace"
    assert by_name.get("request.har") == "har"
    assert by_name.get("video.mp4") == "video"
    # 想定外拡張子は upload されない
    assert "ignore.txt" not in by_name


def test_sessionfinish_skips_when_no_drive_folder(tmp_path: Path):
    """``--pwk-drive-folder`` 未指定なら upload は走らない。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report = out_dir / "report.md"
    report.write_text("# r", encoding="utf-8")

    uploads: list[str] = []

    def fake_upload(*args, **kwargs):
        uploads.append("called")

    with patch.dict(
        "sys.modules",
        {
            "playwright_kit.uploaders": SimpleNamespace(
                upload=fake_upload, detect_kind=lambda f: "any"
            )
        },
    ):
        session = _make_session(
            drive_folder=None, report_path=report, out_dir=out_dir
        )
        pytest_sessionfinish(session, exitstatus=0)

    assert uploads == []
