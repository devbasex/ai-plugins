# Claude Code Skills 調査レポート — 調査サマリーと推奨アクション

**この調査は 3 本に分かれている。**

- [発見した Skills](01-findings.md)
- [調査サマリーと推奨アクション](02-analysis.md)
- [実装戦略と次のステップ](03-plan.md)

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
