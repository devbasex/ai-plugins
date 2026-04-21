# mcp-redash

Redash MCP サーバーをマルチ環境対応で利用するための Claude Code プラグイン。

## 概要

[redash-mcp](https://github.com/suthio/redash-mcp) を Claude Code から利用するためのプラグインです。

- 1つだけ使う場合は最小設定（`REDASH_URL` と `REDASH_API_KEY` のみ）
- 必要になったら suffix 付きで環境を増やせる
- MCP 定義は必要になるまで作られない

## 前提条件

- Claude Code
- Node.js（npx が使える環境）

## インストール

```bash
/plugin install mcp-redash@ai-plugins
```

## 環境変数

プロジェクトの `.env` に設定してください。

```bash
REDASH_URL=https://redash.example.com
REDASH_API_KEY=your-api-key
```

## 使い方

### 基本（1環境）

プラグインインストール後、`redash` MCP が自動的に有効になります。環境変数を設定するだけで利用開始できます。

### 複数環境

```bash
# dev 環境を追加
/redash-add dev

# .env に環境変数を設定
# REDASH_DEV_URL=https://redash-dev.example.com
# REDASH_DEV_API_KEY=your-dev-api-key

# 一覧確認
/redash-list

# 状態確認（環境変数の設定漏れチェック）
/redash-status

# 不要になったら削除
/redash-remove dev
```

## スラッシュコマンド

| コマンド | 説明 |
|---------|------|
| `/redash-add <suffix>` | 任意 suffix の Redash MCP を project `.mcp.json` に追加 |
| `/redash-remove <suffix>` | 指定 suffix の Redash MCP を project `.mcp.json` から削除 |
| `/redash-list` | 有効な Redash MCP を一覧表示 |
| `/redash-status` | 設定状況の詳細確認（環境変数の未設定警告付き） |
| `/redash-guide` | 使い方ガイドを表示 |

## suffix と環境変数の対応

| suffix | MCP 名 | 環境変数 |
|--------|--------|----------|
| (なし) | redash | REDASH_URL, REDASH_API_KEY |
| dev | redash-dev | REDASH_DEV_URL, REDASH_DEV_API_KEY |
| stg | redash-stg | REDASH_STG_URL, REDASH_STG_API_KEY |
| prod2 | redash-prod2 | REDASH_PROD2_URL, REDASH_PROD2_API_KEY |
| sandbox | redash-sandbox | REDASH_SANDBOX_URL, REDASH_SANDBOX_API_KEY |

- suffix は英数字で自由に指定可能
- `default` は予約語のため使用不可（plugin 同梱の `redash` が該当）

## ファイル構成

```
plugins/mcp-redash/
├── .claude-plugin/
│   └── plugin.json              # プラグインメタデータ
├── .mcp.json                    # MCP 設定（redash のみ同梱）
├── .env.example                 # 環境変数テンプレート
├── scripts/
│   └── redash-mcp-config.js     # JSON 編集スクリプト（Node.js）
├── skills/
│   ├── redash-add/SKILL.md      # /redash-add コマンド
│   ├── redash-remove/SKILL.md   # /redash-remove コマンド
│   ├── redash-list/SKILL.md     # /redash-list コマンド
│   ├── redash-status/SKILL.md   # /redash-status コマンド
│   └── redash-guide/SKILL.md    # 使い方ガイド
└── README.md
```

## 設計意図

> 本プラグインは
> 「1つだけ使う場合は最小設定」
> 「必要になったら suffix 付きで増やす」
> という運用を前提にしている。
>
> そのため MCP 定義は **必要になるまで作られない**。

- plugin 同梱: `redash`（常に `/mcp` に表示）
- project `.mcp.json`: `/redash-add` で追加したもののみ表示

## トラブルシューティング

### `/mcp` に redash が表示されない

- プラグインが正しくインストールされているか確認
- `/plugin list` で `mcp-redash@ai-plugins` が表示されるか確認

### 環境変数が反映されない

- `.env` ファイルがプロジェクトルートにあるか確認
- 変数名が正しい形式か確認（`REDASH_{SUFFIX}_URL`）
- `/redash-status` で設定状況を確認

### `/redash-add` でエラーが出る

- `.mcp.json` の JSON が壊れていないか確認（手動編集した場合）
- `default` は予約語のため suffix に使用不可

### suffix 付き MCP が動かない

- 対応する環境変数が `.env` に設定されているか `/redash-status` で確認
- Redash の URL と API Key が正しいか確認

## 参考リンク

- [redash-mcp](https://github.com/suthio/redash-mcp) - Redash MCP サーバー
- [Redash](https://redash.io/) - データ可視化ツール
