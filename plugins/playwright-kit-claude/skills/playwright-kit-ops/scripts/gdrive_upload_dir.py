"""ローカルディレクトリツリーを再帰的に Google Drive へアップロードする。"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _drive_auth import drive_service  # noqa: E402

from googleapiclient.http import MediaFileUpload  # noqa: E402

SCOPES = ["drive.file", "drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"


def get_or_create_folder(service, name: str, parent_id: str) -> str:
    """parent 配下に同名フォルダがあればその ID、なければ作成して ID を返す。"""
    q = (
        f"'{parent_id}' in parents and name='{name}' "
        f"and mimeType='{FOLDER_MIME}' and trashed=false"
    )
    found = service.files().list(
        q=q, spaces="drive", fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    if found:
        return found[0]["id"]
    folder = service.files().create(
        body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute()
    print(f"  [folder] created: {name} -> {folder['id']}")
    return folder["id"]


def upload_file(service, path: Path, parent_id: str) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True)
    file = service.files().create(
        body={"name": path.name, "parents": [parent_id]},
        media_body=media, fields="id,name,size",
        supportsAllDrives=True,
    ).execute()
    print(f"  [file]   {path.name} ({path.stat().st_size:,} bytes) -> {file['id']}")
    return file["id"]


def upload_dir(service, local_dir: Path, drive_parent_id: str) -> None:
    for entry in sorted(local_dir.iterdir()):
        if entry.is_dir():
            sub_id = get_or_create_folder(service, entry.name, drive_parent_id)
            upload_dir(service, entry, sub_id)
        elif entry.is_file():
            upload_file(service, entry, drive_parent_id)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--local", required=True, type=Path)
    p.add_argument("--parent", required=True)
    args = p.parse_args()

    service = drive_service(SCOPES)
    print(f"Upload {args.local} -> drive folder {args.parent}")
    upload_dir(service, args.local, args.parent)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
