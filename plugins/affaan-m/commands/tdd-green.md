# TDD GREEN フェーズコマンド

## コマンド名
`/tdd-green`

## 説明
TDDのGREENフェーズ - テストをパスする最小限の実装を行います。

## 使用方法

```bash
/tdd-green [機能名]
```

**例**:
```bash
/tdd-green "ユーザー認証機能"
```

## GREENフェーズの目的

**「テストをパスする最小実装」**

- シンプルな実装を優先
- テストをすべてパス
- 複雑さを避ける

## 実行ステップ

### 1. 最小実装の作成

**良い最小実装**:
```typescript
interface AuthResult {
  success: boolean;
  token?: string;
  error?: string;
}

async function authenticateUser(
  username: string,
  password: string
): Promise<AuthResult> {
  // 最小限の実装 - まずは動作させることを優先
  const user = await findUserByUsername(username);

  if (!user) {
    return { success: false, error: 'User not found' };
  }

  const passwordMatch = await comparePassword(password, user.passwordHash);

  if (!passwordMatch) {
    return { success: false, error: 'Invalid credentials' };
  }

  const token = generateToken(user.id);
  return { success: true, token };
}
```

### 2. テスト実行

```bash
npm test
```

### 3. 成功の確認

**期待される出力**:
```
PASS  src/auth.test.ts
  authenticateUser
    ✓ 正しい認証情報で成功する (25 ms)
    ✓ 誤ったパスワードで失敗する (15 ms)
    ✓ 存在しないユーザーで失敗する (12 ms)

Test Suites: 1 passed, 1 total
Tests:       3 passed, 3 total
```

## チェックポイント

### ✅ 良いGREEN実装
- すべてのテストがパス
- シンプルで理解しやすい
- 必要最小限の機能のみ
- テストが求める動作を満たす

### ❌ 悪いGREEN実装
- テストがまだ失敗する
- 過剰に複雑な実装
- テストに無い機能を追加
- パフォーマンス最適化を先行

## 出力例

```
🟢 GREENフェーズ: 最小実装を作成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【実装完了】
  ✅ src/auth.ts を作成しました
  ✅ authenticateUser 関数を実装しました

【テスト実行結果】
  ✅ authenticateUser - 正しい認証情報で成功する
  ✅ authenticateUser - 誤ったパスワードで失敗する
  ✅ authenticateUser - 存在しないユーザーで失敗する

  すべてのテストがパスしました！

【次のステップ】
  REFACTORフェーズに進みます:
    /tdd-refactor "ユーザー認証機能"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 最小実装の原則

### 1. YAGNI (You Aren't Gonna Need It)
- 今必要な機能だけを実装
- 将来の拡張性は後で考える

### 2. Keep It Simple
- 複雑なアルゴリズムは避ける
- 直線的なコードフロー
- 明確な変数名

### 3. DRY は後で
- 重複は後のリファクタリングで解消
- まずは動作することが優先

## NDFプラグインとの連携

### corderエージェント
`ndf:corder`が最小実装を生成：
```typescript
// シンプルな実装
// 明確なエラーハンドリング
// 型安全なコード
```

### directorエージェント
`ndf:director`が実装順序を調整：
- 依存関係の整理
- 並列実行可能な部分の特定

## affaan-mプラグインのHooks

GREENフェーズ中、以下のHooksが発火：

- **auto-format**: コードを自動フォーマット
- **typescript-check**: 型チェックを実行
- **detect-console-log**: デバッグコードの検出

## 実装パターン

### ガードクロース（早期リターン）
```typescript
function validateUser(user: User): string | null {
  if (!user) return 'User is required';
  if (!user.email) return 'Email is required';
  if (!user.password) return 'Password is required';
  return null;
}
```

### シンプルなエラーハンドリング
```typescript
try {
  const result = await processData(data);
  return { success: true, data: result };
} catch (error) {
  return { success: false, error: error.message };
}
```

### 明確な制御フロー
```typescript
// ✅ 良い例 - 直線的
async function createUser(data: UserData) {
  const validated = validateUserData(data);
  if (!validated.success) return validated;

  const user = await saveUser(validated.data);
  return { success: true, user };
}

// ❌ 悪い例 - ネストが深い
async function createUser(data: UserData) {
  if (validateUserData(data)) {
    if (await checkDuplicate(data.email)) {
      if (await saveUser(data)) {
        // ...
      }
    }
  }
}
```

## 注意事項

### GREENフェーズの原則
1. **テストをすべてパス** - 1つでも失敗したらGREEN未完了
2. **最小限の実装** - 過剰な機能追加は禁物
3. **リファクタリングは後** - まずは動作させる

### よくある間違い
- ❌ テストがパスする前に最適化
- ❌ テストに無い機能を実装
- ❌ 複雑なデザインパターンを適用

## 次のステップ

GREENフェーズが完了したら：

```bash
/tdd-refactor "ユーザー認証機能"
```

REFACTORフェーズでコード品質を向上させます。

## トラブルシューティング

### テストが一部失敗する
- 失敗するテストケースを特定
- エラーメッセージを確認
- 実装ロジックを見直す

### 実装が複雑になる
- より小さいステップに分割
- テストケースを分ける
- シンプルな解決策を探す

### 型エラーが出る
- TypeScript設定を確認
- 型定義を明確にする
- affaan-mのtypescript-check Hookを確認

## 関連ドキュメント

- [TDDワークフロー](./tdd.md)
- [REFACTORフェーズ](./tdd-refactor.md)

## 参考

- [YAGNI principle](https://en.wikipedia.org/wiki/You_aren%27t_gonna_need_it)
- [KISS principle](https://en.wikipedia.org/wiki/KISS_principle)
- [Test-Driven Development by Example](https://www.amazon.com/Test-Driven-Development-Kent-Beck/dp/0321146530)
