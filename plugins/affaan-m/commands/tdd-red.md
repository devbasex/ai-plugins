# TDD RED フェーズコマンド

## コマンド名
`/tdd-red`

## 説明
TDDのREDフェーズ - 失敗するテストを作成します。

## 使用方法

```bash
/tdd-red [機能名]
```

**例**:
```bash
/tdd-red "ユーザー認証機能"
```

## REDフェーズの目的

**「失敗するテストを書く」**

- 期待する動作を明確にする
- インターフェースを確認する
- テストが正しく失敗することを確認

## 実行ステップ

### 1. テストケースの作成

**良いテストケース**:
```typescript
describe('authenticateUser', () => {
  it('正しい認証情報で成功する', async () => {
    const result = await authenticateUser('user@example.com', 'password123');
    expect(result.success).toBe(true);
    expect(result.token).toBeDefined();
  });

  it('誤ったパスワードで失敗する', async () => {
    const result = await authenticateUser('user@example.com', 'wrongpassword');
    expect(result.success).toBe(false);
    expect(result.error).toBe('Invalid credentials');
  });

  it('存在しないユーザーで失敗する', async () => {
    const result = await authenticateUser('nonexistent@example.com', 'password');
    expect(result.success).toBe(false);
    expect(result.error).toBe('User not found');
  });
});
```

### 2. テスト実行

```bash
# テストフレームワークに応じて実行
npm test
# または
yarn test
# または
pnpm test
```

### 3. 失敗の確認

**期待される出力**:
```
FAIL  src/auth.test.ts
  authenticateUser
    ✕ 正しい認証情報で成功する (2 ms)
    ✕ 誤ったパスワードで失敗する (1 ms)
    ✕ 存在しないユーザーで失敗する (1 ms)

  ● authenticateUser › 正しい認証情報で成功する

    ReferenceError: authenticateUser is not defined
```

## チェックポイント

### ✅ 良いREDテスト
- テストが明確に失敗する
- エラーメッセージが理解しやすい
- 期待する動作が明確
- テストケースが独立している

### ❌ 悪いREDテスト
- テストがパスしてしまう（実装がある）
- エラーメッセージが不明瞭
- 期待する動作が曖昧
- 他のテストに依存している

## 出力例

```
🔴 REDフェーズ: 失敗テストを作成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【テスト作成完了】
  ✅ src/auth.test.ts を作成しました
  ✅ 3つのテストケースを追加しました

【テスト実行結果】
  ❌ authenticateUser - 正しい認証情報で成功する
  ❌ authenticateUser - 誤ったパスワードで失敗する
  ❌ authenticateUser - 存在しないユーザーで失敗する

  エラー: ReferenceError: authenticateUser is not defined

【次のステップ】
  GREENフェーズに進みます:
    /tdd-green "ユーザー認証機能"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## テストケースのパターン

### 正常系テスト
- 基本的な機能が正しく動作する
- 典型的な入力で期待通りの出力

### 異常系テスト
- 不正な入力でエラーハンドリング
- 境界値テスト
- エッジケース

### セキュリティテスト
- 入力検証
- 認証・認可
- データ保護

## NDFプラグインとの連携

### corderエージェント
`ndf:corder`がテストコードを生成：
```typescript
// テストフレームワークに応じた適切な構文
// モックの設定
// アサーションの記述
```

### qaエージェント
`ndf:qa`がテストケースの網羅性をレビュー

## affaan-mプラグインのHooks

REDフェーズ中、以下のHooksが発火：

- **auto-format**: テストコードを自動フォーマット
- **typescript-check**: テストコードの型チェック
- **detect-console-log**: console.logの検出警告

## 注意事項

### REDフェーズの原則
1. **実装コードを書かない** - テストだけを書く
2. **テストが失敗することを確認** - パスしてはいけない
3. **明確なエラーメッセージ** - 何が不足しているか分かる

### よくある間違い
- ❌ テストと同時に実装を書く
- ❌ テストがパスしても気にしない
- ❌ エラーメッセージを確認しない

## 次のステップ

REDフェーズが完了したら：

```bash
/tdd-green "ユーザー認証機能"
```

GREENフェーズで最小実装を行います。

## トラブルシューティング

### テストがパスしてしまう
- 既存の実装が存在する可能性
- テストケースを見直す
- 実装コードを削除する

### エラーメッセージが不明瞭
- アサーションを明確にする
- テストの説明（it/test）を改善
- エラーメッセージをカスタマイズ

### テストが実行できない
- テストフレームワークの設定を確認
- 依存関係のインストール確認
- パスが正しいか確認

## 関連ドキュメント

- [TDDワークフロー](./tdd.md)
- [GREENフェーズ](./tdd-green.md)

## 参考

- [Kent Beck's TDD principles](https://www.amazon.com/Test-Driven-Development-Kent-Beck/dp/0321146530)
- [Jest Documentation](https://jestjs.io/)
- [Vitest Documentation](https://vitest.dev/)
