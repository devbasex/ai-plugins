"""Slack の通知の設定の探索（`ndf.adapters.notify.slack_settings`）。"""

from __future__ import annotations

from pathlib import Path

from ndf.adapters import notify


# ---- slack_settings ----


def test_settings_read_env_file_upward_and_environment_wins(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=from-file\nSLACK_CHANNEL_ID=C-file\nSLACK_USER_MENTION=<@U1>\n", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    got = notify.slack_settings(deep, None, {"SLACK_CHANNEL_ID": "C-env"})
    assert got == notify.SlackSettings(token="from-file", channel="C-env", mention="<@U1>", api_base=notify.SLACK_API_BASE)


def test_settings_stop_at_git_top_and_use_fallback(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=outside\nSLACK_CHANNEL_ID=C\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert notify.slack_settings(repo, None, {}) is None
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / ".env").write_text("SLACK_BOT_TOKEN=plugin\nSLACK_CHANNEL_ID=C2\n", encoding="utf-8")
    got = notify.slack_settings(repo, plugin, {"NDF_SLACK_API_BASE": "https://slack.test/"})
    assert (got.token, got.channel, got.api_base) == ("plugin", "C2", "https://slack.test")


def test_settings_ignore_api_base_in_env_file(tmp_path: Path) -> None:
    # 通知の cwd の .env で、環境変数のトークンの送り先を替えさせない
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("SLACK_CHANNEL_ID=C\nNDF_SLACK_API_BASE=https://evil.test\n", encoding="utf-8")
    got = notify.slack_settings(tmp_path, None, {"SLACK_BOT_TOKEN": "xoxb-env"})
    assert (got.token, got.api_base) == ("xoxb-env", notify.SLACK_API_BASE)
