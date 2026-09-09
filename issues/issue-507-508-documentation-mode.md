# #507 / #508: documentation モードと承認の写像

## 関連リンク

- 課題: #507（`development-workflow` に `documentation` モードを足す）/ #508（承認の 2 つの関門を文書の工程へ写像する）
- 親: #506 / マイルストーン 10「ビジネス文書ワークフロー」
- 要求: [milestone-10-documentation-workflow.md](milestone-10-documentation-workflow.md)
- 設計: [milestone-10-design.md](milestone-10-design.md) / [milestone-10-design-decisions.md](milestone-10-design-decisions.md)

## モード

`standard`。公開インタフェース（モードの値・工程の値・Skill の参照）を追加し、
`development-workflow` と `projects-common.sh` の 2 モジュールにまたがる。

## 目的と非目的

達成したい状態:

- `development-workflow` が 5 つのモードを判定し、工程表が 18 行 × 5 列になる
- 工程名の並びを持つ 4 箇所が揃い、新しい 2 工程を盤面へ記録できる
- 承認の関門が 2 つのまま、文書の工程へ写像された形で読める
- 文書の提出先の宣言（`.ndf/document.json`）の形が決まり、宣言が無ければ何も起きない

やらないこと:

- **Skill の新設**（`document-sources` / `document-drafting` / `layout-review` /
  `document-systems`）。実装 2 〜 4 が行う
- **`design` / `release` / `quality-gates` / `requirements-design` / `cross-review` への追加**。
  実装 2 〜 4 が行う
- **`.ndf/document.json` を読む実装**。宣言の形だけを定める。読むのは `release` と
  `document-systems` で、実装 2 が書く
- **配布 manifest と `README.md` の数の更新**。新設 Skill が無いため動かない
- **版を上げること**

## 前提

- 前提 1: `WF_MODE_HEIGHT` の `documentation` は 5 である。根拠は決定 2 にあり、
  必須の工程の数ではない（`R` を数えると `standard` が 16 個、`documentation` は 14 個）
- 前提 2: `WF_APPROVAL_LABEL`（`design-approved`）と `WF_DESIGN_PREFIX`（`design/`）の値を
  変更しない。既存の `tests/test_approval_gates.py` が関門の数と性質を固定している
- 前提 3: `SKILL.md` は 441 行である。500 行の上限に収めるため、増分は 59 行以内に収める。
  超える場合は `references/` へ移す
- 前提 4: 盤面の単一選択の値は、このリポジトリの GitHub Projects 側にも足す必要がある。
  **リポジトリの変更では完結しない**ため、足りない値は書き込み時に弾かれる

## 受け入れ条件

- [ ] 条件 1: `WF_MODES` が 5 値で、並びは `light` / `operation` / `legacy-refactor` /
      `standard` / `documentation` である
      （検証: `bash -c '. workflow-common.sh; wf_is_mode documentation'` が終了コード 0）
- [ ] 条件 2: `WF_STAGE_MATRIX` が 18 行 × 5 列で、`SKILL.md` の工程表と**並びまで**一致する
      （検証: `pytest tests/test_workflow_stage_matrix.py`）
- [ ] 条件 3: `wf_stage_class documentation 体裁レビュー` が `R` を、
      `wf_stage_class documentation 構造改善` が `-` を返す
- [ ] 条件 4: `wf_mode_height documentation` が 5 を返し、その理由が `SKILL.md` か
      `workflow-common.sh` のコメントに書かれている
- [ ] 条件 5: `PJ_STAGES` が 18 値、`PJ_MODES` が 5 値になり、4 箇所の並びが一致する
      （検証: `pytest tests/test_stage_values.py`）
- [ ] 条件 6: 6 タイプそれぞれについて、判定に使う条件が `references/document-types.md` に
      書かれている
- [ ] 条件 7: 行を 2 つ増やした理由が `references/stage-notes.md` にある
- [ ] 条件 8: 承認の関門が 2 つのままで、`WF_APPROVAL_LABEL` と `WF_DESIGN_PREFIX` が
      変わっていない（検証: `pytest tests/test_approval_gates.py`）
- [ ] 条件 9: `.ndf/document.json` の宣言の形が書かれており、**宣言が無ければ何も起きない**
      ことが明記されている
- [ ] 条件 10: 制作物承認の提示物に、生成物を描画したものと内容照合の差分が含まれる
      （`references/approval-request.md`）
- [ ] 条件 11: すべての `SKILL.md` が 500 行以下である
      （検証: `python3 scripts/check-doc-line-limit.py`）
- [ ] 条件 12: 既存のテストが 1 件も壊れていない
      （検証: `uv run --with pytest pytest scripts/tests plugins/ndf -q`）

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `documentation` を 5 列目に置き、判定の順序では 2 番目にする | 採用 | 列の並びは高さと同じ順という既存の規約を保つ。判定の順序は別の軸である |
| B | 判定の順序と列の並びを揃える | 不採用 | `operation` が既に判定 1 番目・列 2 番目であり、この案は既存の並びも変える |
| C | 文書の工程表を別の表に分ける | 不採用 | `documentation` 列の対象外は 1 セルだけで、分けると同じ規約が 2 箇所へ書かれる |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 文書タイプ | 何を書くか。提案・企画 / 決裁・稟議 / 定例報告 / 指標定義 / 運用マニュアル / 説明・研修の 6 つ |
| 出力の形 | どう見えるか。スライド / 文書 / 表計算 / ページの 4 つ |
| ドキュメンテーションシステム | どこへ置くか。Google Drive / Notion / Confluence / SharePoint / リポジトリ自身の 5 つ |
| 企画承認 | 設計 Pull Request のマージに当たる関門 |
| 制作物承認 | 本番の系へ届く操作に当たる関門 |

## 不変条件

- 承認の関門は 2 つである。増やさない
- 工程名の並びは 4 箇所で同じである
- 宣言ファイルが無いリポジトリでは、この仕組みが何も出力せず終了コード 0 で終わる

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `wf_stage_class` | 5 列目を読む `case` を足す | **追加のみ。** 呼び出し側は変わらない |
| `wf_is_mode` / `wf_mode_height` | 値が 1 つ増える | 追加のみ |
| `PJ_STAGES` / `PJ_MODES` | 値が増える | 追加のみ。**既存の値の並びは変えない** |
| `.ndf/document.json` | 新設 | 読む側がまだ無い。宣言が無ければ何も起きない |

## 修正対象

```text
plugins/ndf/skills/development-workflow/SKILL.md
plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh
plugins/ndf/skills/development-workflow/references/document-types.md          （新規）
plugins/ndf/skills/development-workflow/references/document-destinations.md   （新規）
plugins/ndf/skills/development-workflow/references/workflow-modes.md
plugins/ndf/skills/development-workflow/references/stage-notes.md
plugins/ndf/skills/development-workflow/references/approval-request.md
plugins/ndf/skills/development-workflow/references/projects-tracking.md
plugins/ndf/scripts/lib/projects-common.sh
plugins/ndf/skills/development-workflow/tests/test_documentation_mode.py      （新規）
```

## タスク分解

### Task 1: `documentation` モードを判定できるようにする

- **対象ファイル:** `workflow-common.sh` / `SKILL.md` / `tests/test_documentation_mode.py`
- **変更内容:** `WF_MODES` を 5 値、`WF_MODE_HEIGHT` に `documentation`=5 を足す。
  `SKILL.md` の判定の表へ 2 番目の行を足し、リポジトリの説明文書が対象外であることを書く
- **満たす受け入れ条件:** 1 / 4
- **進め方:** `wf_is_mode documentation` と `wf_mode_height documentation` が落ちる
  テストを先に書く → 値を足す → 高さの理由をコメントへ書く

### Task 2: 工程表を 18 行 × 5 列にする

- **対象ファイル:** `SKILL.md` / `workflow-common.sh`
- **変更内容:** 工程表へ 5 列目と 2 行（素材の収集と出典の確定 / 体裁レビュー）を足し、
  `WF_STAGE_MATRIX` を同じ形にする。`wf_stage_class` の `case` へ `c5` を足す
- **満たす受け入れ条件:** 2 / 3
- **進め方:** `wf_stage_class documentation 体裁レビュー` が落ちるテストを先に書く →
  表と定数を同時に直す（`test_workflow_stage_matrix.py` が突き合わせるため片方だけでは通らない）

### Task 3: 工程名の並びを 4 箇所で揃える

- **対象ファイル:** `projects-common.sh` / `references/projects-tracking.md`
- **変更内容:** `PJ_STAGES` を 18 値、`PJ_MODES` を 5 値にする。対応表へ 2 行を足し、
  記録する Skill の欄には実装 2 〜 4 で新設する Skill の名前を書く
- **満たす受け入れ条件:** 5
- **進め方:** `test_stage_values.py` が落ちることを確かめる → 4 箇所を揃える

### Task 4: 文書タイプの判定を参照へ置く

- **対象ファイル:** `references/document-types.md`（新規）/ `SKILL.md`
- **変更内容:** 6 タイプの判定に使う条件・境界事例・複数に当たったときの決め方を書く。
  `SKILL.md` からは 2 段目の判定として 1 行で指す
- **満たす受け入れ条件:** 6
- **進め方:** 文書のみ。テスト駆動を適用しない（判定は人が読んで行うため、機械で確かめる
  対象が無い）。参照が `SKILL.md` から辿れることは `check-markdown-links.py` が見る

### Task 5: 行を 2 つ増やした理由と、モードごとの経路を残す

- **対象ファイル:** `references/stage-notes.md` / `references/workflow-modes.md`
- **変更内容:** v10.3.0 の「工程表の行は増やさない」の例外にする理由（判定の手段が既存の行と
  違う）を書く。`documentation` の経路と、他のモードと混ざるときの扱いを書く
- **満たす受け入れ条件:** 7
- **進め方:** 文書のみ

### Task 6: 承認の 2 つの関門を文書の工程へ写像する

- **対象ファイル:** `SKILL.md` / `references/approval-request.md` /
  `references/document-destinations.md`（新規）
- **変更内容:** 関門の表へ文書での意味（企画承認 / 制作物承認）を書き、制作物承認の提示物へ
  描画した画像と内容照合の差分を足す。`.ndf/document.json` の宣言の形を新しい参照へ置く
- **満たす受け入れ条件:** 8 / 9 / 10
- **進め方:** `test_approval_gates.py` が通り続けることを確かめながら書く。関門の数を
  増やさない

### Task 7: 分量を上限に収める

- **対象ファイル:** `SKILL.md`
- **変更内容:** 500 行を超えた場合、条件に当たったときだけ読む節を `references/` へ移す
- **満たす受け入れ条件:** 11 / 12
- **進め方:** `check-doc-line-limit.py` と全テストを通す

## 影響範囲

- `development-workflow` を読むすべての工程（モードの値が 1 つ増える）
- 盤面へ記録するすべての Skill（工程の値が 2 つ増える）
- **既存の 4 モードの判定は変わらない。** 足すのは 5 番目の条件だけである

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `SKILL.md` が 500 行を超える（現在 441 行） | Task 7 で `references/` へ移す。移す先は条件に当たったときだけ読む節 |
| 工程表と 4 箇所の並びが片方だけ更新される | 既存の `test_workflow_stage_matrix.py` と `test_stage_values.py` が突き合わせる。**先にテストを落としてから直す** |
| 盤面の単一選択に新しい値が無く、書き込みが弾かれる | リポジトリの変更では完結しない。前提 4 に書き、盤面側の追加を完了報告へ残す |
| `wf_stage_class` の `case` が 5 列目を読み落とす | Task 2 でテストを先に書く。触る対象は 1 関数で構造は保つため、実装後の構造改善で足りる |

## 切り戻し手順

差分は Markdown と shell の定数だけで、データ移行を含まない。Pull Request を revert すれば
元へ戻る。宣言ファイル（`.ndf/document.json`）は読む側がまだ無いため、残っても何も起きない。

## 完了の定義

- [ ] 受け入れ条件 12 件すべてに検証手段と結果が対応している
- [ ] `standard` の必須の段（1 → 2 → 3 → 4）を通す（詳細は `quality-gates`）
