"""Drive アップロード機能を playwright_kit パッケージから直接 import するためのラッパー。

scripts/upload_evidence.py の CLI スタンドアロン用途 (利用者が
``python upload_evidence.py ...`` で叩く) を壊さずに、pytest_sessionfinish から
安全に import できるようにする (Amazon Q Critical-5: sys.path 廃止)。

使い方 (pytest_plugin.py から):
    from playwright_kit.uploaders import upload, detect_kind

この module は google-auth スキルが存在しない環境でも import できる。
実際のアップロード時のみ google-auth を必要とする (遅延 import)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote


# google-auth スキルの sibling-skill discovery (upload_evidence.py と同一ロジック)
_CANDIDATES = (
    os.environ.get("GOOGLE_AUTH_SCRIPTS"),
    os.path.expanduser("~/.claude/skills/google-auth/scripts"),
    str(Path(__file__).resolve().parent.parent.parent / "scripts"),
    str(Path(__file__).resolve().parent.parent.parent.parent / "google-auth" / "scripts"),
)


def _ensure_google_auth_on_path() -> None:
    for p in _CANDIDATES:
        if p and os.path.isdir(p):
            if p not in sys.path:
                sys.path.insert(0, p)
            return
    raise RuntimeError(
        "google-auth スキルが見つかりません。GOOGLE_AUTH_SCRIPTS env で明示してください。"
    )


# 拡張子 → kind の自動判定
_EXT_KIND: dict[str, str] = {
    ".zip": "trace",
    ".har": "har",
    ".mp4": "video",
    ".webm": "video",
}

_MIME_BY_KIND: dict[str, str] = {
    "trace": "application/zip",
    "har": "application/json",
    "video": "video/mp4",
    "any": "application/octet-stream",
}

_MIME_BY_EXT: dict[str, str] = {
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".har": "application/json",
    ".zip": "application/zip",
}

ALLOWED_KINDS: frozenset[str] = frozenset(_MIME_BY_KIND)


def detect_kind(path: Path) -> str:
    """拡張子から evidence kind を自動判定する。"""
    return _EXT_KIND.get(path.suffix.lower(), "any")


def detect_mime(path: Path, kind: str) -> str:
    """拡張子優先で MIME を決定し、未知拡張子は kind の既定値にフォールバック。"""
    return _MIME_BY_EXT.get(
        path.suffix.lower(),
        _MIME_BY_KIND.get(kind, "application/octet-stream"),
    )


def upload(
    file_path: Path,
    *,
    kind: str = "any",
    parent_folder_id: str | None = None,
    public: bool = False,
) -> dict:
    """ファイルを Drive にアップして metadata + 補助 URL を返す。

    ⚠️ trace.zip / HAR / video には DOM snapshot や入力痕跡・HTTP request body が含まれる。
    既定では非公開アップロード。``public=True`` のときだけ anyone/read を付与する。
    ``parent_folder_id`` には **private folder** の ID を指定し、
    共有相手を信頼できるメンバーに限定してください (Amazon Q Critical-5 / Codex Minor 8)。
    """
    if kind not in ALLOWED_KINDS:
        raise ValueError(
            f"未対応の kind: {kind!r} (allowed: {sorted(ALLOWED_KINDS)})"
        )

    _ensure_google_auth_on_path()
    from google_auth import get_credentials  # type: ignore  # noqa: E402
    from googleapiclient.discovery import build  # noqa: E402
    from googleapiclient.http import MediaFileUpload  # noqa: E402

    creds = get_credentials(["drive.file"])
    service = build("drive", "v3", credentials=creds)

    metadata: dict = {"name": file_path.name}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]
    media = MediaFileUpload(
        str(file_path), mimetype=detect_mime(file_path, kind),
    )
    f = service.files().create(
        body=metadata, media_body=media, fields="id,webViewLink",
    ).execute()
    file_id = f["id"]

    if public:
        service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"},
        ).execute()

    direct_url: str | None = None
    viewer_url: str | None = None
    if public:
        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        if kind == "trace":
            viewer_url = (
                f"https://trace.playwright.dev/?trace={quote(direct_url, safe='')}"
            )

    return {
        "file_id": file_id,
        "drive_view": f.get("webViewLink"),
        "direct_download": direct_url,
        "playwright_trace_viewer": viewer_url,
        "is_public": public,
        "kind": kind,
    }
