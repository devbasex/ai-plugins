"""hook のテストの前提。中継・オーガナイザー・通知・止め方の変数を、テストを流す人の値から切り離す。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.domains.hooks import reply_judge

from ._hooks import ENV_NAMES
from ._hooks import counted_tail, relay  # noqa: F401  fixture の登録（会話の記録の読み取りの回数・中継の作業ディレクトリ）


@pytest.fixture(autouse=True)
def _hook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# 詳細設計の「内部のデータと定数」の `hook.stop-wait.judge`（#4000）。`limits.json` に行が入ったら、この値と下の fixture を外す
JUDGE: dict[str, Any] = {
    "model": "haiku",
    "seconds": 5,
    "max_chars": 1600,
    "rest_seconds": 600,
    "lock_seconds": 0.2,
    "rounds": 3,
    "auth_markers": ["Not logged in", "Invalid API key"],
    "endpoint": {
        "entrypoints": ["cli", "sdk-cli"],
        "ancestors": 8,
        "cli_exe": ["*/claude/versions/*"],
        "script_hosts": ["node*"],
        "flags": ["--settings", "--setting-sources"],
        "keys": ["awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh"],
        "env_names": [
            "ANTHROPIC_",
            "CLAUDE_CODE_USE_",
            "CLAUDE_CONFIG_DIR",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_SCOPES",
            "AWS_",
            "GOOGLE_",
            "CLOUD_ML_",
            "VERTEX_",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
            "NODE_EXTRA_CA_CERTS",
        ],
    },
}


@pytest.fixture(autouse=True)
def _judge_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """判定の設定を読む口（`reply_judge._judge`）を差し替える。`limits.json` に `hook.stop-wait.judge` の行が入ったら外す。"""
    monkeypatch.setattr(reply_judge, "_judge", JUDGE.__getitem__)
