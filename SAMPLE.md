---
description: "Create a pull request from current branch with automatic title resolution"
argument-hint: "[pr-title]"
allowed-tools:
    [
        "Bash",
        "mcp__github__create_pull_request",
        "mcp__github__push_files",
        "mcp__Notion__search",
        "mcp__Notion__fetch",
    ]
---

現在のブランチから PR を作成します。ブランチ名からタスク ID を自動抽出し、Notion MCP を使用してタスクのタイトルを自動設定します。

使用方法:

```
/pr                    # タイトル自動解決
/pr "My PR Title"      # カスタムタイトル指定
```

このコマンドは以下の処理を自動実行します：

1. 現在のブランチを確認
2. ブランチ名からタスク ID を抽出（feature/xxx 形式）
3. Notion MCP でタスクを検索してタイトルを取得
4. 現在の変更を commit & push
5. GitHub MCP サーバーを使用して PR を作成
6. プロジェクトの.github/pull_request_template.md テンプレートを適用

## 処理の流れ

### 1. ブランチ名からタスク ID を抽出

現在のブランチが `feature/` で始まる場合、その後の文字列をタスク ID として抽出します。

### 2. Notion MCP でタスク検索

抽出されたタスク ID を使用して Notion でタスクを検索し、タイトルを取得します。

### 3. 自動タイトル生成

-   タスクが見つかった場合：そのタイトルを使用
-   タスクが見つからない場合：作業内容から推測したタイトルを生成

### 4. PR 作成

GitHub MCP 連携でプルリクエストを作成し、適切なテンプレートを適用します。

## 使用例

```bash
# feature/create-pr-command ブランチで実行
/pr
# → "PR作成コマンド機能の実装" というタイトルで自動作成

# カスタムタイトル指定
/pr "新しいPR作成機能を追加"
```

このコマンドにより、開発者はブランチ名の命名規則に従うだけで、適切なタイトルの PR が自動作成されます。

## ベースブランチ指定

```
/pr qa/epsilon          # qa/epsilon をベースに PR 作成
/pr "タイトル" main     # main をベースに PR 作成（デフォルト）
```

引数の末尾が既知のベースブランチ名（`main`, `qa/epsilon`, `qa/delta` 等）の場合、そのブランチをベースとして PR を作成します。

## qa/epsilon 等の検証ブランチへの PR 手順

**重要: feature ブランチから直接 qa/epsilon へ PR を作成してはいけません。**

### アンチパターン（禁止）

```
feature/xxx ──PR──→ qa/epsilon   ← ❌ feature/xxx に qa/epsilon を merge すると main が汚染される
feature/xxx ──PR──→ main         ← このPRに qa/epsilon の未検証コードが混入
```

`qa/epsilon` を `feature/xxx` に merge して conflict を解消すると、`feature/xxx → main` の PR に qa/epsilon 固有のコードが混入します。

### 正しい手順

1. **qa/epsilon から短命ブランチを作成**
2. **feature ブランチから必要なコミットだけを cherry-pick**
3. **短命ブランチから qa/epsilon へ PR を作成**

```bash
# 1. qa/epsilon の最新を取得
git fetch origin qa/epsilon

# 2. qa/epsilon から短命ブランチを作成
git checkout -b feature/xxx-for-epsilon origin/qa/epsilon

# 3. feature ブランチから必要なコミットを cherry-pick
git cherry-pick <commit-hash-1> <commit-hash-2> ...

# 4. conflict があれば解決して continue
git cherry-pick --continue

# 5. push して PR 作成
git push -u origin feature/xxx-for-epsilon
gh pr create --base qa/epsilon --title "feat: ○○機能（epsilon検証用）"
```

### コミット選定のコツ

```bash
# feature ブランチの全コミットを確認（main から分岐後）
git log --oneline main..feature/xxx

# 特定ファイルに関するコミットだけ確認
git log --oneline main..feature/xxx -- path/to/file.php
```

### まとめ

| ケース | 方法 |
|--------|------|
| `feature/xxx → main` | 通常通り PR 作成 |
| `feature/xxx → qa/epsilon` | 短命ブランチ + cherry-pick で PR 作成 |
| conflict 解消で qa/epsilon を merge | **❌ 禁止**（main 汚染の原因） |
