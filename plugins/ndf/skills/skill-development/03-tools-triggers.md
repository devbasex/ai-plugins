# ツールとトリガー設定ガイド

## allowed-tools の設定

### 基本構文

YAMLリスト形式とスペース区切り文字列の両方が有効。

```yaml
# YAMLリスト形式
---
allowed-tools:
  - Read
  - Write
  - Grep
  - Glob
---

# スペース区切り文字列形式
---
allowed-tools: Read Write Grep Glob
---
```

### パターンマッチング

```yaml
---
allowed-tools:
  - Bash(git *)       # gitで始まるすべてのBashコマンド
  - Bash(npm *)       # npmで始まるすべてのBashコマンド
  - Bash(python *)    # pythonで始まるすべてのBashコマンド
  - Bash(uv *)        # uvで始まるすべてのBashコマンド
---
```

### よく使うツール組み合わせ

| 用途 | allowed-tools |
|-----|---------------|
| 読み取り専用 | `Read, Grep, Glob` |
| コード編集 | `Read, Write, Edit, Grep, Glob` |
| Git操作 | `Read, Bash(git *)` |
| Python実行 | `Read, Bash(python *), Bash(uv *)` |
| 調査/探索 | `Read, Grep, Glob, WebFetch` |

### 制限事項

- ユーザーのパーミッション設定が基本承認を制御
- スキル外のツールは通常の承認フローに従う
- `/compact`や`/init`などの組み込みコマンドは利用不可

## description による自動呼び出し制御

### 「Use when」形式で使用タイミングを明示（公式推奨）

```yaml
---
name: code-review
description: |
  コードレビューを実施し、品質とセキュリティをチェックします。
  Use when reviewing code quality, checking pull requests, or when asked to review code.
---
```

### トリガー制御の比較

| 設定 | ユーザー呼び出し | Claude呼び出し | description読み込み |
|-----|---------------|--------------|------------------|
| デフォルト | ✅ | ✅ | 常時 |
| `disable-model-invocation: true` | ✅ | ❌ | 呼び出し時のみ |
| `user-invocable: false` | ❌ | ✅ | 常時 |

### 副作用のあるスキル

```yaml
# deploy, commit, push などは自動呼び出しを禁止
---
name: deploy
description: 本番環境にデプロイします
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(npm *)
---
```

## 引数の使用

### 位置引数

```yaml
---
name: analyze
description: ファイルを分析
---

# 分析対象: $ARGUMENTS

$ARGUMENTS[0] を分析してレポートを生成します。
```

| 変数 | 説明 |
|-----|------|
| `$ARGUMENTS` | すべての引数 |
| `$ARGUMENTS[0]` または `$0` | 最初の引数 |
| `$ARGUMENTS[1]` または `$1` | 2番目の引数 |

### セッション変数

```yaml
${CLAUDE_SESSION_ID}   # セッションID
${CLAUDE_SKILL_DIR}    # SKILL.mdのあるディレクトリの絶対パス
```

### 引数ヒント

```yaml
---
name: fix-issue
argument-hint: "[issue-number]"
---
```

スラッシュメニューで `/fix-issue [issue-number]` と表示される。

## 動的コンテキスト注入

シェルコマンドの結果をスキル内容に注入できる。

```yaml
---
name: pr-summary
context: fork
agent: Explore
allowed-tools:
  - Bash(gh *)
---

## PRコンテキスト

- PR差分: !`gh pr diff`
- PRコメント: !`gh pr view --comments`
- 変更ファイル: !`gh pr diff --name-only`

## タスク

上記のPR情報を要約してください。
```

### 構文

```
!`command`  # コマンド結果を注入
```

## サブエージェント実行

```yaml
---
name: deep-analysis
context: fork
agent: Explore       # または Plan, general-purpose
model: sonnet        # オプション
---

## タスク

$ARGUMENTSを詳細に分析してください。
```

| agent | 用途 |
|-------|-----|
| `Explore` | コードベース調査 |
| `Plan` | 実装計画策定 |
| `general-purpose` | 汎用タスク |

## ベストプラクティス

### DO（推奨）

- **descriptionにトリガーキーワードを明示**
- **副作用スキルは`disable-model-invocation: true`**
- **allowed-toolsは必要最小限に**
- **パターンマッチングで安全に制限**

### DON'T（非推奨）

- **`Bash`を無制限に許可**
- **曖昧なdescription**
- **deploy/pushを自動呼び出し可能に**
