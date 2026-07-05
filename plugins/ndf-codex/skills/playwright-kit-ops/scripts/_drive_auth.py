"""google-auth スキル経由で Drive API クレデンシャルを取得する共通ヘルパ。

3 つの uploader スクリプト (gdrive_upload_dir / build_gdoc_with_drive_links /
upload_md_as_gdoc) はいずれも同じ手順で `google_auth.get_credentials()` を
sys.path から発見する。本モジュールにロジックを集約する。

Drive 連携は optional dependency。`GOOGLE_AUTH_SCRIPTS` 環境変数が設定されて
いればそれを使い、それ以外は標準インストール先と sibling の google-auth
スキルを探す。Codex 公開セットには google-auth を含めないため、Codex で
Drive 系コマンドを使う場合は `GOOGLE_AUTH_SCRIPTS` を明示する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
_CANDIDATES: tuple[Path, ...] = tuple(
    Path(p).expanduser()
    for p in (
        os.environ.get("GOOGLE_AUTH_SCRIPTS"),
        "~/.claude/skills/google-auth/scripts",
        "~/.codex/skills/google-auth/scripts",
        str(_HERE.parent.parent.parent / "google-auth" / "scripts"),
    )
    if p
)


def _ensure_google_auth_on_path() -> None:
    """`from google_auth import get_credentials` できるよう sys.path を整える。"""
    for p in _CANDIDATES:
        if p.is_dir():
            path = str(p)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    searched = "\n  - ".join(str(p) for p in _CANDIDATES)
    raise RuntimeError(
        "Google Drive 連携には optional skill `google-auth` が必要です。\n"
        "Codex 公開セットには同梱していないため、Drive 系コマンドを使う前に "
        "`GOOGLE_AUTH_SCRIPTS` を google-auth/scripts へ設定してください。\n"
        "例: export GOOGLE_AUTH_SCRIPTS=/path/to/plugins/ndf/skills/google-auth/scripts\n"
        "検索した候補:\n  - "
        f"{searched}"
    )


def drive_service(scopes: list[str]):
    """認証済み Drive API v3 service を返す。"""
    _ensure_google_auth_on_path()
    from google_auth import get_credentials  # type: ignore
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=get_credentials(scopes))
