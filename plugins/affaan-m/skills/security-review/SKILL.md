# セキュリティレビュースキル

## スキル名
`security-review`

## 説明
OWASP Top 10（2021）に基づくセキュリティレビューを実施するスキル。コードベース全体のセキュリティ脆弱性を検出し、修正ガイドを提供します。

## トリガー条件

このスキルは以下の場合に自動的に起動されます：

- ユーザーが「セキュリティレビュー」と指示した時
- 「脆弱性をチェック」と指示した時
- `/security-scan`または`/owasp-check`コマンド実行時
- 認証・認可に関連するコード変更時

## OWASP Top 10 チェックリスト

### 1. A01:2021 - Broken Access Control
**アクセス制御の不備**

**チェック項目**:
- 認証なしでアクセス可能なエンドポイント
- ロールベースアクセス制御（RBAC）の実装
- 直接オブジェクト参照の保護
- 権限昇格の脆弱性

### 2. A02:2021 - Cryptographic Failures
**暗号化の失敗**

**チェック項目**:
- パスワードのハッシュ化（bcrypt, Argon2）
- HTTPS通信の強制
- 機密データの暗号化
- 安全な乱数生成器の使用

### 3. A03:2021 - Injection
**インジェクション**

**チェック項目**:
- SQLインジェクション対策
- NoSQLインジェクション対策
- コマンドインジェクション対策
- LDAPインジェクション対策

### 4. A04:2021 - Insecure Design
**安全でない設計**

**チェック項目**:
- セキュリティ要件の定義
- 脅威モデリングの実施
- レート制限の実装
- セキュアな設計パターンの採用

### 5. A05:2021 - Security Misconfiguration
**セキュリティ設定ミス**

**チェック項目**:
- デフォルト認証情報の変更
- エラーメッセージの適切な処理
- セキュリティヘッダーの設定
- 不要な機能の無効化

### 6. A06:2021 - Vulnerable Components
**脆弱で古いコンポーネント**

**チェック項目**:
- 依存関係の定期的な更新
- 既知の脆弱性のスキャン（npm audit）
- サポート終了したライブラリの置き換え
- バージョン固定（lockファイル）

### 7. A07:2021 - Authentication Failures
**識別と認証の失敗**

**チェック項目**:
- 多要素認証（MFA）の実装
- 弱いパスワードの拒否
- セッション管理の適切な実装
- アカウントロックアウト機能

### 8. A08:2021 - Integrity Failures
**ソフトウェアとデータの整合性の失敗**

**チェック項目**:
- CDNの整合性チェック（SRI）
- デジタル署名の検証
- CI/CDパイプラインのセキュリティ
- 自動更新の検証

### 9. A09:2021 - Logging Failures
**セキュリティログとモニタリングの失敗**

**チェック項目**:
- 認証・認可の失敗をログ記録
- 重要な操作のログ記録
- ログの定期的な監視
- アラート設定

### 10. A10:2021 - SSRF
**サーバーサイドリクエストフォージェリ**

**チェック項目**:
- ユーザー入力URLの検証
- 内部ネットワークへのアクセス制限
- URLホワイトリストの使用
- メタデータサービスへのアクセス防止

## 検出パターン

### シークレット検出

```regex
AWS_ACCESS_KEY_ID       : AKIA[0-9A-Z]{16}
AWS_SECRET_ACCESS_KEY   : [A-Za-z0-9/+=]{40}
GITHUB_TOKEN           : ghp_[A-Za-z0-9]{36}
SLACK_TOKEN            : xox[baprs]-[0-9a-zA-Z-]+
PRIVATE_KEY            : -----BEGIN (RSA |DSA )?PRIVATE KEY-----
DATABASE_URL           : postgres://[^:]+:[^@]+@[^/]+/.*
```

### SQLインジェクション

```javascript
// ❌ 危険
db.query(`SELECT * FROM users WHERE id = ${userId}`);

// ✅ 安全
db.query('SELECT * FROM users WHERE id = ?', [userId]);
```

### XSS（クロスサイトスクリプティング）

```javascript
// ❌ 危険
element.innerHTML = userInput;

// ✅ 安全
element.textContent = userInput;
```

### パストラバーサル

```javascript
// ❌ 危険
fs.readFile(req.query.file);

// ✅ 安全
const safePath = path.join(UPLOAD_DIR, path.basename(req.query.file));
fs.readFile(safePath);
```

## NDFプラグインとの連携

### qaエージェント連携
`ndf:qa`がセキュリティレビューを実施：

- OWASP Top 10チェック
- ベストプラクティスの適用確認
- セキュアコーディングガイドラインの遵守

### corderエージェント連携
`ndf:corder`が脆弱性を修正：

- セキュアなコードパターンへの置き換え
- 入力検証の追加
- エスケープ処理の実装

### researcherエージェント連携
`ndf:researcher`がセキュリティ情報を調査：

- 最新のセキュリティベストプラクティス
- CVE情報の収集
- セキュリティアドバイザリの確認

## affaan-mプラグインのHooks連携

### secret-scan Hook
PreCommit時に自動シークレットスキャン：

- シークレット検出時はコミットをブロック
- 警告メッセージを表示

## 重大度分類

### CRITICAL（緊急）
- 認証バイパス
- SQLインジェクション
- リモートコード実行
- 機密情報の漏洩

→ **即座に修正**

### HIGH（高）
- XSS脆弱性
- CSRF脆弱性
- パストラバーサル
- 権限昇格

→ **24時間以内に修正**

### MEDIUM（中）
- 情報漏洩
- セキュリティ設定ミス
- 弱い暗号化
- 不適切なエラーハンドリング

→ **1週間以内に修正**

### LOW（低）
- ディレクトリリスティング
- 情報開示
- 非推奨機能の使用

→ **計画的に修正**

## 修正ガイド

### SQLインジェクション修正

```typescript
// Before
const query = `SELECT * FROM users WHERE id = ${userId}`;
db.query(query);

// After
const query = 'SELECT * FROM users WHERE id = ?';
db.query(query, [userId]);
```

### XSS修正

```typescript
// Before
element.innerHTML = userInput;

// After
import DOMPurify from 'dompurify';
element.innerHTML = DOMPurify.sanitize(userInput);
```

### 認証情報のハードコード修正

```typescript
// Before
const API_KEY = 'sk-1234567890';

// After
const API_KEY = process.env.API_KEY;
```

## セキュリティヘッダー

### 推奨設定（Express）

```typescript
import helmet from 'helmet';

app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'"],
      imgSrc: ["'self'", "data:", "https:"],
    },
  },
  hsts: {
    maxAge: 31536000,
    includeSubDomains: true,
  },
}));
```

## ベストプラクティス

### 開発フロー
1. 機能実装
2. TDDワークフロー
3. セキュリティスキャン（`/security-scan`）
4. OWASP Top 10チェック（`/owasp-check`）
5. コードレビュー
6. デプロイ

### 定期的なチェック
- ✅ 週1回: セキュリティスキャン
- ✅ 月1回: OWASP Top 10チェック
- ✅ 四半期ごと: 外部セキュリティ監査

### シークレット管理
- ✅ 環境変数を使用（`.env`）
- ✅ `.gitignore`に`.env`を追加
- ✅ `.env.example`でテンプレート提供
- ✅ CI/CDで環境変数を設定

## 使用例

### 例1: 認証機能のセキュリティレビュー

```typescript
// セキュリティレビュー実施
/security-scan src/auth

// 検出された問題
// 1. パスワードが平文で保存されている
// 2. セッション固定化の脆弱性
// 3. レート制限なし

// 修正実施（ndf:corderと連携）
// - bcryptでパスワードをハッシュ化
// - セッション再生成を実装
// - express-rate-limitを導入
```

## トラブルシューティング

### 誤検知が多い
- 検出パターンをカスタマイズ
- 特定ファイルを除外（`.securityignore`）
- コメントで誤検知を抑制

### スキャンが遅い
- 対象ディレクトリを限定
- `node_modules`を除外
- 並列スキャンを有効化

## 関連コマンド

- `/security-scan` - 総合セキュリティスキャン
- `/owasp-check` - OWASP Top 10チェック
- `/tdd-coverage` - テストカバレッジ検証

## 関連ドキュメント

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)

## 参考

- [OWASP Top 10 - 2021](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
- [Snyk Vulnerability Database](https://snyk.io/vuln/)
