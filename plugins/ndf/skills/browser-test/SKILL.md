---
name: browser-test
description: "ブラウザで動作確認を実行する。Playwright MCP または Chrome DevTools MCP を利用可能な場合に自動化。Webアプリの機能検証・回帰確認用。"
argument-hint: "[url]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - mcp__playwright__browser_navigate
  - mcp__playwright__browser_snapshot
  - mcp__playwright__browser_click
  - mcp__playwright__browser_fill_form
  - mcp__playwright__browser_take_screenshot
  - mcp__playwright__browser_type
  - mcp__playwright__browser_evaluate
  - mcp__playwright__browser_console_messages
  - mcp__playwright__browser_wait_for
  - mcp__playwright__browser_tabs
  - mcp__playwright__browser_navigate_back
  - mcp__playwright__browser_close
  - mcp__playwright__browser_resize
  - mcp__playwright__browser_handle_dialog
  - mcp__playwright__browser_press_key
  - mcp__playwright__browser_hover
  - mcp__playwright__browser_select_option
  - mcp__playwright__browser_drag
  - mcp__playwright__browser_network_requests
  - mcp__playwright__browser_file_upload
  - mcp__playwright__browser_install
  - mcp__chrome-devtools__navigate_page
  - mcp__chrome-devtools__take_snapshot
  - mcp__chrome-devtools__click
  - mcp__chrome-devtools__fill_form
  - mcp__chrome-devtools__take_screenshot
  - mcp__chrome-devtools__type
  - mcp__chrome-devtools__evaluate_script
  - mcp__chrome-devtools__list_console_messages
  - mcp__chrome-devtools__wait_for
  - mcp__chrome-devtools__list_pages
  - mcp__chrome-devtools__new_page
  - mcp__chrome-devtools__select_page
  - mcp__chrome-devtools__close_page
  - mcp__chrome-devtools__navigate_page_history
  - mcp__chrome-devtools__resize_page
  - mcp__chrome-devtools__handle_dialog
  - mcp__chrome-devtools__hover
  - mcp__chrome-devtools__drag
  - mcp__chrome-devtools__list_network_requests
  - mcp__chrome-devtools__get_network_request
  - mcp__chrome-devtools__upload_file
  - mcp__chrome-devtools__emulate_network
  - mcp__chrome-devtools__emulate_cpu
  - mcp__chrome-devtools__performance_start_trace
  - mcp__chrome-devtools__performance_stop_trace
  - mcp__chrome-devtools__performance_analyze_insight
---

# ブラウザ動作確認コマンド

現在のブランチで実装されたWeb機能をブラウザで動作確認する。Playwright MCP または Chrome DevTools MCP を利用可能な方を自動選択する。

## 前提条件（重要）

このコマンドは以下のいずれかのMCPサーバが必要:

- **Playwright MCP**: 自動的にブラウザを起動（要Playwrightインストール）
- **Chrome DevTools MCP**: 既に開いているChromeを操作（Chromeをデバッグモードで起動しておく必要あり）

どちらも利用できない環境では、手動確認手順を案内する。

## 使用方法

```
/ndf:browser-test                       # 現在のブランチの実装を確認
/ndf:browser-test http://localhost:8080 # 特定URLを確認
```

## MCPの使い分け

### Playwright MCP
- 自動的にブラウザを起動
- 複数ブラウザ対応 (Chromium/Firefox/WebKit)
- 利用可能なら第一選択

### Chrome DevTools MCP
- 既に開いているChromeブラウザを操作
- Chrome デバッグモードでの起動が必要:
  - macOS: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222`
  - Windows: `chrome.exe --remote-debugging-port=9222`
  - Linux: `google-chrome --remote-debugging-port=9222`
- DevTools統合でパフォーマンス分析可能

## 処理フロー

### 1. アプリケーション起動確認

プロジェクトで使っている起動方法に応じて確認:

```bash
# Docker Compose の場合
docker compose ps

# ネイティブ起動の場合
curl -fsS http://localhost:<port>/health || echo "NOT RUNNING"
```

起動していない場合は、起動手順をユーザーに案内。

### 2. ブラウザアクセスと認証

- 指定URL（または `/` ）にアクセス
- 必要に応じてログイン（資格情報はプロジェクト固有、事前に取得しておく）

### 3. 機能画面への遷移

実装された機能に応じて適切な画面に遷移する。

### 4. 動作確認

必要に応じて以下の操作を実行:

- フォーム入力
- ボタンクリック
- データ表示の確認
- コンソールエラーの確認
- ネットワークリクエストの確認
- スクリーンショット（明示的に指示された場合のみ）

### 5. 結果報告

```markdown
## 動作確認結果

### 実施項目
- [x] ログイン
- [x] 機能画面表示
- [x] フォーム送信
- [x] 結果表示

### 確認事項
- コンソールエラー: なし
- ネットワークエラー: なし
- 期待結果との一致: ok

### 気になる点
- ...（あれば）
```

## 注意事項

- **事前にアプリケーション起動が必要**
- **ログイン情報**: プロジェクトの `.env.example` / README 等から確認。機密情報として扱う
- **スクリーンショット**: 必要な場合のみ明示的に指示されたときに取得
- **Chrome DevTools使用時**: Chromeをデバッグモードで起動しておく必要あり
- **MCP未インストール環境**: 手動での確認手順を案内する

## 関連

- `/ndf:review-branch` — 変更差分のコードレビュー
- `/ndf:pr-tests` — PR Test Plan の自動実行
