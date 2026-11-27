# 進め方（`pace`）

SKILL.md の「進め方（`pace`）」の続きである。3 つの進め方の定義の表は SKILL.md にあり、この文書は
11 系の NDF が進め方の値で変えること・スプリントの始め方・承認ゲートの MVV 判定・設定の読まれ方を持つ。

**目次:** [具体例](#具体例) / [進め方の値が変えること](#進め方の値が変えること) / [スプリントを始める](#スプリントを始める) / [MVV 判定](#mvv-判定) / [承認ゲートで止まった後の続け方](#承認ゲートで止まった後の続け方) / [設定（.ndf/pace.json）](#設定ndfpacejson) / [検査のトリガー](#検査のトリガー) / [記録の読み方](#記録の読み方)

## 具体例

課題 #12 を `standard` の `auto` で流す。プロジェクト MVV（[project-mvv.md](project-mvv.md)）は承認済みである。

1. `ndf progress new s12 --mode standard --issues 12 --pace auto` がスプリントのステートマシンを作り、文脈に
   `pace: auto` と承認済みのプロジェクト MVV の sha256（`mvv_ref`）を書く
2. 設計の工程の承認ゲートの直前のステップ `design-mvv` が `ndf records mvv gate --gate design` を打つ。機械の検査を通り、
   レビューの保留が無く、LLM の判定が「従う」でレッドラインが空なので、出力 `mvv_gate.skip` が真になり、判定の記録が設計 PR のコメントに載る
3. 承認ゲートのステップは人へ問わず、回答した者 `service:mvv` の承認で通る
4. デプロイの工程の `release-mvv` が同じ判定をリリースの PR に当てる。リリース承認ゲートは判定の結果によらず省かない
   （下の「[MVV 判定](#mvv-判定)」）ので、`skip` は偽になり、承認ゲートで止まって判定を載せた承認資料で人へ問う

`pace: normal` でも 2 と 4 のステップは判定し、判定の記録を PR のコメントと承認資料に載せる。`skip` は常に偽で、どちらの承認ゲートも人へ問う。

## 進め方の値が変えること

**11 系の NDF で進め方の値が変えるのは、承認ゲートを MVV 判定で省けるかだけである。** 工程の並び・ステージ・検査の通し方は
3 つとも同じで、モードの工程の定義（`contracts/workflow/modes/`）のとおりに流れる。

| 値 | 承認ゲート（設計 Pull Request のマージ・本番系へ届く操作） |
| --- | --- |
| `normal`（既定） | 人が回答する |
| `auto` | 設計承認ゲートは、MVV 判定が省けると出したときに `service:mvv` の承認で通る。ほかは人が回答する |
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

**判定はどの進め方でも打つ。進め方が決めるのは、判定の後に承認ゲートを省くかだけである。** 設計の工程の `design-mvv` と
デプロイの工程の `release-mvv` が、承認ゲートの直前に `ndf records mvv gate --gate <design か release>` を打ち、結果を工程の出力
`mvv_gate` に書く。承認ゲートのステップは直前の判定の出力だけを読み、`skip` が真なら `service:mvv` の回答で通る。
判定の記録を PR のコメントに書けなかった場合は、判定の結果を出力に残したまま `noted` と `skip` を偽にし、承認ゲートで人へ問う
（承認の根拠を PR に残さないまま自動で通さないため）。

| 順 | 見ること | 外れたとき |
| --- | --- | --- |
| 1 | モードの工程の定義がその承認ゲートを持つ | 判定しない |
| 2 | 機械の検査 1（下の一覧） | 判定しない（LLM を呼ばず、PR にコメントを書かない）。承認ゲートで人へ問う |
| 3 | 判定する PR（設計は作業場所のブランチの開いた PR、リリースは文脈の `pr`）の本文と差分を読めて、差分の全体が材料に載る | LLM を呼ばずに人へ問う |
| 4 | 進め方が `fast` か `auto` で、承認ゲートが設計承認ゲート | 判定は打ち、省かない |
| 5 | 機械の検査 2〜7 がすべて通る | 判定は打ち、省かない |
| 6 | LLM の判定（材料は NDF の共通原則・プロジェクト MVV・PR の本文・差分）が「従う」で、レッドライン（`boundary`）が空 | 人へ問う |

**判定を止めるのは順 1〜3 だけである。** 順 4〜6 は判定の後に省くかだけを決め、外れても判定・理由・レッドライン・レビューの保留・
省かない理由は PR のコメントと承認資料に載る。

機械の検査は次の 7 つである。検査 1 が外れれば判定しない。検査 2〜7 は 1 つでも外れれば省かない。

1. PR のベースブランチのプロジェクト MVV が承認済みで、本文の sha256 が承認の記録と一致する
2. スプリントの文脈の `mvv_ref` が、ベースブランチの最新の承認の記録と一致する
3. モードが `operation` でも `documentation` でもない
4. PR の変えたファイル（名前を変えた前のパスも）が、ベースブランチの `.ndf/pace.json` の `boundary_paths` に当たらない
5. PR が `.ndf/mvv.md`・`.ndf/mvv.json` を変えない（共通原則の C7。設定では外せない）
6. 出し直しの指摘への対応表が「記載した」と書く PR 本文のセクションに中身がある
7. 設計承認ゲートでは、その PR のレビューの保留が 0 件である。リリース承認ゲートでは保留を読まず、この検査は何も返さない

**レビューの保留**は、対象の PR の cross-review の記録（`mvv-validation.jsonl` の `mvv-review` の行）に残る、直っていない
レッドラインの指摘と、現在のプロジェクト MVV のバージョンで修正を見送り、利用者の判断を待っているレビュー指摘である。自由記述を伏せて書いた行で終わる指摘も保留に数える。
記録がまだ作られていなければ 0 件として通し、既にある記録を読めなければ、読めない理由を保留の 1 件にする。

**リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）、`fast`・`auto` でも省かない。** 判定は打ち、`why` に省かない理由が入る。

判定の出力 `mvv_gate` のキーは次のとおりである。承認資料の「MVV 判定」の欄はこの値から組む（[approval-request.md の「MVV 判定の欄」](approval-request.md#mvv-判定の欄)）。

| キー | 中身 |
| --- | --- |
| `skip` | 承認ゲートを省いてよいか |
| `verdict` | LLM の判定（`verdict.verdict` が `follow`（従う）・`not_follow`（従わない）・`unknown`（判定できない）、`reasons`・根拠の項目・`boundary`）。判定を打てなかったときは `null` |
| `stopped_by` | `verdict` が `null` のときだけある。判定できなかった種類 `kind` と定型文 `text`（PR から取った値を含まない） |
| `why` | 省かない理由の一覧 |
| `holds` | レビューの保留の一覧。保留を読んで 0 件なら `[]`、読んでいなければ `null` |
| `pr`・`head` | 判定した PR の番号と head の SHA。PR を特定する前に止まったときは無い |
| `noted`・`note_url` | 今回の判定の記録を PR のコメントに書けたか（真偽値）と、書けたときのコメントの URL |

判定を打ったときは、進め方によらず、判定の記録（判定・理由・レッドライン・レビューの保留・省かない理由）を PR のコメントへ載せる。
同じ材料への「従う」の判定は記録を使い直し、LLM を呼ばない。判定は `mvv-validation.jsonl`（MVV の記録の保存先。[project-mvv.md](project-mvv.md)）に残る。

**省いた回答も、回答を読む前に今の PR の head と照らす。** 判定の後に head が進んでいれば、承認ゲートは待ちへ戻り、人へ問う。
レビューの approve（`gh pr review --approve`）は `fast` と `auto` でもしない（`AGENTS.md`）。

### supervisor の経路の打ち方

**プランを使わずに工程を進めるとき（supervisor の経路）は、`--machine` で同じ判定を打つ。**

```text
ndf records mvv gate --gate <design か release> --machine <ステートマシンの ID> [--expect-pr <承認する PR の番号>]
```

- `--machine` は、ステートマシンの文脈から、プランが渡すのと同じ規則で判定の入力を組む。環境変数 `NDF_STAGE_INPUT` と両方を渡すと
  `invalid` で止まる。ID が無い・別のリポジトリのステートマシン・モードがその判定のステップを持たないときは `refused` で止まる
- `NDF_STAGE_INPUT` は工程の入力の規約 `stage-in` の平らな形で、形が違えば `invalid`、キーが足りなければ `input_missing` で止まる
- `--expect-pr` は `--gate release` だけで受ける（`design` では `invalid`）。判定する PR がその番号と違えば、判定せずに `not_applicable` で止まる

**プランを使わずに承認ゲートを返す supervisor は、自分で MVV 判定を打ち、結果を承認資料に載せる（[agent-layers.md](agent-layers.md) の supervisor が守る規則 17）。** プランの経路
（判定のステップ `design-mvv`・`release-mvv` を持つプラン）では判定のステップが打つので、打たない。判定の条件は
[上の「MVV 判定」](#mvv-判定)、承認資料への載せ方は [approval-request.md の「MVV 判定の欄」](approval-request.md#mvv-判定の欄) にある。

打つ場所と保存先は、どちらの承認ゲートでも同じである。

- **打つ場所**: 起動指示の「作業場所」の worktree で打つ。設計承認ゲートでは、判定する PR がその worktree のブランチの開いた PR に決まる
- **保存先**: 起動指示の「作業ディレクトリ」の下の `mvv-gate-design.json`・`mvv-gate-release.json`。結果 JSON は PR から取った
  自由記述（判定の理由・保留の文）を含むので、コミットの対象になる worktree の中に置かない。作業ディレクトリが作業場所の worktree の
  下にある（`git -C <作業場所> rev-parse --show-toplevel` の下）なら、保存せずに `結果: 止まった` で返す（`理由` に作業ディレクトリが
  worktree の中にあることを書く）
- **書き方**: `mktemp` で作った新しいファイル（0600）に書いてから、所定のパスへ `mv` で置き換える。判定が `stopped` で終わったとき
  （終了コードが 0 でない）も置き換えるよう、判定のコマンドと `mv` を `&&` でつながない。打ち直しで同じパスへ書くときも、既にある
  ファイルの権限を引き継がない

```bash
tmp=$(mktemp "<作業ディレクトリ>/.mvv-gate-design.XXXXXX") && { ndf records mvv gate --gate design --machine <ステートマシンの ID> > "$tmp"; mv -f "$tmp" "<作業ディレクトリ>/mvv-gate-design.json"; }
```

[agent-layers.md](agent-layers.md) の supervisor が守る規則 15 の工程の出力の JSON ファイルも、同じく `mktemp` で作ったファイルに書いてから置き換える。

**設計承認ゲート:**

1. 上のコマンドで判定を打ち、作業ディレクトリの `mvv-gate-design.json` に保存する
2. 結果に `outputs.mvv_gate` がある場合だけ、その値を設計の工程の出力の JSON ファイル（agent-layers.md の規則 15）の `outputs.mvv_gate` へそのまま写す。
   結果が `ok` でなくキーが無い場合は、工程の出力に判定を書かない
3. 承認資料の「MVV 判定」の欄を組む。`outputs.mvv_gate` があれば同じ値から組む（[approval-request.md の「MVV 判定の欄」](approval-request.md#mvv-判定の欄)）。
   キーが無ければ、同じセクションの「判定の結果 JSON が `ok` でないとき」の文を載せる。どちらの場合も承認ゲートは返し、人へ問う
4. 承認資料の根拠の行を `ndf records mvv cite --gate design` で確かめる（[approval-request.md の「承認資料を組んだら」](approval-request.md#承認資料を組んだら)）

**リリース承認ゲート（プランを使わず、本番系へ届く操作の前で `結果: 承認ゲート` を返す手動の経路。フェーズの表の「仕上げ」など）:**

1. **承認する PR を先に決める。** このリリースで承認する人がマージを承認する PR で、承認資料の「対象を開くためのもの」に載せる PR である

   | 場合 | 承認する PR | 判定のコマンドに渡すもの |
   | --- | --- | --- |
   | このリリースで `ndf release-steps assemble` を打ち、`ok` の結果 JSON に `outputs.pr` がある（版上げの PR を組み立てた） | `outputs.pr` の PR | `--expect-pr <outputs.pr>` |
   | ブランチ戦略がリリース用の PR を作らない（`ndf release-steps assemble` を打たない。`trunk` の `production_pr` など） | スプリントの PR そのもの | 渡さない（判定する PR が承認する PR である） |
   | 版数を持たない `merge_base_branch`（`release.version_store.kind` が `none`。`ndf release-steps assemble` は版上げの PR を組まない） | リリースの手順がこのリリースで作った、または呼び出し元が渡した、ベースブランチから本番ブランチへの PR | `--expect-pr <その PR の番号>`。その PR を決められなければ下の行と同じく止まる |
   | 版上げの PR を作る戦略なのに、`ndf release-steps assemble` の結果が `ok` でない・`outputs.pr` が無い（`not_applicable` を含む） | 決められない | 判定を打たず、承認も求めない。`結果: 止まった` で返し、`理由` に「承認するリリース用の PR を特定できない」と `ndf release-steps assemble` の結果 JSON のパスを書く |

   文脈のキー `release_pr` は使わない（承認ゲートの時点ではまだ無い）。`gh` でブランチ名から PR を探すこともしない（同じブランチの PR が複数あると決まらない）
2. 作業場所で `ndf records mvv gate --gate release --machine <ステートマシンの ID> [--expect-pr <承認する PR の番号>]` を打ち、上の書き方で
   作業ディレクトリの `mvv-gate-release.json` に保存する。`--expect-pr` を渡すと、判定のコマンドが判定に使う入力の PR と比べ、違えば
   判定せずに（LLM を呼ばず、PR にコメントを書かずに）`not_applicable` で止まる
3. 結果で承認資料の「MVV 判定」の欄を組む。`ok` なら「MVV 判定の欄」の手順で、`ok` でなければ同じセクションの「判定の結果 JSON が `ok` でないとき」の
   文で載せる。どの結果でも承認ゲートは返す。結果の `outputs.mvv_gate` は工程の出力へは載せない
4. 承認資料の根拠の行を `ndf records mvv cite --gate release` で確かめる

**判定の中身を、フェーズレポートの本文へ写さない。** `終えた工程` にはファイルのパスだけを書き、`outputs.mvv_gate` の中身（判定の理由・
レッドライン・`why`・`holds`）を `理由` などへ写さない。conductor はパスを `ndf progress finish --output` へ渡すだけで、中身を読んで人へ
転送しない。`mvv-gate-<区分>.json` は手元の記録で、呼び出し元へは返さない。

## 承認ゲートで止まった後の続け方

承認ゲートで止まると、`ndf progress` の結果は質問（`metrics.gate.id`）と回答ごとのルート（そのまま打てる `ndf answer` の行）を返す。
conductor は承認資料（[approval-request.md](approval-request.md) の形。「MVV 判定」の欄は今回の判定の出力から組む）を利用者へ示し、
回答のルートを打つ。打つのは利用者に示した時点のルートである。ルートに含まれる `--head`・`--material-sha256`・`--presentation` は
そのまま渡し、ルートに無いものは足さない（PR の無い承認ゲートでは `--head` が入らない）。
資料を出し直した後に古いルートを打つと、回答は断られて承認ゲートは待ちのまま残る。

次の例は PR がある承認ゲートのルートに、理由・回答者・冪等のキーを補った形である。

```text
ndf answer <スプリントの ID> --gate <質問の id> --result <回答の語> --head <見た head> --material-sha256 <見た資料の sha256> --presentation <提示の識別子> --reason <理由> --by human --key <冪等のキー> [--notes <指摘ファイル>]
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
