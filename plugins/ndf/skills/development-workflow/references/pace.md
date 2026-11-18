# 進め方（`pace`）

SKILL.md の「進め方（`pace`）」の続きである。3 つの進め方の定義の表は SKILL.md にあり、この文書は
11 系の NDF が進め方の値で変えること・スプリントの始め方・承認ゲートの MVV 判定・設定の読まれ方を持つ。

**目次:** [具体例](#具体例) / [進め方の値が変えること](#進め方の値が変えること) / [スプリントを始める](#スプリントを始める) / [MVV 判定](#mvv-判定) / [承認ゲートで止まった後の続け方](#承認ゲートで止まった後の続け方) / [設定（.ndf/pace.json）](#設定ndfpacejson) / [検査のトリガー](#検査のトリガー) / [記録の読み方](#記録の読み方)

## 具体例

課題 #12 を `standard` の `auto` で流す。プロジェクト MVV（[project-mvv.md](project-mvv.md)）は承認済みである。

1. `ndf progress new s12 --mode standard --issues 12 --pace auto` がスプリントのステートマシンを作り、文脈に
   `pace: auto` と承認済みのプロジェクト MVV の sha256（`mvv_ref`）を書く
2. 設計の工程の承認ゲートの直前のステップ `design-mvv` が `ndf records mvv gate --gate design` を打つ。機械の検査を通り、
   LLM の判定が「従う」でレッドラインが空なので、出力 `mvv_gate.skip` が真になり、判定の記録が設計 PR のコメントに載る
3. 承認ゲートのステップは人へ問わず、回答した者 `service:mvv` の承認で通る
4. デプロイの工程の `release-mvv` が同じ判定をリリースの PR に当てる。判定が「判定できない」だったので `skip` は偽になり、
   承認ゲートで止まって人へ問う

`pace: normal` なら 2 と 4 のステップは判定せずに「進め方 normal は承認ゲートを MVV 判定で省かない」を出力し、どちらの承認ゲートも人へ問う。

## 進め方の値が変えること

**11 系の NDF で進め方の値が変えるのは、承認ゲートを MVV 判定で省けるかだけである。** 工程の並び・ステージ・検査の通し方は
3 つとも同じで、モードの工程の定義（`contracts/workflow/modes/`）のとおりに流れる。

| 値 | 承認ゲート（設計 Pull Request のマージ・本番系へ届く操作） |
| --- | --- |
| `normal`（既定） | 人が回答する |
| `auto` | MVV 判定が省けると出したときは `service:mvv` の承認で通る。ほかは人が回答する |
| `fast` | `auto` と同じ |

`ndf progress new --pace` は値を文脈に保存するだけで、使ってよいかの条件を確かめない。

## スプリントを始める

```text
ndf progress new <スプリント名> --mode <モード> --issues <番号>... --pace <normal か auto か fast>
```

スプリントのステートマシンを作るときに、`.ndf/mvv.json` の最後のバージョンの sha256 を文脈の `mvv_ref` に書く
（承認済みのプロジェクト MVV が無ければ `null`）。MVV 判定の前の検査 2 がこの値と承認の記録を照らすため、
**スプリントを始めた後に MVV を承認し直すと、そのスプリントの承認ゲートは省かれない。**

## MVV 判定

設計の工程の `design-mvv` とデプロイの工程の `release-mvv` が、承認ゲートの直前に `ndf records mvv gate --gate <design か release>` を打つ。
判定のステップはどの進め方でも流れ、結果を工程の出力 `mvv_gate`（`skip`・`verdict`・`why`・判定した PR の `head`）に書く。
承認ゲートのステップは直前の判定の出力だけを読み、`skip` が真なら `service:mvv` の回答で通る。

| 順 | 見ること | 外れたとき |
| --- | --- | --- |
| 1 | モードの工程の定義がその承認ゲートを持つ | 判定しない |
| 2 | 進め方が `fast` か `auto` | 判定しない |
| 3 | 機械の検査（下の 6 つ） | LLM を呼ばずに人へ問う |
| 4 | 判定する PR（設計は作業場所のブランチの開いた PR、リリースは文脈の `pr`）の本文と差分を読めて、差分の全体が材料に載る | LLM を呼ばずに人へ問う |
| 5 | LLM の判定（材料は NDF の共通原則・プロジェクト MVV・PR の本文・差分）が「従う」で、レッドライン（`boundary`）が空 | 人へ問う |

機械の検査は次の 6 つで、1 つでも外れれば省かない。

1. プロジェクト MVV が承認済みで、今の `.ndf/mvv.md` の sha256 が承認の記録と一致する
2. スプリントの文脈の `mvv_ref` が承認の記録と一致する
3. モードが `operation` でも `documentation` でもない
4. PR の変えたファイル（名前を変えた前のパスも）が `.ndf/pace.json` の `boundary_paths` に当たらない
5. PR が `.ndf/mvv.md`・`.ndf/mvv.json` を変えない（共通原則の C7。設定では外せない）
6. 出し直しの指摘への対応表が「記載した」と書く PR 本文のセクションに中身がある

PR を読めた後は、省くときも省かないときも判定の記録（判定・理由・レッドライン・省かない理由）を PR のコメントへ載せる。
同じ材料への「従う」の判定は記録を使い直し、LLM を呼ばない。判定は `mvv-validation.jsonl`（MVV の記録の保存先。[project-mvv.md](project-mvv.md)）に残る。

**省いた回答も、回答を読む前に今の PR の head と照らす。** 判定の後に head が進んでいれば、承認ゲートは待ちへ戻り、人へ問う。
レビューの approve（`gh pr review --approve`）は `fast` と `auto` でもしない（`AGENTS.md`）。

## 承認ゲートで止まった後の続け方

承認ゲートで止まると、`ndf progress` の結果は質問（`metrics.gate.id`）と回答ごとのルート（そのまま打てる `ndf answer` の行）を返す。
conductor は承認資料（[approval-request.md](approval-request.md) の形）に PR のコメントの判定の記録を添えて利用者へ示し、
回答のルートを打つ。

```text
ndf answer <スプリントの ID> --gate <質問の id> --result <回答の語> --reason <理由> --by human --key <冪等のキー> [--notes <指摘ファイル>]
```

回答が直前の MVV 判定と食い違えば（「従う」を退けた・「従わない」か「判定できない」を通した）、改訂の兆候として
`ndf records mvv answer` へ回答を渡す（標準入力の JSON `{"verdict", "approved", "state", "gate", "pr"}`。[project-mvv.md](project-mvv.md) の「改訂の兆候」）。

## 設定（`.ndf/pace.json`）

**11 系の NDF がこの設定から読むのは 2 つの値だけである。** 利用者が変える設定で、リポジトリに置いてレビューを通す。

| 項目 | 型 | 読み手 | 意味 |
| --- | --- | --- | --- |
| `version` | `1` | — | 設定の形のバージョン（必須） |
| `boundary_paths` | glob（gitignore の書き方）の配列 | `ndf records mvv gate` の検査 4 | レッドラインに当たるファイル。無ければ検査 4 は何にも当たらない |
| `triggers.lines` | 整数 | `ndf loop check-trigger` | 検査のトリガーの行数の閾値 |

## 検査のトリガー

```text
ndf loop check-trigger --base <前回の検査の位置> [--head <範囲の終わり>] [--exclude <SHA>...] [--threshold <行数>]
```

範囲の first-parent の各コミットの変更行数（追加 + 削除）を足し、`--exclude` に渡したマージのコミット（検査とリリースの PR）の分を
除いて、`triggers.lines`（`--threshold` を渡せばその値）を超えたら立つ。立てば `ok`、立たなければ `not_applicable` で止まり、
範囲か設定を読めなければ `invalid` で止まる。どの工程のステップもこの判定を自動では打たない。検査を通すかを決めたいときに打つ。

## 記録の読み方

自動で通した承認ゲートの記録の読み方は [pace-records.md](pace-records.md) にある。
