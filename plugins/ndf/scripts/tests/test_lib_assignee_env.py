"""担当の起動の環境と振り替え先のアカウントの選び方（#919 の AC4・I9）。"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assignee_env  # noqa: E402
import claude_accounts as ca  # noqa: E402
from account_fake import accounts  # noqa: E402,F401


def test_seats_without_an_account_keep_the_base_environment():
    base = {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    assert assignee_env.seat_env("codex", "work2", base) == base
    assert assignee_env.seat_env("claude", None, base) == base
    assert assignee_env.seat_env("claude", None, base) is not base


def test_an_account_seat_gets_only_the_account_environment(accounts):  # noqa: F811
    accounts.add("work2")
    base = {"PATH": "/bin", ca.TOKEN_ENV: "secret", ca.SCOPES_ENV: "user:inference"}
    env = assignee_env.seat_env("claude-2", "work2", base)
    assert env[ca.CONFIG_ENV] == ca.account_dir("work2")
    assert ca.TOKEN_ENV not in env and ca.SCOPES_ENV not in env


def test_pick_account_skips_tried_accounts_and_returns_only_a_name(accounts):  # noqa: F811
    accounts.add("a", util5=10, util7=10)
    accounts.add("b", util5=20, util7=20)
    assert assignee_env.pick_account(frozenset({"a"})) == "b"
    assert assignee_env.pick_account(frozenset({"a", "b"})) is None


def test_pick_account_without_registered_accounts_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path / "none"))
    assert assignee_env.pick_account(frozenset()) is None


def test_initial_account_reads_the_launch_environment():
    assert assignee_env.initial_account({"NDF_CLAUDE_ACCOUNT": "w1"}) == "w1"
    assert assignee_env.initial_account({}) is None
