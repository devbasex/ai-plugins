# ai-plugins → Kiro CLI 移植調査結果

## 調査日時
2026-02-05

## 1. NDFプラグインの機能調査

### 1.1 プラグイン構成

**plugin.json**:
- バージョン: 2.5.0
- 9つのカスタムコマンド
- 6つのサブエージェント
- 13個のスキル
- フック機能（SessionStart、Stop）

### 1.2 MCPサーバー統合 (.mcp.json)

| MCPサーバー | タイプ | 用途 | 認証 |
|------------|--------|------|------|
| Serena | stdio | セマンティックコード操作 | GOOGLE_API_KEY, ANTHROPIC_API_KEY |
| Notion | http | Notion統合 | NOTION_TOKEN |
| BigQuery | stdio | BigQueryクエリ | GCPサービスアカウント |
| DBHub | stdio | データベース操作 | DSN |
| Chrome DevTools | stdio | ブラウザ自動化 | なし |
| AWS Docs | stdio | AWS文書検索 | なし |
| Codex CLI | stdio | Codex統合 | Codex認証 |

### 1.3 カスタムコマンド (commands/)

| コマンド | 機能 |
|---------|------|
| `/ndf:serena` | Serena MCP操作ガイド |
| `/ndf:pr` | PR作成ワークフロー |
| `/ndf:pr-tests` | Test Plan自動実行 |
| `/ndf:fix` | レビュー指摘修正 |
| `/ndf:review` | コードレビュー |
| `/ndf:merged` | マージ後処理 |
| `/ndf:clean` | ブランチクリーンアップ |
| `/ndf:mem-review` | Memory戦略レビュー |
| `/ndf:mem-capture` | Memory記録 |

### 1.4 サブエージェント (agents/)

| エージェント | 役割 | ツール |
|------------|------|--------|
| director | タスク統括・指揮者 | 全ツール |
| data-analyst | データ分析 | BigQuery, DBHub |
| corder | コーディング | GitHub, Serena |
| researcher | 調査 | Web検索, AWS Docs |
| scanner | ファイル読み取り | fs_read, PDF/Excel解析 |
| qa | 品質管理 | セキュリティスキャン |

### 1.5 スキル (skills/)

| スキル | 機能 |
|--------|------|
| data-analyst-sql-optimization | SQL最適化 |
| data-analyst-export | データエクスポート |
| corder-code-templates | コードテンプレート |
| corder-test-generation | テスト生成 |
| researcher-report-templates | レポートテンプレート |
| scanner-pdf-analysis | PDF解析 |
| scanner-excel-extraction | Excel抽出 |
| qa-security-scan | セキュリティスキャン |
| markdown-writing | Markdown文書作成 |
| memory-handling | Memory戦略 |
| python-execution | Python実行環境判定 |
| docker-container-access | Dockerコンテナアクセス |
| skill-development | Skill開発ガイド |

### 1.6 フック (hooks/)

**SessionStart**:
1. NDF Plugin Guideを`CLAUDE.md`に自動注入
2. Memory戦略を`.serena/memories`に初期化

**Stop**:
- Slack通知（セッション終了時）

## 2. Claude Code Plugin Marketplace仕様

### 2.1 マーケットプレイス構造

```
.claude-plugin/
└── marketplace.json          # マーケットプレイス定義
```

**marketplace.json**:
```json
{
  "name": "marketplace-name",
  "owner": { "name": "...", "url": "..." },
  "plugins": [
    {
      "name": "plugin-name",
      "source": "./plugins/plugin-name"
    }
  ]
}
```

### 2.2 プラグイン構造

```
plugins/{plugin-name}/
├── .claude-plugin/
│   └── plugin.json           # 必須
├── commands/                 # スラッシュコマンド
├── agents/                   # サブエージェント
├── skills/                   # プロジェクトスキル
├── hooks/                    # フック
└── .mcp.json                 # MCP設定（オプション）
```

### 2.3 主要機能

| 機能 | Claude Code | 説明 |
|------|-------------|------|
| Marketplace | ✅ | プラグインカタログ |
| Plugin | ✅ | 拡張パッケージ |
| Commands | ✅ | スラッシュコマンド |
| Agents | ✅ | サブエージェント |
| Skills | ✅ | 自動起動機能 |
| Hooks | ✅ | ライフサイクルフック |
| MCP | ✅ | Model Context Protocol |

## 3. Kiro CLI類似機能調査

### 3.1 Kiro CLIの機能

| 機能 | Kiro CLI | 説明 |
|------|----------|------|
| Marketplace | ❌ | なし |
| Plugin | ❌ | なし |
| Commands | ✅ | スラッシュコマンド（組み込み） |
| Agents | ✅ | エージェント設定（JSON） |
| Skills | ❌ | なし |
| Hooks | ✅ | コンテキストフック |
| MCP | ✅ | MCP統合 |

### 3.2 Kiro CLIの組み込みコマンド

```
/quit, /clear, /agent, /chat, /context, /code, /editor, /reply, 
/compact, /tools, /issue, /logdump, /changelog, /prompts, /hooks, 
/usage, /mcp, /model, /experiment, /plan, /todos, /paste, /help
```

### 3.3 Kiro CLIのエージェント

**エージェント設定ファイル**: `.kiro/agents/{agent-name}.json`

```json
{
  "description": "エージェントの説明",
  "tools": ["fs_read", "fs_write", "execute_bash"],
  "allowedTools": ["fs_read"],
  "toolsSettings": {
    "fs_write": {
      "allowedPaths": ["~/projects"]
    }
  },
  "resources": [
    "file://README.md",
    "file://docs/**/*.md"
  ]
}
```

### 3.4 Kiro CLIのフック

**フック設定**: エージェント設定内の`hooks`フィールド

```json
{
  "hooks": {
    "agentSpawn": [...],
    "userPromptSubmit": [...],
    "preToolUse": [...],
    "postToolUse": [...],
    "stop": [...]
  }
}
```

### 3.5 Kiro CLIのMCP

**MCP設定**: `.kiro/mcp.json`

```json
{
  "mcpServers": {
    "server-name": {
      "command": "...",
      "args": [...],
      "env": {...}
    }
  }
}
```

### 3.6 Kiro CLIのプロンプト

**プロンプト**: `.kiro/prompts/{name}.md`

- `@research`: コードベース調査
- `@plan`: 実装計画作成
- `@implement`: 計画実行
- `@validate`: 実装検証
- `@commit`: Git コミット

## 4. 機能マッピング

| Claude Code機能 | Kiro CLI相当機能 | 移植可否 | 備考 |
|----------------|-----------------|---------|------|
| Marketplace | なし | ❌ | Kiro CLIに概念なし |
| Plugin | なし | ❌ | Kiro CLIに概念なし |
| Commands | スラッシュコマンド | ⚠️ | 組み込みのみ、カスタム不可 |
| Agents | エージェント設定 | ✅ | JSON設定で実現可能 |
| Skills | なし | ⚠️ | プロンプト+エージェントで代替 |
| Hooks | フック | ✅ | 同等機能あり |
| MCP | MCP | ✅ | 同等機能あり |

### 4.1 移植戦略

#### ✅ 直接移植可能
- **MCP統合**: `.kiro/mcp.json`に設定
- **フック**: エージェント設定の`hooks`フィールド
- **エージェント**: `.kiro/agents/`にJSON設定

#### ⚠️ 代替実装が必要
- **カスタムコマンド**: プロンプト（`.kiro/prompts/`）で代替
- **スキル**: プロンプト+エージェント設定で代替

#### ❌ 移植不可
- **Marketplace**: Kiro CLIに概念なし
- **Plugin**: Kiro CLIに概念なし

## 5. 結論

### 5.1 移植 vs 拡張

**結論**: **別リポジトリで移植**

**理由**:
1. Kiro CLIにはMarketplace/Plugin概念がない
2. 同じリポジトリで管理すると混乱を招く
3. Kiro CLI向けの独自構造が必要

### 5.2 新リポジトリ名案

- `kiro-ndf-config`
- `ndf-kiro`
- `kiro-ndf-agents`

### 5.3 移植範囲

**Phase 1: コア機能**
- MCP統合（7サーバー）
- 基本エージェント（6種類）
- 基本フック（Slack通知）

**Phase 2: 拡張機能**
- プロンプト（コマンド代替）
- スキル相当のプロンプト

**Phase 3: ドキュメント**
- インストールガイド
- 使用方法
- トラブルシューティング
