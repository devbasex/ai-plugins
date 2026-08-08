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

import sys
from pathlib import Path
from urllib.parse import quote


_HERE = Path(__file__).resolve()
# Drive 認証の候補探索は scripts/_drive_auth.py を唯一の実装とする。
# パッケージ側からも同じ探索を使うため、skill 直下の scripts/ を sys.path へ入れて読む。
_SCRIPTS_DIR = _HERE.parent.parent.parent / "scripts"


def _drive_service(scopes: list[str]):
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    from _drive_auth import drive_service  # type: ignore  # noqa: E402
    return drive_service(scopes)


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

    from googleapiclient.http import MediaFileUpload  # noqa: E402

    service = _drive_service(["drive.file"])

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
