"""gws（Google Workspace の CLI）の認証を確かめる口（#2067）。

`gws auth status` の JSON を読み、`decide` で決める。トークンの値は読まず、あるかだけを見る。クライアントの設定だけがあり
トークンが無い状態（`credential_source` が `none` か `client_secret.json`）は未認証。トークンを環境変数で渡した状態は
`auth_method: none` を返しても認証済み。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from typing import Any, Optional

from ndf.contracts import limits


class GwsError(RuntimeError):
    """gws を打てない・結果を読めない。"""


def auth_status() -> dict[str, Any]:
    """`gws auth status` の JSON。打てない・読めなければ `GwsError`。"""
    try:
        done = subprocess.run(
            ["gws", "auth", "status"], capture_output=True, text=True, timeout=limits.seconds("gws.auth-status"), check=False
        )
        raw = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise GwsError(f"gws auth status を読めない: {exc}") from exc
    if not isinstance(raw, dict):
        raise GwsError("gws auth status が object を返さなかった")
    return raw


def decide(status: Mapping[str, Any], token_in_env: bool) -> bool:
    """`gws auth status` の結果から認証済みかを決める（純粋な判断）。"""
    if token_in_env:
        return True
    # クライアントの設定だけでトークンの無い認証の出所（gws auth status の credential_source の値）
    return status.get("credential_source") not in ("none", "client_secret.json")


def authenticated(status: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> bool:
    """認証済みか。トークンを環境変数で渡していれば認証済み。"""
    where = os.environ if env is None else env
    # トークンを渡す環境変数（表 env の行）。値は読まず、あるかだけを見る
    return decide(status, bool(where.get("GOOGLE_WORKSPACE_CLI_TOKEN")))
