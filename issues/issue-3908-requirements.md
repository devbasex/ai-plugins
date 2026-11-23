## 何が起きたか

2026-11-19 以降のスプリント（計画 p8-5 の lrelay-r2・ldocs・lagent・ltrain・lupd、計画 p9 の lz13-deps・ls3-spec、計画 p12 の llimit・lracct・lcrfast・lctx）で、`ndf progress next` が工程のプランを起動するたびに「差し込みの値が無い: worker」で止まった。どのスプリントでも conductor か supervisor が工程の作業を手で済ませ、`ndf progress finish` で閉じた。プランで流れたのは、ほとんどのスプリントでクリーンアップの 1 工程だけだった。

## 種類

不具合（工程表が定義する工程のプランが、ステートマシンの既定の設定だけでは起動できない。工程のプランで工程を流すことは `development-workflow` の手順書と `ndf progress` のコントラクトが既に決めている）。

## 今の姿

スプリントのステートマシン（`ndf progress`）は、工程ごとにプランのテンプレート（`plugins/ndf/contracts/templates/`）を呼んで工程を流す。テンプレートには差し込みの値（params）があり、値の出所は `plugins/ndf/src/ndf/progress/invoke.py` の `FILL_SOURCES` が決める。

差し込みの `worker` は、`plugins/ndf/contracts/templates/params.json` が「既定は持たない（作業のプロンプトを書く処理がまだ無いため、決まらなければ実行の前に止まる）」と定め、`FILL_SOURCES` は出所を `params.json` の既定（`FROM_DEFAULT`）だけにしている。そのため、`worker` を使うテンプレートを呼ぶ工程は、どの実行でも値が決まらず invalid で止まる。

## 現象

```text
プランのテンプレート <名前> を埋められない: テンプレート <名前> の差し込みの値が無い: worker（params.json の既定）
```

止まった工程は、スプリントごとに次のとおりだった（ほかの工程もプランを起動せずに手で閉じた）。

| スプリント | ステートマシン / スプリントの PR | invalid で止まった工程 |
| --- | --- | --- |
| lrelay-r2 | PR #3899 | 要求と受け入れ条件・ドキュメントレビュー・計画・確定仕様化・デプロイ・リリース後テスト・振り返り（7） |
| ldocs（#3868） | PR #3874・#3882 | 要求と受け入れ条件・計画・確定仕様化・デプロイ・リリース後テスト・振り返り・棚卸し（7） |
| lagent（#3851 #3856 #3854） | PR #3870 | 要求と受け入れ条件・計画・確定仕様化・デプロイ・リリース後テスト・振り返り（6） |
| ltrain（#3907） | PR #3926・#3929・#3932 | 要求と受け入れ条件・作業場所の用意・デプロイ・リリース後テスト・振り返り（5） |
| lupd（#3909） | PR #3931 | 要求と受け入れ条件・デプロイ・リリース後テスト・振り返り（4。プランで流れた工程は無い） |
| p9 の lz13-deps（#3370 #3691 #3380） | 設計 PR #3954・スプリント PR #3961 | 要求と受け入れ条件・計画・実装・確定仕様化・デプロイ・リリース後テスト・振り返り（7。残りの 10 工程も手で閉じ、プランで流れたのはクリーンアップだけ） |
| p9 の ls3-spec（#3412） | 設計 PR #3952 | 要求と受け入れ条件・計画（2） |
| p12 の llimit（#3989） | 設計 PR #3993・スプリント PR #4001 | 要求と受け入れ条件・計画・確定仕様化・デプロイ・リリース後テスト・振り返り（6。残りの 10 工程も手で閉じた） |
| p12 の lracct（#3604 #3864） | PR #4152 | 計画・実装・リファクタリング・コードレビュー・完了判定・Pull Request・確定仕様化・デプロイ・リリース後テスト・振り返り（10） |
| p12 の lcrfast（#4014 #4013） | PR #4193 | 要求と受け入れ条件・計画・デプロイ・リリース後テスト・振り返り（5） |
| p12 の lctx（#3995） | 設計 PR #4041・スプリント PR #4242 | 18 工程のうち 13（プランで流れたのはクリーンアップだけ） |

`worker` のほかに欠けた差し込みもあった。

| 工程 | 欠けた差し込み | 出たスプリント |
| --- | --- | --- |
| 実装 | `title`・`body_file`・`issue`（課題の題と本文）・`acceptance`（工程の入力か打った場所） | p9 の lz13-deps（課題 3 つ）、p12 の lracct |
| リファクタリング | `refactor`（`.ndf/project.json` の設定） | p12 の lracct・lctx |

手で閉じるときの出力の値も、取り場所が示されない。lupd ではデプロイの工程の `delivery` の形（`plugins/ndf/contracts/workflow/context.schema.json` の `$defs/delivery`）が分からず `ndf progress finish` に 1 回断られ、`.ndf/project.json` の `branch_strategy.release.deliveries[0]` を写して `result` を足して通った。llimit と lctx では `mvv_gate` を `release-mvv` のステップの outputs から写した。`ndf progress check`（#3856）は欠けたキーの名前を示すが、値の取り場所は示さない。

## 期待する結果

`standard` のスプリントで `ndf progress next` を打つと、工程のプランが差し込みの値を決めて起動する。決められない差し込みがある工程は、起動を試みて failed を書くのではなく、何が決まらないかを `ndf progress next` の結果に出して止まる。

## 再現手順

1. `standard` のモードのスプリントのステートマシンを `ndf progress new` で作る
2. `ndf progress next` で、プランを呼ぶ工程（例: 要求と受け入れ条件）を起動する
3. 「差し込みの値が無い: worker（params.json の既定）」で invalid になり、工程が failed になる

## 環境

- NDF 11.0.9〜11.0.21。11.0.21 を出した後（2026-11-23 17:44 JST）の `main` の `plugins/ndf/contracts/templates/params.json` の `worker` にも既定が無く、`plugins/ndf/src/ndf/progress/invoke.py` の `FILL_SOURCES` も `worker` を `FROM_DEFAULT` だけにしている

## なぜ困るか

工程のステートマシンが `standard` の工程のほとんどで自動では流れず、毎回 failed の記録と手での `finish` が入る。工程の作業を conductor と supervisor が抱えるため、1 体が 6 工程を回して文脈が 29 万〜45 万まで伸びる（lagent・lupd・llimit・lctx・lcrfast の振り返りの実測）。デプロイの工程では、出力（`delivery`・`version`・`mvv_gate`）を conductor が手で組む。

## 目的

- `standard` の工程が、工程のプランで流れるようにする。工程の作業を conductor と supervisor が抱えず、文脈が伸びないようにする
- 手で閉じるときに、出力の値をどこから取るかが分かるようにする

## 対象範囲

含む:

- 工程から呼ぶプランの差し込み `worker` の値が決まるようにすること（出所を足すか、値を組み立てる処理を置くか、`worker` を要らなくするか）
- `worker` のほかに欠ける差し込み（実装の `title`・`body_file`・`issue`・`acceptance`、リファクタリングの `refactor`）の出所
- 値が決まらない差し込みがあるときに、工程を起動せず（failed を書かず）、何が決まらないかを結果に出すこと
- 手で閉じるときの出力の値の取り場所を、`ndf progress finish` が受け付ける前に示すこと（`ndf progress check` の結果に足す）

含まない:

- 工程「実装」への単位の展開とマージ（#3905 が持つ。この課題が直った後に効く）
- デプロイの工程がスプリントの PR へリリース承認ゲートを出すこと（#3913）
- 工程表の工程そのものの増減

## 受け入れ条件

- [ ] 1. `standard` のモードのステートマシンを作り、工程「要求と受け入れ条件」を `ndf progress next` で起動すると、`worker` の差し込みで invalid にならずにプランが起動する（テスト）
- [ ] 2. 工程「実装」「リファクタリング」のプランでも、`worker`・`title`・`body_file`・`issue`・`acceptance`・`refactor` の差し込みが、工程の入力・ステートマシンの文脈・リポジトリの設定から決まる（テスト）
- [ ] 3. 差し込みの値をどうしても決められない工程を起動したとき、ステートマシンに failed の記録を書かず、決まらない差し込みの名前と、その値の出所の候補を `ndf progress next` の結果に出す（テスト）
- [ ] 4. `ndf progress check` が、出力に欠けた文脈のキーを示すとき、そのキーの値の取り場所も示す（例: `delivery` は `.ndf/project.json` の `branch_strategy.release.deliveries`、`mvv_gate` は `release-mvv` のステップの outputs）（テスト）
- [ ] 5. （退行）今プランで流れている工程（クリーンアップ）が、この変更の後も同じ引数で流れる（テスト）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`ndf progress next` が差し込みを決められないときの返りと、`ndf progress check` の結果に値の取り場所が加わる。既存のキーは消さない |
| 重さ | `standard` のスプリントの工程のほとんどが自動で流れない。回避の手段は、conductor と supervisor が作業を手で済ませて `ndf progress finish` で閉じることで、そのぶん文脈が伸びる |
| データ | 無し（ステートマシンの保存の形は変えない） |
| 既存の振る舞い | 工程のプランが起動するようになる。工程表の工程は増減しない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（CI で実行する） |
| 静的解析・型検査 | CI のジョブ |
| 手動確認 | このスプリント自身のステートマシン（`sprint:2cea9b46d998:p9-lsplit-r2-55c601e023a9`）で、直した後の工程が流れることをリリース後テストで確かめる |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 決まらない差し込みを持つ工程は「ステートマシンの定義から外れて、起動のたびに failed を書かない」形でもよい | #3908 の本文の期待する結果（起票者が書いた） | 受け入れ条件 3（定義から外さず、起動せずに理由を返す形に絞った） |
| S2 | 手で閉じるときの出力の値の取り場所を示す先は `ndf progress check` である | #3908 の本文の期待する結果の「キーの名前の欠けは `ndf progress check` が示す」（起票者が書いた） | 受け入れ条件 4 |

## リクエスト（原文）

> 工程から呼ぶプランで `worker` が決まる（作業のプロンプトを書く処理と既定の起動）か、決まらない工程はステートマシンの定義から外れて、起動のたびに failed を書かない
>
> 手で閉じるときの出力の値の取り場所（とくにデプロイの `delivery`・`version`・`mvv_gate`）を、`ndf progress finish` が受け付ける前に示す。キーの名前の欠けは `ndf progress check` が示す

（書き込み元: 起票者、日付: 2026-11-20。スプリント lrelay-r2 の振り返りで起票され、以後のスプリントの振り返りで同じ失敗を書き足している）

## 関連

- #3913（デプロイの工程がスプリントの PR へリリース承認ゲートを出す）
- #3905（`ndf progress run` の工程「実装」に単位の展開とマージを足す。この課題が直った後に効く）
- #4229（実装の工程が、消した設計の worktree を差し込みの値の出所にして `precondition` で落ちた）

