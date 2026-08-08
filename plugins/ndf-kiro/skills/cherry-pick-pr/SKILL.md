---
name: cherry-pick-pr
description: "Create cherry-pick PRs for environment branches and apply the same fix across multiple branches."
argument-hint: "<base-branch> (例: qa/staging, release/v2)"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Grep
---

# cherry-pick PR 作成コマンド

featureブランチから指定ベースブランチ（`qa/*`, `staging/*`, `release/*` 等の環境ブランチ）へ、短命ブランチ経由で cherry-pick PR を作成する。同じ修正を複数ブランチへ並行適用する場面全般で、この原則と手順に従う。

## 使用方法

```
/ndf:cherry-pick-pr qa/staging
/ndf:cherry-pick-pr release/v2
```

## なぜ必要か

featureブランチに環境ブランチ(`qa/staging`等)を merge して conflict を解消すると、`feature → main` の PR に環境ブランチ固有のコードが混入する（main汚染）。短命ブランチ + cherry-pick で、必要なコミットだけを対象ブランチに届ける。

| 観点 | 正しい順序 | 誤った順序 |
|------|-----------|-----------|
| 単一ソース | feature ブランチが唯一の正 | 二箇所で実装 |
| 一貫性 | cherry-pick で完全一致 | 手書き差分でズレる |
| 追跡性 | `-x` で元 commit が明記 | 関連 commit 不明確 |

## 核心ルール

1. **修正は feature ブランチに先に commit し、cherry-pick で環境ブランチへ届ける。** 短命ブランチに先に commit して feature へ手作業で再実装すると、二重作業と不整合の原因になる
2. **環境ブランチを feature ブランチに merge しない。** conflict 解消目的でも禁止（main汚染の原因）
3. **短命ブランチを push する前に `origin/main` を必ず取り込む。** CI で最新 main 必須の Workflow があるため（処理フロー 5）
4. **マージ済みブランチには push しない。** 同名の短命ブランチに既存 PR がないか先に確認する（処理フロー 2）

## 処理フロー

### 1. 引数・現状確認
- 引数からベースブランチ名を取得（必須。未指定なら確認）
- `git branch --show-current` で現在ブランチを取得

### 2. 既存PRのマージ済みチェック（必須）

同じベースブランチ向けの短命ブランチに既存PRがないか確認する。

```bash
# 同名パターンのブランチでマージ済みPRがないか確認
gh pr list --head "<current-branch>-for-<base-short-name>" --state merged \
  --json number,mergedAt --jq '.[]'
```

マージ済みPRが見つかった場合、**同じブランチ名は使えない**。サフィックスを付ける（例: `-v2`, `-v3`）。

### 3. コミット一覧の確認

```bash
git log --oneline main..HEAD
```

ユーザーに cherry-pick 対象コミットを確認（全コミット or 選択）。

### 4. 短命ブランチ作成

```bash
git fetch origin <base-branch>
git checkout -b <current-branch>-for-<base-short-name> origin/<base-branch>
```

- `<base-short-name>`: ベースブランチのスラッシュ以降（例: `qa/staging` → `staging`）
- 例: `feature/add-auth-for-staging`

### 5. origin/main を取り込む（必須）

```bash
git fetch origin main
git merge origin/main --no-edit
```

CIで最新main必須のWorkflowがあるため、取り込み忘れるとconflictやCIエラーになる。

### 6. cherry-pick 実行

```bash
git cherry-pick -x <commit-hash-1> <commit-hash-2> ...
```

`-x` オプションで元のcommit hashが参照として残り、追跡性が向上する。

conflict が発生した場合:
- `git diff --name-only --diff-filter=U` でconflictファイル一覧
- 解消を試み、ユーザーに確認後 `git cherry-pick --continue`

### 7. push して PR 作成

```bash
git push -u origin <short-lived-branch>
gh pr create --base <base-branch> --title "<タイトル>" --body "$(cat <<'EOF'
## Summary
- feature/xxx からcherry-pickした<環境名>向けPR
- 元コミット: <hash一覧>

## Test plan
- [ ] <環境名>で動作確認

<!-- I want to review in Japanese. -->
EOF
)"
```

### 8. 元ブランチに戻る

```bash
git checkout <original-branch>
```

## 注意事項

- 短命ブランチは PR マージ後に削除してよい
- `feature → main` の PR には影響しない
- revert の連鎖（revert → reapply → revert...）ではなく、**最終的なあるべき状態を直接コミット**する。履歴上の意図が明確になり、後の cherry-pick も簡単になる

## 関連

- `/ndf:pr` — 通常のPR作成（base=main）。非 main ベースは本 Skill に誘導される
- `/ndf:merged` — マージ後のブランチ整理と、現ブランチへの main 取り込み
- `/ndf:deploy` — ブランチ全体を環境へデプロイ（cherry-pickとは別用途）
