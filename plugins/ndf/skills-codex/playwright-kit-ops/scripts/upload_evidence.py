"""trace.zip / HAR / video / 任意ファイルを Google Drive にアップロードする統合 uploader。

bug report の証跡を Drive リンクで貼るためのスクリプト。

⚠️ trace.zip / HAR / video は DOM snapshot や入力痕跡、HTTP request body を含む。
既定では非公開アップロード。`--public` 明示時のみ anyone/read を付与する。
playwright.dev の trace viewer URL は `--public` のときだけ生成される
(viewer は GET で trace.zip を取りに行くため anyone/read 必須)。

Usage:
    upload_evidence.py reports/.../trace.zip --kind trace
    upload_evidence.py reports/.../trace.zip --kind trace --public
    upload_evidence.py reports/.../foo.har --kind har
    upload_evidence.py reports/.../foo.mp4 --kind video --parent <DRIVE_FOLDER_ID>
    upload_evidence.py reports/.../foo.zip --kind any
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote


# google-auth スキルの sibling-skill discovery
_CANDIDATES = (
    os.environ.get("GOOGLE_AUTH_SCRIPTS"),
    os.path.expanduser("~/.claude/skills/google-auth/scripts"),
    str(Path(__file__).resolve().parent.parent.parent / "google-auth" / "scripts"),
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
    ".zip": "trace",     # Playwright trace.zip
    ".har": "har",
    ".mp4": "video",
    ".webm": "video",
}


_MIME_BY_KIND: dict[str, str] = {
    "trace": "application/zip",
    "har": "application/json",
    "video": "video/mp4",  # detect_mime() で .webm を別 MIME に振り分ける
    "any": "application/octet-stream",
}

# codex Min-3: kind=video でも実体が .webm の場合は MIME を実体に合わせる
# (Drive 側の preview/処理系の誤判定を避ける)
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
    """ファイルを Drive にアップして metadata + (kind 別) 補助 URL を返す。

    Returns:
        {
          "file_id": str,
          "drive_view": str,             # webViewLink (Drive 上での閲覧 URL)
          "direct_download": str | None, # public=True のときだけ生成
          "playwright_trace_viewer": str | None, # kind=trace + public=True のときだけ
          "is_public": bool,
          "kind": str,
        }
    """
    # Min-7: Python API として呼ばれた場合の防御的検査 (CLI argparse は別途 choices)
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="trace/HAR/video/任意ファイルを Drive にアップロード",
    )
    parser.add_argument("file", type=Path, help="アップロードするファイル")
    parser.add_argument(
        "--kind", choices=["trace", "har", "video", "any"], default=None,
        help="evidence 種別 (省略時は拡張子から自動判定)",
    )
    parser.add_argument(
        "--parent-folder-id", default=None,
        help="Drive の親フォルダ ID (省略時はマイドライブ直下)",
    )
    parser.add_argument(
        "--public", action="store_true",
        help="anyone/read 公開リンクを付与する (既定は非公開、明示 opt-in)",
    )
    args = parser.parse_args()

    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    kind = args.kind or detect_kind(args.file)

    result = upload(
        args.file,
        kind=kind,
        parent_folder_id=args.parent_folder_id,
        public=args.public,
    )

    if not args.public:
        print(
            "[NOTE] 非公開でアップロード済 (既定)。trace/HAR/video には DOM snapshot や"
            "入力痕跡が含まれるため、安易な anyone/read は避けてください。",
            file=sys.stderr,
        )
        if kind == "trace":
            print(
                "       playwright.dev viewer URL は anyone/read 公開時のみ生成されます。"
                "チーム共有が必要なら Drive 上で個別共有するか、`--public` を付け直してください。",
                file=sys.stderr,
            )

    print(f"kind: {result['kind']}")
    print(f"file_id: {result['file_id']}")
    print(f"Drive: {result['drive_view']}")
    if result["direct_download"]:
        print(f"Direct: {result['direct_download']}")
    if result["playwright_trace_viewer"]:
        print()
        print("Playwright Trace Viewer URL (bug report に記載):")
        print(f"  {result['playwright_trace_viewer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
