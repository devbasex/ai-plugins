"""monitor.py の progress.log heartbeat 補助関数テスト。"""


def test_tail_last_nonempty_line_returns_latest_marker(tmp_path, monitor_mod):
    log = tmp_path / "gemini-review-pr1-progress.log"
    log.write_text("start: review PR #1\n\nscan: diff\n  \npost: submit review\n")

    assert monitor_mod._tail_last_nonempty_line(log) == "post: submit review"


def test_tail_last_nonempty_line_handles_missing_file(tmp_path, monitor_mod):
    assert monitor_mod._tail_last_nonempty_line(tmp_path / "missing.log") == ""


def test_safe_size_handles_missing_file(tmp_path, monitor_mod):
    assert monitor_mod._safe_size(tmp_path / "missing.log") == 0
