# セキュリティレポートテンプレート

## 使用方法

このテンプレートをコピーして、セキュリティスキャン結果を報告してください。

---

# セキュリティスキャンレポート - [アプリケーション名]

## エグゼクティブサマリー

- **スキャン日**: YYYY-MM-DD
- **スキャン範囲**: [対象範囲の説明]
- **重大な脆弱性**: X件
- **警告**: Y件
- **情報**: Z件

## 重大な脆弱性 (Critical/High)

### 1. [脆弱性名]

- **場所**: [ファイルパス/エンドポイント]
- **リスクレベル**: 高
- **説明**: [脆弱性の詳細説明]
- **影響**: [悪用された場合の影響]
- **修正方法**:
  ```javascript
  // 修正前
  [問題のあるコード]

  // 修正後
  [修正されたコード]
  ```
- **優先度**: 最高（即座に修正）

### 2. [脆弱性名]

[同様のフォーマットで記載]

## 警告 (Medium)

### 3. [脆弱性名]

- **場所**: [ファイルパス/エンドポイント]
- **リスクレベル**: 中
- **説明**: [脆弱性の詳細説明]
- **修正方法**: [修正方法の説明]

## 情報 (Low/Info)

### 4. [項目名]

- **場所**: [ファイルパス/エンドポイント]
- **説明**: [詳細説明]
- **推奨事項**: [推奨される対応]

## 推奨事項

1. **即座に修正**: 重大な脆弱性X件
2. **1週間以内に修正**: 警告Y件
3. **セキュリティヘッダー追加**: helmet.js 使用
4. **依存ライブラリの更新**: npm audit で検出された脆弱性
5. **定期的なセキュリティスキャン**: 月1回の実施

## 次のステップ

1. [ ] 重大な脆弱性の修正
2. [ ] 修正後の再スキャン
3. [ ] ペネトレーションテストの実施
4. [ ] セキュリティ監視の強化

---

## レポート作成のポイント

### リスクレベルの判断基準

| レベル | 基準 |
|--------|------|
| Critical | リモートコード実行、認証バイパス、データ全体へのアクセス |
| High | SQLインジェクション、XSS（保存型）、権限昇格 |
| Medium | XSS（反射型）、CSRF、情報漏洩（限定的） |
| Low | セキュリティヘッダー不足、詳細なエラーメッセージ |
| Info | ベストプラクティスからの逸脱 |

### 修正優先度

1. **即座に**: Critical/High（本番環境に影響）
2. **1週間以内**: Medium（悪用の可能性あり）
3. **次回リリース**: Low/Info（改善推奨）

## Codex CLI 連携

詳細な独立レビューが必要な場合は `corder` エージェントに委譲するか、`/ndf:external-ai` skill の手順で `codex exec` を直接起動する。例:

```bash
# === 1. プロンプト書き出し（最終出力先を明示し apply_patch で書かせる） ===
FINAL=/tmp/codex-output-sec-scan.md

cat > /tmp/sec-scan-prompt.md <<EOF
あなたはセキュリティレビュアーです。以下の観点で対象ファイルを精査してください:
- OWASP Top 10 の脆弱性
- 認証・認可の問題
- 機密情報の露出

## 対象ファイル（絶対パス）
/workspace/src/...

## 出力先（必須）
最終的なスキャン結果を **必ず** \`${FINAL}\` に \`apply_patch\` で新規作成してください。
**stdout への出力だけでは不十分です**。書き出し後、stdout にも同じ内容を出力してください。

## 出力形式
Markdown。行番号と該当コードスニペットを明記。tool 呼び出しのみで終了せず、
最後に必ず assistant message として 1 回出力してください。
EOF

# === 2. バックグラウンド起動 ===
codex exec --dangerously-bypass-approvals-and-sandbox \
  --config reasoning.effort=medium \
  -C "$PWD" \
  < /tmp/sec-scan-prompt.md \
  > /tmp/sec-scan-stdout.md \
  2> /tmp/sec-scan-err.log &

# === 3. 完了確認（^tokens used$ sentinel を待つ。`ps -p` は zombie を生存と誤判定する） ===
until grep -q '^tokens used$' /tmp/sec-scan-err.log 2>/dev/null; do
  sleep 30
done

# === 4. 成果物を回収（ファイル → stdout → stderr の三段フォールバック） ===
if [ -s "$FINAL" ]; then
    cp "$FINAL" ./sec-scan-result.md
elif [ -s /tmp/sec-scan-stdout.md ]; then
    cp /tmp/sec-scan-stdout.md ./sec-scan-result.md
    echo "WARN: stdout からフォールバック回収（ファイル書き出しなし）" >&2
else
    echo "ERROR: Codex の最終出力を回収できませんでした。stderr 末尾を確認:" >&2
    tail -200 /tmp/sec-scan-err.log
fi
```

詳細は `/ndf:external-ai` skill と `references/cli-codex.md` を参照。
