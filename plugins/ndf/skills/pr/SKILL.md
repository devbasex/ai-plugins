---
name: pr
description: "commit, push, PR作成(または既存PR説明更新)を一括実行するワークフローコマンド。--draft指定でドラフトPR、base非mainの場合はcherry-pick-prに誘導する。"
argument-hint: "[--draft] [base-branch] or [commit-message]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# PR作成

このプロジェクトのコードをcommit, pushし、GitHubでPull Requestを作成する。既にPRがあればPR説明を最新の変更内容に更新する。

**制約**: デフォルトブランチ(main, masterなど)で直接コミット禁止

## 使用方法

```
/ndf:pr                           # main へ通常PR作成
/ndf:pr --draft                   # main へドラフトPR作成
/ndf:pr "新機能の追加"             # コミットメッセージ指定
/ndf:pr --draft "wip: 作業中"      # ドラフトPR + メッセージ指定
/ndf:pr qa/staging                # base非main → cherry-pick-prへ誘導
```

## 引数の解釈

- `--draft` が含まれていればドラフトPR
- 既知のベースブランチ名（`main`, `master`, `qa/*`, `release/*`, `staging/*` 等）が末尾にあればベース指定
- それ以外の文字列はコミットメッセージとして扱う
- デフォルトは `main` ベース、非ドラフト

## 手順

### 0. PR確認

- `git branch --show-current` で現在ブランチを確認
- `gh pr list --head <branch>` で既存PR確認
- 既にPRが存在しOPEN状態なら:
  - `git add` → `git commit`（日本語メッセージ）→ `git push`
  - **既存PR説明を更新** する（「PR説明の更新」節を参照）
  - 終了報告
- PRがない、またはmerge/close済みなら次へ

### 1. ブランチ確認・切り替え

- デフォルトブランチの場合: 新featureブランチを作成して切り替え
- デフォルトブランチ以外: `git stash` → `git pull origin <default-branch>`（コンフリクト時は停止しユーザに報告）→ `git stash pop`

### 2. ベースブランチ判定

- 引数の末尾が `main`/`master` 以外のベースブランチ名（`qa/staging`, `release/v2` 等）の場合:
  - **警告を出して `/ndf:cherry-pick-pr <base>` に誘導する**
  - 理由: base非mainのPRに直接pushすると `feature → main` のPRに環境固有コードが混入する（詳細は `/ndf:branch-fix-strategy`）
  - ユーザーが明示的に継続を指示した場合のみ進める

### 3. 変更コミット

- `git status` → `git add` → `git commit`（日本語メッセージ）
- 引数で指定されたコミットメッセージがあればそれを使用、なければ差分から生成
- 上位階層を含むすべての変更をcommit

### 4. プッシュ

```bash
git push -u origin <branch-name>
```

### 5. PR作成

- `.github/pull_request_template.md` が存在すれば適用
- `--draft` 指定ならドラフトPR作成
- タイトル・説明は日本語、body は `## Summary` + `## Test plan`
- 機密情報（トークン、パスワード、APIキー等）を含めない
- body 末尾に `<!-- I want to review in Japanese. -->` を入れる
- **body は必ずHEREDOC形式で渡す**（`\n` リテラル混入防止）:

```bash
gh pr create --title "タイトル" $DRAFT_FLAG --body "$(cat <<'EOF'
## Summary
- 変更内容

## Test plan
- [ ] テスト項目

<!-- I want to review in Japanese. -->
EOF
)"
```

`DRAFT_FLAG` は `--draft` 指定時のみ `--draft`、それ以外は空。

## PR説明の更新（既存PRがある場合）

既存PRがある場合、以下の手順でPR説明を更新する:

1. **変更内容の分析**:
   - `git log origin/<default-branch>..HEAD` でブランチ全体のコミット履歴
   - `git diff origin/<default-branch>..HEAD --stat` で変更ファイル一覧
   - 必要に応じて変更ファイルの詳細を取得
2. **既存PR説明の確認**:
   - `gh pr view <number> --json body` で現在のPR説明を取得
   - 既存の関連リンク（Issue参照、設計ドキュメント等）は保持する
3. **PR説明の生成**:
   - `.github/pull_request_template.md` のテンプレート構造に従う
   - ブランチの**全コミット**の変更内容を反映する（最新コミットだけでなく全体）
   - 「Summary」「Test plan」「やらないこと」等を適切に記述
4. **更新の実行**:
   ```bash
   gh pr edit <number> --body "<new-description>"
   ```

## 命名規則

- ブランチ: 英語（github flow）
- コミット・PR: 日本語
- コミットメッセージ prefix 例:
  - `feat:` 新機能
  - `fix:` バグ修正
  - `refactor:` リファクタリング
  - `docs:` ドキュメント
  - `test:` テスト
  - `chore:` その他

## 検証ブランチ(qa/*等)へのPR作成

**重要: featureブランチから直接検証ブランチへPRを作成してはいけません。**

### アンチパターン（禁止）

```
feature/xxx ──PR──→ qa/staging   ← ❌ qa/staging をmergeするとmainが汚染される
```

### 正しい手順

`/ndf:cherry-pick-pr <base-branch>` を使う（自動化済み）。詳細な理由と手順は:
- `/ndf:cherry-pick-pr` — 自動化コマンド
- `/ndf:branch-fix-strategy` — 原則と手順

## 作業完了報告（必須）

PR作成/更新完了後、以下を報告:

- 基本情報（PRタイトル、ベース/ソースブランチ、PR番号、ドラフト有無）
- 変更サマリー（コミット数、変更ファイル数、変更行数、主な変更内容）
- コミット履歴
- PR本文の概要（Summary、Test plan）
- PR URL

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR
- `/ndf:deploy` — 環境ブランチへのデプロイPR（ブランチ全体）
- `/ndf:pr-tests` — Test Plan 自動実行
- `/ndf:review` — PR単位レビュー
- `/ndf:sync-main` — 現ブランチに main を取り込み
- `/ndf:branch-fix-strategy` — ブランチ戦略の原則
