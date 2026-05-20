# 機能比較表

## Claude Code NDFプラグイン vs Kiro CLI移植版

| 機能カテゴリ | Claude Code NDF | Kiro CLI移植版 | 実装方法 | 備考 |
|------------|----------------|---------------|---------|------|
| **MCP統合** | ✅ 7サーバー | ✅ 7サーバー | `.kiro/mcp.json` | 同等 |
| **エージェント** | ✅ 6種類 | ✅ 6種類 | `.kiro/agents/*.json` | 同等 |
| **カスタムコマンド** | ✅ 9コマンド | ⚠️ プロンプト | `.kiro/prompts/*.md` | 代替実装 |
| **スキル** | ✅ 13スキル | ⚠️ プロンプト | `.kiro/prompts/skills/*.md` | 代替実装 |
| **フック** | ✅ SessionStart, Stop | ✅ agentSpawn, stop | エージェント設定 | 同等 |
| **Slack通知** | ✅ 自動 | ✅ 自動 | stopフック | 同等 |
| **Marketplace** | ✅ あり | ❌ なし | - | Kiro CLIに概念なし |
| **Plugin** | ✅ あり | ❌ なし | - | Kiro CLIに概念なし |

## 詳細機能比較

### MCP統合

| MCPサーバー | Claude Code | Kiro CLI | 設定方法 | 互換性 |
|-----------|-------------|----------|---------|--------|
| Serena | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| Notion | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| BigQuery | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| DBHub | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| Chrome DevTools | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| AWS Docs | ✅ | ✅ | `.kiro/mcp.json` | 100% |
| Codex CLI | ✅ | ✅ | `.kiro/mcp.json` | 100% |

### エージェント

| エージェント | Claude Code | Kiro CLI | 実装方法 | 機能差異 |
|------------|-------------|----------|---------|---------|
| Director | ✅ | ✅ | `.kiro/agents/director.json` | なし |
| Data-analyst | ✅ | ✅ | `.kiro/agents/data-analyst.json` | なし |
| Corder | ✅ | ✅ | `.kiro/agents/corder.json` | なし |
| Researcher | ✅ | ✅ | `.kiro/agents/researcher.json` | なし |
| Scanner | ✅ | ✅ | `.kiro/agents/scanner.json` | なし |
| QA | ✅ | ✅ | `.kiro/agents/qa.json` | なし |

### カスタムコマンド → プロンプト

| コマンド | Claude Code | Kiro CLI | 実装方法 | UX差異 |
|---------|-------------|----------|---------|--------|
| `/ndf:pr` | ✅ | ⚠️ `@pr` | `.kiro/prompts/pr.md` | `/`→`@` |
| `/ndf:pr-tests` | ✅ | ⚠️ `@pr-tests` | `.kiro/prompts/pr-tests.md` | `/`→`@` |
| `/ndf:fix` | ✅ | ⚠️ `@fix` | `.kiro/prompts/fix.md` | `/`→`@` |
| `/ndf:review` | ✅ | ⚠️ `@review` | `.kiro/prompts/review.md` | `/`→`@` |
| `/ndf:merged` | ✅ | ⚠️ `@merged` | `.kiro/prompts/merged.md` | `/`→`@` |
| `/ndf:clean` | ✅ | ⚠️ `@clean` | `.kiro/prompts/clean.md` | `/`→`@` |
| `/ndf:mem-review` | ✅ | ⚠️ `@mem-review` | `.kiro/prompts/mem-review.md` | `/`→`@` |
| `/ndf:mem-capture` | ✅ | ⚠️ `@mem-capture` | `.kiro/prompts/mem-capture.md` | `/`→`@` |
| `/ndf:serena` | ✅ | ⚠️ `@serena` | `.kiro/prompts/serena.md` | `/`→`@` |

**UX差異の説明**:
- Claude Code: `/ndf:pr`（スラッシュコマンド）
- Kiro CLI: `@pr`（プロンプト）
- 機能的には同等だが、呼び出し方法が異なる

### スキル → プロンプト

| スキル | Claude Code | Kiro CLI | 実装方法 | 自動起動 |
|--------|-------------|----------|---------|---------|
| SQL最適化 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/sql-optimization.md` | ❌ |
| コードテンプレート | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/code-templates.md` | ❌ |
| テスト生成 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/test-generation.md` | ❌ |
| PDF解析 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/pdf-analysis.md` | ❌ |
| Excel抽出 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/excel-extraction.md` | ❌ |
| セキュリティスキャン | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/security-scan.md` | ❌ |
| Markdown文書作成 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/markdown-writing.md` | ❌ |
| Python実行環境判定 | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/python-execution.md` | ❌ |
| Dockerコンテナアクセス | ✅ 自動 | ⚠️ 手動 | `.kiro/prompts/skills/docker-access.md` | ❌ |

**自動起動の差異**:
- Claude Code: スキルは特定のキーワードで自動起動
- Kiro CLI: プロンプトは明示的に`@skill-name`で呼び出し
- 機能的には同等だが、UXが異なる

### フック

| フック | Claude Code | Kiro CLI | 実装方法 | 互換性 |
|--------|-------------|----------|---------|--------|
| SessionStart | ✅ | ✅ agentSpawn | エージェント設定 | 同等 |
| Stop | ✅ | ✅ stop | エージェント設定 | 同等 |
| PreToolUse | ✅ | ✅ preToolUse | エージェント設定 | 同等 |
| PostToolUse | ✅ | ✅ postToolUse | エージェント設定 | 同等 |

### Slack通知

| 機能 | Claude Code | Kiro CLI | 実装方法 | 互換性 |
|------|-------------|----------|---------|--------|
| セッション終了通知 | ✅ | ✅ | stopフック | 100% |
| AI要約生成 | ✅ | ✅ | スクリプト | 100% |
| メンション | ✅ | ✅ | 環境変数 | 100% |

## ユーザー体験の違い

### コマンド実行

**Claude Code**:
```
/ndf:pr
```

**Kiro CLI**:
```
@pr
```

### スキル起動

**Claude Code**:
```
# 自動起動（キーワード検出）
> SQLクエリを最適化してください
# → SQL最適化スキルが自動起動
```

**Kiro CLI**:
```
# 明示的呼び出し
@sql-optimization
> SQLクエリを最適化してください
```

### エージェント切り替え

**Claude Code**:
```
# サブエージェント呼び出し
/agent data-analyst
```

**Kiro CLI**:
```
# エージェント切り替え
/agent data-analyst

# またはサブエージェント使用
> use_subagent tool
```

## 移植による利点

### Kiro CLI固有の利点

| 機能 | 説明 | Claude Codeにない利点 |
|------|------|---------------------|
| `/code` | LSP統合 | コードインテリジェンス |
| `/knowledge` | ナレッジベース | 永続的な知識管理 |
| `/todos` | TODOリスト | タスク管理 |
| `/checkpoint` | チェックポイント | 状態管理 |
| `/tangent` | タンジェントモード | 会話分岐 |
| AWS統合 | `use_aws`ツール | AWS CLI統合 |

### 統合の可能性

Kiro CLI移植版では、以下の統合が可能：

1. **コードインテリジェンス + Serena MCP**
   - LSPとSerenaの組み合わせで強力なコード理解

2. **ナレッジベース + プロジェクトドキュメント**
   - `/knowledge`でプロジェクト知識を永続化

3. **TODOリスト + タスク管理**
   - `/todos`でタスク追跡

4. **AWS統合 + AWS Docs MCP**
   - `use_aws`とAWS Docs MCPの組み合わせ

## 移植の制約

### 実装できない機能

| 機能 | 理由 | 代替案 |
|------|------|--------|
| Marketplace | Kiro CLIに概念なし | Gitリポジトリで配布 |
| Plugin | Kiro CLIに概念なし | 設定ファイルで配布 |
| スキル自動起動 | Kiro CLIに機能なし | プロンプトで明示的呼び出し |
| カスタムスラッシュコマンド | Kiro CLIに機能なし | プロンプトで代替 |

### UXの違い

| 項目 | Claude Code | Kiro CLI | 影響 |
|------|-------------|----------|------|
| コマンド呼び出し | `/ndf:command` | `@command` | 軽微 |
| スキル起動 | 自動 | 手動 | 中程度 |
| プラグインインストール | `/plugin install` | `git clone` + `./install.sh` | 中程度 |
| 更新 | `/plugin update` | `git pull` + `./install.sh` | 軽微 |

## 推奨事項

### ユーザーへの推奨

1. **Claude Codeユーザー**: 
   - NDFプラグインを使用（ネイティブ体験）
   
2. **Kiro CLIユーザー**: 
   - Kiro NDF Configを使用（同等機能）
   - Kiro CLI固有機能も活用

3. **両方使用**: 
   - 環境に応じて使い分け
   - 設定は別々に管理

### 開発者への推奨

1. **新機能追加時**: 
   - 両方のプラットフォームで実装を検討
   
2. **ドキュメント**: 
   - プラットフォーム別のガイドを提供
   
3. **互換性**: 
   - 可能な限り同等の体験を提供
