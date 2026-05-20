# affaan-m プラグイン

**コンテキスト管理、品質保証、TDDワークフローを提供するClaude Codeプラグイン**

## 概要

affaan-mプラグインは、[everything-claude-code](https://github.com/affaan-m/everything-claude-code)（Anthropicハッカソン優勝者による本番環境対応設定集）に基づいて開発されたプラグインです。NDFプラグインと併用することで、本番環境レベルの開発支援環境を構築できます。

**バージョン**: 1.0.0

## NDFプラグインとの併用前提

affaan-mプラグインは**NDFプラグイン（v2.1.0以降）との併用を前提**としています。

### 役割分担

| プラグイン | 役割 |
|-----------|------|
| **NDFプラグイン** | MCP統合、ワークフロー、専門エージェント |
| **affaan-mプラグイン** | コンテキスト管理、品質保証、TDDワークフロー |

### 併用による効果

- NDFプラグイン: MCP統合（6個）+ 専門エージェント（6個）
- affaan-mプラグイン: コンテキスト管理 + 品質保証Hooks
- = **本番環境対応の開発支援環境**

## 主要機能

### 1. コンテキスト管理

**問題**: MCPツール有効化により200k→70kへコンテキストが大幅縮小

**解決策**:
- `/context-status` - コンテキスト使用率の監視
- 自動コンパクト化 - 60%閾値で`/compact`実行推奨
- MCP数警告 - 10個超過時に警告表示
- ツール数監視 - 80個以下を推奨

### 2. 品質保証Hooks

**PreToolUse Hooks**:
- `context-monitor` - コンテキスト使用率を監視
- `detect-package-manager` - パッケージマネージャー自動検出

**PostToolUse Hooks** (Edit|Write時に自動実行):
- `auto-format` - Prettier/ESLintで自動フォーマット
- `detect-console-log` - console.log/debugger検出警告
- `typescript-check` - TypeScript型チェック自動実行
- `secret-scan` - シークレット混入チェック（ファイル変更時）

**Note**: `coverage-check`は`/tdd-coverage`コマンドから手動実行されます

### 3. TDDワークフロー

**5段階TDDプロセス**:
1. インターフェース定義
2. RED - 失敗テスト作成（`/tdd-red`）
3. GREEN - 最小実装（`/tdd-green`）
4. REFACTOR - リファクタリング（`/tdd-refactor`）
5. COVERAGE - カバレッジ検証（`/tdd-coverage`）

**コマンド**:
- `/tdd [機能名]` - TDDワークフロー開始

### 4. セキュリティチェック

**OWASP Top 10準拠**:
- `/security-scan` - 総合セキュリティスキャン
- `/owasp-check` - OWASP Top 10チェックリスト
- シークレット検出 - API key, token, 秘密鍵等
- 脆弱性パターン検出 - SQLインジェクション、XSS等

### 5. パッケージマネージャー自動検出

**検出順序**:
1. 環境変数チェック
2. プロジェクト設定ファイル（package.json）
3. lockファイル検出（pnpm, yarn, bun, npm）

## インストール

### 前提条件

1. **NDFプラグインのインストール**:
```bash
/plugin install ndf@ai-plugins
```

2. **affaan-mプラグインのインストール**:
```bash
/plugin install affaan-m@ai-plugins
```

### 動作確認

```bash
# コンテキスト監視
/context-status

# TDDワークフロー開始
/tdd "テスト機能"

# セキュリティスキャン
/security-scan
```

## 使用方法

### シナリオ1: コーディング作業

```bash
# 1. コンテキスト確認
/context-status

# 2. TDDワークフロー開始
/tdd "ユーザー認証機能"

# 3. NDFのcorderエージェントで実装
# （affaan-mのHooksが自動的にチェック）

# 4. カバレッジ検証
/tdd-coverage "ユーザー認証機能"
```

### シナリオ2: セキュリティレビュー

```bash
# 1. セキュリティスキャン実行
/security-scan

# 2. OWASP Top 10チェック
/owasp-check

# 3. 検出された問題を修正（NDFのcorderと連携）

# 4. 再スキャンで確認
/security-scan
```

### シナリオ3: コンテキスト管理

```bash
# 1. 定期的にコンテキスト確認
/context-status

# 2. 60%超過時
/compact

# 3. MCP設定の最適化
# 未使用MCPを無効化
```

## 設定

### plugin.json でのカスタマイズ

```json
{
  "config": {
    "contextMonitoring": {
      "enabled": true,
      "threshold": 60,
      "autoCompact": true
    },
    "hooks": {
      "autoFormat": true,
      "consoleLogDetection": true,
      "typescriptCheck": true,
      "secretScan": true,
      "coverageCheck": true
    },
    "tdd": {
      "coverageThreshold": 80,
      "enforceRedGreenRefactor": true
    },
    "security": {
      "owaspCheck": true,
      "secretPatterns": [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "SLACK_TOKEN"
      ]
    }
  }
}
```

### Hooksの無効化

特定のHooksを無効化する場合は、上記設定で`false`に変更してください。

## コマンド一覧

### コンテキスト管理
- `/context-status` - コンテキスト使用状況の確認

### TDDワークフロー
- `/tdd [機能名]` - TDDワークフロー開始
- `/tdd-red [機能名]` - REDフェーズ（失敗テスト作成）
- `/tdd-green [機能名]` - GREENフェーズ（最小実装）
- `/tdd-refactor [機能名]` - REFACTORフェーズ（リファクタリング）
- `/tdd-coverage [機能名]` - COVERAGEフェーズ（カバレッジ検証）

### セキュリティ
- `/security-scan [ディレクトリ]` - 総合セキュリティスキャン
- `/owasp-check [ディレクトリ]` - OWASP Top 10チェック

## Hooks一覧

### PreToolUse Hooks
- `context-monitor` - コンテキスト使用率監視
- `detect-package-manager` - パッケージマネージャー自動検出

### PostToolUse Hooks
- `auto-format` - 自動フォーマット（Prettier/ESLint）
- `detect-console-log` - console.log/debugger検出
- `typescript-check` - TypeScript型チェック

### PreCommit Hooks
- `secret-scan` - シークレット混入チェック
- `coverage-check` - テストカバレッジ検証

## Skills一覧

- `tdd-workflow` - TDDワークフロースキル
- `security-review` - セキュリティレビュースキル

## ベストプラクティス

### コンテキスト管理

**推奨MCP設定数**:
- グローバル設定: 20-30個以内
- プロジェクト設定: 10個以下
- 総ツール数: 80個以下

**監視頻度**:
- タスク開始前に確認
- 60%を超えたら定期的にチェック

### TDDワークフロー

**原則**:
- RED → GREEN → REFACTOR の順序を厳守
- 80%以上のカバレッジを目標
- リファクタリング時はテストをパスし続ける

### セキュリティ

**実行タイミング**:
- 機能開発完了後
- Pull Request作成前
- 定期的なスキャン（週1回）

**シークレット管理**:
- 環境変数を使用（`.env`）
- `.gitignore`に`.env`を追加
- `.env.example`でテンプレート提供

## トラブルシューティング

### コンテキスト使用率が常に高い

1. 未使用MCPを無効化
2. プロジェクト固有のMCPのみ有効化
3. 定期的に`/compact`実行

### Hooksが動作しない

1. Node.jsがインストールされているか確認
2. Hookスクリプトの実行権限を確認
3. プラグイン設定を確認（`plugin.json`）

### カバレッジが計測されない

1. テストフレームワークの設定確認
2. カバレッジツールのインストール確認
3. テストコマンドの確認

## 依存関係

- **NDFプラグイン**: ^2.1.0 （必須）
- Node.js: 16.x以上 （推奨）
- npm/pnpm/yarn/bun: 任意のパッケージマネージャー

## ライセンス

MIT License

## 作成者

- **名前**: takemi-ohama
- **URL**: https://github.com/takemi-ohama

## 参考

- [everything-claude-code](https://github.com/affaan-m/everything-claude-code)
- [Claude Code 実践ガイド (Qiita)](https://qiita.com/dai_chi/items/c19be47044d062d59ee8)
- [Claude Code 実装パターン (Zenn)](https://zenn.dev/ttks/articles/a54c7520f827be)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

## 変更履歴

詳細は[CHANGELOG.md](./CHANGELOG.md)を参照してください。

## サポート

Issue報告: https://github.com/takemi-ohama/ai-plugins/issues
