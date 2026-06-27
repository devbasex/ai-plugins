---
name: branch-fix-strategy
description: "Plan multi-branch fixes and cherry-picks."
when_to_use: "同じ修正を複数ブランチ (qa/staging/release等) に適用する必要があるとき。Triggers: 'cherry-pick', '環境ブランチに修正適用', 'qaに反映', 'stagingに反映', 'release branchへ', 'multi-branch fix', 'apply to qa/staging'"
---

# ブランチ修正適用戦略

## 適用タイミング

- featureブランチの修正を `qa/*`, `staging/*`, `release/*` 等の環境ブランチにも適用する必要がある場合
- 同じ修正を複数ブランチに並行適用する場面全般

## 核心ルール

### 1. 修正は feature ブランチに先に commit → cherry-pick で環境ブランチへ

```
✅ feature に commit → cherry-pick して短命ブランチ → 環境ブランチへ PR
❌ 短命ブランチに先に commit → feature に手作業で再実装（二重作業・不整合リスク）
```

### 2. 環境ブランチを feature ブランチに merge しない（main 汚染禁止）

```
❌ feature/xxx ← merge qa/staging（conflict 解消目的でも禁止）
```

環境ブランチを featureブランチにmergeすると、後で `feature → main` のPRに環境固有コードが混入する。

### 3. origin/main を必ず取り込む

短命ブランチを push する前に必ず `git merge origin/main` する。CI で最新 main 必須の Workflow があるため。

### 4. マージ済みブランチに push しない

環境ブランチ向けの短命ブランチに push する前に `gh pr list --head <branch>` で PR 状態を確認する。マージ済みなら新ブランチ + 新 PR を作成する（サフィックス `-v2`, `-v3` を付ける）。

## 実行手順

`/ndf:cherry-pick-pr <base-branch>` で自動化されている。手動で行う場合のみ以下を参照。

```bash
# 1. feature ブランチで修正を commit
git checkout feature/xxx
git add <files> && git commit -m "fix: 修正内容"
git log --oneline -1  # commit hash を記録

# 2. 短命ブランチを作成
git fetch origin qa/staging
git checkout -b feature/xxx-for-staging origin/qa/staging

# 3. origin/main を取り込む（必須）
git fetch origin main
git merge origin/main --no-edit

# 4. cherry-pick（-x で元 commit hash を参照に残す）
git cherry-pick -x <commit-hash>

# 5. push して PR 作成
git push -u origin feature/xxx-for-staging
gh pr create --base qa/staging --title "fix: 修正内容（staging検証用）"

# 6. 元のブランチに戻る
git checkout feature/xxx
```

## なぜこの順序が重要か

| 観点 | 正しい順序 | 誤った順序 |
|------|-----------|-----------|
| 単一ソース | feature ブランチが唯一の正 | 二箇所で実装 |
| 一貫性 | cherry-pick で完全一致 | 手書き差分でズレる |
| 追跡性 | `-x` で元 commit が明記 | 関連 commit 不明確 |

## revert 操作の注意

revertの連鎖（revert → reapply → revert...）ではなく、**最終的なあるべき状態を直接コミット**するのが望ましい。履歴上の意図が明確になり、後の cherry-pick も簡単になる。

## 関連コマンド・スキル

| リソース | 用途 |
|---------|------|
| `/ndf:cherry-pick-pr` | cherry-pick + 短命ブランチ + origin/main 取り込み + PR 作成を自動化 |
| `/ndf:pr` | 通常のPR作成。非 main ベースは `cherry-pick-pr` に誘導される |
| `/ndf:sync-main` | 現在のブランチに最新 main を取り込む |
| `/ndf:deploy` | 環境ブランチへのデプロイPR作成（ブランチ全体をmerge main経由で適用） |
