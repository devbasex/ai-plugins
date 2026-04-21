# TDD REFACTOR フェーズコマンド

## コマンド名
`/tdd-refactor`

## 説明
TDDのREFACTORフェーズ - テストをパスしたままコード品質を向上させます。

## 使用方法

```bash
/tdd-refactor [機能名]
```

**例**:
```bash
/tdd-refactor "ユーザー認証機能"
```

## REFACTORフェーズの目的

**「テストをパスしたまま品質向上」**

- コードの重複を排除（DRY原則）
- 可読性を改善
- 保守性を向上
- パフォーマンスの最適化

## 実行ステップ

### 1. リファクタリング対象の特定

**チェックポイント**:
- 重複コード
- 長すぎる関数
- 複雑なネスト
- 不明瞭な変数名
- マジックナンバー

### 2. リファクタリング実施

**リファクタリング前**:
```typescript
async function authenticateUser(username: string, password: string) {
  const user = await db.query('SELECT * FROM users WHERE username = ?', [username]);
  if (!user) {
    return { success: false, error: 'User not found' };
  }
  const hash = crypto.createHash('sha256').update(password).digest('hex');
  if (user.password !== hash) {
    return { success: false, error: 'Invalid credentials' };
  }
  const token = jwt.sign({ id: user.id }, 'secret-key', { expiresIn: '1h' });
  return { success: true, token };
}
```

**リファクタリング後**:
```typescript
async function authenticateUser(
  username: string,
  password: string
): Promise<AuthResult> {
  const user = await findUserByUsername(username);

  if (!user) {
    return createAuthError('User not found');
  }

  if (!await verifyPassword(password, user.passwordHash)) {
    return createAuthError('Invalid credentials');
  }

  const token = generateAuthToken(user.id);
  return createAuthSuccess(token);
}

// ヘルパー関数に分割
async function findUserByUsername(username: string): Promise<User | null> {
  return db.query('SELECT * FROM users WHERE username = ?', [username]);
}

async function verifyPassword(
  password: string,
  storedHash: string
): Promise<boolean> {
  const hash = crypto.createHash('sha256').update(password).digest('hex');
  return hash === storedHash;
}

function generateAuthToken(userId: string): string {
  return jwt.sign({ id: userId }, process.env.JWT_SECRET, { expiresIn: '1h' });
}

function createAuthError(message: string): AuthResult {
  return { success: false, error: message };
}

function createAuthSuccess(token: string): AuthResult {
  return { success: true, token };
}
```

### 3. テスト実行（各ステップ後）

```bash
npm test
```

**重要**: リファクタリングの各ステップ後、必ずテストを実行してください。

## チェックポイント

### ✅ 良いリファクタリング
- テストがすべてパスし続ける
- コードの意図が明確になる
- 重複が排除される
- 関数が単一責任を持つ

### ❌ 悪いリファクタリング
- テストが壊れる
- 可読性が下がる
- 過度に抽象化する
- 不必要な複雑さを追加

## 出力例

```
🔧 REFACTORフェーズ: コード品質を向上
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【リファクタリング実施】
  ✅ authenticateUser を複数の関数に分割
  ✅ マジックナンバーを定数化
  ✅ エラーハンドリングを統一
  ✅ 型定義を明確化

【テスト実行結果】
  ✅ すべてのテストがパス（3/3）
  ✅ テストカバレッジ: 85%

【改善内容】
  - 関数の平均行数: 45 → 12
  - 循環的複雑度: 8 → 3
  - コードの重複: 25% → 5%

【次のステップ】
  COVERAGEフェーズに進みます:
    /tdd-coverage "ユーザー認証機能"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## リファクタリングパターン

### 1. 関数の抽出（Extract Function）

**目的**: 長い関数を小さな関数に分割

```typescript
// Before
function processOrder(order: Order) {
  // 検証ロジック
  if (!order.items.length) throw new Error('No items');
  // 計算ロジック
  const total = order.items.reduce((sum, item) => sum + item.price, 0);
  // 保存ロジック
  db.save(order);
}

// After
function processOrder(order: Order) {
  validateOrder(order);
  const total = calculateTotal(order);
  saveOrder(order);
}
```

### 2. マジックナンバーの定数化

```typescript
// Before
if (user.age >= 18) { }
setTimeout(callback, 3600000);

// After
const ADULT_AGE = 18;
const ONE_HOUR_MS = 60 * 60 * 1000;

if (user.age >= ADULT_AGE) { }
setTimeout(callback, ONE_HOUR_MS);
```

### 3. 条件式の名前付け

```typescript
// Before
if (user.age >= 18 && user.hasLicense && !user.isBanned) {
  allowDriving();
}

// After
const canDrive = user.age >= 18 && user.hasLicense && !user.isBanned;
if (canDrive) {
  allowDriving();
}
```

### 4. DRY原則の適用

```typescript
// Before
function formatUserName(user: User): string {
  return `${user.firstName} ${user.lastName}`;
}

function formatAdminName(admin: Admin): string {
  return `${admin.firstName} ${admin.lastName}`;
}

// After
function formatFullName(person: { firstName: string; lastName: string }): string {
  return `${person.firstName} ${person.lastName}`;
}
```

### 5. ガードクローズの導入

```typescript
// Before
function processPayment(order: Order) {
  if (order.isPaid) {
    if (order.amount > 0) {
      if (order.user) {
        // 実際の処理
      }
    }
  }
}

// After
function processPayment(order: Order) {
  if (!order.isPaid) return;
  if (order.amount <= 0) return;
  if (!order.user) return;

  // 実際の処理
}
```

## NDFプラグインとの連携

### corderエージェント
`ndf:corder`がリファクタリングを支援：
- デザインパターンの適用
- コードレビュー
- ベストプラクティスの提案

### qaエージェント
`ndf:qa`がコード品質を評価：
- 複雑度の計算
- 重複コードの検出
- 保守性の評価

## affaan-mプラグインのHooks

REFACTORフェーズ中、以下のHooksが発火：

- **auto-format**: 自動フォーマット（Prettier/ESLint）
- **typescript-check**: 型チェックを実行
- **detect-console-log**: デバッグコードの検出

## リファクタリングの注意事項

### 原則
1. **小さなステップで** - 大きな変更は避ける
2. **各ステップでテスト** - 必ず動作確認
3. **一度に一つ** - 複数のリファクタリングを同時にしない

### よくある間違い
- ❌ テストを実行せずにリファクタリング
- ❌ 機能追加とリファクタリングを同時に行う
- ❌ 過度な最適化
- ❌ 不必要な抽象化

## 次のステップ

REFACTORフェーズが完了したら：

```bash
/tdd-coverage "ユーザー認証機能"
```

COVERAGEフェーズでテストカバレッジを検証します。

## トラブルシューティング

### リファクタリング後にテストが壊れる
- 変更を元に戻す（git revert）
- より小さなステップに分割
- テストケースを見直す

### どこをリファクタリングすべきか分からない
- 長い関数（50行以上）を探す
- 重複コードを検索
- 複雑な条件式を特定

### リファクタリングが終わらない
- 優先順位をつける
- 80/20ルール（重要な20%に集中）
- 完璧を目指さない

## 関連ドキュメント

- [TDDワークフロー](./tdd.md)
- [COVERAGEフェーズ](./tdd-coverage.md)

## 参考

- [Refactoring: Improving the Design of Existing Code (Martin Fowler)](https://refactoring.com/)
- [Clean Code (Robert C. Martin)](https://www.amazon.com/Clean-Code-Handbook-Software-Craftsmanship/dp/0132350882)
- [リファクタリングカタログ](https://refactoring.com/catalog/)
