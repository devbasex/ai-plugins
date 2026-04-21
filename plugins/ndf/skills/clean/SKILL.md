---
name: clean
description: "mainマージ済みブランチをローカル/リモート一括削除する"
disable-model-invocation: true
allowed-tools:
  - Bash
---

# ブランチクリーンアップコマンド

mainマージ済みブランチをローカル/リモート削除。

## 手順

1. `git branch --merged main`確認
2. main・現在ブランチ除外
3. `git branch -d <branch>`
4. `git push origin --delete <branch>`

**注意**: 削除前確認・main除外・現在ブランチ除外
