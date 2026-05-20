"""google-auth スキル経由で Drive API クレデンシャルを取得する共通ヘルパ。

3 つの uploader スクリプト (gdrive_upload_dir / build_gdoc_with_drive_links /
upload_md_as_gdoc) はいずれも同じ手順で `google_auth.get_credentials()` を
sys.path から発見する。本モジュールにロジックを集約する。

`GOOGLE_AUTH_SCRIPTS` 環境変数が設定されていればそれを使い、それ以外は
~/.claude/skills/google-auth/scripts → 並列の google-auth スキル の順で探す。
"""

from __future__ import annotations

import os
import sys


_CANDIDATES: tuple[str | None, ...] = (
    os.environ.get("GOOGLE_AUTH_SCRIPTS"),
    os.path.expanduser("~/.claude/skills/google-auth/scripts"),
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "google-auth", "scripts",
    ),
)


def _ensure_google_auth_on_path() -> None:
    """`from google_auth import get_credentials` できるよう sys.path を整える。"""
    for p in _CANDIDATES:
        if p and os.path.isdir(p):
            if p not in sys.path:
                sys.path.insert(0, p)
            return
    raise RuntimeError(
        "google_auth スキルの scripts/ が見つかりません。"
        "GOOGLE_AUTH_SCRIPTS 環境変数で明示してください。"
    )


def drive_service(scopes: list[str]):
    """認証済み Drive API v3 service を返す。"""
    _ensure_google_auth_on_path()
    from google_auth import get_credentials  # type: ignore
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=get_credentials(scopes))
