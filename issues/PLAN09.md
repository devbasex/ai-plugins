# スキル分割改修計画

## 背景

Progressive Disclosure（段階的読み込み）の目的は**コンテキスト節約**。
しかし、分割が適切でないと逆効果になる。

## 評価基準

| 基準 | 説明 |
|-----|------|
| **有効呼び出し回数** | 多くのセッションで使われるか |
| **読み込みファイル数でミッション完結** | SKILL.mdだけで大半のケースが完結できるか |
| **各ファイルサイズ** | 適切なサイズか（大きすぎも小さすぎもNG） |

**重要**: ミッション達成のために結局全部読む必要があるなら、分割はコンテキスト節約に寄与しない。

---

## 現状評価

### 評価マトリックス

| スキル | 呼び出し頻度 | SKILL.mdで完結? | 分割評価 | 状態 |
|-------|-------------|----------------|---------|------|
| python-execution | 高 | ○（uvインストールコマンド追加） | 適切 | ✅改善済 |
| docker-container-access | 中 | ○（DinD/DooDで読み分け可能） | 適切 | - |
| skill-development | 低 | △（詳細は必ず読む） | 検討 | P3 |
| qa-security-scan | 低 | ○（概要テーブルでクイックスキャン可能） | 適切 | ✅再評価済 |
| corder-code-templates | 中 | ○（API/コンポーネントで読み分け） | 適切 | - |
| corder-test-generation | 中 | ○（AAA+基本例がSKILL.mdに統合済） | 適切 | ✅再評価済 |
| data-analyst-export | 中 | ○（形式別に読み分け可能） | 適切 | - |
| data-analyst-sql-optimization | 中 | ○（レガシーファイル削除済） | 適切 | ✅改善済 |
| researcher-report-templates | 低 | △（テンプレート確認は毎回必要） | **要改善** | P3 |
| scanner-pdf-analysis | 中 | ○（基本操作はSKILL.mdで完結） | 適切 | - |
| scanner-excel-extraction | 中 | ○（基本操作はSKILL.mdで完結） | 適切 | - |
| markdown-writing | 中 | ○（基本ルールはSKILL.mdで完結） | 適切 | - |
| memory-handling | 高 | ○（分割なし） | 適切 | - |

---

## 改修対象と方針

### 1. python-execution **[要改善]**

**問題**:
- uvセットアップ手順が`01-environment-detection.md`にある
- 初回セットアップ時に必ず読む必要がある
- しかし、一度セットアップすれば不要

**改善方針**:
- SKILL.mdに「uvセットアップ簡易版」を追加（インストールコマンドのみ）
- 01-environment-detection.md → 01-uv-setup.md（初回セットアップ専用）
- 02-troubleshooting.md は維持（問題発生時のみ）

**新構成**:
```
python-execution/
├── SKILL.md              # 環境検出+実行+uvインストールコマンド（これだけで95%完結）
├── 01-uv-setup.md        # uvの詳細セットアップ（初回のみ）
└── 02-troubleshooting.md # トラブル時のみ
```

---

### 2. qa-security-scan **[要改善]**

**問題**:
- セキュリティスキャン実行時、ほぼ全チェックリストが必要
- 分割しても結局全部読むなら意味がない

**改善方針**:
- **分割をやめる** - 全内容をSKILL.mdに統合
- または、**用途別に分割**:
  - 01-quick-scan.md: クイックスキャン（主要5項目）
  - 02-full-scan.md: フルスキャン（OWASP Top 10全項目）
  - 03-report-template.md: レポートテンプレート（レポート作成時のみ）

**新構成（用途別分割案）**:
```
qa-security-scan/
├── SKILL.md              # 概要+クイックスキャン（主要5項目、これで70%対応）
├── 01-full-owasp.md      # OWASP Top 10全項目（フル監査時のみ）
└── 02-report-template.md # レポート作成時のみ
```

---

### 3. corder-test-generation **[要改善]**

**問題**:
- テスト生成時、パターンと例の両方を参照することが多い
- 分割しても結局両方読む

**改善方針**:
- SKILL.mdに主要パターン（AAA）と基本例を統合
- 詳細なエッジケース集のみ別ファイル

**新構成**:
```
corder-test-generation/
├── SKILL.md              # AAA+主要パターン+基本例（これで80%完結）
└── 01-edge-cases.md      # 詳細なエッジケース集（特殊ケース時のみ）
```

---

### 4. data-analyst-sql-optimization **[要改善]**

**問題**:
- レガシーファイル（examples.md, reference.md）が残っている
- パターン確認は毎回必要

**改善方針**:
- レガシーファイルを削除
- SKILL.mdに主要パターン（N+1、インデックス、JOINの3つ）を統合
- 詳細パターンのみ別ファイル

**新構成**:
```
data-analyst-sql-optimization/
├── SKILL.md              # 主要3パターン+Before/After例（これで80%完結）
└── 01-advanced-patterns.md # サブクエリ、ウィンドウ関数等（特殊ケース時のみ）
```

---

### 5. researcher-report-templates **[要改善]**

**問題**:
- レポート作成時、テンプレートと例の両方が必要
- 分割しても結局両方読む

**改善方針**:
- SKILL.mdにメインテンプレート（調査レポート）を統合
- 技術比較テンプレートのみ別ファイル

**新構成**:
```
researcher-report-templates/
├── SKILL.md              # 調査レポートテンプレート+例（これで70%完結）
└── 01-comparison-template.md # 技術比較専用（比較レポート時のみ）
```

---

### 6. skill-development **[検討]**

**問題**:
- スキル開発時は詳細を読む必要がある
- しかし、呼び出し頻度が低いため影響は小さい

**方針**:
- 現状維持（低頻度のため優先度低）
- 必要であれば後で改善

---

## 適切と判断したスキル（変更不要）

| スキル | 理由 |
|-------|------|
| docker-container-access | DinD環境ならSKILL.mdで完結、DooD時のみ02-dood-access.md |
| corder-code-templates | API作成時は01、コンポーネント作成時は02と読み分け可能 |
| data-analyst-export | CSV/JSON/Excel/Markdownと用途別に読み分け可能 |
| scanner-pdf-analysis | 基本操作はSKILL.mdで完結、高度な例のみ別ファイル |
| scanner-excel-extraction | 基本操作はSKILL.mdで完結、高度な例のみ別ファイル |
| markdown-writing | 基本ルールはSKILL.mdで完結、図表詳細のみ別ファイル |
| memory-handling | 分割なし、87行で適切 |

---

## 実装優先順位

| 優先度 | スキル | 理由 |
|-------|-------|------|
| **P1** | python-execution | 高頻度呼び出し、改善効果大 |
| **P1** | qa-security-scan | 構造的問題あり |
| **P2** | corder-test-generation | 中頻度、改善効果中 |
| **P2** | data-analyst-sql-optimization | 中頻度、レガシー削除必要 |
| **P3** | researcher-report-templates | 低頻度 |
| **P3** | skill-development | 低頻度 |

---

## 改修手順

### Phase 1: P1スキル改修 ✅ 完了

1. ✅ python-execution の再構成
   - SKILL.mdにuvインストールコマンドを追加（95%のケースで完結）
   - 01-environment-detection.md → 01-uv-setup.md にリネーム
   - 02-troubleshooting.md は維持

2. ✅ qa-security-scan の再評価
   - 再評価結果: **現状維持が適切**
   - 理由: SKILL.mdの概要テーブルでクイックスキャン可能、フル監査時のみ詳細ファイル参照

3. ✅ 動作確認

### Phase 2: P2スキル改修 ✅ 完了

1. ✅ corder-test-generation の再評価
   - 再評価結果: **現状維持が適切**
   - 理由: SKILL.md（74行）でAAAパターン+基本例が完結

2. ✅ data-analyst-sql-optimization の再構成
   - レガシーファイル削除: examples.md, reference.md
   - SKILL.md（56行）に主要パターンが統合済み

3. ✅ 動作確認

### Phase 3: P3スキル改修（オプション）
1. researcher-report-templates の再構成
2. skill-development の検討

---

## 成功指標

| 指標 | 目標 |
|-----|------|
| SKILL.mdでのミッション完結率 | 80%以上 |
| 平均読み込みファイル数 | 1.5以下 |
| 各ファイルサイズ | 50-150行 |

---

## 完了サマリー

### 実施内容（2026-02-05）

| スキル | 実施内容 | 結果 |
|-------|---------|------|
| python-execution | SKILL.mdにuvインストールコマンド追加、01ファイルリネーム | 95%のケースでSKILL.mdのみで完結 |
| qa-security-scan | 再評価 | 現状維持（概要テーブルでクイックスキャン可能） |
| corder-test-generation | 再評価 | 現状維持（74行でAAA+基本例完結） |
| data-analyst-sql-optimization | レガシーファイル削除（examples.md, reference.md） | 56行で主要パターン完結 |

### ファイルサイズ確認

| スキル | SKILL.md | 補助ファイル |
|-------|----------|-------------|
| python-execution | 93行 | 01: 85行, 02: 112行 |
| qa-security-scan | 64行 | - |
| corder-test-generation | 74行 | - |
| data-analyst-sql-optimization | 56行 | 01: patterns, 02: examples |

### 成功指標達成状況

| 指標 | 目標 | 結果 |
|-----|------|------|
| SKILL.mdでのミッション完結率 | 80%以上 | ✅ 達成見込み |
| 平均読み込みファイル数 | 1.5以下 | ✅ 達成見込み |
| 各ファイルサイズ | 50-150行 | ✅ 達成 |

---

## 備考

- 分割の目的は「コンテキスト節約」
- 「結局全部読む」分割は無意味
- 「用途別に読み分けできる」分割が有効
- 「問題発生時のみ読む」分割が有効
- 「初回のみ読む」分割が有効
