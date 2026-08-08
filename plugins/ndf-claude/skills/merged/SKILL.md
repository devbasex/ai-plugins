---
name: merged
description: "Clean up after a PR is merged: update main, remove the worktree, and delete merged branches."
argument-hint: "[PR番号]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# マージ後クリーンアップコマンド

PR マージ後の後始末をまとめて実行する。対象 PR のブランチ削除に加えて、残っているマージ済みブランチの整理と main の取り込みもこの Skill で扱う。

## 手順

1. **マージ確認**: 引数の（引数がなければ自身が作成した最新の）PR が main に merge されていることを github mcp で確認。merge されていなければ終了
2. **作業ツリー退避**: `git status` を確認し、変更があれば `git stash`
3. **main 更新**: `git checkout main` → `git pull`
4. **worktree クリーンアップ**: `git worktree list` で当該 PR 番号に対応する worktree (`pr<PR番号>`) を探し、あれば `git worktree remove <path>` で削除（worktree 内の `.cross_review/` も一緒に消える）
5. **ブランチ削除**: `git branch -d <feature-branch>`
6. **マージ済みブランチの整理**: 下記の手順で残存ブランチをまとめて削除
7. **復元**: 手順 2 で stash していれば `git stash pop`

**注意**: 冪等性保証・エラー時中断・削除済み無視

## マージ済みブランチの整理

```bash
git branch --merged main          # 1. マージ済みブランチを列挙
git branch -d <branch>            # 2. ローカル削除
git push origin --delete <branch> # 3. リモートにも残っていれば削除
```

- main と現在のブランチは必ず除外する
- 削除対象を提示し、確認を取ってから削除する
- リモート削除は共有ブランチに影響するため、対象を明示してから実行する

## main の取り込み

作業中のブランチへ最新のデフォルトブランチ (main/master) を取り込む場合はこちらを使う。

1. **ブランチ確認**: `git branch --show-current`。デフォルトブランチ自身なら `git pull` のみ実行して終了
2. **作業ツリー確認**: 未コミット変更があれば `git stash` で退避
3. **最新取得**: `git fetch origin <default-branch>`
4. **マージ実行**: `git merge origin/<default-branch> --no-edit`
   - コンフリクト時は `git diff --name-only --diff-filter=U` で一覧を表示し、**自動解決はしない**。ユーザーに報告し、確認後に作業継続
5. **後処理**: stash していれば `git stash pop`。コンフリクトがなければ `git push` で反映し、マージ済みコミット数と変更ファイル数を報告

## 作業完了報告（必須）

- 実行サマリー（PR タイトル、マージコミット、削除したブランチ、現在のブランチ）
- main ブランチの状態
- PR URL

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへの cherry-pick PR 作成と、複数ブランチへ同じ修正を適用する原則
