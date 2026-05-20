# playwright Plugin

Playwright MCPを自動的にセットアップし、ブラウザ自動化機能を提供するプラグインです。

## 概要

このプラグインは、Playwright MCPサーバーとChromiumブラウザを自動的にインストール・設定します。Claude Codeでブラウザ自動化タスク（Webスクレイピング、E2Eテスト、スクリーンショット取得など）を簡単に実行できるようになります。

## 機能

- **自動セットアップ**: 初回起動時にPlaywright Chromiumブラウザを自動インストール
- **バージョン互換性**: @playwright/mcpと互換性のあるPlaywrightバージョンを自動検出
- **冪等性**: 既にインストール済みの場合はスキップ（高速起動）
- **環境変数対応**: PLAYWRIGHT_BROWSERS_PATHで共有キャッシュを利用

## インストール

このプラグインをインストールすると、以下が自動的に設定されます：

1. Playwright MCP設定（`.mcp.json`）
2. SessionStart hookによる自動セットアップ
3. Chromiumブラウザのインストール

## 使用方法

### 基本的な使い方

プラグインをインストールして、Claude Codeを起動するだけで自動的にセットアップが実行されます。

```bash
# Claude Codeを起動
claude
```

初回起動時、以下のようなメッセージが表示されます：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎭 Playwright MCP Plugin: 初回セットアップ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Playwright Chromiumブラウザをインストール中...
ネットワーク環境により1-2分かかる場合があります。

🔍 @playwright/mcpが使用するPlaywrightバージョンを確認中...
✓ Playwright 1.49.1 を使用します

📦 Playwright 1.49.1 でChromiumをインストール中...
[インストールログ...]

✅ セットアップ完了！Playwright Chromiumの準備ができました。
   Playwrightバージョン: 1.49.1
   ブラウザパス: ~/.cache/ms-playwright
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Playwright MCPツールの使用例

セットアップ完了後、以下のようなタスクが実行できます：

```
// Webページにアクセスしてスクリーンショットを取得
mcp__plugin_playwright__browser_navigate(url: "https://example.com")
mcp__plugin_playwright__browser_take_screenshot()

// フォームに入力してボタンをクリック
mcp__plugin_playwright__browser_fill(selector: "#email", value: "test@example.com")
mcp__plugin_playwright__browser_click(selector: "button[type='submit']")

// ページのスナップショットを取得（アクセシビリティツリー）
mcp__plugin_playwright__browser_snapshot()
```

## 設定

### MCP設定（`.mcp.json`）

プラグインは以下のMCP設定を提供します：

```json
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@playwright/mcp@latest",
        "--browser",
        "chromium",
        "--isolated",
        "--no-sandbox"
      ],
      "env": {
        "PLAYWRIGHT_BROWSERS_PATH": "${HOME}/.cache/ms-playwright"
      }
    }
  }
}
```

### ブラウザキャッシュパス

デフォルトでは、Chromiumブラウザは `~/.cache/ms-playwright` にインストールされます。

別の場所を使用したい場合は、環境変数を設定してください：

```bash
export PLAYWRIGHT_BROWSERS_PATH=/path/to/custom/location
```

## トラブルシューティング

### インストールが失敗する場合

1. **手動インストール**:
   ```bash
   # 互換性のあるバージョンを確認
   npm view @playwright/mcp@latest dependencies

   # Chromiumをインストール
   PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright npx playwright@<version> install chromium
   ```

2. **キャッシュをクリア**:
   ```bash
   rm ~/.claude-playwright-installed
   ```

3. **ログを確認**:
   プラグインは詳細なログを出力するので、エラーメッセージを確認してください。

### ブラウザが見つからない場合

環境変数 `PLAYWRIGHT_BROWSERS_PATH` が正しく設定されているか確認してください：

```bash
echo $PLAYWRIGHT_BROWSERS_PATH
ls -la ~/.cache/ms-playwright
```

### タイムアウトエラー

ネットワーク環境が遅い場合、インストールに時間がかかることがあります。
デフォルトのタイムアウトは5分です。

## 参考資料

- [Playwright公式ドキュメント](https://playwright.dev/)
- [@playwright/mcp README](https://www.npmjs.com/package/@playwright/mcp)
- [Playwright Browser Management](https://playwright.dev/docs/browsers)

## ライセンス

MIT License

## サポート

問題が発生した場合は、GitHubリポジトリでissueを作成してください：
https://github.com/takemi-ohama/ai-plugins/issues
