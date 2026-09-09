# 156-2: 指摘の構造化と独立発見（実装計画）

## 関連リンク

- 設計: `issues/issue-156-design.md`（設計 Pull Request #531 でマージ済み）
- 1 本目: PR #535（却下の記録の器）
- 課題: #156（4 本のうち **2 本目**）

## モード

`standard`。レビュワーが書き出す形と、進行側が取り込む形を変える。

## 目的と非目的

達成したい状態:

- 指摘が根拠と反証条件を持ち、進行側がそれを読める
- 総評だけへ投稿した指摘も、進行側から見える
- 発見を終えるまで、担当が同じラウンドの他の担当の投稿を参照しない

やらないこと:

- **収束の判定の変更** — 3 本目が扱う。`cmd_judge` の判定そのものには触れない
- **反証・実行検証・証拠ベース集約** — 3 本目
- **効果の測定** — 4 本目
- `pr-review/SKILL.md` の変更 — 単独で使うときはその場の既存コメントを見るのが正しい

## 前提

- 前提 1: 4 項目を持たない `payload.json` も受け取る。持たない指摘は区別できる形で記録し、捨てない
- 前提 2: `comments_count` は投稿したインラインの数のままにする（GitHub 側の実数との
  突き合わせに使う）

## 受け入れ条件

| # | 条件 | 検証手段 |
| --- | --- | --- |
| 1 | `payload.json` の各指摘が `evidence` / `falsification` / `suggested_check` / `posted_to` を持てる | `cross-review/tests/` の新しいテスト |
| 2 | 総評だけへ投稿した指摘も `comments[]` に載る | プロンプトの検査 |
| 3 | 取り込んだ指摘が `review_findings[]` へ蓄積される | 取り込みのテスト |
| 4 | `review_findings[]` の要素が `pr` / `round` / `agent` を持つ | 同上 |
| 5 | 4 項目を持たない指摘も記録され、`has_evidence` が偽になる | 同上 |
| 6 | `has_evidence` は `evidence` と `falsification` の両方が空でないときだけ真 | 同上 |
| 7 | 発見を終えるまで参照してよい既存コメントを限る規約がプロンプトにある | プロンプトの検査 |
| 8 | 既存コメントのスナップショットは渡し続ける | 同上 |
| 9 | 旧い状態ファイル（`review_findings` を持たない）を読める | 取り込みのテスト |
| 10 | `comments_count` の意味が変わらない | 既存テストがそのまま通ること |

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `payload.json` の `comments[]` | 4 項目を足し、総評の指摘も載せる | 足りない要素も受け取る |
| `state.json` | `review_findings` を足す | 足すだけ。持たない状態ファイルも読める |
| `result.json` | 変えない | `comments_count` の意味も変えない |
| `pr-review/SKILL.md` | 変えない | — |

## 修正対象

```text
plugins/ndf/skills/cross-review/
├── scripts/launch-reviewer.sh          # プロンプト（payload の形・独立発見の規約）
├── scripts/state.py                    # 取り込み（review_findings）
├── docs/04-contracts.md                # payload と review_findings の契約
├── docs/06-evidence.md                 # 新設。根拠・反証条件の規約
└── tests/test_review_findings.py       # 新設
```

## タスク分解

### Task 1: 指摘を `review_findings[]` へ取り込む

- **対象ファイル:** `scripts/state.py` / `tests/test_review_findings.py`（新設）
- **変更内容:** `cmd_read_result` が `payload.json` を読み、`pr` / `round` / `agent` と
  `has_evidence` を添えて `review_findings[]` へ蓄積する
- **満たす受け入れ条件:** 3 / 4 / 5 / 6 / 9 / 10
- **進め方:** 失敗するテスト → 実装 → 既存テストを通す

### Task 2: プロンプトへ 4 項目と独立発見の規約を書く

- **対象ファイル:** `scripts/launch-reviewer.sh` / `tests/test_review_findings.py`
- **変更内容:** `payload.json` の形へ 4 項目を足し、総評の指摘も載せることを書く。
  発見を終えるまで参照してよい既存コメントを起動時のスナップショットに限る規約を足す
- **満たす受け入れ条件:** 1 / 2 / 7 / 8
- **進め方:** プロンプトの検査 → 記述 → 検査を通す

### Task 3: 契約を書く

- **対象ファイル:** `docs/04-contracts.md` / `docs/06-evidence.md`（新設）
- **変更内容:** `payload.json` と `review_findings[]` の形を契約へ書き、根拠・反証条件の
  規約を新しいファイルへ置く
- **満たす受け入れ条件:** —（1〜8 の裏付け）
- **進め方:** 記述 → 行数の検査

## 影響範囲

| 影響を受けるもの | 内容 |
| --- | --- |
| レビュワーのプロンプト | 求める項目が 4 つ増える |
| 状態ファイル | キーが 1 つ増える |
| 振動の検知 | `_finding_keys` が読む `payload.json` に総評の指摘が入る。**母集合が増える** |

**振動の検知への影響は 3 本目で測る。** この変更では母集合だけが変わり、一致の判定・
層の順序・閾値（0.5）には触れない。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 総評の指摘が入って振動の検知の母集合が増える | 3 本目で測る。この変更では判定式に触れない |
| レビュワーが 4 項目を返さない | 持たない指摘も記録し、`has_evidence` で区別する |
| プロンプトが長くなり、読み飛ばされる | 4 項目は既存の payload の形の記述へ足す。新しい節を増やさない |

## 切り戻し手順

3 つのタスクはそれぞれ独立して戻せる。状態ファイルの移行を伴わない。

## 完了の定義

- [ ] 受け入れ条件 10 件をすべて満たす
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` の終了コードが 0
- [ ] `python3 scripts/check-doc-line-limit.py` の終了コードが 0
