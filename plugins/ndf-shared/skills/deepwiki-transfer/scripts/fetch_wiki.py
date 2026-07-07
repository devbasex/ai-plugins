#!/usr/bin/env python3
"""
DeepWiki MCP から read_wiki_contents を直接HTTP呼び出しし、
レスポンス全体をファイルに保存するスクリプト。

LLMコンテキストを経由せず、MCPサーバーに直接HTTPリクエストを送信して
全コンテンツをファイル化する。

使用方法:
  # プライベートリポジトリ（Devin MCP、APIキー必要）
  python fetch_wiki.py --repo owner/repo --output /tmp/deepwiki_raw.md --api-key YOUR_KEY

  # 環境変数 DEVIN_API_KEY 利用
  DEVIN_API_KEY=xxx python fetch_wiki.py --repo owner/repo --output /tmp/deepwiki_raw.md

  # 公開リポジトリ（DeepWiki MCP、認証不要）
  python fetch_wiki.py --repo facebook/react --output /tmp/deepwiki_raw.md --public

  # Wiki構造のみ取得
  python fetch_wiki.py --repo owner/repo --output /tmp/deepwiki_structure.md --tool read_wiki_structure
"""

import argparse
import json
import os
import sys
import uuid

try:
    import requests
except ImportError:
    print("エラー: requests ライブラリが必要です。pip install requests を実行してください。", file=sys.stderr)
    sys.exit(1)

# MCPサーバーのエンドポイント
DEVIN_MCP_URL = "https://mcp.devin.ai/mcp"
DEEPWIKI_MCP_URL = "https://mcp.deepwiki.com/mcp"


def create_jsonrpc_request(method: str, params: dict, request_id: int) -> dict:
    """JSON-RPC 2.0 リクエストを生成する"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def parse_sse_json(resp: requests.Response) -> dict:
    """SSE（Server-Sent Events）形式のレスポンスからJSON-RPCメッセージを抽出する"""
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        # SSE形式: "event: message\ndata: {...}\n\n" をパースする
        for line in resp.text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data_str = line[6:]
                return json.loads(data_str)
        raise RuntimeError("SSEレスポンスにdataフィールドが見つかりません")
    else:
        return resp.json()


def mcp_initialize(session: requests.Session, url: str) -> str | None:
    """MCPサーバーとの初期化ハンドシェイクを行い、セッションIDを取得する"""
    init_request = create_jsonrpc_request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "deepwiki-fetch", "version": "1.0.0"},
        },
        1,
    )
    resp = session.post(url, json=init_request)
    resp.raise_for_status()

    # SSE形式のレスポンスをパース
    parse_sse_json(resp)

    # セッションIDをヘッダーから取得
    session_id = resp.headers.get("Mcp-Session-Id")

    # initialized 通知を送信
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    headers = {}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    session.post(url, json=notification, headers=headers)

    return session_id


def mcp_call_tool(
    session: requests.Session,
    url: str,
    tool_name: str,
    arguments: dict,
    session_id: str | None,
) -> str:
    """MCPツールを呼び出し、テキスト結果を返す"""
    call_request = create_jsonrpc_request(
        "tools/call",
        {"name": tool_name, "arguments": arguments},
        2,
    )
    headers = {}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    resp = session.post(url, json=call_request, headers=headers, timeout=300)
    resp.raise_for_status()

    result = parse_sse_json(resp)

    if "error" in result:
        error = result["error"]
        raise RuntimeError(f"MCPエラー: [{error.get('code')}] {error.get('message')}")

    # レスポンスからテキストコンテンツを抽出
    content_parts = result.get("result", {}).get("content", [])
    texts = []
    for part in content_parts:
        if part.get("type") == "text":
            texts.append(part["text"])

    if not texts:
        raise RuntimeError("MCPレスポンスにテキストコンテンツが含まれていません")

    return "\n".join(texts)


def main():
    parser = argparse.ArgumentParser(
        description="DeepWiki MCPからWikiコンテンツを取得してファイルに保存する"
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="対象リポジトリ（owner/repo形式、例: volareinc/carmo-system-console）",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="出力先ファイルパス（例: /tmp/deepwiki_raw.md）",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Devin APIキー（未指定時は環境変数 DEVIN_API_KEY を使用）",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="公開リポジトリ用のDeepWiki MCPを使用（認証不要）",
    )
    parser.add_argument(
        "--tool",
        default="read_wiki_contents",
        choices=["read_wiki_contents", "read_wiki_structure"],
        help="使用するMCPツール（デフォルト: read_wiki_contents）",
    )
    args = parser.parse_args()

    # エンドポイントと認証の設定
    if args.public:
        url = DEEPWIKI_MCP_URL
        api_key = None
        print(f"DeepWiki MCP（公開）を使用: {url}")
    else:
        url = DEVIN_MCP_URL
        api_key = args.api_key or os.environ.get("DEVIN_API_KEY")
        if not api_key:
            print(
                "エラー: --api-key または環境変数 DEVIN_API_KEY が必要です（公開リポジトリは --public を指定）",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Devin MCP（プライベート対応）を使用: {url}")

    # HTTPセッション作成
    session = requests.Session()
    if api_key:
        session.headers["Authorization"] = f"Bearer {api_key}"
    session.headers["Content-Type"] = "application/json"
    session.headers["Accept"] = "application/json, text/event-stream"

    try:
        # 1. MCP初期化
        print("MCPサーバーに接続中...")
        session_id = mcp_initialize(session, url)
        print(f"接続完了（セッションID: {session_id or 'なし'}）")

        # 2. ツール呼び出し
        print(f"{args.tool} を呼び出し中（リポジトリ: {args.repo}）...")
        content = mcp_call_tool(
            session, url, args.tool, {"repoName": args.repo}, session_id
        )

        # 3. ファイルに保存
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)

        file_size = os.path.getsize(args.output)
        line_count = content.count("\n") + 1
        print(f"保存完了: {args.output}")
        print(f"  サイズ: {file_size:,} bytes")
        print(f"  行数: {line_count:,}")

        # ページ数をカウント（read_wiki_contents の場合）
        if args.tool == "read_wiki_contents":
            page_count = content.count("# Page: ")
            print(f"  ページ数: {page_count}")

    except requests.exceptions.RequestException as e:
        print(f"HTTP通信エラー: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
