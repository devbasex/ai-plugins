# #418 / #420 / #422 / #392: 工程表とモードの単位を直す

## 関連リンク

- 課題: [#418](https://github.com/devbasex/ai-plugins/issues/418) /
  [#420](https://github.com/devbasex/ai-plugins/issues/420) /
  [#422](https://github.com/devbasex/ai-plugins/issues/422) /
  [#392](https://github.com/devbasex/ai-plugins/issues/392)
- 要求と受け入れ条件: [issue-418-417-workflow-gate/01-requirements.md](issue-418-417-workflow-gate/01-requirements.md) の A1〜A6 / B1〜B6
- 設計: [issue-418-417-workflow-gate/02-design.md](issue-418-417-workflow-gate/02-design.md) の決定 1・決定 2・決定 2-b・決定 9
- 確定仕様: [ndf-workflow-unit-and-gates.md](../../docs/specifications/ndf-workflow-unit-and-gates.md)

## モード

`standard`。工程表は `WF_STAGE_MATRIX` と盤面の値の一覧を通じて gate と記録の
振る舞いを決めるため、本文だけの変更ではない。対象にはテストがある。

## 目的と非目的

達成したい状態:

- **Pull Request を出す変更が、必ずレビューを通る。** `light` のレビューが
  `cross-review` になる
- **モードを判定する単位が Pull Request になる。** 束ねたときにどのモードを採るかという
  問いが生じない
- **`light` が gate の検査から外れない。** 必須の工程が少ないモードほど、通していない
  ことが見えにくい

やらないこと:

- gate の実装（`workflow-guard.sh` / `wf_evidence_report`）。本 3 が扱う
- 承認の関門と `production_branch` の宣言。本 3 が扱う
- **工程名を増やすこと。** 盤面の値の一覧は集合として変わらない（並びだけが変わる）
- モードの定義そのもの

## 前提

- 前提 1: 工程の並びは 4 か所（工程表・対応表の 2 列・`PJ_STAGES`）が一致していることを
  `test_stage_values.py` が見る。**並びを変えるなら 4 か所を同時に変える**
- 前提 2: 盤面（GitHub Projects）の単一選択の並びは会話の外にある。ファイル側の並びを
  変えても、盤面の選択肢の並びは手で直すまで揃わない。**値の集合は変わらないため、
  記録そのものは通る**

## 受け入れ条件

`01-requirements.md` の A1〜A6 / B1〜B6 をそのまま採る。

- [ ] A1: 工程表の `light` の「レビュー」が `cross-review`
- [ ] A2: `WF_STAGE_MATRIX` の「レビュー」の `light` 列が `R`
- [ ] A3: `test_workflow_stage_matrix.py` が終了コード 0
- [ ] A4: 標準フローの mermaid 図で、`light` の経路がレビューを通る
- [ ] A5: `workflow-modes.md` の `light` の経路の記述が A4 と一致する。
      リリース後テストと振り返りを行わない理由が、**レビューの有無を根拠にしない**
- [ ] A6: `stage-check.sh report` が `light` のとき「レビュー」を必須として扱う
- [ ] B1: 判定の単位が Pull Request と書かれている。束ねたときの規約は置かない
- [ ] B2: 工程の順序が「要求と受け入れ条件 → モード判定 → 作業場所の用意 → …」
- [ ] B3: 工程表の `light` の「要求と受け入れ条件」が `—` ではない
- [ ] B4: 束ねずに分ける判断の基準が書かれている
- [ ] B5: 1 つの Pull Request に対しモードが 1 つ。全課題の控えへ同じ値を書く
- [ ] B6: 控えの名前・JSON の鍵・CLI の引数が変わらない。記録が食い違うときの扱いが
      `stage-completeness.md` にある
- [ ] #392: 同じ段落が 2 つ並んでいる箇所が 1 つになる
- [ ] 退行しないこと: 検査 10 本とテストが終了コード 0

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 判定の単位を Pull Request にする | 採用 | 数える対象が 1 つになる（決定 2） |
| B | 束ねるときは最も高いモードを採る規約を足す | 不採用 | 束ね方を変えるたびに同じ判断が要る |
| C | 「モード判定」を工程表の行にする | 不採用 | 盤面の値が増える。**順序は本文で書ける** |
| D | 控えの鍵を Pull Request へ広げる | 不採用 | 3 つの契約が同時に変わるのに対し、得られるのは値の一致でも表せること（決定 9） |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 控え | 通過工程を課題ごとに残すファイル（`<所有者>__<リポジトリ>__<課題番号>.json`） |
| 工程表 | `SKILL.md` の「モードごとに起動する Skill」の表 |
| 分類表 | `WF_STAGE_MATRIX`。工程表を機械の側へ写したもの |

## 不変条件

- **工程の並びは 4 か所で一致する。** 集合ではなく並びまで一致する
- **1 つの Pull Request に対しモードは 1 つである。** 鍵ではなく値の一致で表す

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `stage-check.sh` / `projects-sync.sh` の引数 | 変えない | 変えない |
| 控えの JSON の鍵 | 変えない | 変えない |
| 盤面の値の集合 | 変えない（並びだけが変わる） | 記録は通る。選択肢の並びは手で直す |
| `light` の必須の工程 | 「要求と受け入れ条件」と「レビュー」が増える | **費用は上がる。** 決定 1 と決定 2 で受け入れた |

## 修正対象

- `plugins/ndf/skills/development-workflow/SKILL.md`
- `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh`
- `plugins/ndf/skills/development-workflow/references/workflow-modes.md`
- `plugins/ndf/skills/development-workflow/references/projects-tracking.md`
- `plugins/ndf/skills/development-workflow/references/stage-completeness.md`
- `plugins/ndf/scripts/lib/projects-common.sh`
- `plugins/ndf/skills/development-workflow/tests/`

## タスク分解

### Task 1: `light` にレビューと要求の工程を課す

- **対象ファイル:** `SKILL.md` の工程表 / `workflow-common.sh` の `WF_STAGE_MATRIX`
- **変更内容:** 「レビュー」の `light` 列を `cross-review` に、「要求と受け入れ条件」の
  `light` 列を `requirements-design` にする。分類表も同時に直す
- **満たす受け入れ条件:** A1 / A2 / A3 / A6 / B3
- **進め方:** `light` の控えで `wf_report` の `記録なし:` に「レビュー」と
  「要求と受け入れ条件」が出ることを、先にテストで固定する

### Task 2: 工程の順序を判定の単位へ合わせる

- **対象ファイル:** `SKILL.md` / `workflow-common.sh` /
  `references/projects-tracking.md` / `plugins/ndf/scripts/lib/projects-common.sh`
- **変更内容:** 「要求と受け入れ条件」を「作業場所の用意」より前へ移す。
  **モード判定は工程表の行にしない**（盤面の値を増やさない）。順序は本文で書く
- **満たす受け入れ条件:** B2
- **進め方:** 4 か所の並びが一致することは既存の `test_stage_values.py` が見る。
  順序そのものを固定するテストを足す

### Task 3: 判定の単位と、分けるときの基準を書く

- **対象ファイル:** `SKILL.md`
- **変更内容:** 判定の単位が Pull Request であること、束ねたときの規約は置かないこと、
  触るファイルが重ならないなら分けてよいことを書く
- **満たす受け入れ条件:** B1 / B4 / B5
- **進め方:** 記述の有無をテストで固定する

### Task 4: 控えの契約と、食い違うときの扱いを書く

- **対象ファイル:** `references/stage-completeness.md`
- **変更内容:** モードの記録を課題ごとに持つ理由と、記録が食い違うときの扱いを書く。
  控えの名前・JSON の鍵・CLI の引数が変わらないことを明記する
- **満たす受け入れ条件:** B5 / B6
- **進め方:** 契約が変わっていないことと、記述があることをテストで固定する

### Task 5: 図と参照を工程表へ合わせ、重複した段落を消す

- **対象ファイル:** `SKILL.md` の mermaid 図 / `references/workflow-modes.md`
- **変更内容:** `light` の経路がレビューを通る形へ図を直す。`workflow-modes.md` の
  `light` の記述を合わせ、リリース後テストと振り返りを行わない理由を別の根拠で述べる。
  同じ段落が 2 つ並んでいる箇所を 1 つにする
- **満たす受け入れ条件:** A4 / A5 / #392
- **進め方:** 図の経路と記述の一致をテストで固定する

## 影響範囲

- `light` で進める変更すべて。**要求と受け入れ条件とレビューが増える**
- 盤面の選択肢の並び（手で直す）
- gate の案内（本 3 が読む分類表がここで変わる）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 並びを 1 か所だけ直して検査が落ちる | 4 か所を同じコミットで直す |
| `light` の費用が上がる | 設計で受け入れた（決定 1）。実測は次のまとまりで行う |
| 盤面の選択肢の並びが揃わない | 値の集合は変わらないため記録は通る。並びは手で直す |

## 切り戻し手順

- Pull Request 単位で戻せる。データの移行は無い。控えの形も変わらない

## 完了の定義

- [ ] A1〜A6 / B1〜B6 と #392 に、テストか実行の証跡が対応している
- [ ] 検査 10 本とテストが終了コード 0
- [ ] `cross-review` が収束している（未解決スレッド 0 件）
