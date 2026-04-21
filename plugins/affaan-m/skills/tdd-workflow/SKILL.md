# TDDワークフロースキル

## スキル名
`tdd-workflow`

## 説明
Test-Driven Development (TDD) ワークフローを5段階プロセスでガイドするスキル。RED → GREEN → REFACTOR → COVERAGE の完全なサイクルを実現します。

## トリガー条件

このスキルは以下の場合に自動的に起動されます：

- ユーザーが「TDDで実装」と指示した時
- 「テストファーストで」と指示した時
- `/tdd`コマンドが実行された時
- 新機能開発時にテストの言及があった時

## 5段階TDDプロセス

### ステップ1: インターフェース定義

**目的**: 実装前に公開APIを明確にする

**実行内容**:
- 関数シグネチャの定義
- 型定義（TypeScript）
- ドキュメントコメントの作成
- 期待する動作の明文化

**例**:
```typescript
/**
 * ユーザーを認証する
 * @param username - ユーザー名
 * @param password - パスワード
 * @returns 認証結果（成功時はトークン付き）
 */
interface AuthResult {
  success: boolean;
  token?: string;
  error?: string;
}

function authenticateUser(
  username: string,
  password: string
): Promise<AuthResult>;
```

### ステップ2: RED（失敗テスト作成）

**目的**: 期待する動作を表現するテストを書く

**実行内容**:
1. テストケースを作成
2. テストを実行して失敗を確認
3. エラーメッセージが明確か確認

**コマンド**: `/tdd-red "機能名"`

**チェックポイント**:
- ✅ テストが明確に失敗する
- ✅ エラーメッセージが理解しやすい
- ✅ 期待する動作が明確

### ステップ3: GREEN（最小実装）

**目的**: テストをパスする最小限の実装

**実行内容**:
1. シンプルな実装を作成
2. すべてのテストがパスすることを確認
3. 複雑さを避ける

**コマンド**: `/tdd-green "機能名"`

**チェックポイント**:
- ✅ すべてのテストがパス
- ✅ シンプルで理解しやすい
- ✅ 必要最小限の機能のみ

### ステップ4: REFACTOR（リファクタリング）

**目的**: テストをパスしたままコード品質を向上

**実行内容**:
1. 重複コードの排除（DRY原則）
2. 可読性の改善
3. 小さな関数への分割
4. 各ステップでテストを実行

**コマンド**: `/tdd-refactor "機能名"`

**チェックポイント**:
- ✅ テストが全てパスし続ける
- ✅ コードの意図が明確
- ✅ 関数が単一責任を持つ

### ステップ5: COVERAGE（カバレッジ検証）

**目的**: テストカバレッジの確認と追加テスト判断

**実行内容**:
1. カバレッジ測定（`npm test -- --coverage`）
2. 未カバー箇所の特定
3. 追加テストの必要性判断

**コマンド**: `/tdd-coverage "機能名"`

**チェックポイント**:
- ✅ カバレッジ80%以上（推奨）
- ✅ 重要な分岐がカバーされている
- ✅ エラーハンドリングがテストされている

## NDFプラグインとの連携

### corderエージェント連携
`ndf:corder`がTDDワークフローの各ステップを実装：

**インターフェース定義**:
- 適切な型定義
- ドキュメントコメント
- 例外処理の設計

**RED - テスト作成**:
- テストフレームワークの選定（Jest, Vitest）
- モックの設定
- アサーションの記述

**GREEN - 実装**:
- 最小限の実装
- エラーハンドリング
- 型安全なコード

**REFACTOR - リファクタリング**:
- デザインパターンの適用
- コードの整理
- 保守性の向上

### qaエージェント連携
`ndf:qa`がテスト品質をレビュー：

- テストケースの網羅性チェック
- エッジケースの指摘
- カバレッジ不足箇所の特定

### directorエージェント連携
`ndf:director`がTDDワークフロー全体を調整：

- 複雑な機能を小さなステップに分割
- 各ステップの完了を確認
- 進捗管理

## affaan-mプラグインのHooks連携

TDDワークフロー中、以下のHooksが自動発火：

### PostToolUse Hooks
- **auto-format**: コードとテストを自動フォーマット
- **detect-console-log**: デバッグコードの検出
- **typescript-check**: 型チェック実行

### PreCommit Hooks
- **secret-scan**: シークレット混入チェック
- **coverage-check**: カバレッジ検証

## 使用例

### 例1: 認証機能の実装

```typescript
// ステップ1: インターフェース定義
interface AuthResult {
  success: boolean;
  token?: string;
  error?: string;
}

// ステップ2: RED（失敗テスト）
describe('authenticateUser', () => {
  it('正しい認証情報で成功', async () => {
    const result = await authenticateUser('user@example.com', 'password123');
    expect(result.success).toBe(true);
    expect(result.token).toBeDefined();
  });
});

// ステップ3: GREEN（最小実装）
async function authenticateUser(username: string, password: string): Promise<AuthResult> {
  const user = await findUser(username);
  if (!user) return { success: false, error: 'User not found' };

  const valid = await verifyPassword(password, user.passwordHash);
  if (!valid) return { success: false, error: 'Invalid credentials' };

  const token = generateToken(user.id);
  return { success: true, token };
}

// ステップ4: REFACTOR（リファクタリング）
// - ヘルパー関数に分割
// - エラーハンドリング統一
// - 可読性向上

// ステップ5: COVERAGE（カバレッジ検証）
// カバレッジ: 85% ✅
```

## カバレッジ目標

| 機能カテゴリ | 推奨カバレッジ |
|-------------|---------------|
| セキュリティ機能 | 90%以上 |
| ビジネスロジック | 85%以上 |
| ユーティリティ | 80%以上 |
| UIコンポーネント | 70%以上 |

## ベストプラクティス

### DO（推奨）
- ✅ RED → GREEN → REFACTOR の順序を厳守
- ✅ テストが失敗することを確認してから実装
- ✅ 最小限の実装でテストをパス
- ✅ リファクタリング時はテストをパスし続ける
- ✅ 80%以上のカバレッジを目標

### DON'T（非推奨）
- ❌ 実装してからテストを書く
- ❌ テストを飛ばして次に進む
- ❌ カバレッジを無視する
- ❌ リファクタリングを省略する
- ❌ 過度に複雑な実装を最初から行う

## トラブルシューティング

### テストが失敗し続ける
- インターフェース定義を見直す
- テストケースが正しいか確認
- 実装ロジックをシンプルにする

### カバレッジが目標に達しない
- エッジケースのテストを追加
- エラーハンドリングのテストを追加
- 未カバー箇所を`/tdd-coverage`で特定

### リファクタリング後にテストが壊れる
- 小さな変更に分割する
- 各変更後にテストを実行
- テストケースの見直しも検討

## 関連コマンド

- `/tdd` - TDDワークフロー開始
- `/tdd-red` - REDフェーズ
- `/tdd-green` - GREENフェーズ
- `/tdd-refactor` - REFACTORフェーズ
- `/tdd-coverage` - COVERAGEフェーズ

## 関連ドキュメント

- TDDの詳細は関連コマンドを参照してください（`/tdd`, `/tdd-red`, `/tdd-green`, `/tdd-refactor`, `/tdd-coverage`）

## 参考

- Kent Beck『Test-Driven Development』
- Robert C. Martin『Clean Code』
- [everything-claude-code - TDD workflow](https://github.com/affaan-m/everything-claude-code)
