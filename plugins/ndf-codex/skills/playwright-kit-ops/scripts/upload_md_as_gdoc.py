"""Markdown ファイルを Google Drive にアップロードし、Google Docs に自動変換する。"""

from __future__ import annotations

import argparse
import os
import sys

# このスクリプトを `python scripts/upload_md_as_gdoc.py` で直接実行できるように
# 自身のディレクトリを sys.path に追加 (`_drive_auth` を import するため)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _drive_auth import drive_service  # noqa: E402

from googleapiclient.http import MediaFileUpload  # noqa: E402

SCOPES = ["drive.file"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md", required=True, help="Local markdown file")
    p.add_argument("--parent", required=True, help="Drive parent folder ID")
    p.add_argument("--name", default=None, help="Doc name (default: basename)")
    args = p.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.md))[0]
    service = drive_service(SCOPES)

    media = MediaFileUpload(args.md, mimetype="text/markdown", resumable=True)
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [args.parent],
    }
    file = service.files().create(
        body=meta, media_body=media,
        fields="id,name,webViewLink,mimeType",
        supportsAllDrives=True,
    ).execute()
    print(f"OK: created {file['name']} ({file['mimeType']})")
    print(f"     id: {file['id']}")
    print(f"     url: {file['webViewLink']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
