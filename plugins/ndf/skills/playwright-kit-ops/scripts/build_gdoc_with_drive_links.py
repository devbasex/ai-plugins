"""report.md の相対リンクを Google Drive URL に置換し、Google Docs として再アップロードする。

事前に対象ディレクトリを Drive にアップロード済みである前提。
このスクリプトは:
  1. Drive 上の <run-id> フォルダから {相対パス: file_id} mapping を構築
  2. report.md 中の `(./TC-XX/foo.ext)` 形式リンクを Drive URL に書き換え
  3. text/markdown としてアップロードし mimeType=Google Docs 指定で自動変換
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _drive_auth import drive_service  # noqa: E402

from googleapiclient.http import MediaFileUpload  # noqa: E402

SCOPES = ["drive.file", "drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
LINK_PATTERN = re.compile(r"\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)")


def list_folder_files(service, folder_id: str, prefix: str = "") -> dict[str, str]:
    """folder_id 配下のファイルを再帰的に列挙し、{相対パス: file_id} を返す。"""
    out: dict[str, str] = {}
    page_token: str | None = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType)",
            pageSize=200, pageToken=page_token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        for f in resp.get("files", []):
            rel = f"{prefix}/{f['name']}".lstrip("/")
            if f["mimeType"] == FOLDER_MIME:
                out.update(list_folder_files(service, f["id"], rel))
            else:
                out[rel] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def find_run_folder_id(service, parent_id: str, run_id: str) -> str:
    """parent 配下の run_id 名フォルダの ID を返す。なければ例外。"""
    files = service.files().list(
        q=(
            f"'{parent_id}' in parents and name='{run_id}' "
            f"and mimeType='{FOLDER_MIME}' and trashed=false"
        ),
        fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    if not files:
        raise SystemExit(f"ERROR: run-id folder '{run_id}' not found under {parent_id}")
    return files[0]["id"]


def _drive_url_for(rel: str, fid: str) -> str:
    # PNG は uc?id (画像直接表示)、その他 (動画/zip/etc) は file/d/<id>/view
    if rel.endswith(".png"):
        return f"https://drive.google.com/uc?id={fid}"
    return f"https://drive.google.com/file/d/{fid}/view"


def rewrite_links(md: str, mapping: dict[str, str]) -> tuple[str, int]:
    """`(./TC-XX/foo.ext)` 形式リンクを Drive URL に置換し、(新md, 置換件数) を返す。"""
    replaced = 0

    def rep(m: re.Match[str]) -> str:
        nonlocal replaced
        rel = m.group(1)
        fid = mapping.get(rel)
        if fid is None:
            return m.group(0)  # 未マップは原文のまま
        replaced += 1
        return f"({_drive_url_for(rel, fid)})"

    return LINK_PATTERN.sub(rep, md), replaced


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md", required=True, type=Path)
    p.add_argument("--folder", required=True,
                   help="Drive folder containing the run-id subfolder")
    p.add_argument("--run-id", required=True,
                   help="Run id subfolder name (= local report dir name)")
    p.add_argument("--name", required=True)
    args = p.parse_args()

    service = drive_service(SCOPES)
    run_folder_id = find_run_folder_id(service, args.folder, args.run_id)
    print(f"run folder: {run_folder_id}")

    mapping = list_folder_files(service, run_folder_id)
    print(f"Indexed {len(mapping)} files")

    md_new, replaced = rewrite_links(args.md.read_text(encoding="utf-8"), mapping)
    print(f"Replaced links: {replaced} matches")

    tmp_md = Path("/tmp/report_with_drive_links.md")
    tmp_md.write_text(md_new, encoding="utf-8")

    media = MediaFileUpload(str(tmp_md), mimetype="text/markdown", resumable=True)
    file = service.files().create(
        body={"name": args.name, "mimeType": DOC_MIME, "parents": [args.folder]},
        media_body=media,
        fields="id,name,webViewLink,mimeType",
        supportsAllDrives=True,
    ).execute()
    print(f"OK: created {file['name']} ({file['mimeType']})")
    print(f"     id: {file['id']}")
    print(f"     url: {file['webViewLink']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
