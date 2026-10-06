"""監視の理由つきの致命（#1290 の AC9・I8）。

#461 と #1589 の実物の行を err.log に置き、監視が `model_unavailable` / `auth_expired` を付けて止めることと、
差分・表・リストの行は拾わないことを確かめる。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import monitor_outcome  # noqa: E402
import monitor_scan  # noqa: E402
import monitor_types  # noqa: E402

# #461（PR #460）
WARN_404 = (
    "warning: Falling back from WebSockets to HTTPS transport. unexpected status 404 Not Found: "
    "The model `gpt-5.5` does not exist or you do not have access to it."
)
ERROR_404 = (
    "ERROR: unexpected status 404 Not Found: The model `gpt-5.5` does not exist or you do not have access to it., "
    "url: https://chatgpt.com/backend-api/codex/responses"
)
# #461（PR #661）
REFRESH_401 = (
    'ERROR codex_login::auth::manager: Failed to refresh token: 401 Unauthorized: {"error": '
    '{"message": "Your session has ended. Please log in again.", "code": "refresh_token_invalidated"}}'
)
REVOKED = "ERROR: Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in again."
# #1589（PR #1588）
ERROR_400 = (
    'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":'
    "\"The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.\"}}"
)


def _paths(tmp_path, err="", stdout="", agent="codex"):
    err_log = tmp_path / f"{agent}-err.log"
    err_log.write_text(err, encoding="utf-8")
    out_log = tmp_path / f"{agent}-stdout.log"
    out_log.write_text(stdout, encoding="utf-8")
    return monitor_types.AgentPaths(
        agent=agent,
        pr=1,
        pidfile=tmp_path / f"{agent}.pid",
        err_log=err_log,
        stdout_log=out_log,
        progress_log=tmp_path / f"{agent}-progress.log",
        result=tmp_path / f"{agent}-result.json",
    )


@pytest.mark.parametrize(
    "lines, reason",
    [
        ([WARN_404, ERROR_404], "model_unavailable"),
        ([REFRESH_401, REVOKED], "auth_expired"),
        (["OpenAI Codex v0.157.1", "model: gpt-6.1-sol", ERROR_400], "model_unavailable"),
    ],
)
def test_real_lines_stop_the_agent_with_a_reason(tmp_path, lines, reason):
    fatal, _ = monitor_scan._early_error(_paths(tmp_path, "\n".join(lines) + "\n"), "codex", disabled=False)
    assert fatal is not None and (fatal.source, fatal.reason) == ("err.log", reason)
    assert monitor_scan._scan_early_fatal(tmp_path / "codex-err.log") is not None


def test_claude_stdout_404_is_model_unavailable(tmp_path):
    out = '{"type":"result","is_error":true,"api_error_status":404,"result":"model not found"}\n'
    fatal, _ = monitor_scan._early_error(_paths(tmp_path, stdout=out, agent="claude"), "claude", disabled=False)
    assert fatal is not None and (fatal.source, fatal.reason) == ("stdout.log", "model_unavailable")


def test_usage_limit_stays_first(tmp_path):
    err = "ERROR: You've hit your usage limit.\n" + REVOKED + "\n"
    fatal, _ = monitor_scan._early_error(_paths(tmp_path, err), "codex", disabled=False)
    assert fatal.reason == "usage_limit"


@pytest.mark.parametrize(
    "line",
    [
        "+    " + REVOKED,
        "| `auth_expired` | " + REVOKED + " |",
        "- " + ERROR_404,
        '    assert "' + REVOKED + '"',
        "> " + ERROR_400,
    ],
)
def test_quoted_diff_table_and_list_lines_are_not_fatal(tmp_path, line):
    fatal, _ = monitor_scan._early_error(_paths(tmp_path, line + "\n"), "codex", disabled=False)
    assert fatal is None


@pytest.mark.parametrize("reason", ["model_unavailable", "auth_expired"])
def test_the_monitor_reason_wins_over_the_result_file(reason):
    """監視が付けた 2 つの理由は、結果ファイルの状態で `missing` に畳まれない。"""
    assert reason in monitor_outcome.REASONS
    assert reason in monitor_outcome._MONITOR_DECIDED_REASONS
