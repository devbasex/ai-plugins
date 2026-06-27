---
name: review-pr-comments
description: "Classify existing PR comments before fixing."
when_to_use: "既存PRのレビューコメントを分類・優先度判定したいとき (修正前)。Triggers: 'PRコメントを確認', 'PRコメントを分類', 'コメント対応の優先度', 'PR comments review', 'classify PR comments', 'PRレビュー結果を見て'"
argument-hint: "[PR番号]"
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# PRコメント分析コマンド (READ-ONLY)

GitHub PRのレビューコメントを全て確認し、対応可否を判定する。**修正は一切行わない。分析・判定のみ**。

## 使用方法

```
/ndf:review-pr-comments           # 現在のブランチのPRを対象
/ndf:review-pr-comments 9352      # PR番号を指定
```

## `/ndf:fix` との使い分け

| 観点 | review-pr-comments | fix |
|---|---|---|
| 動作 | 分類・優先度判定のみ | 実際にコード修正 |
| 出力 | 分類テーブル+推奨アクション | 修正差分+commit |
| 推奨順序 | 最初に実行 | review-pr-commentsの結果を見て実行 |

「まず全体像を把握 → 優先度を決めてから修正」という流れに使う。

## 処理フロー

### 1. PR情報の取得

引数でPR番号が指定されていればそれを使用、なければ現在のブランチから取得。

```bash
CURRENT_BRANCH=$(git branch --show-current)
PR_NUMBER="${ARGUMENTS:-$(gh pr view --json number --jq .number)}"
```

### 2. PRコメント取得 (3 ソース)

fix skill の共有スクリプトで インラインコメント / レビュー body / PR レベルコメントを一括取得:

```bash
FETCH_SCRIPT="$CLAUDE_PLUGIN_ROOT/skills/fix/scripts/fetch-pr-comments.sh"
"$FETCH_SCRIPT" "$(gh repo view --json nameWithOwner -q .nameWithOwner)" "$PR_NUMBER"
```

補助情報 (reviewDecision 等):

```bash
gh pr view "$PR_NUMBER" --json reviewDecision
```

GitHub MCP を使う場合は `mcp__github__get_pull_request_comments` を利用。

### 3. コメント分析・分類

各コメントを以下のカテゴリに分類する:

| カテゴリ | 説明 | 対応判断 |
|---------|------|---------|
| 🔴 重大 | セキュリティ、データ整合性、クラッシュの可能性 | **対応必須** |
| 🟡 改善推奨 | コード品質、保守性、ベストプラクティス | **対応推奨** |
| 🟢 軽微 | タイポ、フォーマット、命名規則 | **対応すべき** |
| ⚪ 参考 | 提案、質問、情報共有 | **対応任意** |
| 🔵 別PR対応 | 別PRで対応予定と明記されている内容 | **対応不要** |

### 4. 出力フォーマット

```markdown
## PR #XXXX コメントレビュー結果

### サマリー
- 総コメント数: X件
- 対応必須: X件
- 対応推奨: X件
- 対応すべき: X件
- 対応任意/不要: X件

### 詳細

| # | ファイル | 行 | 指摘内容 | 分類 | 対応判断 |
|---|---------|----|---------|----|---------|
| 1 | path/to/file.ext | 123 | 指摘の要約 | 🔴 重大 | **対応必須** |
| 2 | ... | ... | ... | ... | ... |

### 推奨アクション
1. まず対応すべき項目（重大+軽微）
2. 次に対応推奨項目
3. 別PRで対応（コメントで返信推奨）
```

## 重要ルール

- **READ-ONLY**: コードの修正は一切行わない
- **PR説明文を確認**: 「やらないこと」「別PR対応」セクションに記載されている内容は「🔵 別PR対応」として分類
- **コンテキスト理解**: コメントが指摘している問題の本質を理解して分類
- **判断根拠**: なぜその分類になったかの理由を簡潔に説明

## 関連

- `/ndf:fix` — 分類結果を踏まえてコード修正を実施
- `/ndf:resolve-pr-comments` — 修正完了後の返信＋Resolve
- `/ndf:review` — PRを新規にレビューする (Approve/Request Changes判定)
