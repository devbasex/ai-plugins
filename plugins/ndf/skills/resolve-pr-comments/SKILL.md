---
name: resolve-pr-comments
description: "対応済みPRコメントに返信し、スレッドをresolvedにする。/ndf:fixで修正完了後のクロージング作業。修正は行わず、コメント返信とresolve操作のみ実行する。"
argument-hint: "[PR番号]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# PRコメントResolveコマンド

対応済みのPRコメント全てに返信し、スレッドを resolved にする。`/ndf:fix` で修正完了後に呼び出す**クロージング専用**コマンド。

## 使用方法

```
/ndf:resolve-pr-comments           # 現在のブランチのPRを対象
/ndf:resolve-pr-comments 9352      # PR番号を指定
```

## `/ndf:fix` との使い分け

| 観点 | fix | resolve-pr-comments |
|---|---|---|
| 動作 | コード修正+commit+push | 返信+スレッドresolve |
| 前提 | レビュー後、修正が必要 | 修正済み、クロージングのみ |
| 推奨順序 | 先に実行 | fix後の最後に実行 |

## 処理フロー

### 1. PR情報の取得

```bash
PR_NUMBER="${ARGUMENTS:-$(gh pr view --json number --jq .number)}"
```

### 2. PRコメント取得

GitHub API でレビューコメントを取得:

```bash
gh api "repos/:owner/:repo/pulls/$PR_NUMBER/comments"
```

### 3. 対応状況の確認

各コメントについて、対応済みかどうかを確認する:
- コードの変更履歴（`git log`, `git diff`）と照合
- 指摘された問題が修正されているか確認
- PR body の「やらないこと」セクションで別PR対応と明記されているか確認

### 4. コメントへの返信

対応済みのコメントに対して、内容に応じた返信を投稿する:

#### 修正対応した場合
```
対応しました。

{修正内容の簡潔な説明}
```

#### 別PRで対応予定の場合
```
別PRで対応予定です。

PR説明の「やらないこと」に記載の通り、{理由}のため別PRで対応します。
```

#### 対応不要と判断した場合
```
確認しました。

{対応不要と判断した理由}
```

### 5. gh CLI コマンド

#### レビューコメントに返信（スレッド内）

```bash
gh api "repos/:owner/:repo/pulls/$PR_NUMBER/comments" \
  -f body="返信メッセージ" \
  -f in_reply_to=<comment_id>
```

#### スレッドをResolve（GraphQL）

まず Thread Node ID を取得:

```bash
gh api "repos/:owner/:repo/pulls/comments/<comment_id>" --jq '.node_id'
```

その上でResolve:

```bash
gh api graphql -f query='
  mutation {
    resolveReviewThread(input: {threadId: "<THREAD_NODE_ID>"}) {
      thread { isResolved }
    }
  }
'
```

### 6. 実行フロー

各コメントに対して以下を順次実行:

1. コメントの内容と対応状況を確認
2. 適切な返信メッセージを生成
3. 返信を投稿
4. スレッドをresolve
5. 結果を報告

### 7. 出力フォーマット

```markdown
## PR #XXXX コメント対応結果

### 処理結果
| # | コメント | 返信内容 | Resolve |
|---|---------|---------|---------|
| 1 | {指摘要約} | 対応しました | ✅ |
| 2 | {指摘要約} | 別PRで対応予定 | ✅ |

### サマリー
- 処理済み: X件
- Resolved: X件
- エラー: X件
```

## 重要ルール

- **確認してから実行**: 各コメントの対応状況を必ず確認してから返信
- **コード修正はしない**: 修正は `/ndf:fix` の責務。このコマンドはクロージングのみ
- **適切な返信**: 対応内容に応じた適切な返信メッセージを使用
- **エラーハンドリング**: API エラー発生時は報告して継続
- **ユーザー確認**: 判断に迷う場合はユーザーに確認を求める

## 関連

- `/ndf:review-pr-comments` — コメント分類・優先度判定 (READ-ONLY)
- `/ndf:fix` — コメント対応の修正を実施
