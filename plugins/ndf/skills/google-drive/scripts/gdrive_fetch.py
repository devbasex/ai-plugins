"""Google Drive ファイル取得スクリプト

Usage:
    # Google Docをテキストでエクスポート
    python3 gdrive_fetch.py --id 1vwDAZJLlLjtjFFITB23qTF8ldWBgRZg8MPUO4kUt-Ys

    # HTML形式でエクスポート
    python3 gdrive_fetch.py --id FILE_ID --mime text/html --output /tmp/doc.html

    # PDF形式でエクスポート
    python3 gdrive_fetch.py --id FILE_ID --mime application/pdf --output /tmp/doc.pdf

    # バイナリファイルをダウンロード（画像、PDF等）
    python3 gdrive_fetch.py --id FILE_ID --download --output /tmp/file.png

    # ファイルをアップロード（公開共有リンク付き）
    python3 gdrive_fetch.py --upload /path/to/file.png
"""

import argparse
import io
import os
import sys
from pathlib import Path

# google-auth スキルの get_credentials() を使う。
# 環境変数 GOOGLE_AUTH_SCRIPTS で google-auth/scripts のパスを指定可能。
# 未指定の場合は次の候補を順に探す:
#   1. ~/.claude/skills/google-auth/scripts (uttarov 互換)
#   2. ../../google-auth/scripts (ndf プラグイン内の隣接スキル)
_CANDIDATES = (
    os.environ.get("GOOGLE_AUTH_SCRIPTS"),
    os.path.expanduser("~/.claude/skills/google-auth/scripts"),
    str(Path(__file__).resolve().parent.parent.parent / "google-auth" / "scripts"),
)
for _p in _CANDIDATES:
    if _p and os.path.isdir(_p):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
from google_auth import get_credentials  # type: ignore  # noqa: E402

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload  # noqa: E402


SCOPES_READONLY = ['drive.readonly']
SCOPES_FILE = ['drive.file']


def export_doc(file_id, mime_type='text/plain', output=None, port=None):
    """Google Docs/Sheets/Slidesをエクスポート"""
    creds = get_credentials(SCOPES_READONLY, port=port) if port else get_credentials(SCOPES_READONLY)
    service = build('drive', 'v3', credentials=creds)
    content = service.files().export(fileId=file_id, mimeType=mime_type).execute()

    if output is None:
        output = '/tmp/gdoc_export.txt'

    with open(output, 'wb') as f:
        f.write(content)
    print(f'OK: exported {len(content)} bytes to {output}')


def download_file(file_id, output, port=None):
    """バイナリファイルをダウンロード"""
    creds = get_credentials(SCOPES_READONLY, port=port) if port else get_credentials(SCOPES_READONLY)
    service = build('drive', 'v3', credentials=creds)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f'  Download {int(status.progress() * 100)}%')
    with open(output, 'wb') as f:
        f.write(fh.getvalue())
    print(f'OK: downloaded {len(fh.getvalue())} bytes to {output}')


def upload_file(filepath, public=True, port=None):
    """ファイルをGoogle Driveにアップロード"""
    creds = get_credentials(SCOPES_FILE, port=port) if port else get_credentials(SCOPES_FILE)
    service = build('drive', 'v3', credentials=creds)

    filename = os.path.basename(filepath)
    file_metadata = {'name': filename}
    media = MediaFileUpload(filepath)
    file = service.files().create(
        body=file_metadata, media_body=media, fields='id,webViewLink'
    ).execute()

    file_id = file['id']
    if public:
        service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()

    direct_url = f'https://drive.google.com/uc?export=view&id={file_id}'
    print(f'File ID: {file_id}')
    print(f'View: {file.get("webViewLink", "N/A")}')
    print(f'Direct URL: {direct_url}')


def main():
    parser = argparse.ArgumentParser(description='Google Drive ファイル操作')
    parser.add_argument('--id', help='Google Drive ファイルID')
    parser.add_argument('--mime', default='text/plain',
                        help='エクスポート形式 (text/plain, text/html, application/pdf)')
    parser.add_argument('--output', '-o', help='出力ファイルパス')
    parser.add_argument('--download', action='store_true',
                        help='バイナリダウンロードモード')
    parser.add_argument('--upload', metavar='FILE', help='アップロードするファイルパス')
    parser.add_argument('--port', type=int, default=None,
                        help='OAuth認証ポート（初回認証時のローカルコールバック用）')
    args = parser.parse_args()

    if args.upload:
        upload_file(args.upload, port=args.port)
        return

    if not args.id:
        parser.print_help()
        return

    if args.download:
        output = args.output or '/tmp/gdrive_download'
        download_file(args.id, output, port=args.port)
    else:
        export_doc(args.id, args.mime, args.output, port=args.port)


if __name__ == '__main__':
    main()
