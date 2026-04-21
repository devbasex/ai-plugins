# SKILL.md構造ガイド

## 基本構造

```yaml
---
name: my-skill-name
description: |
  スキルの説明。Claudeがいつ使用するか判断するために使用。

  Use when asked about keyword1 or keyword2.
allowed-tools:
  - Read
  - Write
---

# スキルタイトル

## 概要

スキルの目的と使用方法の概要。

## 使用方法

具体的な使用手順。
```

## YAMLフロントマター フィールド一覧

### 必須/推奨フィールド

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | スキル名（省略時はディレクトリ名）。小文字、数字、ハイフンのみ。最大64文字 |
| `description` | **推奨** | スキルの目的と使用タイミング。Claudeの自動読み込み判断に使用 |

### ツール制御

| フィールド | デフォルト | 説明 |
|-----------|----------|------|
| `allowed-tools` | なし | 許可なく使用できるツールのリスト |

```yaml
# 例
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git *)      # gitで始まるBashコマンドのみ
  - Bash(npm *)      # npmで始まるBashコマンドのみ
```

### 呼び出し制御

| フィールド | デフォルト | 説明 |
|-----------|----------|------|
| `disable-model-invocation` | `false` | `true`でClaude自動呼び出し禁止（手動のみ） |
| `user-invocable` | `true` | `false`でスラッシュメニューから非表示 |

```yaml
# deployなど副作用のあるスキル
---
name: deploy
disable-model-invocation: true
---
```

### 実行コンテキスト

| フィールド | デフォルト | 説明 |
|-----------|----------|------|
| `context` | なし | `fork`でサブエージェントとして実行 |
| `agent` | なし | `context: fork`時のエージェントタイプ |
| `model` | 継承 | 使用するモデル |
| `effort` | 継承 | `low` / `medium` / `high` / `max`（maxはOpus 4.6のみ） |
| `paths` | なし | globパターンで自動有効化を特定ファイルに制限 |
| `shell` | `bash` | `!`command``のシェル（`bash` or `powershell`） |

```yaml
# サブエージェントとして実行
---
name: analyze
context: fork
agent: Explore
---
```

```yaml
# 特定ファイルパターンでのみ自動有効化
---
name: react-helper
paths: "src/components/**/*.tsx, src/hooks/**/*.ts"
---
```

```yaml
# effortレベル指定
---
name: deep-analysis
effort: high
context: fork
agent: Explore
---
```

### フック

settings.jsonと同じ構造をYAMLフロントマター内に記述する。

```yaml
---
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/security-check.sh"
  PostToolUse:
    - matcher: "Write"
      hooks:
        - type: command
          command: "./scripts/lint.sh"
---
```

> **注意**: `pre-invoke`/`post-invoke`というイベント名は存在しない。`PreToolUse`, `PostToolUse`等のイベント名を使用すること。

## name フィールドのルール

- **使用可能文字**: 小文字、数字、ハイフン（`-`）
- **最大長**: 64文字
- **省略時**: ディレクトリ名を使用

```yaml
# OK
name: my-skill-name
name: deploy-prod
name: code-review

# NG
name: MySkill       # 大文字
name: my_skill      # アンダースコア
name: my skill      # スペース
```

## description のベストプラクティス

### 良い例

```yaml
description: |
  Explains code with visual diagrams and analogies.
  Use when explaining how code works, teaching about a codebase,
  or when the user asks "how does this work?"
```

### 悪い例

```yaml
# 曖昧すぎる
description: コードに関するスキル

# 長すぎる（250文字でtruncateされる）
description: このスキルは...（長文）
```

## スキルの配置場所

| レベル | パス | 適用範囲 |
|-------|-----|---------|
| Personal | `~/.claude/skills/<name>/SKILL.md` | 全プロジェクト |
| Project | `.claude/skills/<name>/SKILL.md` | このプロジェクトのみ |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | プラグイン有効時 |

**優先順位**: Enterprise > Personal > Project（同名の場合）
