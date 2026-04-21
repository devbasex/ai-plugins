# Changelog

## [1.0.0] - 2026-01-30

### 追加機能

#### コンテキスト管理
- `/context-status`コマンド - コンテキスト使用状況の監視
- 自動コンパクト化機能 - 60%閾値で`/compact`実行推奨
- MCP数警告 - 10個超過時に警告表示
- ツール数監視 - 80個以下を推奨

#### 品質保証Hooks
- `auto-format` Hook - Prettier/ESLintで自動フォーマット
- `detect-console-log` Hook - console.log/debugger検出警告
- `typescript-check` Hook - TypeScript型チェック自動実行
- `secret-scan` Hook - シークレット混入チェック（コミットブロック）
- `coverage-check` Hook - テストカバレッジ検証（80%以上推奨）
- `context-monitor` Hook - コンテキスト使用率の自動監視
- `detect-package-manager` Hook - パッケージマネージャー自動検出

#### TDDワークフロー
- `/tdd`コマンド - 5段階TDDプロセスの開始
- `/tdd-red`コマンド - REDフェーズ（失敗テスト作成）
- `/tdd-green`コマンド - GREENフェーズ（最小実装）
- `/tdd-refactor`コマンド - REFACTORフェーズ（リファクタリング）
- `/tdd-coverage`コマンド - COVERAGEフェーズ（カバレッジ検証）
- `tdd-workflow` Skill - TDDワークフローの自動ガイド

#### セキュリティチェック
- `/security-scan`コマンド - 総合セキュリティスキャン
- `/owasp-check`コマンド - OWASP Top 10チェックリスト
- `security-review` Skill - セキュリティレビューの自動実施
- シークレット検出パターン - AWS, GitHub, Slack等のトークン検出
- 脆弱性パターン検出 - SQLインジェクション、XSS、パストラバーサル等

#### パッケージマネージャー自動検出
- npm/pnpm/yarn/bun の自動検出
- 環境変数、プロジェクト設定ファイル、lockファイルからの検出

#### ドキュメント
- README.md - プラグイン概要と使用方法
- CHANGELOG.md - 変更履歴
- コマンドドキュメント（8個）- 各コマンドの詳細説明
- Skillドキュメント（2個）- TDDワークフローとセキュリティレビュー

### 依存関係

- NDFプラグイン: ^2.1.0 （必須）

### 設定

- `contextMonitoring` - コンテキスト監視設定
- `hooks` - 各Hookの有効/無効設定
- `tdd` - TDDワークフロー設定（カバレッジ閾値等）
- `security` - セキュリティチェック設定（シークレットパターン等）

### 技術仕様

- Node.js 16.x以上を推奨
- クロスプラットフォーム対応（Windows/macOS/Linux）
- エラーハンドリング徹底（Hook失敗でもメイン処理は継続）

### 参考実装

このバージョンは以下の情報源に基づいて開発されました：

- [everything-claude-code](https://github.com/affaan-m/everything-claude-code) - Anthropicハッカソン優勝者による本番環境対応設定集
- [Claude Code 実践ガイド (Qiita)](https://qiita.com/dai_chi/items/c19be47044d062d59ee8) - 5つの中核原則
- [Claude Code 実装パターン (Zenn)](https://zenn.dev/ttks/articles/a54c7520f827be) - 4つの基本原則

### 既知の制限

- カバレッジ測定はJest/Vitestのみサポート
- 一部のHooksはNode.js環境が必要
- TypeScriptチェックはTypeScript導入プロジェクトのみ有効

### 今後の予定

v1.1.0で実装予定：
- 並列ワークフロー対応（`/fork`コマンドサポート）
- より高度なコンテキスト最適化アドバイザー
- 追加のセキュリティチェックパターン
- tmux使用促進Hook

---

## バージョニングポリシー

このプラグインは[セマンティックバージョニング](https://semver.org/lang/ja/)に従います：

- **MAJOR**: 破壊的変更
- **MINOR**: 後方互換性のある新機能
- **PATCH**: バグフィックス
