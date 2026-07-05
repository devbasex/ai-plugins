# 03. テスト技法ライブラリ

ISTQB CTFL 4.2 のブラックボックス技法を中心に、Web E2E で実用される技法を整理する。
各技法に **(1) 定義 (2) 適用例 (3) 適用すべき page role (4) 限界** を記す。

テストケース YAML には **使用した技法名を必ず記録** すること:

```yaml
- name: パスワード境界テスト
  technique: BVA
  oracle: Claims              # FEW HICCUPPS のどの軸
  inputs: [7, 8, 64, 65]
  expect_behavior: ...
```

## 1. Equivalence Partitioning (EP) — 同値分割

### 定義 (ISTQB CTFL 4.2.1)
入力ドメインを「同じ処理が期待される」分割に分け、各分割から代表値 1 つでテストする。
不具合は「分割を取り違えた処理」に起因することが多いという前提。

### 適用例
- 年齢: `<0` / `0–17` / `18–64` / `65–120` / `>120` → 5 分割 → 各 1 テスト
- 入力欄の文字種: `空` / `半角英数` / `全角` / `絵文字` / `制御文字` / `BiDi`

### 適用 role
全 role。特に `edit`, `form`, `search`.

### 限界
- 分割の境界は別途 BVA で補完が必要
- 順序の無いカテゴリ (色 / 言語) は EP のみ

---

## 2. Boundary Value Analysis (BVA) — 境界値分析

### 定義 (ISTQB CTFL 4.2.1)
順序のある分割の境界値そのもの、境界 ± 1 をテストする。
「不具合は境界に集まる」という経験的事実を踏まえる。

### 適用例
- パスワード長 (仕様 8〜64): `7 / 8 / 9 / 63 / 64 / 65`
- ページ番号 (1 始まり): `0 / 1 / 2 / last-1 / last / last+1 / -1 / 'abc'`
- 金額 (0 円許容?): `-1 / 0 / 1 / max-1 / max / max+1`

### 適用 role
`edit`, `form`, `list` (ページネーション), `cart` (金額境界), `search` (件数 0/1/many).

### 限界
- 順序の無いカテゴリには無効
- 境界仕様が曖昧な場合は **どこを境界としたか** を bug report に明記

---

## 3. Decision Table — 判定表

### 定義 (ISTQB CTFL 4.2.2)
入力条件と期待結果の組合せを表で網羅する。

```
| 国 | 会員ランク | クーポン | 期待送料 |
|----|----------|---------|---------|
| JP | Free     | なし    | 500 円  |
| JP | Free     | あり    | 0 円    |
| JP | Pro      | なし    | 0 円    |
| US | Free     | なし    | 1500 円 |
| US | Pro      | なし    | 0 円    |
```

ルール ≤ 6 個の条件で網羅。> 6 個なら Classification Tree + Pairwise で削減。

### 適用例
- 認証: (auth_present, csrf_token, captcha) × 期待 status code
- 配送料計算: (国, 会員, クーポン, 重量) × 配送料

### 適用 role
**`form`, `auth`, `cart`/`checkout` で必須**。
Edit / Search でも入力分岐があれば適用。

### 限界
- 条件 6 個で `2^6 = 64` 行になり管理不能 → 上位概念で集約 (Classification Tree)
- 条件の独立性が前提 (相互作用は別テスト)

---

## 4. State Transition Testing — 状態遷移

### 定義 (ISTQB CTFL 4.2.2)
状態と遷移を表/図で定義し、有効遷移と無効遷移を網羅。
0-switch (1 遷移) / 1-switch (2 連続遷移) でカバレッジを階層化。

### 適用例
注文の状態遷移:

```
[draft] ─submit→ [submitted] ─pay→ [paid] ─ship→ [shipped] ─deliver→ [delivered]
   │                              │
   └──cancel────────────────────[cancelled]
```

無効遷移: `delivered → cancel`, `submitted → ship`, etc.

### 適用 role
`cart`/`checkout` (cart→checkout→paid→fulfilled), `auth` (logged_out→logging_in→logged_in→locked), `form` (step1→step2→...→complete), `edit` (clean→dirty→saving→saved/error).

### 限界
- 状態爆発時 (>20 状態) に階層化が必要
- 並行状態 (multi-tab) は別モデル

---

## 5. Use Case Testing — ユースケース

### 定義 (ISTQB CTFL 4.2.2)
アクター × ゴールから主流れと例外流れを抽出してシナリオ化。

### 適用例
「ユーザが商品を返品する」:
- 主流れ: 注文一覧 → 該当注文 → 返品申請 → 理由選択 → 返品ラベル DL → 完了
- 例外: 期限超過 / 配送中 / 既返品済み / 一部返品

### 適用 role
全 role の **シナリオ束ね** に有効。本 Skill では「1 testcase = 1 use case scenario」。

### 限界
- UI 詳細はカバーしない (BVA / EP で補強)

---

## 6. Pairwise / All-Pairs Testing

### 定義 (ISTQB CTFL 4.2.3)
多次元組合せを「全 2 因子組合せ」に絞る。
**不具合の 70%以上が 2 因子相互作用**という経験則 (Kuhn et al. 2004) に基づく。

### 適用例
OS (3) × Browser (4) × 言語 (5) × 端末タイプ (3) = 全 180 通り → All-Pairs で ~20 通り.
ツール: PICT (Microsoft), allpairs.py (Python).

### 適用 role
`form` (国 × 配送 × 支払い × 法人/個人), `dashboard` (期間 × dimension × フィルタ), `search` (ファセット組合せ), `list` (ソート × フィルタ × ページ).

### 限界
- 3 因子以上の交互作用は見逃す
- ツール依存 (手作業では困難)

---

## 7. Classification Tree Method (CTM)

### 定義 (Grimm/Grochtmann 1993)
入力因子と値クラスを階層的なツリーに整理し、葉ノードを Pairwise で組合せ生成。

### 適用例
```
申込フォーム
├── 顧客種別: [個人, 法人]
├── 国: [JP, US, EU, 他]
├── 支払い: [カード, 銀振, PayPal]
└── 配送: [標準, 速達, 店舗受取]
```
→ 葉 4 因子の All-Pairs で ~20 ケース生成。

### 適用 role
`form` (複数ステップ), `cart`/`checkout` (オプション組合せ).

---

## 8. Domain Testing (HTSM)

### 定義 (Kaner / Bach)
入力/出力データを系統的に分割し、典型値・境界値・無効値を選ぶ。
EP/BVA を Web の文字列・日付・URL・ファイル等に拡張した実用版。

### 標準分割セット
| データ型 | 分割 |
|---|---|
| 整数 | min-1, min, min+1, 0, 1, -1, mid, max-1, max, max+1, NaN, ∞ |
| 文字列 | 空, 1 文字, 短, 平均, 長, max, max+1, 半角, 全角, 絵文字, BiDi, 制御文字, ヌル文字 |
| 日付 | 過去 / 現在 / 未来 / 閏年 2/29 / DST 切替 / 年末年始 / TZ 境界 / RFC 3339 違反 |
| URL | http/https/file/ftp / 異なるドメイン / クエリ / フラグメント / IDN / IPv6 / open redirect |
| ファイル | 0 byte / 拡張子のみ / 拡張子偽装 / 大ファイル / Unicode 名 / 同名重複 |

### 適用 role
`edit`, `form`, `search`, `list`, `cart` の数値/金額.

---

## 9. その他 HTSM / 経験的技法

### Stress Testing
過負荷・低リソース・大量データで応答を観察。
Playwright での近似: `route` で全リクエストに 300ms delay を注入、`context.set_offline(True)` で回線切断、データ生成スクリプトで 10000 件投入。

### Flow Testing
リセットせず連続操作。副作用を引き出す。
例: カートに追加 50 連発 → 二重登録 / 在庫不整合.

### Scenario Testing (HTSM)
「重要人物が重要な事をする物語」を実行。
本 Skill の pytest テスト関数 1 件 = 1 シナリオ.

### Claims Testing (HTSM)
仕様 / 広告 / SLA の主張を逐一検証。
例:「3秒以内に表示」「99.9% uptime」「Drag & Drop に対応」.

### Risk Testing (HTSM)
想定欠陥を仮説立てし、それを暴く試験を設計。
例: 「他人の注文を IDOR で見れるはずだ」→ 別ユーザの id で URL を踏む.

### User Testing (HTSM)
ペルソナごとの利用シナリオを実行。スクリーンリーダー利用者 / 高齢者 / 非ネイティブ言語話者.

### Automatic Checking
機械的に oracle で照合できる部分を網羅 (axe-core / visual diff / API レスポンス検査).

---

## 10. Hendrickson Test Heuristics (20 guideword)

| guideword | 説明 |
|---|---|
| Boundaries | 境界値 (BVA) |
| Goldilocks | 短すぎ・長すぎ・適切 |
| CRUD | Create / Read / Update / Delete を全網羅 |
| Position | 並び順の最初/最後/中間 |
| Selection | 選択 0/1/全/不正 |
| **Count** | 0 件 / 1 件 / 多数 |
| Multi-user | 同時編集 / 競合 |
| Flood | 連打 / 大量データ |
| Sequences | 順序を入替えて副作用を引き出す |
| Sorting | 昇順 / 降順 / null 含む / i18n 並び |
| **Interruptions** | ネットワーク切断 / タブ閉じる / セッション切れ |
| Constraints | 必須 / 一意 / 関係 / FK |
| Input Method | キーボード / マウス / タッチ / 音声 / コピペ |
| Configurations | OS / ブラウザ / viewport |
| Starvation | 低メモリ / 低帯域 / バッテリー低下 |
| Dependencies | 上位/下位データ / 第三者 API |
| Touch Points | 通知 / メール / SMS への副作用 |
| Variable Analysis | 変数の生死範囲 / scope |
| **State** | 状態遷移とその境界 |
| Map Making | テスト対象の地図を描く (探索) |

太字は Web E2E で見落としやすい必須項目。

## 11. 「揺らぎ排除」のための技法選択ルール

AI / 人間が「思いつき」でテストを書かないように、**page role × データ型 → 必須技法** を以下に固定する。

```
入力: page_role, data_type
出力: 必須技法のリスト

(role, data) → techniques
─────────────────────────────────────────
(*, 数値)              → BVA + EP
(*, 文字列)            → BVA (長さ) + Domain (文字種)
(*, 日付)              → Domain (日付セット)
(*, URL/path)          → Domain (URL 種別) + Risk (open redirect)
(*, ファイル)          → Domain (ファイル種別)
(form, *)              → Decision Table 必須
(checkout, 金額)       → BVA + Decision Table (税/送料/クーポン)
(list, *)              → EP (Count: 0/1/Many)
(search, クエリ)        → Domain (clean/inj) + Claims (relevance)
(auth, 認証情報)        → Decision Table + Risk (列挙/ロック)
(*, 状態を持つ操作)     → State Transition
(*, 多次元組合せ)       → Pairwise (All-Pairs)
(*, 仕様で主張あり)     → Claims Testing
(*, IDOR/CSRF/XSS リスク) → Risk Testing
```

このマッピングは利用者が pytest テストを書く際の指針として使う。
``@pytest.mark.parametrize`` で各境界値 / 各 row を test 関数として展開し、
4 軸以上は事前に Pairwise で削減してから ``parametrize`` する運用を推奨する。

## 参考文献

- ISTQB CTFL Syllabus 4.2, "Black-box Test Techniques"
- Glenford Myers, "The Art of Software Testing" 3rd ed.
- Cem Kaner, "Domain Testing Workbook"
- Grimm/Grochtmann (1993), "Classification Trees for Partition Testing"
- Kuhn et al. (2004), "Software Fault Interactions and Implications for Software Testing", IEEE TSE
- Hendrickson/Lyndsay/Emery, "Test Heuristics Cheat Sheet", https://www.ministryoftesting.com/articles/test-heuristics-cheat-sheet
- James Bach, HTSM v6.3
