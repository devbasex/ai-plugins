---
name: knowledge-reorg
description: "AGENTS.mdとSkillsを「AI Agent Knowledge Architecture Policy」に基づいて整理・再構成する"
argument-hint: "[--target AGENTS.md|skills|docs|all] [--dry-run] [--migrate-memory]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - Task
---

# Knowledge Reorg Command

AGENTS.md・Skills・docsを「AI Agent Knowledge Architecture Policy」に基づいて分析・整理する。

## 入力

$ARGUMENTS

## ポリシー（AI Agent Knowledge Architecture）

知識を以下の3層に分離する。**実際のパスはリポジトリやAIエージェントごとに異なる。**

| 層 | 役割 |
|----|------|
| エントリポイント | ナビゲーション + ポリシー（軽量、300行以下推奨） |
| ドキュメント | リポジトリ知識（アーキテクチャ、モジュール説明、依存関係） |
| スキル | 実行可能なワークフロー（手順、コマンド、チェックリスト） |

### マルチエージェント対応戦略

**推奨**: `AGENTS.md`を共通エントリポイントとし、各エージェント固有ファイルからインポートで参照する。

```
AGENTS.md                ← 共通エントリポイント（本体）
.claude/CLAUDE.md        ← @../AGENTS.md でインポート
.gemini/GEMINI.md        ← @../AGENTS.md でインポート
（Codex CLI）             ← AGENTS.md を直接読込（設定不要）
（Kiro CLI）              ← AGENTS.md を直接読込（設定不要）
```

#### エージェント固有ファイルの設定例

**Claude Code** (`.claude/CLAUDE.md`):
```markdown
@../AGENTS.md
```

**Gemini CLI** (`.gemini/GEMINI.md`):
```markdown
@../AGENTS.md
```
または`.gemini/settings.json`で:
```json
{ "context": { "fileName": ["AGENTS.md", "GEMINI.md"] } }
```

**Codex CLI / Kiro CLI**: `AGENTS.md`をネイティブに読込。追加設定不要。

#### エージェント別ファイル配置リファレンス

##### エントリポイント

| エージェント | 固有ファイル | グローバル | AGENTS.md |
|-------------|------------|-----------|-----------|
| Claude Code | `.claude/CLAUDE.md` | `~/.claude/CLAUDE.md` | `@../AGENTS.md`でインポート |
| Kiro CLI | `.kiro/steering/*.md` | `~/.kiro/steering/*.md` | 直接読込 |
| Codex CLI | なし（AGENTS.md標準） | `~/.codex/AGENTS.md` | 直接読込 |
| Gemini CLI | `.gemini/GEMINI.md` | `~/.gemini/GEMINI.md` | `@../AGENTS.md`でインポート or settings.json |

**補足**:
- Kiro CLIはsteering体系（`product.md`/`tech.md`/`structure.md`）で構造化指示を管理
- Codex CLIは`AGENTS.override.md`による上書き機構あり
- Gemini CLIは`@`構文でネストインポート対応（循環検出あり、最大深度5）

##### スキル配置

| エージェント | プロジェクト | ユーザー |
|-------------|------------|---------|
| Claude Code | `.claude/skills/<name>/SKILL.md` | `~/.claude/skills/<name>/SKILL.md` |
| Kiro CLI | `.kiro/skills/<name>/SKILL.md` | なし（エージェント経由） |
| Codex CLI | `.agents/skills/<name>/SKILL.md` | `~/.agents/skills/<name>/SKILL.md` |
| Gemini CLI | `.agents/skills/<name>/SKILL.md` or `.gemini/skills/<name>/SKILL.md` | `~/.agents/skills/<name>/SKILL.md` or `~/.gemini/skills/<name>/SKILL.md` |

**共通**: 全エージェントが`SKILL.md`ファイルを採用（Agent Skillsオープンスタンダードに収束傾向）。

##### ドキュメント

ドキュメントディレクトリはエージェント標準では規定されていない。`docs/`が一般的な慣例。

### 0) パス検出（実行時に最初に行う）

整理を始める前に、対象リポジトリで以下を検出する:

1. **エントリポイント**: 上表を参考に、存在するファイルを特定（複数エージェント対応の場合はすべて列挙）
2. **ドキュメントディレクトリ**: `docs/`が存在するか、なければ作成先を提案
3. **スキルディレクトリ**: 上表を参考に、存在するディレクトリを特定
4. **対象エージェント**: どのエージェント向けに整理するかを確認

検出結果を`AskUserQuestion`でユーザーに確認し、以降の作業で使用する。

### エントリポイントに含めるべきもの

- リポジトリ概要
- ドキュメントへのナビゲーション
- エージェント行動ルール
- スキルへの参照

### エントリポイントに含めてはいけないもの

- 詳細なアーキテクチャ説明
- データベーススキーマ
- 長い説明文
- 運用手順（スキルへ）

### ドキュメントに含めるべきもの

- システムアーキテクチャ
- リポジトリ構造の説明
- モジュール説明
- 依存関係
- インフラ概要
- 設計思想

### スキルに含めるべきもの

- ステップバイステップの手順
- 実行コマンド
- バリデーションルール
- チェックリスト

## 手順

### 1) 現状分析

まず「0) パス検出」でエントリポイント・ドキュメントディレクトリ・スキルディレクトリを特定した上で、以下を分析する:

1. **エントリポイントの行数とトークン概算を計測**
   - 300行以上なら「肥大化」と判定
   - 含まれている情報の種類を分類（ナビゲーション/知識/手順/ポリシー）

2. **ドキュメントディレクトリの状態を確認**
   - 存在するか
   - どの程度の知識が格納されているか

3. **スキルディレクトリの状態を確認**
   - 各スキルの役割分類
   - 知識がスキルに混入していないか

4. **Serena memoryの状態を確認**（`--migrate-memory`指定時）
   - `.serena/memories/`の内容を一覧
   - 各メモリーの分類（知識→ドキュメント / 手順→スキル / 一時的→削除候補）

### 2) 分析レポート作成

以下の形式でユーザーに報告する:

```markdown
## 現状分析レポート

### エントリポイント（{検出したファイル名}）
- 行数: X行（目標: 300行以下）
- 状態: 適正 / 肥大化
- 含まれる情報の内訳:
  - ナビゲーション: X行
  - 知識（ドキュメント移動候補）: X行
  - 手順（スキル移動候補）: X行
  - ポリシー: X行

### ドキュメント（{検出したパス}）
- 状態: 未作成 / 不足 / 適正
- 不足している知識: [リスト]

### スキル（{検出したパス}）
- 状態: 適正 / 知識混入あり
- 問題のあるスキル: [リスト]

### Serena Memory（該当時）
- 移行対象: X件
  - ドキュメント移動: X件
  - スキル移動: X件
  - 削除候補: X件
```

### 3) 整理計画の提案

`AskUserQuestion`で以下を確認:

1. **対象スコープ**: AGENTS.md / skills / docs / all
2. **実行モード**: dry-run（レポートのみ） / 実行（実際に変更）
3. **Serena memory移行**: する / しない

### 4) 実行（dry-runでない場合）

#### エントリポイント整理
- 詳細な知識をドキュメントディレクトリに抽出
- 手順をスキルディレクトリに抽出
- エントリポイントをナビゲーション+ポリシーのみに圧縮
- ドキュメントへのリンクを追加

#### ドキュメント整理
- 必要なサブディレクトリを作成（architecture/, modules/など）
- エントリポイントから抽出した知識を配置
- Serena memoryから移行（該当時）

#### スキル整理
- 知識が混入しているスキルを特定
- 知識部分をドキュメントに抽出し、スキルからはリンク参照に変更

#### Serena memory移行（該当時）
以下のルールで再配置:

| Memory種別 | 移行先 |
|-----------|--------|
| リポジトリ構造 | ドキュメント |
| アーキテクチャ説明 | ドキュメント |
| モジュール説明 | ドキュメント |
| 依存関係 | ドキュメント |
| エージェント手順 | スキル |
| 一時的な調査結果 | 削除 |
| タスク履歴 | 削除 |

### 5) 検証

整理後に以下を検証:
- エントリポイントが300行以下か
- 3層構造（エントリポイント/ドキュメント/スキル）が守られているか
- リンク切れがないか
- マルチエージェント互換性（Claude Code, Codex CLI, Gemini CLI, Kiro）

### 6) 完了報告

```markdown
## 整理完了レポート

### 変更サマリー
- エントリポイント: X行 → Y行（Z行削減）
- ドキュメント: X件のファイルを追加/更新
- スキル: X件のスキルを整理
- Serena memory: X件を移行、Y件を削除候補

### 作成/変更したファイル
- [ファイル一覧]

### 次のステップ
- [ ] 変更内容のレビュー
- [ ] 不要なSerena memoryの削除（手動確認推奨）
- [ ] コミット＆PR作成（`/ndf:pr`）
```

## マルチエージェント互換性

整理時は以下を遵守:
- ツール固有のフォーマットを避ける
- Serena固有の知識ストレージに依存しない
- プレーンMarkdownを使用（図はmermaidで記述）
- 安定したディレクトリ構造を維持

## 注意事項

- `--dry-run`指定時はレポートのみ出力し、ファイルを変更しない
- 大規模な変更前は必ずユーザー確認を取る
- 既存のリンクやパス参照を壊さないよう注意する
- AGENTS.md内のClaude Code固有設定（CLAUDE.md参照など）は保持する
