---
name: sync-main
description: "最新のデフォルトブランチ(main/master)を現在のブランチに取り込むワークフロー。feature branchをmainに追従させる際に使用。"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# main取り込みコマンド

最新のデフォルトブランチ(main/master)を現在のブランチにマージする。

## 処理フロー

1. **ブランチ確認**
   - `git branch --show-current` で現在ブランチ確認
   - デフォルトブランチ(main/master)自身の場合は `git pull` のみ実行して終了

2. **作業ツリー確認**
   - `git status` で未コミット変更を確認
   - 未コミット変更があれば `git stash` で退避

3. **最新取得**
   - `git fetch origin <default-branch>` でリモート最新を取得

4. **マージ実行**
   - `git merge origin/<default-branch> --no-edit` でマージ
   - コンフリクト発生時:
     - `git diff --name-only --diff-filter=U` でコンフリクトファイル一覧を表示
     - ユーザーに報告し、**自動解決はしない**
     - ユーザー確認後に作業継続

5. **後処理**
   - stash退避していた場合は `git stash pop` で復元
   - コンフリクトがなければ `git push` でリモートに反映
   - 完了報告（マージ済みコミット数、変更ファイル数）

## 制約

- デフォルトブランチ自身での実行は `git pull` に自動フォールバック
- コンフリクトは自動解決しない（ユーザーが解決）
- 作業ツリーが汚れている場合は必ず stash で退避してから実行

## 関連

- `/ndf:branch-fix-strategy` — 複数ブランチへの修正適用戦略
- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR作成
