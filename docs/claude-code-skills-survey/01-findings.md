# Claude Code Skills 調査レポート

**この調査は 3 本に分かれている。**

- [発見した Skills](01-findings.md)
- [調査サマリーと推奨アクション](02-analysis.md)
- [実装戦略と次のステップ](03-plan.md)

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
