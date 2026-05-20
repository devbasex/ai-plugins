# Kiro CLI移植計画 - サマリー

## 調査結果

### NDFプラグインの構成
- **MCP統合**: 7サーバー（Serena、Notion、BigQuery、DBHub、Chrome DevTools、AWS Docs、Codex CLI）
- **カスタムコマンド**: 9コマンド（PR作成、テスト実行、レビュー、修正、マージ、クリーンアップ、Memory管理）
- **サブエージェント**: 6種類（director、data-analyst、corder、researcher、scanner、qa）
- **スキル**: 13個（SQL最適化、コードテンプレート、テスト生成、PDF解析、Excel抽出、セキュリティスキャン等）
- **フック**: SessionStart、Stop（Slack通知）

### Kiro CLIの類似機能
- **MCP統合**: ✅ 同等機能あり（`.kiro/mcp.json`）
- **エージェント**: ✅ 同等機能あり（`.kiro/agents/*.json`）
- **カスタムコマンド**: ⚠️ プロンプト（`.kiro/prompts/*.md`）で代替
- **スキル**: ⚠️ プロンプトで代替（自動起動は不可）
- **フック**: ✅ 同等機能あり（エージェント設定内）
- **Marketplace/Plugin**: ❌ 概念なし

## 移植戦略

### 結論: 別リポジトリで移植

**新リポジトリ名**: `kiro-ndf-config`

**理由**:
1. Kiro CLIにはMarketplace/Plugin概念がない
2. 同じリポジトリで管理すると混乱を招く
3. Kiro CLI向けの独自構造が必要

### 機能マッピング

| Claude Code機能 | Kiro CLI実装 | 互換性 |
|----------------|-------------|--------|
| MCP統合 | `.kiro/mcp.json` | 100% |
| エージェント | `.kiro/agents/*.json` | 100% |
| カスタムコマンド | `.kiro/prompts/*.md` | 代替（`/`→`@`） |
| スキル | `.kiro/prompts/skills/*.md` | 代替（手動起動） |
| フック | エージェント設定 | 100% |

## 実装計画

### Phase 1: プロジェクト基盤（Week 1-2）
- GitHubリポジトリ作成
- ディレクトリ構造作成
- 基本ドキュメント作成

### Phase 2: MCP統合（Week 1-2）
- `.kiro/mcp.json`作成
- 7つのMCPサーバー設定
- `.env.example`作成

### Phase 3: エージェント移植（Week 1-2）
- 6つのエージェント設定作成
- エージェント間連携ドキュメント

### Phase 4: プロンプト作成（Week 3-4）
- 9つの開発ワークフロープロンプト
- 13個のスキルプロンプト

### Phase 5: フック実装（Week 3-4）
- Slack通知スクリプト
- セットアップスクリプト

### Phase 6: インストールスクリプト（Week 5）
- 自動セットアップスクリプト
- 環境確認スクリプト

### Phase 7: ドキュメント整備（Week 5）
- ユーザーガイド
- 開発者ガイド
- トラブルシューティング

### Phase 8: テストとリリース（Week 6）
- 機能テスト
- ドキュメントレビュー
- v1.0.0リリース

## インストール方法（予定）

```bash
# リポジトリのクローン
git clone https://github.com/takemi-ohama/kiro-ndf-config.git
cd kiro-ndf-config

# 自動インストール
./scripts/install.sh

# 環境変数の設定
vi .env

# 確認
kiro-cli mcp
kiro-cli agent list
```

## 利用方法（予定）

### エージェント起動
```bash
kiro-cli chat --agent director
kiro-cli chat --agent data-analyst
kiro-cli chat --agent corder
```

### プロンプト使用
```bash
@pr              # PR作成
@review          # コードレビュー
@fix             # レビュー修正
@sql-optimization # SQL最適化
```

### MCP活用
```bash
# Serena MCPでコード操作
> Serenaを使ってシンボル一覧を取得

# BigQuery MCPでデータ分析
> BigQueryでユーザー集計
```

## ユーザー体験の違い

### コマンド呼び出し
- **Claude Code**: `/ndf:pr`
- **Kiro CLI**: `@pr`

### スキル起動
- **Claude Code**: 自動起動（キーワード検出）
- **Kiro CLI**: 明示的呼び出し（`@skill-name`）

### プラグインインストール
- **Claude Code**: `/plugin install ndf@ai-plugins`
- **Kiro CLI**: `git clone` + `./install.sh`

## Kiro CLI固有の利点

移植版では、Kiro CLI固有の機能も活用可能：

1. **コードインテリジェンス** (`/code`): LSP統合
2. **ナレッジベース** (`/knowledge`): 永続的な知識管理
3. **TODOリスト** (`/todos`): タスク管理
4. **チェックポイント** (`/checkpoint`): 状態管理
5. **タンジェントモード** (`/tangent`): 会話分岐
6. **AWS統合** (`use_aws`): AWS CLI統合

## 成功基準

### 機能要件
- ✅ 7つのMCPサーバーが正常に動作
- ✅ 6つのエージェントが利用可能
- ✅ 9つのプロンプト（コマンド代替）が動作
- ✅ 13個のスキルプロンプトが動作
- ✅ Slack通知フックが動作

### ドキュメント要件
- ✅ インストール手順が明確
- ✅ 使用方法が理解しやすい
- ✅ トラブルシューティングが充実

### ユーザー体験
- ✅ Claude Code NDFプラグインと同等の機能
- ✅ Kiro CLI固有の利点を活用
- ✅ 簡単にセットアップ可能

## リスクと対策

### リスク1: MCP互換性
**対策**: 各MCPサーバーの動作確認を徹底

### リスク2: エージェント設定の複雑さ
**対策**: デフォルト設定を提供、段階的なカスタマイズを推奨

### リスク3: ドキュメント不足
**対策**: 豊富な例とトラブルシューティングを用意

### リスク4: UXの違い
**対策**: 明確なドキュメントと移行ガイド

## 次のステップ

1. ✅ 調査完了
2. ✅ 移植計画作成
3. ⏭️ GitHubリポジトリ作成
4. ⏭️ 基本ディレクトリ構造の作成
5. ⏭️ MCP設定ファイルの作成
6. ⏭️ 最初のエージェント（director）の実装

## 参考資料

- [調査結果](./01-analysis.md)
- [移植計画](./02-migration-plan.md)
- [インストール・利用方法](./03-installation-usage.md)
- [機能比較表](./04-feature-comparison.md)
