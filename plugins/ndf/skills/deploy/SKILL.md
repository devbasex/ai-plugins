---
name: deploy
description: "現在のfeatureブランチを環境ブランチ(qa/staging等)へデプロイPRを作成する。featureブランチ全体をorigin/main取り込み済みのdeployブランチ経由でPRする。cherry-pick-prと異なり、部分選択でなくブランチ全体を適用する用途。"
argument-hint: "<env-branch> (例: qa/staging, release/v2)"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
---

# 環境デプロイPR作成コマンド

現在のfeatureブランチを指定した環境ブランチへデプロイするためのPRを作成する。`{feature}_to_{env}` という命名のdeployブランチを作成し、最新 origin/main を取り込んでから環境ブランチへPRを出す。

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
| main取り込み | 必須 | 必須 |
| 用途 | 特定修正のみ検証環境に届けたい | feature機能全体を環境で検証したい |

## 処理フロー

### 1. バリデーション

```bash
CURRENT_BRANCH=$(git branch --show-current)
[[ "$CURRENT_BRANCH" == "main" || "$CURRENT_BRANCH" == "master" ]] && \
  echo "❌ Error: デフォルトブランチからデプロイできません" && exit 1
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

### 4. deployブランチ作成 + main取り込み

```bash
git fetch origin main
git checkout -b "$DEPLOY_BRANCH"
git merge origin/main --no-edit || {
  echo "❌ main とのmerge conflict。手動解決が必要です"
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
- main取り込み済み

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

- デフォルトブランチからの実行は禁止
- main取り込みで conflict が出た場合、deployブランチを削除して戻る（featureブランチ側を先に同期すべき）
- deployブランチは PR マージ後に削除してよい
- 環境ブランチへの再デプロイは「同じ deployブランチに push」でPRが更新される

## 関連

- `/ndf:cherry-pick-pr` — 一部コミットだけを環境に届ける場合
- `/ndf:branch-fix-strategy` — ブランチ運用戦略の原則
- `/ndf:sync-main` — featureブランチに main を取り込む
