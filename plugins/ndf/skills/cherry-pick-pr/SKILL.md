---
name: cherry-pick-pr
description: "Cherry-pick a merged fix onto an environment branch (qa/staging, release) as a new PR. 明示指示のみで実行する。Use when the same fix must reach qa or staging（qaにも同じ修正を適用・stagingにも反映・cherry-pick）."
argument-hint: "ベースブランチ名 (例: qa/staging, release/v2)"
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

featureブランチに環境ブランチ(`qa/staging`等)を merge して conflict を解消すると、`feature → 起点ブランチ` の PR に環境ブランチ固有のコードが混入する（起点の汚染）。短命ブランチ + cherry-pick で、必要なコミットだけを対象ブランチに届ける。

| 観点 | 正しい順序 | 誤った順序 |
|------|-----------|-----------|
| 単一ソース | feature ブランチが唯一の正 | 二箇所で実装 |
| 一貫性 | cherry-pick で完全一致 | 手書き差分でズレる |
| 追跡性 | `-x` で元 commit が明記 | 関連 commit 不明確 |

## 核心ルール

原則は `ndf-policies`「ブランチ運用の原則」に定義されている。本 Skill の処理フローはその原則を手順へ落としたもので、対応は次のとおり。

| 原則 | 対応する処理フロー |
|------|------------------|
| feature に先に commit し cherry-pick で届ける | 3・6 |
| 環境ブランチを feature に merge しない | 「なぜ必要か」 |
| push 前に起点ブランチを取り込む | 5 |
| マージ済みブランチには push しない | 2 |

## 処理フロー

### 1. 引数・現状確認
- 引数から環境ブランチ名を取得（必須。未指定なら確認）
- `git branch --show-current` で現在ブランチを取得
- 開発の起点ブランチを決める。以降の手順はこの値を使う

```bash
# 起点は開発の本流であって、既定ブランチとは限らない。宣言（`.ndf/worktree.json` の
# `base_branch`）が無ければ origin の HEAD が指す先を使う
dev_base=$(jq -r 'select(.version == 1) | .base_branch | select(type == "string")' \
  .ndf/worktree.json 2>/dev/null)
dev_base=${dev_base:-$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')}
dev_base=${dev_base:-main}
```

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
git log --oneline "$dev_base"..HEAD
```

ユーザーに cherry-pick 対象コミットを確認（全コミット or 選択）。

### 4. 短命ブランチ作成

**実行する場所は、cherry-pick 元のコミットを持つ作業ディレクトリである。** 開発を
`.worktrees/<ブランチ名>` の作業ツリーで行っている場合は、その中で実行する。短命
ブランチはその作業ディレクトリで作られ、push した後に削除する。

```bash
git fetch origin <base-branch>
git checkout -b <current-branch>-for-<base-short-name> origin/<base-branch>
```

- `<base-short-name>`: ベースブランチのスラッシュ以降（例: `qa/staging` → `staging`）
- 例: `feature/add-auth-for-staging`

### 5. 起点ブランチを取り込む（必須）

```bash
git fetch origin "$dev_base"
git merge "origin/$dev_base" --no-edit
```

CIで最新の起点必須のWorkflowがあるため、取り込み忘れるとconflictやCIエラーになる。

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
- `feature → 起点ブランチ` の PR には影響しない
- revert の扱いは `ndf-policies`「ブランチ運用の原則」5 に従う

## 関連

- `ndf-policies` — 環境ブランチへの適用原則とブランチ汚染の回避（本 Skill の前提）
- `/ndf:pr` — 通常のPR作成（宛先は起点ブランチ）。環境ブランチ宛は本 Skill に誘導される
- `/ndf:merged` — マージ後のブランチ整理と、現ブランチへの起点ブランチの取り込み
- `/ndf:deploy` — ブランチ全体を環境へデプロイ（cherry-pickとは別用途）
