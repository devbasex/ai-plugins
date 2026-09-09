# #513 / #512: 体裁設計と体裁レビュー、進行の記録の締め

## 関連リンク

- 課題: #513（体裁設計を `design` へ足し、体裁レビューを新設する）/
  #512（進行の記録を `documentation` モードへ広げる）
- 親: #506 / マイルストーン 10「ビジネス文書ワークフロー」
- 設計: [milestone-10-design.md](milestone-10-design.md) /
  [milestone-10-design-decisions.md](milestone-10-design-decisions.md)
- 前提となる実装: #517（工程表）/ #519（システムと形）/ 実装 3（執筆と素材）

## モード

`standard`。Skill を 1 個新設し、`design` へ参照を 4 本足す。配布数の締めもここで行う。

## 目的と非目的

達成したい状態:

- 体裁設計が `design` の参照として入り、**新しい Skill を作らない**
- 体裁レビューが**描画した結果を見る**手順を持つ
- **新設 4 個がすべて manifest に載り**、配布数を持つ箇所がすべて揃う
- `PJ_STAGES` / `PJ_MODES` と 4 箇所の並びが一致している（#517 で入れた分の確認）

やらないこと:

- **`01-diagram-guide.md` の記述を移すこと。** 根拠が違うため軸を足すにとどめる（決定 32）
- 描画の手段の実装。**手段は `document-systems` が持つ**
- 内容のレビュー（実装レビューと事実確認が持つ）
- 版を上げること

## 前提

- 前提 1: **新設は `layout-review` の 1 個。** 体裁設計は `design` の参照である（決定 28）
- 前提 2: **画像を読めるランタイムを実測していない。** 4 つとも配り、読めないときは止める
  （決定 29 / 33）
- 前提 3: 配布数はこの実装で **45 / 44 / 43 / 43**（claude / kiro / codex / agy）になる。
  実装 2（+1）と実装 3（+2）がマージされた後に起点を取り直す
- 前提 4: 盤面（GitHub Projects）の単一選択へ新しい 2 工程と `documentation` を足す作業は、
  **リポジトリの変更では完結しない**。完了報告に残す

## 受け入れ条件

- [ ] 条件 1: 体裁設計が `design/references/layout-<出力の形>.md` にあり、**新しい Skill を
      作っていない**
- [ ] 条件 2: 体裁設計の産物がソースの Markdown に埋め込まれておらず、生成へ渡す入力になっている
- [ ] 条件 3: 特定の変換器を前提にしていない
- [ ] 条件 4: `layout-review` が工程表の行として `配布` の後にある（#517 で入っている）
- [ ] 条件 5: 体裁レビューが**描画した結果を見る**手順を持ち、描画の手段が出力の形ごとに
      書かれている
- [ ] 条件 6: 描画できない・画像を読めないときの倒し方が決まっている
- [ ] 条件 7: 機械で見る項目と人が見る項目の振り分けが表で示されている
- [ ] 条件 8: `01-diagram-guide.md` の記述を移していない
- [ ] 条件 9: `PJ_STAGES` が 18 値、`PJ_MODES` が 5 値で、4 箇所の並びが一致する
- [ ] 条件 10: **新設 4 個がすべて 4 つの manifest に載る**
- [ ] 条件 11: `README.md` の公開 Skill 数とカテゴリ内訳が更新される
- [ ] 条件 12: `check-skill-frontmatter.py` / `check-skill-repo-assumptions.py` /
      `check-cross-skill-refs.py` / `check-doc-staleness.py` / `validate-runtime-plugins.sh` が通る
- [ ] 条件 13: すべての `SKILL.md` が 500 行以下
- [ ] 条件 14: 既存のテストが 1 件も壊れていない

## 修正対象

```text
plugins/ndf/skills/layout-review/SKILL.md                       （新規）
plugins/ndf/skills/design/SKILL.md
plugins/ndf/skills/design/references/layout-slide.md            （新規）
plugins/ndf/skills/design/references/layout-document.md         （新規）
plugins/ndf/skills/design/references/layout-spreadsheet.md      （新規）
plugins/ndf/skills/design/references/layout-page.md             （新規）
plugins/ndf/manifests/{claude,kiro,codex,agy}-skills.txt
README.md / plugins/ndf/README.md / plugin.json 3 本 / marketplace.json
scripts/tests/test_agy_distribution.py
```

## タスク分解

### Task 1: 体裁設計を `design` の参照として足す

- **対象ファイル:** `design/SKILL.md` / `references/layout-*.md` 4 本
- **変更内容:** 触る領域の表へ 1 行。出力の形が読ませる参照を決める形にする
- **満たす受け入れ条件:** 1 / 2 / 3 / 8
- **進め方:** 文書のみ

### Task 2: `layout-review` を新設する

- **対象ファイル:** `layout-review/SKILL.md`
- **変更内容:** 描画して見る手順、機械と人の振り分け、描画できないときの倒し方、
  差し戻し先、見た結果の残し方
- **満たす受け入れ条件:** 4 / 5 / 6 / 7
- **進め方:** 文書のみ

### Task 3: 配布数の締め

- **対象ファイル:** `manifests/*` / `README.md` / `plugins/ndf/README.md` /
  `plugin.json` 3 本 / `marketplace.json` / `test_agy_distribution.py`
- **変更内容:** 新設 4 個がすべて載る状態にし、数を持つ箇所をすべて更新する
- **満たす受け入れ条件:** 9 / 10 / 11 / 12 / 13 / 14
- **進め方:** 検査が落ちることを先に確かめてから直す

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 実装 2 / 3 のマージ前に数を締めると合わない | **両方がマージされてから起点を取り直す** |
| `layout-*.md` が `01-diagram-guide.md` と重なる | **根拠が違うことを本文へ書き、形式を列挙しない** |
| 画像を読めない環境で工程が進まない | 決定 29 のとおり止め、人が見る経路を残す |

## 完了の定義

- [ ] 受け入れ条件 14 件すべてに検証手段と結果が対応している
- [ ] `standard` の必須の段（1 → 2 → 3 → 4）を通す
