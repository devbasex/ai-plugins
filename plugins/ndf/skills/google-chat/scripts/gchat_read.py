"""Google Chat API メッセージ読み取りスクリプト

Usage:
    # デフォルトSpace IDでメッセージ取得
    python3 gchat_read.py

    # Space IDを指定
    python3 gchat_read.py --space AAQA6AWG1iE

    # 日付フィルタ付き
    python3 gchat_read.py --space AAQA6AWG1iE --after "2024-01-01T00:00:00+09:00"

    # 出力先を変更
    python3 gchat_read.py --space AAQA6AWG1iE --output /tmp/my_chat.json

    # スペース一覧を表示
    python3 gchat_read.py --list-spaces
"""

import argparse
import json
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
from google_auth import get_credentials as _get_credentials  # type: ignore  # noqa: E402

from google.apps import chat_v1 as google_chat  # noqa: E402

SCOPES = [
    'chat.messages.readonly',
    'chat.spaces.readonly',
]

DEFAULT_SPACE_ID = os.environ.get('GCHAT_DEFAULT_SPACE') or None
DEFAULT_OUTPUT = '/tmp/gchat_messages.json'


def get_credentials():
    return _get_credentials(SCOPES)


def create_client():
    creds = get_credentials()
    full_scopes = [f'https://www.googleapis.com/auth/{s}' for s in SCOPES]
    return google_chat.ChatServiceClient(
        credentials=creds,
        client_options={"scopes": full_scopes},
    )


def list_spaces():
    client = create_client()
    request = google_chat.ListSpacesRequest(filter='space_type = "SPACE"')
    for space in client.list_spaces(request):
        print(f"{space.name} - {space.display_name}")


def list_messages(space_id, page_size=200, filter_str=None, order_by='createTime DESC'):
    client = create_client()
    kwargs = {
        'parent': f'spaces/{space_id}',
        'page_size': page_size,
        'order_by': order_by,
    }
    if filter_str:
        kwargs['filter'] = filter_str

    request = google_chat.ListMessagesRequest(**kwargs)
    messages = []
    for message in client.list_messages(request):
        d = type(message).to_dict(message)

        # attachment からDriveリンクを抽出
        attachments = []
        for att in (d.get('attachment') or []):
            drive_ref = att.get('drive_data_ref') or {}
            file_id = drive_ref.get('drive_file_id')
            content_name = att.get('content_name', '')
            content_type = att.get('content_type', '')
            if file_id:
                url = f'https://drive.google.com/file/d/{file_id}/view'
                if 'folder' in content_type:
                    url = f'https://drive.google.com/drive/folders/{file_id}'
                elif 'spreadsheet' in content_type:
                    url = f'https://docs.google.com/spreadsheets/d/{file_id}/edit'
                elif 'document' in content_type:
                    url = f'https://docs.google.com/document/d/{file_id}/edit'
                elif 'presentation' in content_type:
                    url = f'https://docs.google.com/presentation/d/{file_id}/edit'
                attachments.append({
                    'name': content_name,
                    'type': content_type,
                    'url': url,
                })

        # annotation からrich_linkを抽出
        rich_links = []
        for ann in (d.get('annotations') or []):
            rl = ann.get('rich_link_metadata')
            if rl and rl.get('uri'):
                rich_links.append(rl['uri'])

        messages.append({
            'name': message.name,
            'sender': message.sender.name if message.sender else None,
            'create_time': message.create_time.isoformat() if message.create_time else None,
            'text': message.text or '',
            'thread': message.thread.name if message.thread else None,
            'attachments': attachments,
            'rich_links': rich_links,
        })
    return messages


def main():
    parser = argparse.ArgumentParser(description='Google Chat メッセージ読み取り')
    parser.add_argument('--space', default=DEFAULT_SPACE_ID,
                        help='Space ID (URL末尾。env GCHAT_DEFAULT_SPACE で既定値も指定可)')
    parser.add_argument('--output', default=DEFAULT_OUTPUT, help='出力ファイルパス')
    parser.add_argument('--after', help='この日時以降のメッセージを取得 (RFC-3339形式)')
    parser.add_argument('--before', help='この日時以前のメッセージを取得 (RFC-3339形式)')
    parser.add_argument('--list-spaces', action='store_true', help='スペース一覧を表示')
    parser.add_argument('--page-size', type=int, default=200, help='1ページあたりの取得件数 (最大1000)')
    args = parser.parse_args()

    if args.list_spaces:
        list_spaces()
        return

    if not args.space:
        parser.error("--space が必要です (または env GCHAT_DEFAULT_SPACE で既定値を指定)")

    # フィルタ構築
    filters = []
    if args.after:
        filters.append(f'createTime > "{args.after}"')
    if args.before:
        filters.append(f'createTime < "{args.before}"')
    filter_str = ' AND '.join(filters) if filters else None

    messages = list_messages(args.space, page_size=args.page_size, filter_str=filter_str)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    print(f'OK: {len(messages)} messages saved to {args.output}')

    for msg in messages[:5]:
        text_preview = msg['text'][:100] if msg['text'] else '(empty)'
        print(f"[{msg['create_time']}] {text_preview}")


if __name__ == '__main__':
    main()
