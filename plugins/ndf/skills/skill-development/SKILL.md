---
name: skill-development
description: |
  Claude Code Skills の開発ガイド。SKILL.md構造、フロントマター、Progressive Disclosure、動的コンテンツ注入を解説。
  Use when creating new skills, writing SKILL.md files, configuring allowed-tools, or designing skill architecture. Also use when asked about skill frontmatter fields, description best practices, or context: fork setup.
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
---

# Skill Development Guide

公式ドキュメント準拠のClaude Code Skills開発ガイド。

> 情報源: [Skills公式ドキュメント](https://docs.claude.com/en/docs/claude-code/skills) / [Agent Skills仕様](https://agentskills.io/specification) / [anthropics/skills](https://github.com/anthropics/skills)

## フロントマター完全リファレンス

全フィールドは**任意**。`description`のみ推奨(Recommended)。

```yaml
---
name: my-skill                        # 省略時はディレクトリ名。小文字・数字・ハイフンのみ（最大64文字）
description: |                         # 推奨。250文字で切り詰め。重要な用途を先頭に
  What this skill does.
  Use when ...
argument-hint: "[issue-number]"        # オートコンプリートに表示されるヒント
disable-model-invocation: false        # trueでClaude自動呼び出し禁止（手動/nameのみ）
user-invocable: true                   # falseでスラッシュメニュー非表示
allowed-tools: Read Grep Glob         # スペース区切り文字列 or YAMLリスト
model: sonnet                          # セッションのモデルを上書き
effort: high                           # low / medium / high / max（maxはOpus 4.6のみ）
context: fork                          # forkでサブエージェント実行
agent: Explore                         # context:fork時のエージェント（省略時: general-purpose）
paths: "src/**/*.ts, lib/**"           # 自動有効化のglobパターン
shell: bash                            # !`command`のシェル（bash / powershell）
hooks:                                 # settings.jsonと同じ構造
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/check.sh"
---
```

### 呼び出し制御マトリクス

| 設定 | ユーザー呼出 | Claude呼出 | descriptionのコンテキスト |
|------|:-----------:|:----------:|:------------------------:|
| デフォルト | Yes | Yes | 常時 |
| `disable-model-invocation: true` | Yes | No | **含まれない** |
| `user-invocable: false` | No | Yes | 常時 |

## description の書き方（公式推奨）

**原則**: 「何をするか」+「いつ使うか」の両方を含める。

公式注意: Claudeはスキルを**使い損ねる傾向(undertrigger)**がある。少し積極的(pushy)な記述が推奨。

```yaml
# パターン1: Use when（公式標準）
description: |
  Extracts text and tables from PDF files, fills forms, and merges PDFs.
  Use when working with PDF documents or when the user mentions PDFs,
  forms, or document extraction.

# パターン2: TRIGGER when / DO NOT TRIGGER when（claude-apiスキルで使用）
description: |
  Build apps with the Claude API or Anthropic SDK.
  TRIGGER when: code imports `anthropic`/`@anthropic-ai/sdk`, or user asks
  to use Claude API. DO NOT TRIGGER when: code imports `openai`/other AI SDK.
```

**制限**: 250文字で切り詰め。重要な用途は先頭に。全スキルdescription合計はコンテキストの1%（フォールバック8,000文字）。

## Progressive Disclosure（3層構造）

| 層 | 内容 | タイミング | サイズ目安 |
|----|------|-----------|-----------|
| Metadata | name + description | 常時コンテキストに存在 | ~100トークン |
| Instructions | SKILL.md本文 | スキル起動時にロード | <5,000トークン（500行以下推奨） |
| Resources | 参照ファイル | 必要時のみロード | 制限なし |

### ディレクトリ構成

```
my-skill/
├── SKILL.md              # 必須 — 概要とナビゲーション（500行以下推奨）
├── reference.md          # 詳細APIドキュメント
├── examples.md           # 使用例
└── scripts/
    └── helper.py         # 実行用スクリプト（読み込まず実行）
```

SKILL.md内で参照ファイルを明示:
```markdown
## Additional resources
- For complete API details, see [reference.md](reference.md)
- For usage examples, see [examples.md](examples.md)
```

> 300行超の参照ファイルには目次を含めること。ファイル参照はSKILL.mdから1階層に留める。

## 動的コンテンツ

### シェルコマンド前処理

`` !`command` `` — Claudeに渡す**前に**実行され、結果で置換される。

```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
allowed-tools: Bash(gh *)
---
## PR context
- PR diff: !`gh pr diff`
- Changed files: !`gh pr diff --name-only`
```

### 文字列置換変数

| 変数 | 説明 |
|------|------|
| `$ARGUMENTS` | 全引数。未使用時は自動で末尾に`ARGUMENTS: <value>`が付与 |
| `$ARGUMENTS[N]` / `$N` | N番目の引数（0始まり） |
| `${CLAUDE_SESSION_ID}` | セッションID |
| `${CLAUDE_SKILL_DIR}` | SKILL.mdのあるディレクトリの絶対パス |

### Extended Thinking

スキル内に `ultrathink` と記述するだけで有効化。

## スキルの配置場所

| レベル | パス | 適用範囲 |
|-------|-----|---------|
| Enterprise | managed settings | 組織全体 |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 全プロジェクト |
| Project | `.claude/skills/<name>/SKILL.md` | プロジェクトのみ |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | プラグイン有効時 |

**優先順位**: Enterprise > Personal > Project（同名時）。Plugin skillsは`plugin-name:skill-name`の名前空間で競合しない。

## スキルのコンテンツタイプ

| タイプ | 用途 | 例 |
|--------|------|-----|
| **Reference** | 知識をインラインで適用（規約、パターン、スタイルガイド） | API設計規約、コーディング規約 |
| **Task** | 特定アクションの手順書（`/name`で手動実行） | デプロイ、コミット、コード生成 |

Task型は `disable-model-invocation: true` 推奨。

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| [01-skill-structure.md](01-skill-structure.md) | フロントマター各フィールドの詳細と例 |
| [02-progressive-disclosure.md](02-progressive-disclosure.md) | 多段階読み込みの実装 |
| [03-tools-triggers.md](03-tools-triggers.md) | allowed-tools、呼び出し制御、引数 |

## 関連リソース

- [Claude Code Skills 公式ドキュメント](https://docs.claude.com/en/docs/claude-code/skills)
- [Agent Skills 仕様](https://agentskills.io/specification)
- [anthropics/skills リポジトリ](https://github.com/anthropics/skills)（17個の公式スキル実例）
