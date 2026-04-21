# Chrome DevTools MCP

Chrome DevTools MCPサーバーをClaude Codeで利用するためのプラグインです。

## 概要

このプラグインは、ブラウザ自動化とテストのためのChrome DevTools Protocol (CDP)へのアクセスを提供します。

## 機能

- ブラウザの自動操作
- スクリーンショット撮影
- ページナビゲーション
- JavaScriptの実行
- ネットワークリクエストの監視
- コンソールログの取得

## インストール

```bash
/plugin install mcp-chrome-devtools@ai-plugins
```

## 使用方法

### 基本的な使用例

```bash
# ページにアクセスしてスクリーンショットを撮影
mcp__plugin_chrome-devtools-mcp__chrome-devtools__navigate_page "https://example.com"
mcp__plugin_chrome-devtools-mcp__chrome-devtools__take_screenshot
```

## 推奨される使用シーン

- Webアプリケーションのテスト
- スクレイピング
- パフォーマンス測定
- UIの自動検証

## 注意事項

- Dockerコンテナ内で実行する場合、`--no-sandbox`オプションが必要です
- DinD/DooD環境では接続方法が異なる場合があります（`ndf:docker-container-access` Skillを参照）

## 参考リンク

- [chrome-devtools-mcp](https://www.npmjs.com/package/chrome-devtools-mcp)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
