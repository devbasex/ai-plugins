# Git / gh 共通エラー事例集

## 1. git add pathspec エラー

### 事象
```
fatal: pathspec 'lambda-batch/CarImageProcessingPipeline/src/foo.py' did not match any files
```

### 原因
CWD が `/work/repo/lambda-batch/CarImageProcessingPipeline/` なのに、
リポジトリルートからの相対パスで `git add` した。

`git status` はリポジトリルートからの相対パスで表示するが、
`git add` は CWD からの相対パスで解決する。

### 予防策
```bash
# Step 1: CWD確認
pwd
# => /work/repo/lambda-batch/CarImageProcessingPipeline/

# Step 2: git status の出力を確認
git status
# modified: lambda-batch/CarImageProcessingPipeline/src/foo.py
#           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#           これはリポジトリルートからの相対パス

# Step 3: CWD からの相対パスに変換
git add src/foo.py
# または
git add .  # CWD以下のすべての変更
```

## 2. gh api 404 エラー

### 事象
```
gh api repos/owner/repo/pulls/comments/123/replies -f body='message'
# => 404 Not Found
```

### 原因
POST メソッドが必要な API エンドポイントに GET でアクセスした。
`gh api` はデフォルトで GET を使用する。

### 修正
```bash
gh api -X POST repos/owner/repo/pulls/comments/123/replies -f body='message'
```

## 3. GitHub 自己 Approve エラー

### 事象
```
Could not approve for pull request review. Can not approve your own pull request
```

### 原因
GitHub はセキュリティ上、自分で作成した PR を APPROVE できない。

### 対策
```bash
# pending review を削除してから COMMENT として再送信
# method: "delete_pending" → method: "create" + event: "COMMENT"
```

## 4. AWS CLI [$LATEST] パースエラー

### 事象
```
Unknown options: , , ,
```

### 原因
CloudWatch ログストリーム名に含まれる `[$LATEST]` が
`--query` JMESPath パーサーや shell の glob として解釈される。

### 対策
```bash
# シングルクォートで囲んでも --query との組み合わせで問題が出る
# --output json + python パースが最も安全
aws logs get-log-events \
  --log-group-name "/aws/lambda/func-name" \
  --log-stream-name '2026/02/18/[$LATEST]abc123' \
  --output json | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
for e in data['events']:
    print(e['message'].strip())
"
```

## 5. git commit メッセージの特殊文字

### 事象
コミットメッセージに日本語や改行が含まれるとエスケープ問題が発生。

### 対策
常に HEREDOC 形式を使用:
```bash
git commit -m "$(cat <<'EOF'
日本語メッセージ

詳細説明

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

注意: `<<'EOF'` （シングルクォート付き）で変数展開を抑制する。

## 6. gh pr checks が exit code 1 で止まる

### 事象
```
gh pr checks 11765 2>&1
# => チェック結果は表示されるが、1つでもfailがあると exit code 1 で終了
# => Claude Code が「コマンド失敗」と判定して処理を中断
```

### 原因
`gh pr checks` は CI チェックに失敗があると非0の exit code を返す仕様。
Claude Code の Bash ツールはコマンドの exit code が 0 以外だとエラーとして扱う。

### 対策
常に `|| true` を付けて exit code を 0 にする:
```bash
# チェック一覧を取得（failがあっても止まらない）
gh pr checks 11765 2>&1 || true

# --watch で完了待ちする場合も同様
gh pr checks 11765 --watch 2>&1 || true

# 失敗のみフィルタする場合
gh pr checks 11765 2>&1 | grep -i fail || true
```

### 補足
同様の問題が発生する gh コマンド:
- `gh run view RUN_ID --log-failed` （失敗ログ取得時）
- `gh pr diff` （差分が大きい場合にパイプ破損）

いずれも `2>&1 || true` を付けることで安全に実行できる。
