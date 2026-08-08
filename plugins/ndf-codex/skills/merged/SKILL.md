---
name: merged
description: "Clean up after a PR is merged: update main, remove the worktree, and delete the merged branch. Use when a PR has just been merged or leftover branches and worktrees need clearing. Triggers: 'マージ後の後片付け', 'ブランチを整理', 'worktreeを削除', 'merged cleanup'"
argument-hint: "[PR番号]"
allowed-tools:
  - Bash
  - Read
---

# マージ後クリーンアップコマンド

PR マージ後の後始末をまとめて実行する。対象 PR のブランチ削除に加えて、残っているマージ済みブランチの整理と main の取り込みもこの Skill で扱う。

## 用途の切り分け（最初に判定する）

| 依頼の意図 | 実行する節 |
|---|---|
| PR マージ後のクリーンアップ | 「クリーンアップの手順」→ 必要なら「マージ済みブランチの整理」 |
| マージ済みブランチの整理のみ | 「マージ済みブランチの整理」のみ（クリーンアップの手順は実行しない） |
| 作業中ブランチへ最新 main を取り込む | 「main の取り込み」のみ（クリーンアップの手順は実行しない） |

「クリーンアップの手順」以外を目的とする場合、対象 PR は未マージであるのが通常のため、
手順 1（PR のマージ確認）を前提条件にしてはならない。「マージ済みブランチの整理」と
「main の取り込み」はいずれも **単独で実行可能** で、PR のマージ状態に依存しない。

## クリーンアップの手順

1. **マージ確認**: 引数の（引数がなければ自身が作成した最新の）PR が main に merge されていることを github mcp で確認。merge されていなければクリーンアップは実施せず終了
2. **作業ツリー退避**: `git branch --show-current` で**退避元のブランチ名を記録**し、`git status` を確認して変更があれば `git stash`
3. **main 更新**: `git checkout main` → `git pull`
4. **worktree クリーンアップ**: `git worktree list` で当該 PR 番号に対応する worktree (`pr<PR番号>`) を探し、あれば `git worktree remove <path>` で削除（worktree 内の `.cross_review/` も一緒に消える）
5. **ブランチ削除**: `git branch -d <feature-branch>`
6. **マージ済みブランチの整理**: 下記の手順で残存ブランチをまとめて削除
7. **復元**: 手順 2 で stash していれば、**退避元のブランチへ戻してから**復元する
   - 退避元のブランチが残っている場合: `git checkout <退避元のブランチ>` → `git stash pop`
   - 退避元のブランチを手順 5 / 6 で削除した場合: **`git stash pop` を実行しない**。手順 3 以降は main に居るため、そのまま pop すると無関係な変更が main の作業ツリーへ展開される。stash は残したまま `git stash list` の該当エントリを作業完了報告に記載し、復元先ブランチの作成か破棄かをユーザーに判断してもらう

**注意**: 冪等性保証・エラー時中断・削除済み無視

## マージ済みブランチの整理

残存するマージ済みブランチをまとめて削除する。**単独で実行可能**な節であり、
「クリーンアップの手順」の手順 1（PR のマージ確認）を前提にしない。
OPEN な PR が残っている状態でも、ブランチ整理だけを目的に実行してよい。

```bash
git branch --merged main          # 1. マージ済みブランチを列挙
git branch -d <branch>            # 2. ローカル削除
git push origin --delete <branch> # 3. リモートにも残っていれば削除
```

- main と現在のブランチは必ず除外する
- 削除対象を提示し、確認を取ってから削除する
- リモート削除は共有ブランチに影響するため、対象を明示してから実行する

## main の取り込み

作業中のブランチへ最新のデフォルトブランチ (main/master) を取り込む場合はこちらを使う。PR のマージ有無は前提条件にしない。

1. **ブランチ確認**: `git branch --show-current`。デフォルトブランチ自身なら `git pull` のみ実行して終了
2. **作業ツリー確認**: 未コミット変更があれば `git stash` で退避
3. **最新取得**: `git fetch origin <default-branch>`
4. **マージ実行**: `git merge origin/<default-branch> --no-edit`
   - コンフリクト時は `git diff --name-only --diff-filter=U` で一覧を表示し、**自動解決はしない**。ユーザーに報告し、確認後に作業継続
5. **後処理**: stash していれば `git stash pop`。コンフリクトがなければ `git push` で反映し、マージ済みコミット数と変更ファイル数を報告

## 作業完了報告（必須）

- 実行サマリー（PR タイトル、マージコミット、削除したブランチ、現在のブランチ）
- main ブランチの状態
- 復元していない stash が残っている場合はその旨と `git stash list` の該当エントリ
- PR URL

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへの cherry-pick PR 作成と、複数ブランチへ同じ修正を適用する原則
