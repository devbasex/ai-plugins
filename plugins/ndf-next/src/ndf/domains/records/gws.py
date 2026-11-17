"""gws（Google Workspace の CLI）の認証を確かめる口（#2067）。

`gws auth status` の JSON を読み、`decide` で決める。トークンの値は読まず、あるかだけを見る。未認証は 2 つの形だけ:
`credential_source` が `none` のときと、クライアントの設定だけがありトークンが無い（`credential_source` が
`client_secret.json` で `auth_method` が `none`）とき。OAuth でログインした状態（`client_secret.json` と `oauth2`）は
認証済み。トークンを環境変数で渡した状態は `auth_method: none` を返しても認証済み。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf.contracts import limits

# 状態の応答が必ず持つキー（gws auth status が成功したときの形）。どれかが無ければ読めない応答として止まる
STATUS_KEYS = ("credential_source", "auth_method", "client_config_exists")


class GwsError(RuntimeError):
    """gws を打てない・結果を読めない。`stderr` は gws の標準エラー（起動できない・打ち切ったときは空文字）。"""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def auth_status(limit: str = "gws.auth-status") -> dict[str, Any]:
    """`gws auth status` の JSON（上限は設定 `limit` の秒数）。打てない・失敗した・状態のキーがそろわなければ `GwsError`。"""
    try:
        done = subprocess.run(["gws", "auth", "status"], capture_output=True, text=True, timeout=limits.seconds(limit), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GwsError(f"gws auth status を打てない: {exc}") from exc
    # 失敗した応答の JSON を認証の判定へ渡さない
    if done.returncode != 0:
        raise GwsError(f"gws auth status が終了コード {done.returncode} で終わった", done.stderr)
    try:
        raw = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise GwsError(f"gws auth status を読めない: {exc}", done.stderr) from exc
    if not isinstance(raw, dict) or any(k not in raw for k in STATUS_KEYS):
        raise GwsError(f"gws auth status の出力に {' / '.join(STATUS_KEYS)} のどれかが無い", done.stderr)
    return raw


def public_keys(status: Mapping[str, Any], keys: Sequence[str] = STATUS_KEYS) -> dict[str, Any]:
    """認証の状態から公開するキーだけを取り出す（ほかのキーは結果へ写さない）。"""
    return {key: status[key] for key in keys}


def decide(status: Mapping[str, Any], token_in_env: bool) -> bool:
    """`gws auth status` の結果から認証済みかを決める（純粋な判断）。"""
    if token_in_env:
        return True
    source = status.get("credential_source")
    if source == "none":
        return False
    # クライアントの設定だけがありトークンが無い形（#2067）。OAuth でログインすれば auth_method が none でなくなる
    return not (source == "client_secret.json" and status.get("auth_method") == "none")


def authenticated(status: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> bool:
    """認証済みか。トークンを環境変数で渡していれば認証済み。"""
    where = os.environ if env is None else env
    # トークンを渡す環境変数（表 env の行）。値は読まず、あるかだけを見る
    return decide(status, bool(where.get("GOOGLE_WORKSPACE_CLI_TOKEN")))
