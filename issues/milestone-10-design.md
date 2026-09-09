# マイルストーン 10: ビジネス文書ワークフロー — 設計

要求と受け入れ条件は [milestone-10-documentation-workflow.md](milestone-10-documentation-workflow.md)
にある。この文書は「どう作るか」だけを扱う。決定の記録は
[milestone-10-design-decisions.md](milestone-10-design-decisions.md) にある。

対象は親 #506 と子 9 件（#507 / #508 / #509 / #510 / #511 / #512 / #513 / #514 / #515）である。

## 3 つの軸

**この設計の中核は、1 つに潰れていた軸を 3 つへ分けることである。** #506 は「出力の形」と
「文書タイプ」を挙げ、#515 が「ドキュメンテーションシステム」を足した。3 つは**別々に変わる**。

| 軸 | 値 | 何を決めるか | 持つ場所 |
| --- | --- | --- | --- |
| 文書タイプ | 提案・企画 / 決裁・稟議 / 定例報告 / 指標定義 / 運用マニュアル / 説明・研修 | **何を書くか**（節・読み手が最初に問うこと） | 判定は `development-workflow`、中身は `document-drafting` |
| 出力の形 | スライド / 文書 / 表計算 / ページ | **どう見えるか**（版面・書体・1 枚あたりの情報量） | 決めるのは `design`、生成するのは `release` |
| ドキュメンテーションシステム | Google Drive / Notion / Confluence / SharePoint / リポジトリ自身 | **どこへ置くか**（認証・API・記法・図の扱い） | `document-systems` |

**3 つは掛け合わせになる。** 同じ「提案・企画」が、スライドの形で Google Drive にも、
ページの形で Notion にも載る。1 つの軸へ潰すと、値の数が 6 × 4 × 5 に膨らむ。

```mermaid
flowchart LR
    T["文書タイプ<br/>何を書くか"] --> S["Markdown のソース"]
    S --> F["出力の形<br/>どう見えるか"]
    F --> Y["システム<br/>どこへ置くか"]
```

**軸をまたぐ規約を持たせない。** `form-slide.md` は投稿の手順を書かず、`system-notion.md` は
文書タイプごとの節を書かない。同じ規約が 2 箇所にあると片方だけが更新される。

## 構成要素

### 新設する Skill（4 個）

| 要素 | 責務 | 工程 |
| --- | --- | --- |
| `document-sources` | 使う数値・図・引用の出所を一覧にし、後から同じ値へ戻れる形で残す | 素材の収集と出典の確定 |
| `document-drafting` | 6 タイプそれぞれの中身（節・読み手が最初に問うこと・よくある欠落）を書かせる | 実装（執筆） |
| `layout-review` | 生成物を描画し、版面の欠陥を見る | 体裁レビュー |
| `document-systems` | システムごとの認証・取得・投稿・描画の手順を 1 システム 1 ファイルで持つ | 配布 / 素材の収集 / 体裁レビューの 3 つが読む |

### 既存 Skill へ足すもの

| 要素 | 足すもの |
| --- | --- |
| `development-workflow` | `documentation` モード、工程 2 行、`references/document-types.md` |
| `design` | `references/layout-<出力の形>.md` 4 本。触る領域の表へ「読み手へ渡す文書を作る」の行 |
| `release` | `references/form-<出力の形>.md` 4 本。生成と提出の 2 段 |
| `quality-gates` | モード別の表へ `documentation` の行。追加で要るものとして事実確認 |
| `requirements-design` | `references/document-requirements.md`（読み手・目的・読み手に求める判断） |
| `cross-review` | `state.py` の `PATH_CATEGORY_RULES` へ文書のカテゴリ |

### 変える定数

| 要素 | 現行 | 変更後 |
| --- | --- | --- |
| `workflow-common.sh` の `WF_MODES` | 4 値 | 5 値 |
| 同 `WF_STAGE_MATRIX` | 16 行 × 4 列 | **18 行 × 5 列** |
| 同 `WF_MODE_HEIGHT` | 4 値 | 5 値 |
| 同 `wf_stage_class` | 列を `c1`〜`c4` で読む | `c5` を足す |
| `projects-common.sh` の `PJ_STAGES` | 16 値 | 18 値 |
| 同 `PJ_MODES` | 4 値 | 5 値 |

## 工程表（18 行 × 5 列）

**列の並びは高さと同じ順である。** `documentation` は必須の工程が最も多く、5 列目に来る。

| 工程 | `light` | `operation` | `legacy-refactor` | `standard` | `documentation` |
| --- | --- | --- | --- | --- | --- |
| 要求と受け入れ条件 | R | R | — | R | R |
| 作業場所の用意 | C | C | R | R | R |
| 設計 | C | C | R | R | R |
| **素材の収集と出典の確定** | — | — | — | — | R |
| ドキュメント再構成 | — | — | C | R | R |
| ドキュメントレビュー | — | — | C | R | R |
| 計画 | — | R | R | R | C |
| 実装 | R | R | R | R | R |
| 構造改善 | — | — | R | R | — |
| 実装レビュー | R | R | R | R | R |
| 完了判定 | R | R | R | R | R |
| Pull Request | R | R | R | R | R |
| 確定仕様化 | — | C | — | R | C |
| 後片付け | R | R | R | R | R |
| 配布 | R | R | R | R | R |
| **体裁レビュー** | — | — | — | — | R |
| リリース後テスト | — | C | R | R | C |
| 振り返り | — | C | R | R | R |

**`documentation` 列の対象外は 1 セルだけである**（構造改善）。90 セル全体では 21 セルが
対象外で、うち 9 セルは新しい 2 行が既存 4 モードで対象外になる分である。**表を分ける根拠は
無い。**

**新しい 2 行の位置は固定である。** 素材の収集と出典の確定は `設計` の後、体裁レビューは
`配布` の後に置く。盤面の単一選択は順序が工程の順序を表すため、4 箇所が同じ並びを持つ。

## 判定の順序

**`documentation` を 2 番目に置く。** 最初に該当したモードを採る規約は変えない。

| 順 | モード | 該当条件 |
| --- | --- | --- |
| 1 | `operation` | 本番コードも文書も変えず、外部の系の状態だけを変える |
| 2 | **`documentation`** | **読み手へ渡すビジネス文書を作る・改訂する** |
| 3 | `standard` | 公開インタフェースの変更ほか（現行のまま） |
| 4 | `legacy-refactor` | 現行のまま |
| 5 | `light` | 現行のまま |

**リポジトリの説明文書は `documentation` ではない。** `README.md` や `docs/` の変更は
これまでどおり `light` である。判定を分けるのは**読み手**で、リポジトリの外にいる人へ渡す
ものだけが `documentation` に当たる。この境界は `references/document-types.md` が持つ。

## 文書タイプの判定（6 タイプ）

**タイプ判定はモード判定の 2 段目である。** `documentation` に決まった後に 1 つを選ぶ。

| タイプ | 判定に使う条件 | 実在した例 |
| --- | --- | --- |
| 提案・企画 | 読み手に**採否の判断**を求める。まだ決まっていないことを提案する | `買取エージェントサービス_ご提案資料` |
| 決裁・稟議 | 読み手に**承認**を求める。金額・期間・責任の所在が本体 | `2026_1Q開発稟議` |
| 定例報告 | **決まった周期**で同じ枠に値を入れる。読み手は差分を見る | `0907月次MTG資料（8月振り返り）` |
| 指標定義 | **何を数えるか**を決める。値ではなく定義が本体 | `DXM事業部OKR管理` |
| 運用マニュアル | 読み手が**その場で手を動かす**。改訂が続く | `クエステトラの操作マニュアル` |
| 説明・研修 | 読み手に**理解**だけを求める。判断も操作も求めない | `全社AI活用方針_全社説明資料` |

**境界は「読み手に何を求めるか」で切る。** 同じ内容でも、採否を求めれば提案、承認を求めれば
決裁である。複数に当たるときは**読み手が実際に取る行動**で決め、それでも決まらないときは
上の順で先に来るものを採る。

## 入出力の契約

### `.ndf/document.json`（新設の宣言ファイル）

**文書の提出先は git の外にあるため、別の宣言が要る。** `.ndf/worktree.json` の
`production_branch` は git のブランチしか指せない。

| 項目 | 内容 |
| --- | --- |
| 名前 | `.ndf/document.json` |
| 入力 | リポジトリの根からの相対パスで読む |
| 出力 | 提出先の一覧。`production` が真のものが本番の系 |
| 失敗の形 | **無ければ何も起きず終了コード 0。** 提出の工程は「提出先が宣言されていない」と伝えて止まる |
| 互換性 | 新設のため既存の呼び出し側は無い。`.ndf/worktree.json` と `.ndf/projects.json` の宣言方式に揃える |

```json
{
  "version": 1,
  "source_root": "docs/documents",
  "destinations": [
    {
      "name": "proposal-drive",
      "system": "gdrive",
      "location": "https://drive.google.com/drive/folders/<識別子>",
      "visibility": "internal",
      "production": true,
      "auth": { "kind": "env", "keys": ["GOOGLE_APPLICATION_CREDENTIALS"] }
    },
    {
      "name": "draft-notion",
      "system": "notion",
      "location": "https://www.notion.so/<識別子>",
      "visibility": "internal",
      "production": false
    }
  ],
  "index": { "system": "notion", "location": "https://www.notion.so/<識別子>" }
}
```

- **認証情報そのものは書かない。** `auth` が持つのは出所（環境変数の名前、秘密情報の管理系の
  識別子）だけである
- `production` が真の提出先へ届く操作が**制作物承認の関門**である。偽の提出先（下書きの共有）
  は承認を求めない
- `index` は索引への登録先である。無ければ索引への登録の手順を飛ばす

### `wf_stage_class` の列の追加

| 項目 | 内容 |
| --- | --- |
| 名前 | `wf_stage_class <モード> <工程>` |
| 入力 | モード名と工程名。変更なし |
| 出力 | `R` / `C` / `-` のいずれか。変更なし |
| 失敗の形 | 知らないモードで終了コード 1。変更なし |
| 互換性 | **呼び出し側は変わらない。** 列を読む `case` へ 5 番目を足すだけである |

### 新設 Skill の起動

| Skill | 入力 | 出力 |
| --- | --- | --- |
| `document-sources` | 文書のソースのパス | ソースの `## 出典` の節が埋まる |
| `document-drafting` | 文書タイプ、文書のソースのパス | 本文の Markdown |
| `layout-review` | 生成物のパスまたは URL、出力の形 | 描画した画像と、見た結果 |
| `document-systems` | システム名、用途（取得 / 投稿 / 描画） | 該当する `system-<名前>.md` の手順 |

## 処理の流れ

```mermaid
flowchart TD
    A[要求と受け入れ条件] --> B[設計<br/>構成案 + 体裁設計]
    B --> C[素材の収集と出典の確定]
    C --> D[ドキュメント再構成]
    D --> E[ドキュメントレビュー<br/>企画承認]
    E --> F[執筆]
    F --> G[実装レビュー]
    G --> H[完了判定<br/>事実確認]
    H --> I[Pull Request]
    I --> J[後片付け]
    J --> K[配布・生成]
    K --> L[体裁レビュー<br/>描画して見る]
    L --> M{制作物承認}
    M -->|承認| N[提出・索引への登録]
    M -->|差し戻し| B
```

**体裁レビューが配布の後にあるのは、Markdown の時点では版面が存在しないためである。**
生成してはじめて描画できる。

**差し戻しは設計へ戻る。** 版面の欠陥は体裁設計が決めた版面か、生成の経路のどちらかに
由来する。執筆へ戻しても直らない。

## 承認の 2 つの関門（写像）

**新しい関門を作らない。** `WF_APPROVAL_LABEL` と `WF_DESIGN_PREFIX` を変更しない。

| 既存の関門 | 文書での意味 | 引き金 | 提示するもの |
| --- | --- | --- | --- |
| 設計 Pull Request のマージ | **企画承認** | head のブランチ名 `design/` + ラベル `design-approved` | 読み手 / 読み手に求める判断 / 章立て / 使う数値の出所 / 触れない範囲 / 版面と図表の種類 |
| 本番の系へ届く操作 | **制作物承認** | `production` が真の提出先への操作 | **生成物を描画した画像** / 受け入れ条件の充足 / 事実確認の記録 / 提出先と公開範囲 / 取り消しの手段と限界 |

**文字だけでは体裁を承認できない。** 制作物承認の提示物には描画した画像を必ず含める。

**検証への提出は承認を求めない。** `production` が偽の提出先（社内の下書き共有）は、開発側の
「検証への配布」に当たる。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 工程表が 18 行 × 5 列 | `tests/test_workflow_stage_matrix.py` が `SKILL.md` の表と `WF_STAGE_MATRIX` を突き合わせる |
| 4 箇所の並びが一致 | 同テストへ `PJ_STAGES` と盤面の値の一覧を足す |
| `WF_MODE_HEIGHT` に `documentation` | `wf_mode_height documentation` が 5 を返す |
| `wf_stage_class` が 5 列目を読む | `wf_stage_class documentation 体裁レビュー` が `R` を返す |
| 承認の関門が 2 つ | `WF_APPROVAL_LABEL` と `WF_DESIGN_PREFIX` の値を固定するテスト |
| 新設 4 個が manifest に載る | `skills/` の実体と `manifests/*-skills.txt` の突き合わせ（既存テスト） |
| 境界をまたぐ参照 | 4 つの manifest すべてが参照元と参照先を載せることを固定してから `check-cross-skill-refs.py` の例外へ |
| `SKILL.md` が 500 行以下 | `python3 scripts/check-doc-line-limit.py` |
| 自リポジトリ前提を持たない | `python3 scripts/check-skill-repo-assumptions.py` |
| 公開 Skill 数の記載 | `python3 scripts/check-doc-staleness.py` |
| 宣言が無ければ何も起きない | `.ndf/document.json` が無い状態で提出の手順が終了コード 0 で終わる |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 往復で保たれるもの | 外部 → Markdown → 生成 → 外部の 1 周を実測していない。#514 の受け入れ条件が求めるため、実装 2 で実測する |
| 画像を読めないランタイム | 4 ランタイムのどれで画像を読めるかを実測していない。`layout-review` は 4 つとも配り、読めないときは止める側へ倒す |
| コメントの取り込み | Google スライドと Notion のコメントが取れるかは未確認である。**取り込まない**を既定にし、取れると分かった時点で足す |
| ページを描画して見る手段 | Notion / Confluence / SharePoint のページを画像にする手段が未確認。公開 URL を `playwright-evidence` で撮る案を書き、実測は各システムの導入時に行う |
| Confluence / SharePoint の実測 | 既存実装（別リポジトリ）の知識から書く。このリポジトリでは実行して確かめていない |
