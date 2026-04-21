---
name: merged
description: "PRマージ後のクリーンアップを実行する（main更新、ブランチ削除）"
argument-hint: "[PR番号]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# マージ後クリーンアップコマンド

PRマージ後のクリーンアップを実行。

## 手順

0. **事前確認**: github mcpで引数の（引数が無ければ自身が作成した最新の）PRがmainにmergeされていることを確認。mergeされていなければ終了
1. **事前確認**: `git status`→変更あればstash
2. **main更新**: `git checkout main`→`git pull`
3. **ブランチ削除**: `git branch -d <feature-branch>` → stash復元

**注意**: 冪等性保証・エラー時中断・削除済み無視

## 作業完了報告（必須）

- 実行サマリー（PRタイトル、マージコミット、削除したブランチ、現在のブランチ）
- mainブランチの状態
- PR URL
