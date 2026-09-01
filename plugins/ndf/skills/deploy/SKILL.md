---
name: deploy
description: "Create a deploy PR to an environment branch such as qa/staging. 明示指示のみで実行する。Use when deploying to an environment branch（qaに上げる・stagingへデプロイ）."
argument-hint: "環境ブランチ名 (例: qa/staging, release/v2)"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# 環境デプロイPR作成コマンド

現在のfeatureブランチを指定した環境ブランチへデプロイするためのPRを作成する。`{feature}_to_{env}` という命名のdeployブランチを作成し、最新の起点ブランチを取り込んでから環境ブランチへPRを出す。

## 使用方法

```
/ndf:deploy qa/staging
/ndf:deploy release/v2
```

## cherry-pick-pr との使い分け

| 観点 | cherry-pick-pr | deploy |
|---|---|---|
| 適用範囲 | featureブランチの**一部コミット**を選択 | featureブランチ**全体**を適用 |
| ブランチ戦略 | 環境ブランチから短命ブランチ派生 | featureブランチから deploy ブランチ派生 |
| 起点ブランチの取り込み | 必須 | 必須 |
| 用途 | 特定修正のみ検証環境に届けたい | feature機能全体を環境で検証したい |

## 処理フロー

### 1. バリデーション

```bash
# 起点は開発の本流であって、既定ブランチとは限らない。宣言（`.ndf/worktree.json` の
# `base_branch`）が無ければ origin の HEAD が指す先を使う
dev_base=$(jq -r 'select(.version == 1) | .base_branch | select(type == "string")' \
  .ndf/worktree.json 2>/dev/null)
dev_base=${dev_base:-$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')}
dev_base=${dev_base:-main}
```

```bash
CURRENT_BRANCH=$(git branch --show-current)
[[ "$CURRENT_BRANCH" == "$dev_base" ]] && \
  echo "❌ Error: 起点ブランチからデプロイできません" && exit 1
```

### 2. deployブランチ名の導出

```bash
FEATURE_BRANCH=$(git branch --show-current)
# 環境名を抽出: "qa/staging" → "staging", "release/v2" → "v2"
ENV_SUFFIX=$(echo "$ARGUMENTS" | sed 's|.*/||')
DEPLOY_BRANCH="${FEATURE_BRANCH}_to_${ENV_SUFFIX}"
```

### 3. 既存PRチェック

```bash
EXISTING_PR=$(gh pr list --head "$DEPLOY_BRANCH" --base "$ARGUMENTS" \
  --json number,url --jq '.[0].url // empty')
if [[ -n "$EXISTING_PR" ]]; then
  echo "✅ PR already exists: $EXISTING_PR"
  exit 0
fi
```

既存PRがあれば更新は「deployブランチにpushする」だけで済むため、再作成しない。

### 4. deployブランチ作成 + 起点ブランチの取り込み

**実行する場所は、対象の feature ブランチを持つ作業ディレクトリである。** 開発を
`.worktrees/<ブランチ名>` の作業ツリーで行っている場合は、その中で実行する。同じ
ブランチを 2 つの作業ディレクトリへ checkout できないため、主ディレクトリから
feature ブランチへ切り替えようとすると拒否される。

```bash
git fetch origin "$dev_base"
git checkout -b "$DEPLOY_BRANCH"
git merge "origin/$dev_base" --no-edit || {
  echo "❌ 起点ブランチとのmerge conflict。手動解決が必要です"
  git merge --abort
  git checkout "$FEATURE_BRANCH"
  git branch -D "$DEPLOY_BRANCH"
  exit 1
}
```

### 5. push + PR作成

```bash
git push -u origin "$DEPLOY_BRANCH"
gh pr create --base "$ARGUMENTS" --head "$DEPLOY_BRANCH" \
  --title "$DEPLOY_BRANCH → $ARGUMENTS" \
  --body "$(cat <<'EOF'
## Summary
- 環境デプロイ用PR
- 元ブランチ: $FEATURE_BRANCH
- 起点ブランチ取り込み済み

## Test plan
- [ ] $ARGUMENTS 環境で動作確認

<!-- I want to review in Japanese. -->
EOF
)"
```

### 6. 元ブランチに復帰

```bash
git checkout "$FEATURE_BRANCH"
```

## 注意事項

- 起点ブランチからの実行は禁止
- 起点ブランチの取り込みで conflict が出た場合、deployブランチを削除して戻る（featureブランチ側を先に同期すべき）
- deployブランチは PR マージ後に削除してよい
- 環境ブランチへの再デプロイは「同じ deployブランチに push」でPRが更新される

## 関連

- `/ndf:cherry-pick-pr` — 一部コミットだけを環境に届ける場合とブランチ運用戦略の原則
- `/ndf:merged` — featureブランチに起点ブランチを取り込む / マージ後のブランチ整理
