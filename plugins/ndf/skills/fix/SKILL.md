---
name: fix
description: "PRのレビューコメントを確認し、修正対応を実行する"
argument-hint: "[PR番号]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# PR修正コマンド

直前PR、または引数で指定されたPRのreview comment確認・修正対応実行。

## 手順

1. review comment確認
2. 修正可否判断
3. 問題点修正
4. コミット・プッシュ
5. PRにSummaryコメントを追加
6. 対応したコードコメントに個別に返信
7. reviewerに再レビューを依頼
8. 対応完了したコードコメントを「Resolve Conversation」にする

- 4はgit、1と5以降はgithub mcpまたはghを利用

## ghコマンド例

### コメントへの返信

```bash
# PRのレビューコメント一覧を取得
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments

# 特定のコメントに返信（in_reply_to にコメントIDを指定）
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \
  -f body="修正しました。" \
  -F in_reply_to={comment_id}
```

### Resolve Conversation

```bash
# GraphQL APIでスレッドをresolveする
gh api graphql -f query='
  mutation {
    resolveReviewThread(input: {threadId: "{thread_node_id}"}) {
      thread { isResolved }
    }
  }
'
```

### thread_node_idの取得方法

```bash
# PRのレビュースレッド一覧を取得（node_id含む）
gh api graphql -f query='
  query {
    repository(owner: "{owner}", name: "{repo}") {
      pullRequest(number: {pr_number}) {
        reviewThreads(first: 100) {
          nodes {
            id
            isResolved
            comments(first: 1) {
              nodes { body }
            }
          }
        }
      }
    }
  }
'
```

**方針**:
- 品質・可読性・セキュリティ向上、既存機能影響なし
- 指摘がすべて正しいとは限らない。修正前に仕様を調査し、実施の可否を判断すること
- 未対応の場合はその理由をコメントに書き込む

## 作業完了報告（必須）

PRにSummaryコメントを追加:
- 対応した指摘の一覧（優先度、ファイル、指摘内容、対応状況）
- 各修正の問題点と修正内容
- テスト結果
- 修正ファイル一覧
