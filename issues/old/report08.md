# Claude Code ベストプラクティス調査レポート

## 調査日時
2026-01-30

## 調査対象記事
1. [everything-claude-code](https://github.com/affaan-m/everything-claude-code) - Anthropicハッカソン優勝者による本番環境対応設定集
2. [Claude Code 実践ガイド (Qiita)](https://qiita.com/dai_chi/items/c19be47044d062d59ee8)
3. [Claude Code 実装パターン (Zenn)](https://zenn.dev/ttks/articles/a54c7520f827be)

---

## 📊 各記事の要約

### 1. everything-claude-code (GitHub)

**概要**: 10ヶ月以上の実戦使用から生まれた本番環境対応の設定集

**主要コンポーネント**:
- **エージェント**: 専門化された部分タスク実行（例: code-reviewer, tdd-guide）
- **スキル**: 再利用可能なワークフロー定義（TDD実装、バックエンド設計パターン等）
- **ルール**: 常時適用される指針（セキュリティチェック、コーディング規約、テスト要件）
- **ホック**: ツール実行時に自動発火する処理（console.log検出警告等）

**重要な発見**:
- ⚠️ **コンテキスト窓の縮小問題**: MCPツール有効化により200k→70kへ縮小する可能性
- 推奨設定: MCP設定時20～30個、プロジェクト単位で10個以下、アクティブツール80未満
- パッケージマネージャー自動検出機構（npm/pnpm/yarn/bun）
- 継続学習システム（instinct-based learning with confidence scoring）

**実装パターン**:
- TDDワークフロー: インターフェース定義→RED→GREEN→リファクタリング→カバレッジ検証
- 検証ループ: チェックポイント vs 継続評価の使い分け
- クロスプラットフォーム対応（Windows/macOS/Linux）
- GitHub App ベースのスキル自動生成機能

### 2. Claude Code 実践ガイド (Qiita)

**概要**: 5つの中核原則による効率的な設定管理

**5つの中核原則**:
1. **段階的改善** - 完璧性より実用性を優先
2. **コンテキスト効率化** - ツール過剰問題を回避、未使用MCPを無効化
3. **並列実行活用** - `/fork`コマンドとgit worktreesの使い分け
4. **定型作業自動化** - Hooksで繰り返し作業を排除
5. **エージェントスコープ制限** - Subagentに限定ツールのみ許可

**実装レベルのテクニック**:

| Hooks設定例 | 用途 |
|------------|------|
| Prettier自動フォーマット | PostToolUse時の整形 |
| console.log検出警告 | 2段階チェック |
| TypeScript型チェック | 自動実行 |
| tmux使用促進 | PreToolUse時 |

**並列ワークフロー判断基準**:
- 異なるファイル群 → `/fork`（軽量）
- 同一ファイル編集可能性 → git worktrees（競合防止）

**コンテキスト管理ルール**:
- MCP設定数: 20-30個以内
- プロジェクト有効化: 10個以下
- 総ツール数: 80個以下維持
- `/status`で監視、50-60%で`/compact`実行

### 3. Claude Code 実装パターン (Zenn)

**概要**: 4つの基本原則に基づく実証済みアプローチ

**4つの基本原則**:
1. **エージェント分業制**: 専門エージェントに必要最小限のツール(5個程度)を配置
2. **TDD中心ワークフロー**: RED→GREEN→REFACTORサイクル、80%以上カバレッジ必須
3. **セキュリティファースト**: コミット前の脆弱性、入力検証、シークレット混入チェック
4. **コンテキスト管理**: MCPは20～30個設定、プロジェクト単位で10個以下を有効化

**実装パターン**:
- **agents/**: planner.md、architect.md、tdd-guide.md、code-reviewer.md、security-reviewer.md等
- **commands/**: `/tdd`、`/plan`、`/code-review`、`/build-fix`等のスラッシュコマンド
- **rules/**: モジュラーに分割されたルールファイル（セキュリティ、コーディング規格、テスト要件）
- **hooks/**: PostToolUse時の自動フォーマット、console.log検出等

**重要な実績データ**:
- MCPサーバー有効化で200k→70kへコンテキスト減少
- ツール数80個以下制限と選別が重要

---

## 🎯 affaan-mプラグインとして実装すべき機能

### 前提: NDFプラグインとの併用

**NDFプラグインの役割（既存）**:
- MCP統合（6個のMCPサーバー）
- ワークフローコマンド（6個のスラッシュコマンド）
- 専門エージェント（6個のサブエージェント）
- スキル（8個のClaude Code Skills）

**affaan-mプラグインの役割（新規）**:
- コンテキスト管理
- 品質保証機能
- TDDワークフロー
- セキュリティチェック
- 開発効率化機能

### Phase 1: 基盤整備（v1.0.0）

#### 1. コンテキスト管理機能

**問題**: MCPツール有効化により200k→70kへコンテキストが大幅縮小

**実装機能**:
- コンテキスト監視コマンド: `/context-status`
- 自動コンパクト化: 60%閾値で`/compact`実行
- MCP数警告: 10個超過時に警告表示
- ツール数監視: 80個以下を推奨

**実装場所**:
```
plugins/affaan-m/
├── commands/
│   └── context-status.md       # コンテキスト監視コマンド
├── hooks/
│   └── context-monitor.js      # 自動監視フック
└── docs/
    └── context-management.md   # コンテキスト管理ガイド
```

#### 2. Hooksシステム

**実装するHooks**:

| Hook タイプ | 機能 | 優先度 |
|-----------|------|--------|
| PostToolUse | 自動フォーマット（Prettier/ESLint） | 高 |
| PostToolUse | console.log/debugger検出警告 | 高 |
| PostToolUse | TypeScript型チェック自動実行 | 中 |
| PreCommit | シークレット混入チェック | 高 |
| PreCommit | テストカバレッジ検証（80%以上） | 中 |
| PreToolUse | tmux使用促進、環境チェック | 低 |

**実装場所**:
```
plugins/affaan-m/
├── hooks/
│   ├── hooks.json               # Hooks定義
│   ├── auto-format.js           # 自動フォーマット
│   ├── detect-console-log.js    # console.log検出
│   ├── typescript-check.js      # TypeScript型チェック
│   ├── secret-scan.js           # シークレット混入チェック
│   └── coverage-check.js        # カバレッジ検証
└── docs/
    └── hooks-guide.md           # Hooks設定ガイド
```

#### 3. TDDワークフローコマンド

**5段階TDDプロセス**:
1. インターフェース定義
2. RED（失敗テスト作成）
3. GREEN（最小実装）
4. リファクタリング
5. カバレッジ検証（80%以上）

**実装コマンド**:
- `/tdd` - TDDワークフロー開始
- `/tdd-red` - 失敗テスト作成
- `/tdd-green` - 最小実装
- `/tdd-refactor` - リファクタリング
- `/tdd-coverage` - カバレッジ検証

**実装場所**:
```
plugins/affaan-m/
├── commands/
│   ├── tdd.md                  # TDDワークフローコマンド
│   ├── tdd-red.md              # REDフェーズ
│   ├── tdd-green.md            # GREENフェーズ
│   ├── tdd-refactor.md         # リファクタリング
│   └── tdd-coverage.md         # カバレッジ検証
├── skills/
│   └── tdd-workflow/
│       └── SKILL.md            # TDDワークフロースキル
└── docs/
    └── tdd-guide.md            # TDDガイド
```

#### 4. セキュリティチェック機能

**実装機能**:
- OWASP Top 10チェックリスト
- シークレット混入検出
- 入力検証パターン
- セキュリティレビューコマンド

**実装コマンド**:
- `/security-scan` - セキュリティスキャン
- `/owasp-check` - OWASP Top 10チェック

**実装場所**:
```
plugins/affaan-m/
├── commands/
│   ├── security-scan.md        # セキュリティスキャン
│   └── owasp-check.md          # OWASP Top 10チェック
├── skills/
│   └── security-review/
│       └── SKILL.md            # セキュリティレビュースキル
└── docs/
    └── security-guide.md       # セキュリティガイド
```

#### 5. パッケージマネージャー自動検出

**検出順序**:
1. 環境変数チェック
2. プロジェクト設定ファイル
3. lockファイル検出（package-lock.json, pnpm-lock.yaml, yarn.lock, bun.lockb）

**実装場所**:
```
plugins/affaan-m/
├── hooks/
│   └── detect-package-manager.js  # パッケージマネージャー検出
└── docs/
    └── package-manager-guide.md   # パッケージマネージャーガイド
```

---

## 🔄 NDFプラグインとの役割分担

### NDFプラグイン（既存）

**役割**: MCP統合、ワークフロー、専門エージェント

| カテゴリ | 機能 |
|---------|------|
| **MCP統合** | Codex CLI, BigQuery, AWS Docs, Chrome DevTools等（6個） |
| **ワークフローコマンド** | `/commit`, `/review-pr`, `/slack-notify`等（6個） |
| **専門エージェント** | director, corder, data-analyst, researcher, scanner, qa（6個） |
| **スキル** | データ分析、コード生成、リサーチ等（8個） |

### affaan-mプラグイン（新規）

**役割**: コンテキスト管理、品質保証、TDDワークフロー

| カテゴリ | 機能 |
|---------|------|
| **コンテキスト管理** | `/context-status`、自動コンパクト化、MCP数監視 |
| **品質保証** | Hooks（自動フォーマット、console.log検出、型チェック） |
| **TDDワークフロー** | `/tdd`関連コマンド、TDDスキル |
| **セキュリティ** | `/security-scan`、`/owasp-check`、シークレット検出 |
| **開発効率化** | パッケージマネージャー自動検出 |

### 併用シナリオ

**シナリオ1: コーディング作業**
1. NDFプラグイン: `ndf:corder`エージェントでコード実装
2. affaan-mプラグイン: PostToolUse Hooksで自動フォーマット、型チェック
3. affaan-mプラグイン: `/tdd`コマンドでテスト駆動開発

**シナリオ2: セキュリティレビュー**
1. NDFプラグイン: `ndf:qa`エージェントでコード品質レビュー
2. affaan-mプラグイン: `/security-scan`で脆弱性チェック
3. affaan-mプラグイン: PreCommit Hooksでシークレット混入検出

**シナリオ3: コンテキスト管理**
1. affaan-mプラグイン: `/context-status`でコンテキスト使用率確認
2. affaan-mプラグイン: 60%超過時に自動コンパクト化
3. NDFプラグイン: MCP統合を継続（affaan-mが監視）

---

## 📦 affaan-mプラグインの初期構成案

### ディレクトリ構造

```
plugins/affaan-m/
├── .claude-plugin/
│   └── plugin.json              # プラグインメタデータ
├── commands/
│   ├── context-status.md        # コンテキスト監視
│   ├── tdd.md                   # TDDワークフロー
│   ├── tdd-red.md               # REDフェーズ
│   ├── tdd-green.md             # GREENフェーズ
│   ├── tdd-refactor.md          # リファクタリング
│   ├── tdd-coverage.md          # カバレッジ検証
│   ├── security-scan.md         # セキュリティスキャン
│   └── owasp-check.md           # OWASP Top 10チェック
├── hooks/
│   ├── hooks.json               # Hooks定義
│   ├── context-monitor.js       # コンテキスト監視
│   ├── auto-format.js           # 自動フォーマット
│   ├── detect-console-log.js    # console.log検出
│   ├── typescript-check.js      # TypeScript型チェック
│   ├── secret-scan.js           # シークレット混入チェック
│   ├── coverage-check.js        # カバレッジ検証
│   └── detect-package-manager.js # パッケージマネージャー検出
├── skills/
│   ├── tdd-workflow/
│   │   └── SKILL.md             # TDDワークフロースキル
│   └── security-review/
│       └── SKILL.md             # セキュリティレビュースキル
├── docs/
│   ├── context-management.md    # コンテキスト管理ガイド
│   ├── hooks-guide.md           # Hooks設定ガイド
│   ├── tdd-guide.md             # TDDガイド
│   ├── security-guide.md        # セキュリティガイド
│   └── package-manager-guide.md # パッケージマネージャーガイド
├── README.md                    # プラグイン説明
└── CHANGELOG.md                 # 変更履歴
```

### plugin.json

```json
{
  "name": "affaan-m",
  "version": "1.0.0",
  "description": "コンテキスト管理、品質保証、TDDワークフローを提供するClaude Codeプラグイン（NDFプラグイン併用前提）",
  "author": {
    "name": "takemi-ohama",
    "url": "https://github.com/takemi-ohama"
  },
  "keywords": [
    "context-management",
    "quality-assurance",
    "tdd",
    "security",
    "hooks",
    "productivity"
  ],
  "commands": [
    "./commands/context-status.md",
    "./commands/tdd.md",
    "./commands/tdd-red.md",
    "./commands/tdd-green.md",
    "./commands/tdd-refactor.md",
    "./commands/tdd-coverage.md",
    "./commands/security-scan.md",
    "./commands/owasp-check.md"
  ],
  "skills": [
    "./skills/tdd-workflow",
    "./skills/security-review"
  ],
  "hooks": "./hooks/hooks.json",
  "dependencies": {
    "ndf": "^2.1.0"
  },
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

---

## 📋 導入ロードマップ

### Phase 1: 基盤整備（v1.0.0）【優先度: 高】

**目標**: コンテキスト管理、基本Hooks、TDDワークフロー

**実装内容**:
- [ ] プラグイン基盤構築（plugin.json、ディレクトリ構造）
- [ ] コンテキスト管理機能（`/context-status`、自動コンパクト化）
- [ ] 基本Hooks（auto-format, secret-scan, console.log検出）
- [ ] TDDワークフローコマンド（`/tdd`関連）
- [ ] TDDワークフロースキル
- [ ] パッケージマネージャー自動検出
- [ ] ドキュメント作成（README、各種ガイド）

**成功基準**:
- プラグインが正常にインストールできる
- `/context-status`でコンテキスト使用率を確認できる
- `/tdd`コマンドでTDDワークフローを実行できる
- Hooksが正常に発火する
- NDFプラグインと併用できる

**期間**: 2週間

### Phase 2: 品質向上（v1.1.0）【優先度: 中】

**目標**: セキュリティ機能、追加Hooks

**実装内容**:
- [ ] セキュリティスキャン機能（`/security-scan`、`/owasp-check`）
- [ ] セキュリティレビュースキル
- [ ] TypeScript型チェックHook
- [ ] カバレッジ検証Hook
- [ ] tmux使用促進Hook
- [ ] セキュリティガイド充実

**成功基準**:
- `/security-scan`でOWASP Top 10チェックができる
- PreCommit Hooksでシークレット混入を検出できる
- カバレッジ80%未満時に警告が表示される

**期間**: 1週間

### Phase 3: 効率化（v1.2.0）【優先度: 低】

**目標**: 並列ワークフロー、高度なコンテキスト管理

**実装内容**:
- [ ] `/fork`コマンドのサポート（git worktrees）
- [ ] 並列実行判断ロジック
- [ ] コンテキスト最適化アドバイザー
- [ ] MCP推奨設定ガイド

**成功基準**:
- 並列実行可能なタスクを自動判断できる
- `/fork`コマンドでgit worktreesを活用できる
- MCP数超過時に最適化アドバイスが表示される

**期間**: 1週間

---

## 🔍 実装時の注意事項

### 1. NDFプラグインとの互換性

**必須事項**:
- NDFプラグインのMCP統合に干渉しない
- NDFプラグインのエージェントと重複しない
- NDFプラグインのコマンドと命名衝突しない
- `dependencies`に`ndf: ^2.1.0`を明記

**推奨事項**:
- NDFプラグインの`ndf:corder`、`ndf:qa`と連携するHooks設計
- NDFプラグインの`/commit`、`/review-pr`と補完的な機能提供

### 2. Hooksシステム

**必須事項**:
- Node.js統一で記述（OS依存性排除）
- Windows/macOS/Linux対応
- エラーハンドリング徹底（Hook失敗でもメイン処理は継続）

**推奨事項**:
- Hooksは設定ファイルでON/OFF可能にする
- Hook実行時間を監視（遅延防止）
- Hook失敗時のフォールバック処理

### 3. TDDワークフロー

**必須事項**:
- 80%カバレッジは推奨値（強制ではない）
- プロジェクトの性質に応じてカスタマイズ可能
- カバレッジ未達時は警告のみ（ブロックしない）

**推奨事項**:
- RED→GREEN→REFACTORの順序を厳守
- カバレッジ閾値を設定ファイルで調整可能にする

### 4. セキュリティチェック

**必須事項**:
- OWASP Top 10に準拠
- シークレット検出パターンは設定ファイルで管理
- 誤検知を減らす正規表現パターン

**推奨事項**:
- セキュリティスキャン結果をレポート化
- 検出した脆弱性の修正ガイド提供

### 5. コンテキスト管理

**必須事項**:
- MCP数の上限警告（10個超過）
- ツール数の上限警告（80個超過）
- コンテキスト使用率の監視（60%閾値）

**推奨事項**:
- `/compact`の自動実行（ユーザー確認あり）
- MCP最適化のアドバイス表示
- コンテキスト使用率の履歴記録

---

## 🔧 NDFプラグインの修正提案

affaan-mプラグインとの円滑な連携のため、NDFプラグイン側にも以下の修正を推奨します。

### 【優先度: 高】ドキュメントの更新

#### 1. README.md の更新

**追加セクション**:
```markdown
## 推奨プラグイン併用

### affaan-m プラグイン

NDFプラグインと併用することで、以下の機能が追加されます：

- **コンテキスト管理**: `/context-status`でコンテキスト使用率を監視
- **品質保証**: 自動フォーマット、console.log検出、シークレットスキャン
- **TDDワークフロー**: `/tdd`コマンドで5段階TDDプロセスをガイド
- **セキュリティチェック**: OWASP Top 10準拠の脆弱性検出

インストール方法:
\```bash
/plugin install affaan-m@ai-plugins
\```

詳細は[affaan-mプラグインREADME](../affaan-m/README.md)を参照してください。
```

#### 2. CLAUDE.ndf.md の更新

**追加セクション**:
```markdown
### 8. 推奨プラグイン併用

**affaan-m プラグイン（推奨）**:

NDFプラグインと併用することで、以下の機能が追加されます：

- **コンテキスト管理**: コンテキスト使用率の監視と自動最適化
- **品質保証Hooks**: 自動フォーマット、セキュリティスキャン
- **TDDワークフロー**: テストファーストな開発サイクルのガイド

**使用例**:
\```
# コンテキスト使用率を確認
/context-status

# TDDワークフローを開始
/tdd "ユーザー認証機能"

# セキュリティスキャンを実行
/security-scan
\```

**注意事項**:
- affaan-mプラグインのHooksは自動的に発火します
- コンテキスト管理機能は常時監視モードで動作します
- TDDワークフローはNDFの`corder`エージェントと連携します
```

### 【優先度: 中】directorエージェントの更新

**agents/director.md に追加**:

```markdown
### affaan-mプラグインとの連携

**コンテキスト管理**:
- タスク開始前に`/context-status`でコンテキスト使用率を確認
- 60%を超える場合は警告し、`/compact`の実行を推奨

**TDDワークフローの推奨**:
- コーディングタスクでは`/tdd`コマンドの使用を提案
- テスト未実装の場合はTDDワークフローを推奨

**品質保証**:
- コード生成後、affaan-mプラグインのHooksが自動的にチェックを実行
- 警告が出た場合は修正を指示

**使用例**:
\```
# タスク開始前
1. コンテキスト確認: `/context-status`
2. TDDワークフロー開始: `/tdd "機能名"`
3. サブエージェント起動（ndf:corder等）
4. Hooksによる自動チェック（affaan-mが自動実行）
5. 完了確認
\```
```

### 【優先度: 中】corderエージェントの更新

**agents/corder.md に追加**:

```markdown
### TDDワークフローとの連携

**affaan-mプラグインのTDDワークフローと連携する場合**:

1. **インターフェース定義**（TDD Step 1）
   - 関数シグネチャ、型定義を先に決定

2. **RED（失敗テスト）**（TDD Step 2）
   - `/tdd-red`コマンドで失敗テストを作成
   - テストが失敗することを確認

3. **GREEN（最小実装）**（TDD Step 3）
   - `/tdd-green`コマンドで最小実装
   - テストをパスする最小限のコード

4. **リファクタリング**（TDD Step 4）
   - `/tdd-refactor`コマンドでコード品質向上
   - テストは常にパスする状態を維持

5. **カバレッジ検証**（TDD Step 5）
   - `/tdd-coverage`コマンドでカバレッジ確認
   - 80%以上を目標（affaan-mプラグインが自動チェック）

**注意事項**:
- affaan-mプラグインのHooksが自動的にコード品質をチェックします
- console.log、シークレット、型エラーは自動検出されます
```

### 【優先度: 低】コンテキスト管理のベストプラクティス追加

**CLAUDE.md に追加**:

```markdown
## コンテキスト管理のベストプラクティス

### MCP設定の推奨事項

**推奨上限**:
- グローバル設定: 20-30個以内
- プロジェクト設定: 10個以下
- 総ツール数: 80個以下

**監視方法**:
- affaan-mプラグインの`/context-status`コマンドで監視
- コンテキスト使用率が60%を超えたら`/compact`を実行

**最適化手順**:
1. 使用頻度の低いMCPサーバーを無効化
2. プロジェクト固有のMCPのみを有効化
3. 定期的に`/context-status`でチェック

**例**:
\```bash
# コンテキスト使用率を確認
/context-status

# 60%を超えている場合
/compact

# MCP設定を最適化
# 不要なMCPサーバーをdisableにする
\```
```

### 【優先度: 低】marketplace.jsonの更新

**推奨プラグイン情報の追加**:

```json
{
  "name": "ai-plugins",
  "owner": {
    "name": "takemi-ohama",
    "url": "https://github.com/takemi-ohama"
  },
  "plugins": [
    {
      "name": "ndf",
      "source": "./plugins/ndf"
    },
    {
      "name": "affaan-m",
      "source": "./plugins/affaan-m",
      "recommended": true,
      "complementary": ["ndf"]
    }
  ],
  "recommendations": [
    {
      "plugins": ["ndf", "affaan-m"],
      "description": "NDFプラグインとaffaan-mプラグインの併用で、MCP統合、ワークフロー、コンテキスト管理、品質保証の完全な開発環境を構築できます。"
    }
  ]
}
```

### 修正の優先順位

| 優先度 | 修正内容 | 影響範囲 | 工数 |
|-------|---------|---------|------|
| **高** | README.md更新 | ユーザー向けドキュメント | 小 |
| **高** | CLAUDE.ndf.md更新 | AI向けガイドライン | 中 |
| **中** | directorエージェント更新 | タスク実行フロー | 中 |
| **中** | corderエージェント更新 | コーディングワークフロー | 小 |
| **低** | CLAUDE.md更新 | 開発者向けガイド | 小 |
| **低** | marketplace.json更新 | マーケットプレイス連携 | 小 |

### 実装タイミング

- **v2.2.1（パッチ版）**: ドキュメント更新のみ
  - README.md
  - CLAUDE.ndf.md
  - CLAUDE.md

- **v2.3.0（マイナー版）**: エージェント更新
  - directorエージェント
  - corderエージェント

- **v2.3.1（パッチ版）**: マーケットプレイス連携
  - marketplace.json

---

## 📚 参考リンク

- [everything-claude-code](https://github.com/affaan-m/everything-claude-code)
- [Claude Code 実践ガイド (Qiita)](https://qiita.com/dai_chi/items/c19be47044d062d59ee8)
- [Claude Code 実装パターン (Zenn)](https://zenn.dev/ttks/articles/a54c7520f827be)
- [Claude Code 公式ドキュメント](https://docs.claude.com/en/docs/claude-code)

---

## 📝 まとめ

### affaan-mプラグインの目的

**NDFプラグインとの併用を前提**として、以下の補完的な機能を提供：

1. **コンテキスト管理** - MCPツール過剰によるコンテキスト枯渇を防止
2. **品質保証機能** - Hooksによる自動チェックと人的ミス防止
3. **TDDワークフロー** - テストファーストな開発文化の確立
4. **セキュリティチェック** - OWASP Top 10に準拠した脆弱性検出
5. **開発効率化** - パッケージマネージャー自動検出等

### NDFプラグインとの役割分担

| プラグイン | 役割 |
|-----------|------|
| **NDFプラグイン** | MCP統合、ワークフロー、専門エージェント |
| **affaan-mプラグイン** | コンテキスト管理、品質保証、TDDワークフロー |

### 実装ロードマップ

#### Phase 1: affaan-mプラグイン作成（v1.0.0）

**実装内容**:
- コンテキスト管理（`/context-status`、自動コンパクト化）
- 基本Hooks（auto-format, secret-scan, console.log検出）
- TDDワークフロー（`/tdd`関連コマンド、TDDスキル）
- パッケージマネージャー自動検出
- ドキュメント整備

#### Phase 2: NDFプラグイン更新（v2.2.1 〜 v2.3.0）

**v2.2.1（パッチ版）- ドキュメント更新**:
- README.md: affaan-m併用の推奨
- CLAUDE.ndf.md: 併用時のガイドライン
- CLAUDE.md: コンテキスト管理ベストプラクティス

**v2.3.0（マイナー版）- エージェント更新**:
- directorエージェント: affaan-m連携ロジック追加
- corderエージェント: TDDワークフロー連携

**v2.3.1（パッチ版）- マーケットプレイス連携**:
- marketplace.json: 推奨プラグイン情報追加

### 期待される効果

**併用による相乗効果**:
- NDFプラグイン（MCP統合 + 専門エージェント）
- affaan-mプラグイン（コンテキスト管理 + 品質保証）
- = **本番環境対応の開発支援環境**

これらを段階的に導入することで、Anthropicハッカソン優勝者（Affaan Mustafa氏）が実証した本番環境レベルの開発支援環境を構築できます。
