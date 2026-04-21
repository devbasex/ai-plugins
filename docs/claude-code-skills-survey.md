# Claude Code Skills 調査レポート

**調査日**: 2025-12-15
**調査対象**: 公開されているClaude Code Skillsのリポジトリとマーケットプレイス
**目的**: NDFプラグインの6つのsub-agent（director、data-analyst、corder、researcher、scanner、qa）に適用できるSkillsを特定

---

## エグゼクティブサマリー

- **発見したSkills総数**: 50個以上
- **取り込み推奨（高）**: 15個
- **取り込み推奨（中）**: 12個
- **主要リポジトリ**:
  - obra/superpowers（9.8k stars）- 20個の実戦テスト済みskills
  - travisvn/awesome-claude-skills（2.8k stars）- キュレーション済みリスト
  - anthropics/skills - 公式スキル（document-skills、example-skills）
  - jeremylongshore/claude-code-plugins-plus - 240個のエージェントスキル

---

## 発見された公開Skills

### 【高優先度】obra/superpowers - コアライブラリ

#### 1. test-driven-development
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/test-driven-development
- **説明**: RED-GREEN-REFACTORサイクルによるTDD実践
- **提供機能**:
  - テスト先行開発の強制
  - 失敗確認→実装→リファクタリングの3フェーズ
  - テストが本当に正しい動作を検証していることの保証
- **適用先sub-agent**: corder、qa
- **取り込み推奨度**: 高
- **理由**: corderの実装品質向上、qaのテスト戦略に直接活用可能。NDFプラグインのコーディング品質を大幅に向上できる

#### 2. systematic-debugging
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/systematic-debugging
- **説明**: 4フェーズのデバッグ手法（根本原因調査→パターン分析→仮説検証→実装）
- **提供機能**:
  - 根本原因を特定せずに修正しない原則
  - スタックトレース解析
  - 段階的仮説検証
  - アーキテクチャ見直しの判断基準
- **適用先sub-agent**: corder、qa
- **取り込み推奨度**: 高
- **理由**: バグ修正タスクの品質向上。表面的な対処ではなく根本原因解決を徹底できる

#### 3. brainstorming
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/brainstorming
- **説明**: ソクラテス式質問による設計改良
- **提供機能**:
  - 1問ずつの質問で要件を精緻化
  - 2-3個の代替案提示とトレードオフ分析
  - 段階的デザイン検証
  - YAGNIの徹底
- **適用先sub-agent**: director
- **取り込み推奨度**: 高
- **理由**: directorのタスク分解・計画立案フェーズで威力発揮。要件を深掘りして最適な設計を導ける

#### 4. writing-plans
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/writing-plans
- **説明**: 詳細な実装計画の生成
- **提供機能**:
  - 2-5分単位のタスク分解
  - 正確なファイルパスと完全なコード例
  - TDD手法に従ったステップ構造（テスト→実装→検証→コミット）
  - DRY、YAGNI、TDD原則の適用
- **適用先sub-agent**: director、corder
- **取り込み推奨度**: 高
- **理由**: directorの計画立案に最適。corderへの指示も具体化できる。NDFの並列実行推奨機能と相性が良い

#### 5. dispatching-parallel-agents
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/dispatching-parallel-agents
- **説明**: 並列タスク実行の判断と調整
- **提供機能**:
  - 並列実行可能性の判断基準（独立性、スコープ分離、非干渉）
  - エージェント間の調整メカニズム
  - 結果統合とコンフリクト回避
- **適用先sub-agent**: director
- **取り込み推奨度**: 高
- **理由**: NDFプラグインの並列実行推奨機能を強化。directorが適切に並列化判断できる

#### 6. executing-plans
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/executing-plans
- **説明**: 計画の段階的実行
- **提供機能**:
  - バッチ実行とチェックポイント
  - タスクごとの検証
  - 進捗追跡
- **適用先sub-agent**: director、corder
- **取り込み推奨度**: 中
- **理由**: writing-plansとセットで使用。NDFのTodoList機能と統合すると効果的

#### 7. verification-before-completion
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/verification-before-completion
- **説明**: 完了前の多層検証
- **提供機能**:
  - 要件充足確認
  - テスト実行確認
  - エッジケース検証
- **適用先sub-agent**: qa、corder
- **取り込み推奨度**: 高
- **理由**: タスク完了判定の品質向上。qaエージェントのレビュー基準として活用

#### 8. defense-in-depth
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/defense-in-depth
- **説明**: 多層防御による検証
- **提供機能**:
  - 複数レイヤーでの検証
  - セキュリティ考慮
- **適用先sub-agent**: qa、corder
- **取り込み推奨度**: 中
- **理由**: セキュアなコード実装のガイダンス

#### 9. root-cause-tracing
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/root-cause-tracing
- **説明**: 問題の根本原因特定
- **提供機能**:
  - データフロー逆追跡
  - 境界診断
- **適用先sub-agent**: qa、corder
- **取り込み推奨度**: 中
- **理由**: systematic-debuggingと併用で効果的

#### 10. requesting-code-review / receiving-code-review
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/requesting-code-review
- **説明**: コードレビュー依頼と受領
- **提供機能**:
  - レビュー前チェックリスト
  - フィードバック処理
- **適用先sub-agent**: qa、corder
- **取り込み推奨度**: 中
- **理由**: チーム開発プロセスとの統合

#### 11. using-git-worktrees
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/using-git-worktrees
- **説明**: 並列ブランチ管理
- **提供機能**:
  - 複数作業ディレクトリ管理
- **適用先sub-agent**: director、corder
- **取り込み推奨度**: 低
- **理由**: 高度なGit機能。初心者には複雑すぎる可能性

#### 12. finishing-a-development-branch
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/finishing-a-development-branch
- **説明**: ブランチ完了判断
- **提供機能**:
  - マージ判断基準
- **適用先sub-agent**: director
- **取り込み推奨度**: 中
- **理由**: PR作成前の最終確認

#### 13. subagent-driven-development
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/subagent-driven-development
- **説明**: サブエージェント主導の開発
- **提供機能**:
  - 自律実行
- **適用先sub-agent**: director
- **取り込み推奨度**: 中
- **理由**: NDFのマルチエージェント協調と相性が良い

#### 14. writing-skills / testing-skills-with-subagents
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/writing-skills
- **説明**: 新しいSkillの作成と検証
- **提供機能**:
  - Skill作成ガイダンス
  - サブエージェントによる検証
- **適用先sub-agent**: director、qa
- **取り込み推奨度**: 中
- **理由**: NDFプラグイン自体の拡張に活用

#### 15. condition-based-waiting / testing-anti-patterns
- **リポジトリ**: https://github.com/obra/superpowers/tree/main/skills/condition-based-waiting
- **説明**: 非同期パターン、テストアンチパターンの回避
- **提供機能**:
  - 非同期処理パターン
  - よくあるテストの落とし穴
- **適用先sub-agent**: corder、qa
- **取り込み推奨度**: 中
- **理由**: テスト品質向上

---

### 【高優先度】anthropics/skills - 公式Skills

#### 16. document-skills/pdf
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/document-skills/pdf
- **説明**: PDF操作（テキスト抽出、フォーム入力、マージ）
- **提供機能**:
  - テキスト・テーブル抽出
  - PDFフォーム処理
  - 文書マージ・分割
- **適用先sub-agent**: scanner
- **取り込み推奨度**: 高
- **理由**: scannerの主要機能。pypdf、pdfplumberライブラリの活用方法を標準化

#### 17. document-skills/xlsx
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/document-skills/xlsx
- **説明**: Excel操作（作成、編集、データ分析）
- **提供機能**:
  - スプレッドシート作成・編集
  - 数式適用
  - データ分析
- **適用先sub-agent**: data-analyst、scanner
- **取り込み推奨度**: 高
- **理由**: data-analystのデータ処理、scannerのファイル読み取りに活用

#### 18. document-skills/docx
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/document-skills/docx
- **説明**: Word文書操作
- **提供機能**:
  - 文書作成・編集
  - 変更履歴管理
  - フォーマット適用
- **適用先sub-agent**: scanner、researcher
- **取り込み推奨度**: 中
- **理由**: レポート作成、ドキュメント処理

#### 19. document-skills/pptx
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/document-skills/pptx
- **説明**: PowerPoint操作
- **提供機能**:
  - プレゼンテーション作成
  - レイアウト・チャート
- **適用先sub-agent**: scanner、data-analyst
- **取り込み推奨度**: 低
- **理由**: レポート作成には有用だが、NDFの主要ユースケースではない

#### 20. example-skills/skill-creator
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/example-skills/skill-creator
- **説明**: Q&A形式でSkill作成支援
- **提供機能**:
  - 対話型Skill作成
  - YAMLフロントマター生成
  - ベストプラクティス適用
- **適用先sub-agent**: director
- **取り込み推奨度**: 中
- **理由**: NDFプラグインの拡張に活用

#### 21. example-skills/webapp-testing
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/example-skills/webapp-testing
- **説明**: Playwrightによるローカルwebアプリテスト
- **提供機能**:
  - Playwright自動化
  - UIテスト
- **適用先sub-agent**: qa、researcher
- **取り込み推奨度**: 高
- **理由**: Chrome DevTools MCPと組み合わせて強力。qaのテスト自動化に最適

#### 22. example-skills/mcp-builder
- **リポジトリ**: https://github.com/anthropics/skills/tree/main/example-skills/mcp-builder
- **説明**: MCPサーバー作成ガイド
- **提供機能**:
  - MCPサーバー構築手順
  - 外部API統合
- **適用先sub-agent**: corder
- **取り込み推奨度**: 低
- **理由**: 高度な拡張。一般的なタスクではない

---

### 【中優先度】diet103/claude-code-infrastructure-showcase

#### 23. skill-developer
- **リポジトリ**: https://github.com/diet103/claude-code-infrastructure-showcase/tree/main/skills/skill-developer
- **説明**: メタスキル - 他のスキル作成・管理
- **提供機能**:
  - スキル作成支援（426行）
  - モジュール構造ガイダンス
- **適用先sub-agent**: director
- **取り込み推奨度**: 中
- **理由**: NDFプラグインの保守に有用

#### 24. backend-dev-guidelines
- **リポジトリ**: https://github.com/diet103/claude-code-infrastructure-showcase/tree/main/skills/backend-dev-guidelines
- **説明**: Node.js/Express/Prisma/Sentryパターン
- **提供機能**:
  - バックエンド開発ベストプラクティス（304行）
- **適用先sub-agent**: corder
- **取り込み推奨度**: 中
- **理由**: 特定スタック向け。汎用性は低いが参考になる

#### 25. frontend-dev-guidelines
- **リポジトリ**: https://github.com/diet103/claude-code-infrastructure-showcase/tree/main/skills/frontend-dev-guidelines
- **説明**: React/MUI v7/TypeScriptコンポーネント
- **提供機能**:
  - フロントエンド開発パターン（398行）
- **適用先sub-agent**: corder
- **取り込み推奨度**: 中
- **理由**: 特定スタック向け。汎用性は低いが参考になる

#### 26. route-tester
- **リポジトリ**: https://github.com/diet103/claude-code-infrastructure-showcase/tree/main/skills/route-tester
- **説明**: 認証付きAPIエンドポイントテスト
- **提供機能**:
  - APIテスト（389行）
  - 認証処理
- **適用先sub-agent**: qa
- **取り込み推奨度**: 中
- **理由**: API開発プロジェクトで有用

#### 27. error-tracking
- **リポジトリ**: https://github.com/diet103/claude-code-infrastructure-showcase/tree/main/skills/error-tracking
- **説明**: Sentry統合パターン
- **提供機能**:
  - エラートラッキング（約250行）
- **適用先sub-agent**: corder、qa
- **取り込み推奨度**: 低
- **理由**: Sentry利用者向け。汎用性低い

---

### 【中優先度】個別専門Skills

#### 28. playwright-skill
- **リポジトリ**: https://github.com/lackeyjb/playwright-skill（900 stars）
- **説明**: ブラウザ自動化（Playwright）
- **提供機能**:
  - カスタムPlaywrightコード生成
  - 実行エンジン（run.js）
  - ブラウザ可視化モード
  - スクリーンショット取得
- **適用先sub-agent**: researcher、qa
- **取り込み推奨度**: 高
- **理由**: Chrome DevTools MCPの補完。researcher/qaのWebテスト自動化に最適

#### 29. notebooklm-skill
- **リポジトリ**: https://github.com/PleasePrompto/notebooklm-skill（624 stars）
- **説明**: Google NotebookLM統合
- **提供機能**:
  - ドキュメントクエリ
  - ソース根拠付き回答
  - ライブラリ管理
  - 多段階調査
- **適用先sub-agent**: researcher
- **取り込み推奨度**: 中
- **理由**: 技術ドキュメント調査に有用。ただしNotebookLMアカウント必須

#### 30. raptor - セキュリティフォーカス
- **リポジトリ**: https://github.com/gadievron/raptor（823 stars）
- **説明**: セキュリティエージェント化
- **提供機能**:
  - Semgrep/CodeQLスキャン
  - AFL fuzzing
  - 脆弱性分析・PoC生成・パッチ
  - WebアプリSecテスト
- **適用先sub-agent**: qa
- **取り込み推奨度**: 中
- **理由**: セキュリティ重視プロジェクト向け。専門的すぎる可能性

#### 31. n8n-skills
- **リポジトリ**: https://github.com/czlonkowski/n8n-skills（898 stars）
- **説明**: n8nワークフロー自動化
- **提供機能**:
  - n8n式構文
  - 525+ノード設定
  - 5つの実証済みパターン
  - エラー解決
- **適用先sub-agent**: corder、data-analyst
- **取り込み推奨度**: 低
- **理由**: n8n利用者向け。汎用性低い

---

### 【参考】claude-code-plugins-plus（240スキル）

#### 32. Project Health Auditor
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: コードベース健全性分析
- **適用先sub-agent**: qa
- **取り込み推奨度**: 中
- **理由**: 技術的負債の可視化

#### 33. Conversational API Debugger
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: API障害デバッグ
- **適用先sub-agent**: qa、corder
- **取り込み推奨度**: 中
- **理由**: API開発に有用

#### 34. Domain Memory Agent
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: ナレッジベース構築
- **適用先sub-agent**: researcher
- **取り込み推奨度**: 中
- **理由**: 調査結果の蓄積

#### 35. Web-to-GitHub Issue
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: 調査結果をGitHub issueに変換
- **適用先sub-agent**: researcher、director
- **取り込み推奨度**: 中
- **理由**: 調査→タスク化の自動化

#### 36. Git Commit Smart
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: コミットメッセージ自動生成
- **適用先sub-agent**: corder
- **取り込み推奨度**: 低
- **理由**: 既存のgit hookで対応可能

#### 37. Skills Powerkit
- **リポジトリ**: https://github.com/jeremylongshore/claude-code-plugins-plus
- **説明**: スキル自動スキャフォールド・検証
- **適用先sub-agent**: director
- **取り込み推奨度**: 中
- **理由**: NDFプラグイン拡張に活用

---

## 調査サマリー

### 発見したSkills総数
- **obra/superpowers**: 20個（戦闘テスト済み）
- **anthropics/skills**: 10個以上（公式）
- **diet103/showcase**: 5個（本番検証済み）
- **個別リポジトリ**: 10個以上（コミュニティ人気）
- **claude-code-plugins-plus**: 240個（マーケットプレイス）
- **合計**: 50個以上の質の高いSkills

### 取り込み推奨度別
- **高（15個）**: すぐに取り込むべき
- **中（12個）**: カスタマイズして取り込み
- **低（10個以上）**: 参考にとどめる

### 各sub-agentへの適用候補

#### director向け（計9個）
**高優先度:**
1. brainstorming - 設計改良
2. writing-plans - 実装計画生成
3. dispatching-parallel-agents - 並列実行判断
4. executing-plans - 計画実行

**中優先度:**
5. finishing-a-development-branch - ブランチ完了判断
6. subagent-driven-development - サブエージェント主導
7. skill-creator - Skill作成支援
8. skill-developer - メタスキル
9. Skills Powerkit - スキャフォールド

#### data-analyst向け（計4個）
**高優先度:**
1. xlsx - Excel操作・データ分析

**中優先度:**
2. pptx - プレゼン作成
3. n8n-skills - ワークフロー自動化（n8n利用者のみ）

**低優先度:**
4. その他データ可視化系（個別調査必要）

#### corder向け（計12個）
**高優先度:**
1. test-driven-development - TDD実践
2. systematic-debugging - デバッグ手法
3. writing-plans - 実装計画（directorから受領）
4. executing-plans - 計画実行

**中優先度:**
5. defense-in-depth - 多層防御
6. root-cause-tracing - 根本原因特定
7. requesting-code-review - レビュー依頼
8. receiving-code-review - レビュー受領
9. condition-based-waiting - 非同期パターン
10. backend-dev-guidelines - バックエンドパターン
11. frontend-dev-guidelines - フロントエンドパターン
12. error-tracking - エラートラッキング

#### researcher向け（計7個）
**高優先度:**
1. playwright-skill - ブラウザ自動化

**中優先度:**
2. webapp-testing - Webアプリテスト
3. notebooklm-skill - NotebookLM統合
4. docx - Word文書操作
5. Domain Memory Agent - ナレッジベース
6. Web-to-GitHub Issue - 調査→Issue化

**低優先度:**
7. その他Web調査系（個別調査必要）

#### scanner向け（計5個）
**高優先度:**
1. pdf - PDF操作
2. xlsx - Excel操作

**中優先度:**
3. docx - Word文書操作

**低優先度:**
4. pptx - PowerPoint操作
5. その他OCR/画像処理系（Codex MCPでカバー済み）

#### qa向け（計11個）
**高優先度:**
1. test-driven-development - TDD実践
2. systematic-debugging - デバッグ手法
3. verification-before-completion - 完了前検証
4. webapp-testing - Webアプリテスト
5. playwright-skill - ブラウザ自動化

**中優先度:**
6. defense-in-depth - 多層防御
7. root-cause-tracing - 根本原因特定
8. testing-anti-patterns - アンチパターン回避
9. writing-skills / testing-skills-with-subagents - Skill検証
10. route-tester - APIテスト
11. raptor - セキュリティスキャン
12. Project Health Auditor - 健全性分析
13. Conversational API Debugger - APIデバッグ

---

## 推奨アクション

### 【Phase 1】すぐに取り込むべきSkills（高優先度15個）

#### 全sub-agent共通
1. **test-driven-development** - corder、qaの開発品質向上
2. **systematic-debugging** - corder、qaのデバッグ品質向上
3. **verification-before-completion** - qa、corderの完了判定基準

#### director専用
4. **brainstorming** - 要件精緻化
5. **writing-plans** - 詳細計画生成
6. **dispatching-parallel-agents** - 並列実行判断（NDFの並列推奨機能強化）

#### data-analyst専用
7. **xlsx** - Excel操作標準化

#### scanner専用
8. **pdf** - PDF操作標準化

#### researcher/qa共通
9. **playwright-skill** - ブラウザ自動化（Chrome DevTools MCP補完）
10. **webapp-testing** - Webアプリテスト

### 【Phase 2】カスタマイズが必要なSkills（中優先度12個）

#### 計画・実行系
1. **executing-plans** - directorの計画実行（TodoList統合）
2. **finishing-a-development-branch** - directorのブランチ完了判断
3. **subagent-driven-development** - directorのマルチエージェント協調

#### コード品質系
4. **defense-in-depth** - corder、qaの多層防御
5. **root-cause-tracing** - corder、qaの根本原因特定
6. **requesting-code-review / receiving-code-review** - チーム開発統合
7. **condition-based-waiting / testing-anti-patterns** - corder、qaのテスト品質

#### 専門機能系
8. **route-tester** - qaのAPIテスト（カスタマイズ要）
9. **notebooklm-skill** - researcherの技術調査（NotebookLM要）
10. **Domain Memory Agent** - researcherの知見蓄積

#### メタスキル系
11. **skill-creator** - directorのSkill作成
12. **skill-developer** - directorのメタスキル（NDFプラグイン拡張）

### 【Phase 3】参考にすべきSkills（低優先度）

#### 高度なGit機能
1. **using-git-worktrees** - 並列ブランチ（上級者向け）

#### 特定スタック向け
2. **backend-dev-guidelines** - Node.js/Express/Prisma/Sentry
3. **frontend-dev-guidelines** - React/MUI v7/TypeScript
4. **error-tracking** - Sentry統合
5. **n8n-skills** - n8nワークフロー

#### 高度な専門機能
6. **raptor** - セキュリティエージェント化（専門的）
7. **mcp-builder** - MCPサーバー作成（高度）

#### その他
8. **pptx** - PowerPoint（主要ユースケースでない）
9. **Git Commit Smart** - コミットメッセージ（既存hook十分）

---

## 実装戦略

### 1. 短期（1-2週間）- Phase 1実装
**目標**: 高優先度15個のSkillsを取り込み、各sub-agentの基本能力向上

**アプローチ:**
- obra/superpowersから7個（brainstorming、writing-plans、dispatching-parallel-agents、test-driven-development、systematic-debugging、verification-before-completion、executing-plans）
- anthropics/skillsから3個（pdf、xlsx、webapp-testing）
- 個別リポジトリから1個（playwright-skill）

**検証:**
- 各sub-agentで実際のタスクを実行
- Skillsの適用効果を測定
- 必要に応じてカスタマイズ

### 2. 中期（1ヶ月）- Phase 2実装
**目標**: 中優先度12個のSkillsをカスタマイズして取り込み

**アプローチ:**
- NDFプラグインの特性に合わせてカスタマイズ
- TodoList機能、並列実行推奨機能との統合
- チーム開発プロセスとの統合

**検証:**
- 複雑なマルチエージェント協調タスクで検証
- カスタマイズ内容のドキュメント化

### 3. 長期（継続的）- Phase 3参考
**目標**: 低優先度Skillsを参考にして独自Skillsを開発

**アプローチ:**
- 特定スタック向けSkillsを参考に、汎用的なパターンを抽出
- NDFプラグイン独自のSkillsを開発
- コミュニティからのフィードバックを反映

---

## 技術的考慮事項

### 1. Skillsの配置場所
```
plugins/ndf/
├── skills/
│   ├── director/
│   │   ├── brainstorming/SKILL.md
│   │   ├── writing-plans/SKILL.md
│   │   ├── dispatching-parallel-agents/SKILL.md
│   │   └── ...
│   ├── corder/
│   │   ├── test-driven-development/SKILL.md
│   │   ├── systematic-debugging/SKILL.md
│   │   └── ...
│   ├── data-analyst/
│   │   ├── xlsx/SKILL.md
│   │   └── ...
│   ├── researcher/
│   │   ├── playwright-skill/SKILL.md
│   │   ├── webapp-testing/SKILL.md
│   │   └── ...
│   ├── scanner/
│   │   ├── pdf/SKILL.md
│   │   ├── xlsx/SKILL.md
│   │   └── ...
│   └── qa/
│       ├── test-driven-development/SKILL.md
│       ├── systematic-debugging/SKILL.md
│       ├── verification-before-completion/SKILL.md
│       └── ...
```

### 2. YAMLフロントマター標準化
```yaml
---
name: skill-name
description: 何をするか + いつ使うか + トリガーキーワード（最大1024文字）
allowed-tools: Read, Grep, Glob  # オプション: ツール制限
---
```

### 3. 段階的ディスクロージャー
- メインファイル（SKILL.md）: 500行以下に抑える
- 詳細ドキュメント（REFERENCE.md、EXAMPLES.md）: 必要時に読み込み
- スクリプト（scripts/）: 実行可能なヘルパー

### 4. 依存関係管理
- Python依存: `requirements.txt`に記載
- Node.js依存: `package.json`に記載
- システム依存: ドキュメントに明記

### 5. NDFプラグインとの統合
- Serena MCPとの連携
- GitHub MCPとの連携
- Codex MCPとの連携
- BigQuery MCPとの連携
- AWS Docs MCPとの連携
- Chrome DevTools MCPとの連携

---

## リスクと対策

### リスク1: Skillsの過剰適用
**リスク**: Claudeが不適切なタイミングでSkillsを適用
**対策**:
- `description`に明確なトリガー条件を記載
- `allowed-tools`で適用範囲を制限
- sub-agent専用Skillsとして分離

### リスク2: コンテキスト制限
**リスク**: 大量のSkillsでコンテキストを消費
**対策**:
- 段階的ディスクロージャー（メイン500行以下）
- 参照ファイルは必要時のみ読み込み
- sub-agentごとに必要なSkillsのみ配置

### リスク3: 依存関係の不整合
**リスク**: 必要なライブラリやツールが未インストール
**対策**:
- 依存関係を明確にドキュメント化
- インストールスクリプト提供
- エラーメッセージに解決方法を含める

### リスク4: Skillsの競合
**リスク**: 複数のSkillsが同じタイミングで適用され、矛盾した指示
**対策**:
- 各Skillsの`description`で適用条件を明確に分離
- トリガーキーワードを重複させない
- sub-agent専用として分離

---

## 次のステップ

### 1. 優先順位付け完了
✅ 高優先度15個を特定
✅ 中優先度12個を特定
✅ 低優先度を分類

### 2. Phase 1実装準備
- [ ] obra/superpowersから高優先度Skillsをフォーク
- [ ] anthropics/skillsから公式Skillsをフォーク
- [ ] playwright-skillをフォーク
- [ ] NDFプラグインのskills/ディレクトリ構造を設計
- [ ] YAMLフロントマターを標準化
- [ ] 依存関係を整理

### 3. 検証計画策定
- [ ] 各sub-agentでのテストケース作成
- [ ] 実際のタスクでの検証シナリオ作成
- [ ] 効果測定指標の定義

### 4. ドキュメント整備
- [ ] 各Skillsの使い方ガイド作成
- [ ] sub-agent別Skills一覧作成
- [ ] トラブルシューティングガイド作成

---

## 参考リンク

### 主要リポジトリ
- [obra/superpowers](https://github.com/obra/superpowers) - 9.8k stars、20個の戦闘テスト済みskills
- [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) - 2.8k stars、キュレーション済みリスト
- [anthropics/skills](https://github.com/anthropics/skills) - 公式Skills（document-skills、example-skills）
- [jeremylongshore/claude-code-plugins-plus](https://github.com/jeremylongshore/claude-code-plugins-plus) - 257プラグイン、240スキル

### 専門リポジトリ
- [lackeyjb/playwright-skill](https://github.com/lackeyjb/playwright-skill) - 900 stars、ブラウザ自動化
- [PleasePrompto/notebooklm-skill](https://github.com/PleasePrompto/notebooklm-skill) - 624 stars、NotebookLM統合
- [gadievron/raptor](https://github.com/gadievron/raptor) - 823 stars、セキュリティエージェント
- [czlonkowski/n8n-skills](https://github.com/czlonkowski/n8n-skills) - 898 stars、n8nワークフロー
- [diet103/claude-code-infrastructure-showcase](https://github.com/diet103/claude-code-infrastructure-showcase) - 7.7k stars、インフラ例

### 公式ドキュメント
- [Claude Code Skills公式ガイド](https://code.claude.com/docs/ja/skills)
- [Anthropic公式ブログ - Agent Skills](https://anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [サポートドキュメント - What are Skills](https://support.claude.com/en/articles/12512176-what-are-skills)

---

**調査完了日**: 2025-12-15
**次回更新予定**: Phase 1実装完了後
