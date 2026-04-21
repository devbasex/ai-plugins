# TDDワークフロー開始コマンド

## コマンド名
`/tdd`

## 説明
Test-Driven Development (TDD) ワークフローを5段階プロセスでガイドします。

## 使用方法

```bash
/tdd [機能名]
```

**例**:
```bash
/tdd "ユーザー認証機能"
```

## TDD 5段階プロセス

このコマンドは以下の5つのステップを順番に実行します：

### ステップ1: インターフェース定義
- 関数シグネチャ、型定義を決定
- 公開APIの設計
- ドキュメント作成

### ステップ2: RED（失敗テスト作成）
- `/tdd-red`で失敗するテストを作成
- テストが失敗することを確認
- エラーメッセージが明確であることを確認

### ステップ3: GREEN（最小実装）
- `/tdd-green`でテストをパスする最小限の実装
- 複雑さを避け、まずは動作することを優先
- すべてのテストがパスすることを確認

### ステップ4: REFACTOR（リファクタリング）
- `/tdd-refactor`でコード品質を向上
- 重複を排除
- 可読性を改善
- テストは常にパスする状態を維持

### ステップ5: COVERAGE（カバレッジ検証）
- `/tdd-coverage`でテストカバレッジを確認
- 80%以上を目標（推奨値）
- 未カバー箇所を特定して追加テストを検討

## 実行フロー

```
┌──────────────────────┐
│ 1. インターフェース定義 │
└──────┬───────────────┘
       │
       v
┌──────────────────────┐
│ 2. RED: 失敗テスト    │ ← /tdd-red
└──────┬───────────────┘
       │
       v
┌──────────────────────┐
│ 3. GREEN: 最小実装    │ ← /tdd-green
└──────┬───────────────┘
       │
       v
┌──────────────────────┐
│ 4. REFACTOR: 品質向上 │ ← /tdd-refactor
└──────┬───────────────┘
       │
       v
┌──────────────────────┐
│ 5. COVERAGE: 検証     │ ← /tdd-coverage
└──────────────────────┘
```

## 出力例

```
🔴🟢🔧 TDDワークフローを開始します: "ユーザー認証機能"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【ステップ1: インターフェース定義】
以下のAPIを定義します：

function authenticateUser(username: string, password: string): Promise<AuthResult>
function logoutUser(sessionId: string): Promise<void>
function refreshToken(refreshToken: string): Promise<string>

【ステップ2: RED - 失敗テスト作成】
次のコマンドを実行します:
  /tdd-red "ユーザー認証機能"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## NDFプラグインとの連携

このコマンドは`ndf:corder`エージェントと連携して動作します：

### コーディング実装
```bash
# TDDワークフロー開始
/tdd "認証機能"

# ndf:corderエージェントが自動的に起動
# またはdirectorエージェントが調整
```

### 品質保証
- `ndf:qa`エージェントがセキュリティレビューを実施
- affaan-mプラグインのHooksが自動チェック

## affaan-mプラグインのHooks連携

TDDワークフロー中、以下のHooksが自動的に発火します：

### PostToolUse Hooks
- **auto-format**: コード自動フォーマット（Prettier/ESLint）
- **detect-console-log**: console.log検出警告
- **typescript-check**: TypeScript型チェック

### PreCommit Hooks
- **secret-scan**: シークレット混入チェック
- **coverage-check**: テストカバレッジ検証（80%以上）

## 各ステップの詳細コマンド

- `/tdd-red` - REDフェーズ（失敗テスト作成）
- `/tdd-green` - GREENフェーズ（最小実装）
- `/tdd-refactor` - REFACTORフェーズ（リファクタリング）
- `/tdd-coverage` - COVERAGEフェーズ（カバレッジ検証）

## カバレッジ目標

### 推奨カバレッジ
- **一般機能**: 80%以上
- **セキュリティ機能**: 90%以上
- **ビジネスロジック**: 85%以上

### 調整方法
`plugin.json`の`config.tdd.coverageThreshold`で変更可能：

```json
{
  "config": {
    "tdd": {
      "coverageThreshold": 80
    }
  }
}
```

## ベストプラクティス

### TDD原則を守る
- ✅ RED → GREEN → REFACTOR の順序を厳守
- ✅ テストが失敗することを確認してから実装
- ✅ 最小限の実装でテストをパス
- ✅ リファクタリング時はテストをパスし続ける

### 避けるべきパターン
- ❌ 実装してからテストを書く
- ❌ テストを飛ばして次に進む
- ❌ カバレッジを無視する
- ❌ リファクタリングを省略する

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

## 関連ドキュメント

- [TDDワークフロースキル](../skills/tdd-workflow/SKILL.md)
- [corderエージェント連携](../../ndf/agents/corder.md)

## 参考

TDDの詳細については以下を参照：
- Kent Beck『Test-Driven Development』
- [everything-claude-code - TDD workflow](https://github.com/affaan-m/everything-claude-code)
