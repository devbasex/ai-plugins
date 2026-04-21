# OWASP Top 10 チェックコマンド

## コマンド名
`/owasp-check`

## 説明
OWASP Top 10（2021）に基づくセキュリティチェックリストを実行します。

## 使用方法

```bash
/owasp-check [オプション: 対象ディレクトリ]
```

**例**:
```bash
/owasp-check
/owasp-check src/api
```

## OWASP Top 10 (2021)

### 1. A01:2021 – Broken Access Control
**アクセス制御の不備**

チェック項目:
- [ ] 認証なしでアクセス可能なエンドポイントの確認
- [ ] ロールベースアクセス制御（RBAC）の実装
- [ ] ユーザーごとの権限検証
- [ ] 直接オブジェクト参照の保護

**検出例**:
```typescript
// ❌ 危険: 権限チェックなし
app.delete('/api/users/:id', (req, res) => {
  deleteUser(req.params.id);
});

// ✅ 安全: 権限チェックあり
app.delete('/api/users/:id', authenticate, authorize('admin'), (req, res) => {
  if (req.user.id !== req.params.id && !req.user.isAdmin) {
    return res.status(403).json({ error: 'Forbidden' });
  }
  deleteUser(req.params.id);
});
```

### 2. A02:2021 – Cryptographic Failures
**暗号化の失敗**

チェック項目:
- [ ] パスワードのハッシュ化（bcrypt, Argon2）
- [ ] HTTPS通信の強制
- [ ] 機密データの暗号化（保存時・転送時）
- [ ] 安全な乱数生成器の使用

**検出例**:
```typescript
// ❌ 危険: 平文パスワード
const user = { password: req.body.password };

// ✅ 安全: ハッシュ化
import bcrypt from 'bcrypt';
const hashedPassword = await bcrypt.hash(req.body.password, 10);
const user = { password: hashedPassword };
```

### 3. A03:2021 – Injection
**インジェクション**

チェック項目:
- [ ] SQLインジェクション対策（パラメータ化クエリ）
- [ ] NoSQLインジェクション対策
- [ ] コマンドインジェクション対策
- [ ] LDAPインジェクション対策

**検出例**:
```typescript
// ❌ 危険: SQLインジェクション
db.query(`SELECT * FROM users WHERE id = ${req.params.id}`);

// ✅ 安全: パラメータ化クエリ
db.query('SELECT * FROM users WHERE id = ?', [req.params.id]);
```

### 4. A04:2021 – Insecure Design
**安全でない設計**

チェック項目:
- [ ] セキュリティ要件の定義
- [ ] 脅威モデリングの実施
- [ ] セキュアな設計パターンの採用
- [ ] レート制限の実装

**検出例**:
```typescript
// ❌ 危険: レート制限なし
app.post('/api/login', loginHandler);

// ✅ 安全: レート制限あり
import rateLimit from 'express-rate-limit';
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15分
  max: 5, // 最大5回
});
app.post('/api/login', limiter, loginHandler);
```

### 5. A05:2021 – Security Misconfiguration
**セキュリティ設定ミス**

チェック項目:
- [ ] デフォルト認証情報の変更
- [ ] エラーメッセージの適切な処理
- [ ] 不要な機能・ポートの無効化
- [ ] セキュリティヘッダーの設定

**検出例**:
```typescript
// ❌ 危険: 詳細なエラー情報を返す
app.use((err, req, res, next) => {
  res.status(500).json({ error: err.stack });
});

// ✅ 安全: 一般的なエラーメッセージ
app.use((err, req, res, next) => {
  console.error(err.stack); // サーバーログに記録
  res.status(500).json({ error: 'Internal Server Error' });
});
```

### 6. A06:2021 – Vulnerable and Outdated Components
**脆弱で古いコンポーネント**

チェック項目:
- [ ] 依存関係の定期的な更新
- [ ] 既知の脆弱性のスキャン（npm audit）
- [ ] サポート終了したライブラリの置き換え
- [ ] バージョン固定（package-lock.json）

**検出例**:
```bash
# 脆弱性スキャン
npm audit

# 自動修正
npm audit fix
```

### 7. A07:2021 – Identification and Authentication Failures
**識別と認証の失敗**

チェック項目:
- [ ] 多要素認証（MFA）の実装
- [ ] 弱いパスワードの拒否
- [ ] セッション管理の適切な実装
- [ ] アカウントロックアウト機能

**検出例**:
```typescript
// ❌ 危険: セッションIDの固定化
app.post('/login', (req, res) => {
  // 既存のセッションIDを再利用
  req.session.userId = user.id;
});

// ✅ 安全: セッション再生成
app.post('/login', (req, res) => {
  req.session.regenerate((err) => {
    req.session.userId = user.id;
  });
});
```

### 8. A08:2021 – Software and Data Integrity Failures
**ソフトウェアとデータの整合性の失敗**

チェック項目:
- [ ] CDNの整合性チェック（SRI）
- [ ] デジタル署名の検証
- [ ] CI/CD パイプラインのセキュリティ
- [ ] 自動更新の検証

**検出例**:
```html
<!-- ❌ 危険: 整合性チェックなし -->
<script src="https://cdn.example.com/library.js"></script>

<!-- ✅ 安全: SRI（Subresource Integrity） -->
<script
  src="https://cdn.example.com/library.js"
  integrity="sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/ux..."
  crossorigin="anonymous">
</script>
```

### 9. A09:2021 – Security Logging and Monitoring Failures
**セキュリティログとモニタリングの失敗**

チェック項目:
- [ ] 認証・認可の失敗をログ記録
- [ ] 重要な操作のログ記録
- [ ] ログの定期的な監視
- [ ] アラート設定

**検出例**:
```typescript
// ❌ 危険: ログ記録なし
app.post('/api/login', async (req, res) => {
  const user = await authenticate(req.body);
  if (!user) return res.status(401).json({ error: 'Invalid credentials' });
});

// ✅ 安全: ログ記録あり
import logger from './logger';
app.post('/api/login', async (req, res) => {
  const user = await authenticate(req.body);
  if (!user) {
    logger.warn(`Failed login attempt for ${req.body.username} from ${req.ip}`);
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  logger.info(`Successful login: ${user.username} from ${req.ip}`);
});
```

### 10. A10:2021 – Server-Side Request Forgery (SSRF)
**サーバーサイドリクエストフォージェリ**

チェック項目:
- [ ] ユーザー入力URLの検証
- [ ] 内部ネットワークへのアクセス制限
- [ ] URLホワイトリストの使用
- [ ] メタデータサービスへのアクセス防止

**検出例**:
```typescript
// ❌ 危険: URLの検証なし
app.get('/fetch', async (req, res) => {
  const data = await fetch(req.query.url);
  res.json(data);
});

// ✅ 安全: URLの検証あり
const ALLOWED_DOMAINS = ['api.example.com', 'cdn.example.com'];
app.get('/fetch', async (req, res) => {
  const url = new URL(req.query.url);
  if (!ALLOWED_DOMAINS.includes(url.hostname)) {
    return res.status(400).json({ error: 'Invalid URL' });
  }
  const data = await fetch(url);
  res.json(data);
});
```

## 出力例

```
🔐 OWASP Top 10 チェック実行中...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【A01: Broken Access Control】
  ⚠️  2件の問題を検出
  - src/api/users.ts:45 - 権限チェックが不足
  - src/api/posts.ts:32 - 直接オブジェクト参照

【A02: Cryptographic Failures】
  ✅ 問題なし

【A03: Injection】
  🚨 3件の問題を検出
  - src/db/users.ts:28 - SQLインジェクション
  - src/db/search.ts:15 - SQLインジェクション
  - src/api/exec.ts:42 - コマンドインジェクション

【A04: Insecure Design】
  ⚠️  1件の問題を検出
  - src/api/login.ts:10 - レート制限なし

【A05: Security Misconfiguration】
  ⚠️  2件の問題を検出
  - src/app.ts:55 - 詳細なエラーメッセージ
  - src/config.ts:12 - デフォルト認証情報

【A06: Vulnerable Components】
  🚨 5件の脆弱性を検出
  - express@4.17.1 (HIGH)
  - lodash@4.17.19 (MEDIUM)

【A07: Authentication Failures】
  ⚠️  1件の問題を検出
  - src/auth/session.ts:22 - セッション固定化

【A08: Integrity Failures】
  ✅ 問題なし

【A09: Logging Failures】
  ⚠️  3件の問題を検出
  - src/api/login.ts - ログ記録なし
  - src/api/delete.ts - ログ記録なし
  - src/api/admin.ts - ログ記録なし

【A10: SSRF】
  🚨 1件の問題を検出
  - src/api/fetch.ts:18 - URL検証なし

【サマリー】
  🔴 CRITICAL: 4件
  🟡 WARNING: 9件
  ✅ PASS: 7件

【推奨アクション】
  1. CRITICAL問題を即座に修正
  2. WARNING問題を計画的に修正
  3. 定期的に再スキャン

【詳細レポート】
  レポートを保存しました: ./owasp-check-report.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## NDFプラグインとの連携

### qaエージェント
`ndf:qa`がOWASP Top 10チェックを実施：
- 包括的なセキュリティレビュー
- 修正提案
- ベストプラクティスのアドバイス

### corderエージェント
`ndf:corder`が脆弱性を修正：
- セキュアなコードへの置き換え
- セキュリティ対策の実装

## affaan-mプラグインのSkill

### security-review Skill
OWASP Top 10チェックを自動化：
- プロジェクト構造を分析
- 脆弱性パターンを検出
- 修正ガイドを提供

## セキュリティヘッダーの設定

### Express の例

```typescript
import helmet from 'helmet';

app.use(helmet()); // 基本的なセキュリティヘッダー

// カスタマイズ
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
    },
  },
}));
```

### 推奨セキュリティヘッダー

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Content-Security-Policy: default-src 'self'
```

## ベストプラクティス

### 開発フロー
1. 機能実装
2. TDDワークフロー（`/tdd`）
3. セキュリティスキャン（`/security-scan`）
4. OWASP Top 10チェック（`/owasp-check`）
5. コードレビュー
6. デプロイ

### 定期的なチェック
- ✅ 週1回: セキュリティスキャン
- ✅ 月1回: OWASP Top 10チェック
- ✅ 四半期ごと: 外部セキュリティ監査

### 継続的な改善
- セキュリティトレーニング
- 脆弱性情報の収集
- インシデント対応計画の策定

## トラブルシューティング

### 誤検知が多い
- `.owaspignore`ファイルで除外
- 検出ルールをカスタマイズ
- コメントで抑制

### チェックが遅い
- 対象ディレクトリを限定
- キャッシュを活用
- 並列チェックを有効化

## 関連コマンド

- `/security-scan` - 総合セキュリティスキャン
- `/tdd-coverage` - テストカバレッジ検証

## 関連ドキュメント

- [セキュリティレビュースキル](../skills/security-review/SKILL.md)

## 参考

- [OWASP Top 10 - 2021](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
