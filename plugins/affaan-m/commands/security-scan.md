# セキュリティスキャンコマンド

## コマンド名
`/security-scan`

## 説明
コードベース全体をスキャンし、セキュリティ脆弱性を検出します。

## 使用方法

```bash
/security-scan [オプション: 対象ディレクトリ]
```

**例**:
```bash
/security-scan
/security-scan src/auth
```

## スキャン対象

### 1. シークレット混入検出
- APIキー、トークン
- パスワード、認証情報
- プライベートキー
- データベース接続文字列

### 2. セキュリティベストプラクティス
- 入力検証の欠如
- SQLインジェクション脆弱性
- XSS（クロスサイトスクリプティング）
- CSRF対策の有無

### 3. 依存関係の脆弱性
- 既知の脆弱性を持つパッケージ
- 古いバージョンの使用
- セキュリティアップデートの必要性

## 実行内容

### スキャンステップ

```
┌─────────────────────────────────┐
│ 1. シークレット検出             │
│    - API keys                   │
│    - Tokens                     │
│    - Passwords                  │
└──────────┬──────────────────────┘
           │
           v
┌─────────────────────────────────┐
│ 2. コード脆弱性スキャン         │
│    - SQL injection              │
│    - XSS                        │
│    - Path traversal             │
└──────────┬──────────────────────┘
           │
           v
┌─────────────────────────────────┐
│ 3. 依存関係チェック             │
│    - npm audit                  │
│    - Known vulnerabilities      │
└──────────┬──────────────────────┘
           │
           v
┌─────────────────────────────────┐
│ 4. レポート生成                 │
│    - 検出結果サマリー           │
│    - 修正推奨事項               │
└─────────────────────────────────┘
```

## 出力例

```
🔒 セキュリティスキャン実行中...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【シークレット検出】
  ⚠️  2件の潜在的なシークレットを検出

  1. src/config/database.ts:15
     const DB_PASSWORD = "mypassword123";
     推奨: 環境変数を使用してください

  2. src/api/client.ts:8
     const API_KEY = "sk-1234567890abcdef";
     推奨: .envファイルに移動してください

【コード脆弱性】
  🚨 3件の脆弱性を検出

  1. HIGH: SQLインジェクションの可能性
     ファイル: src/user/repository.ts:42
     コード: db.query(`SELECT * FROM users WHERE id = ${userId}`)
     修正: パラメータ化クエリを使用

  2. MEDIUM: XSS脆弱性
     ファイル: src/components/Profile.tsx:25
     コード: <div dangerouslySetInnerHTML={{ __html: userInput }} />
     修正: サニタイズ処理を追加

  3. LOW: パストラバーサル
     ファイル: src/file/handler.ts:18
     コード: fs.readFile(req.query.file)
     修正: ファイルパスの検証を追加

【依存関係の脆弱性】
  ⚠️  5件の既知の脆弱性を検出

  1. express@4.17.1 → 4.18.2 (HIGH)
     CVE-2022-24999: ReDos vulnerability
     修正: npm update express

  2. lodash@4.17.19 → 4.17.21 (MEDIUM)
     Prototype pollution
     修正: npm update lodash

【スキャン結果サマリー】
  🔴 HIGH: 2件
  🟡 MEDIUM: 4件
  🔵 LOW: 1件
  ✅ PASS: 145件

【推奨アクション】
  1. 即座に修正: HIGH severity（2件）
  2. 計画的に修正: MEDIUM severity（4件）
  3. 余裕があれば修正: LOW severity（1件）

【詳細レポート】
  レポートを保存しました: ./security-report.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 検出パターン

### シークレット検出パターン

```regex
AWS_ACCESS_KEY_ID       : AKIA[0-9A-Z]{16}
AWS_SECRET_ACCESS_KEY   : [A-Za-z0-9/+=]{40}
GITHUB_TOKEN           : ghp_[A-Za-z0-9]{36}
SLACK_TOKEN            : xox[baprs]-[0-9a-zA-Z-]+
PRIVATE_KEY            : -----BEGIN (RSA |DSA )?PRIVATE KEY-----
DATABASE_URL           : postgres://[^:]+:[^@]+@[^/]+/.*
```

**カスタマイズ可能**（plugin.json）:
```json
{
  "config": {
    "security": {
      "secretPatterns": [
        "AWS_ACCESS_KEY_ID",
        "CUSTOM_API_KEY"
      ]
    }
  }
}
```

### コード脆弱性パターン

**SQLインジェクション**:
```javascript
// ❌ 危険
db.query(`SELECT * FROM users WHERE id = ${userId}`);
db.query("SELECT * FROM users WHERE name = '" + userName + "'");

// ✅ 安全
db.query('SELECT * FROM users WHERE id = ?', [userId]);
db.query('SELECT * FROM users WHERE name = $1', [userName]);
```

**XSS（クロスサイトスクリプティング）**:
```javascript
// ❌ 危険
element.innerHTML = userInput;
<div dangerouslySetInnerHTML={{ __html: userInput }} />

// ✅ 安全
element.textContent = userInput;
<div>{sanitize(userInput)}</div>
```

**パストラバーサル**:
```javascript
// ❌ 危険
fs.readFile(req.query.file);
fs.readFile('../../../etc/passwd');

// ✅ 安全
const safePath = path.join(UPLOAD_DIR, path.basename(req.query.file));
fs.readFile(safePath);
```

## NDFプラグインとの連携

### qaエージェント
`ndf:qa`がセキュリティレビューを実施：
- OWASP Top 10チェック
- ベストプラクティスの適用確認
- セキュアコーディングガイドラインの遵守

### corderエージェント
`ndf:corder`が脆弱性を修正：
- セキュアなコードパターンへの置き換え
- 入力検証の追加
- エスケープ処理の実装

## affaan-mプラグインのHooks

### secret-scan Hook
PreCommit時に自動シークレットスキャン：

**動作**:
- コミット前に自動実行
- シークレット検出時はコミットをブロック
- 警告メッセージを表示

## 修正ガイド

### HIGH Severity（即座に修正）

**SQLインジェクション**:
```typescript
// Before
const query = `SELECT * FROM users WHERE id = ${userId}`;
db.query(query);

// After
const query = 'SELECT * FROM users WHERE id = ?';
db.query(query, [userId]);
```

**認証情報のハードコード**:
```typescript
// Before
const API_KEY = 'sk-1234567890';

// After
const API_KEY = process.env.API_KEY;
```

### MEDIUM Severity（計画的に修正）

**XSS脆弱性**:
```typescript
// Before
element.innerHTML = userInput;

// After
import DOMPurify from 'dompurify';
element.innerHTML = DOMPurify.sanitize(userInput);
```

**CSRF対策**:
```typescript
// Before (Express)
app.post('/api/transfer', (req, res) => {
  // CSRF対策なし
});

// After
import csrf from 'csurf';
app.use(csrf({ cookie: true }));
app.post('/api/transfer', (req, res) => {
  // CSRFトークンが自動検証される
});
```

### LOW Severity（余裕があれば修正）

**ディレクトリリスティング**:
```typescript
// Before
app.use(express.static('public', { dotfiles: 'allow' }));

// After
app.use(express.static('public', { dotfiles: 'ignore' }));
```

## 依存関係の脆弱性修正

### npm audit の実行

```bash
# 脆弱性スキャン
npm audit

# 自動修正（可能な場合）
npm audit fix

# 強制アップデート（破壊的変更を含む）
npm audit fix --force
```

### 個別パッケージのアップデート

```bash
# 特定パッケージのアップデート
npm update express
npm update lodash@4.17.21

# package.jsonを更新してから
npm install
```

## ベストプラクティス

### 実行タイミング
- ✅ 機能開発完了後
- ✅ Pull Request作成前
- ✅ 定期的なスキャン（週1回）
- ✅ 本番デプロイ前

### シークレット管理
- ✅ 環境変数を使用（`.env`）
- ✅ `.gitignore`に`.env`を追加
- ✅ `.env.example`でテンプレート提供
- ✅ CI/CDで環境変数を設定

### 入力検証
- ✅ すべてのユーザー入力を検証
- ✅ ホワイトリスト方式を優先
- ✅ 型チェックを活用（TypeScript）

## トラブルシューティング

### 誤検知が多い
- 検出パターンをカスタマイズ
- 特定ファイルを除外（`.securityignore`）
- コメントで誤検知を抑制

### スキャンが遅い
- 対象ディレクトリを限定
- `node_modules`を除外
- 並列スキャンを有効化

### 依存関係の更新で問題発生
- 段階的にアップデート
- テストを実行して確認
- 必要に応じてロールバック

## 関連コマンド

- `/owasp-check` - OWASP Top 10チェックリスト
- `/tdd-coverage` - テストカバレッジ検証
- `/review-pr` - コードレビュー（NDFプラグイン）

## 関連ドキュメント

- [セキュリティレビュースキル](../skills/security-review/SKILL.md)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

## 参考

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [npm audit documentation](https://docs.npmjs.com/cli/v8/commands/npm-audit)
- [Snyk Vulnerability Database](https://snyk.io/vuln/)
