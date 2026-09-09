# 156-1: 却下の記録の器（実装計画）

## 関連リンク

- 要求と受け入れ条件: `issues/issue-156-evidence-based-review.md`
- 設計: `issues/issue-156-design.md`（設計 Pull Request #531 でマージ済み）
- 課題: #156（4 本のうち **1 本目**）

## モード

`standard`。`state.py` の保存する形を変え、`fix` が返す約束を広げる。

## 目的と非目的

達成したい状態:

- 却下した指摘が、位置・重要度・理由とともにラウンドをまたいで読める

やらないこと:

- **指摘の構造化（根拠・反証条件）** — 2 本目が扱う
- **収束の判定の変更** — 3 本目が扱う。この変更では `cmd_judge` に触れない
- 却下の記録を次のラウンドのレビュワーへ渡すこと — 2 本目が扱う（渡す先はレビュープロンプト）

## 前提

- 前提 1: 既存の状態ファイル（`rejected_findings` を持たない）を読めることを保つ
- 前提 2: `rounds[].fix.rejected` の件数は残す。報告が読む値である

## 受け入れ条件

| # | 条件 | 検証手段 |
| --- | --- | --- |
| 1 | 却下した指摘が位置・重要度・理由とともに状態ファイルへ残る | `cross-review/tests/` の新しいテスト |
| 2 | 却下の記録がラウンドをまたいで蓄積される | 同上。2 ラウンド分を積んで読み出す |
| 3 | `deferred_nits` と同じ形で読める | 同上。両者の要素の形を突き合わせる |
| 4 | 旧い状態ファイル（`rejected_findings` を持たない）を読める | 同上 |
| 5 | `rounds[].fix.rejected` の件数が変わらない | 既存テストがそのまま通ること |
| 6 | `fix` の `rejected[]` が 6 項目を持つ | 手順書の形の検査 |

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `state.json` | `rejected_findings` を足す | 足すだけ。持たない状態ファイルも読める |
| `rounds[].fix.rejected` | 変えない | 件数のまま |
| `fix` の戻り値の `rejected[]` | 3 項目を足す | 足りない要素も受け取る（記録は落とさない） |

## 修正対象

```text
plugins/ndf/skills/cross-review/
├── scripts/state.py                  # 初期化・蓄積・報告
├── docs/04-contracts.md              # rejected_findings の形
└── tests/test_rejected_findings.py   # 新設
plugins/ndf/skills/fix/SKILL.md       # rejected[] の 6 項目
```

## タスク分解

### Task 1: 却下の記録を per-item で残す

- **対象ファイル:** `scripts/state.py` / `tests/test_rejected_findings.py`（新設）
- **変更内容:** `_init_state` へ `rejected_findings: []` を足し、`cmd_merge_fix` が
  `deferred_nits` と同じ形で蓄積する。`rounds[].fix.rejected` の件数は変えない
- **満たす受け入れ条件:** 1 / 2 / 3 / 4 / 5
- **進め方:** 失敗するテスト → 実装 → 既存テストを通す

### Task 2: `rejected[]` の形と契約を書く

- **対象ファイル:** `plugins/ndf/skills/fix/SKILL.md` / `cross-review/docs/04-contracts.md`
- **変更内容:** `rejected[]` の例へ `path` / `line` / `severity` を足し、`rejected_findings` の
  形を契約へ書く
- **満たす受け入れ条件:** 6
- **進め方:** 手順書の形を検査するテスト → 記述 → 検査を通す

### Task 3: 報告へ却下の記録を出す

- **対象ファイル:** `scripts/state.py` の `cmd_report`
- **変更内容:** `残 deferred nit` と同じ形で、却下した指摘の一覧を出す
- **満たす受け入れ条件:** 2（読めることの確認）
- **進め方:** 出力を見るテスト → 実装

## 影響範囲

| 影響を受けるもの | 内容 |
| --- | --- |
| `cross-review` の状態ファイル | キーが 1 つ増える |
| `fix` のサブエージェント | 返す項目が 3 つ増える |
| ラウンドの報告 | 却下の一覧が増える |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `fix` が返す形が LLM の出力に依存し、項目が欠けることがある | `deferred` と同じ正規化を通す。欠けた要素も落とさず記録する |
| 状態ファイルのキーが増えて既存の読み出しが壊れる | 旧い状態ファイルを読むテストで固定する |

**「先に構造を整える」は選ばない。** 触るのは `cmd_merge_fix` の 1 関数と初期化の 1 行で、
`deferred_nits` の実装がそのまま手本になる。

## 切り戻し手順

1 コミットで戻せる。状態ファイルの移行を伴わない（キーが無ければ空として扱う）。

## 確定仕様化の時期

**この 1 本目では `plan-to-spec` を通さない。** #156 は 4 本に分かれており、器だけを仕様へ
移すと、記録の形の説明と、それを読む側の説明が別のファイルに分かれる。**4 本目が入った
時点で 1 本の確定仕様へまとめる。**

計画ファイル（この文書）は 4 本目まで `issues/` に残す。

## 完了の定義

- [ ] 受け入れ条件 6 件をすべて満たす
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` の終了コードが 0
- [ ] `python3 scripts/check-doc-line-limit.py` の終了コードが 0
