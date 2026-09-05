# Claude Code Skills 調査レポート — 実装戦略と次のステップ

**この調査は 3 本に分かれている。**

- [発見した Skills](01-findings.md)
- [調査サマリーと推奨アクション](02-analysis.md)
- [実装戦略と次のステップ](03-plan.md)

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
plugins/ndf-shared/
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
