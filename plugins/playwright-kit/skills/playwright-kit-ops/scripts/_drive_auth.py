"""google-auth スキル経由で Drive API クレデンシャルを取得する共通ヘルパ。

3 つの uploader スクリプト (gdrive_upload_dir / build_gdoc_with_drive_links /
upload_md_as_gdoc) はいずれも同じ手順で `google_auth.get_credentials()` を
sys.path から発見する。本モジュールにロジックを集約する。

Drive 連携は optional dependency。`GOOGLE_AUTH_SCRIPTS` 環境変数が設定されて
いればそれを使い、それ以外は標準インストール先と sibling の google-auth
スキルを探す。google-auth はどの公開セットにも含めていないため、Drive 系
コマンドを使う場合は `GOOGLE_AUTH_SCRIPTS` を明示するか、同スキルを利用先へ
導入する。
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
        "~/.kiro/skills/google-auth/scripts",
        str(_HERE.parent.parent.parent / "google-auth" / "scripts"),
    )
    if p
)


def _ensure_google_auth_on_path() -> None:
    """`from google_auth import get_credentials` できるよう sys.path を整える。

    ディレクトリの存在だけで採用すると、`google_auth.py` を含まない別の
    `scripts/` を先に拾って後続の候補を見ないまま import に失敗する。
    実体の有無まで確かめてから sys.path へ入れる。
    """
    for p in _CANDIDATES:
        if (p / "google_auth.py").is_file():
            path = str(p)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    searched = "\n  - ".join(str(p) for p in _CANDIDATES)
    raise RuntimeError(
        "Google Drive 連携には optional skill `google-auth` が必要です。\n"
        "どの公開セットにも同梱していないため、Drive 系コマンドを使う前に "
        "`GOOGLE_AUTH_SCRIPTS` を google-auth/scripts へ設定してください。\n"
        "例: export GOOGLE_AUTH_SCRIPTS=<ai-plugins のパス>/plugins/ndf/optional-skills/google-auth/scripts\n"
        "google_auth.py を探した候補:\n  - "
        f"{searched}"
    )


def drive_service(scopes: list[str]):
    """認証済み Drive API v3 service を返す。"""
    _ensure_google_auth_on_path()
    from google_auth import get_credentials  # type: ignore
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=get_credentials(scopes))
