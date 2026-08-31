"""report.md の証跡の位置を Google Drive URL に置換し、Google Docs として再アップロードする。

事前に対象ディレクトリを Drive にアップロード済みである前提。
このスクリプトは:
  1. Drive 上の <run-id> フォルダから {相対パス: file_id} mapping を構築
  2. report.md 中の証跡の位置を Drive URL に書き換え。対象はコード表記
     (`<パス>`) とリンク記法 ([文言](<パス>)) の 2 つで、mapping に載っている
     位置だけを書き換える。case ディレクトリ名は限定しない
  3. text/markdown としてアップロードし mimeType=Google Docs 指定で自動変換
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SCOPES = ["drive.file", "drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
# 証跡の位置は report.md に 2 通りの書き方で載る。
#   コード表記 `<パス>`  … pytest_report.py が出す形 (- trace: `<パス>`)
#   リンク記法 [文言](<パス>)
# 前後にバッククォートが続く並び (``x``) は、置換すると入れ子のリンクになって
# 読めなくなるため対象にしない。
EVIDENCE_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)|\(([^()\s]+)\)")


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


def _lookup(candidate: str, mapping: dict[str, str]) -> tuple[str, str] | None:
    """候補の末尾と一致する mapping のキーを探し、(相対パス, file_id) を返す。

    report.md は評価環境の root からの絶対パスを書くのに対し、mapping のキーは
    run-id フォルダからの相対パス。先頭の要素を 1 つずつ落としながら突き合わせ、
    最初に一致した = 最も長いキーを採る。区切りの境界を無視した部分一致は採らない。
    """
    if "://" in candidate:  # 外部 URL は証跡ではない
        return None
    rel = candidate[2:] if candidate.startswith("./") else candidate
    rel = rel.lstrip("/")
    parts = rel.split("/")
    for i in range(len(parts)):
        key = "/".join(parts[i:])
        fid = mapping.get(key)
        if fid is not None:
            return key, fid
    return None


def rewrite_links(md: str, mapping: dict[str, str]) -> tuple[str, int]:
    """証跡の位置を Drive URL に置換し、(新md, 置換件数) を返す。

    mapping に載っている位置だけを書き換える。載っていない文字列は原文のまま残す。
    コード表記はリンク記法へ変え、リンクの文言には report.md が書いた位置を使う。
    """
    replaced = 0

    def rep(m: re.Match[str]) -> str:
        nonlocal replaced
        code_span, link_target = m.group(1), m.group(2)
        candidate = code_span if code_span is not None else link_target
        hit = _lookup(candidate, mapping)
        if hit is None:
            return m.group(0)  # 未マップは原文のまま
        rel, fid = hit
        replaced += 1
        url = _drive_url_for(rel, fid)
        if code_span is not None:
            return f"[{code_span}]({url})"
        return f"({url})"

    return EVIDENCE_PATTERN.sub(rep, md), replaced


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md", required=True, type=Path)
    p.add_argument("--folder", required=True,
                   help="Drive folder containing the run-id subfolder")
    p.add_argument("--run-id", required=True,
                   help="Run id subfolder name (= local report dir name)")
    p.add_argument("--name", required=True)
    args = p.parse_args()

    # Drive 連携は optional dependency。rewrite_links を単体で読み込めるよう、
    # 依存の解決は実行時に行う (scripts/upload_evidence.py と同じ扱い)。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _drive_auth import drive_service  # noqa: E402
    from googleapiclient.http import MediaFileUpload  # noqa: E402

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
