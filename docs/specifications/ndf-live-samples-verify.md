# テストの見本の突き合わせ: `ndf samples verify` が見本を本物の公開の記述と毎日比べ、食い違いを課題にする

テストが本物の代わりに差し込むテストダブルは、記録した JSON（見本）だけから応答を組み立てる。`ndf samples verify` は、見本を相手が公開する記述
（GitHub の REST の OpenAPI 3.1）と突き合わせ、相手がキーを足した・消した・型を変えたことを見つける。毎日 06:00 JST のワークフロー
`runtime-plugin-smoke-latest.yml` のジョブ `live-samples` がこれを打ち、食い違えば相手ごとに 1 件の課題を作る。

例: GitHub が課題の応答へキー `foo` を足すと、次の日の `live-samples` が次の結果 JSON を出して終了コード 1 で終わり、題が
「テストの見本が本物の記述と食い違った: github」の課題ができる（同じ題の課題が開いていればコメントを足す）。

```json
{"tool": "samples", "status": "stopped", "cause": {"kind": "samples_mismatch"},
 "items": [{"sample": "tests/samples/github/rest/issue.json", "peer": "github", "kind": "mismatch", "detail": "(最上位): 記述に足されたキー foo"}],
 "metrics": {"samples": 2, "calls": {"github": 1}}}
```

## 用語

定義は [用語集](../glossary.md) が正である。この文書で使う語は「テストダブル」「見本」である。

## 理由

テストダブルは本物と比べられず、相手の API が変わってもテストは通り続けていた。公開の記述は、相手が本番のリクエストの検証に使う記述と同期しており、
認証も固定のデータも要らない。API を実際に呼ぶ形は、認証が要り、呼び出しの上限を毎日使い、固定のデータが消えると落ちるため採らない。

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 見本の定義、`ndf samples verify`、毎日のジョブ `live-samples` と `report` | ランタイムの CLI の `--help`（ジョブ `runtime-smoke-latest` が比べる。[runtime-smoke-cli-versions.md](runtime-smoke-cli-versions.md)） |
| GitHub の REST の課題とラベルの要素の見本 | 誤りの返し方・ページング・利用上限（公開の記述に現れない）、GraphQL |
| 見本から応答を組み立てるテストダブル `tests/support/sample_hub.py` | 既存の `FakeHub` を見本へ寄せること |

## 仕様

### 見本の定義（`plugins/ndf/contracts/live-samples/`。1 見本 1 ファイル）

| キー | 意味 |
| --- | --- |
| `peer` | 相手の名前。要求の数は相手ごとに数える |
| `sample` | 見本のファイル（プラグインの根からの相対。`tests/samples/` の下） |
| `source.schema` | 公開の記述の URL。認証情報・クエリ・フラグメントは書けない |
| `source.pointer` | 記述の中の定義を指す JSON ポインタ |
| `absent_keys` | 見本を録った時点で記述にあり、見本に無かったキーの一覧（見本の根からのパス。配列の要素は `*`） |
| `extra_keys` | 見本にあり、記述に無いと分かっているキーの一覧 |
| `known_mismatches` | 本物と記述が食い違うと分かっている所の説明の一覧（出力の `detail` の文と同じ形） |

見本は本物の応答のキーをそのまま残し、人や課題を特定する値は相手の文書の例の値へ置き換える。今の見本は課題 1 件（`issue.json`）と
ラベルの要素（`issue_label.json`）の 2 つで、ラベルの要素の 2 つの既知の食い違いだけを定義に書く。

### 食い違いとみなすもの

| 種類 | 見るもの |
| --- | --- |
| 検証の誤り | 見本が記述の定義の JSON Schema（2020-12）に合わない。`anyOf`・`oneOf` は見本の型を受けるただ 1 つの枝で比べる |
| 定義に無いキー | 見本の辞書のキーが、その位置の定義の `properties` に無い（`additionalProperties`・`patternProperties` があればそれに従う） |
| 足されたキー | 定義の `properties` のキーが、見本のキーと `absent_keys` のどちらにも無い |
| 消えたキー | `absent_keys` のキーが定義から消えた |
| 既知の食い違いが消えた | `extra_keys` のキーか `known_mismatches` の説明が、もう食い違わない |
| 参照・ポインタ・見本の欠け | 文書の中の参照が解けない、ポインタの先が無い、見本のファイルが無い |

「定義に無いキー」と「足されたキー」は JSON Schema の検証とは別の判定である。GitHub の記述は `additionalProperties` を書かないため、検証だけでは
どちらも通る。

### 取得

- 1 つの URL を 1 回だけ取り、同じ URL を指す見本で使い回す。打ち直さない。転送は追わない（3xx は取れないものとして扱う）
- 上限は `plugins/ndf/contracts/limits/samples.fetch.json` の `seconds`・`retries`（0）・`max_bytes`（64 MiB）・`peers`（相手ごとの要求の数）。
  最悪の要求の数（異なる URL の数 ×（1 + `retries`））が `peers` の上限を超える定義と、`peers` に無い相手の見本は、1 回も取らずに止まる
- 記述は OpenAPI 3.1（`openapi` が `3.1.` で始まるもの）だけを受ける。参照は文書の中だけで解き、外の URL を取らない

### `ndf samples verify [--peer <相手>]`

| 終わり方 | 終了コード | 停止理由 |
| --- | ---: | --- |
| 食い違い 0 件・取れない見本 0 件 | 0 | — |
| 食い違い 1 件以上（取れない見本があってもこちら。`items` に両方を載せる） | 1 | `samples_mismatch` |
| 記述が OpenAPI 3.1 でない | 2 | `config_broken` |
| 要求の数が上限に収まらない | 3 | `precondition` |
| 食い違い 0 件で、取れない見本が 1 件以上 | 75 | `infra_wait` |

`items` は食い違い 1 つにつき 1 件で、`sample`・`peer`・`kind`（`mismatch`・`unreachable`）・`detail` を持つ。`metrics` は `samples`（比べた数）と
`calls`（相手ごとに送った要求の数）を持つ。

### 毎日のジョブ

| ジョブ | 権限 | すること |
| --- | --- | --- |
| `live-samples` | `contents: read` | `main` を取り出し、`ndf samples verify --peer github` を打って結果 JSON を成果物 `live-samples` に残す。食い違いがあれば成果物の ID を出力 `mismatch-artifact-id` へ渡す |
| `report` | `contents: read`・`issues: write` | ランタイムのスモークが失敗したか、見本に食い違いがある日だけ起動する。`main` の `scripts/runtime-smoke-latest.py report` が、失敗したランタイムごと・食い違った相手ごとに課題を作るか、同じ題の開いている課題へコメントを足す |

- `report` は、課題への書き込みの権限を持つただ 1 つのジョブである。任意の `ref` か `floors_ref` を試した手の起動では動かない
  （そのコードが成果物と出力を書けるため）
- `report` が成果物から題と本文へコピーするのは、決めた形に合う値だけである。相手の名前・見本のパス・1 行の説明（500 文字まで）を確かめ、
  食い違いは 50 件まで並べる
- 取れないだけの日は課題を作らない。ジョブは失敗のまま終わり、GitHub の失敗の通知で気づく
- 課題を自動では終わらせない。直す Pull Request が終わらせる

## 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | テストダブルは見本のファイルだけから応答を組み立てる | `tests/support/test_sample_hub.py` が落ちる |
| I2 | 出力に、取得した記述の本文・見本の値・見本の側のキーの名前・URL の認証情報を出さない（位置は定義の語で書き、ほかは `*` か `(文書の外)` に伏せる） | `tests/domains/samples/test_verify.py` が落ちる |
| I3 | 1 回の実行で相手へ送る要求は `peers` の上限を超えない | 1 回も送らずに `precondition` で止まる |

## 運用

- 食い違いの課題が来たら、見本を録り直し、定義の `absent_keys` を合わせ、`ndf samples verify --peer <相手>` が 0 で終わることを確かめる
- 記述の URL が転送を返したら、定義の URL を人が直す
- 見本を足すときは、定義 1 ファイルと見本 1 ファイルを足す。同じ記述を指すなら要求の数は増えない

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| 記述を固定して見本だけ、見本を固定して記述だけを変えたときの食い違い、値とキーの名前を出さないこと | `plugins/ndf/tests/domains/samples/test_verify.py` |
| 終了コードと停止理由、取れない見本と食い違いが同時に起きた結果 | `plugins/ndf/tests/domains/samples/test_cli.py` |
| 本文の上限と転送を追わない取得 | `plugins/ndf/tests/adapters/test_notify.py` |
| 見本からの応答の組み立て | `plugins/ndf/tests/support/test_sample_hub.py` |
| 報告のジョブの権限・値の確かめ | `scripts/tests/test_runtime_smoke_latest.py` |

## 関連リンク

- [issue #3687](https://github.com/devbasex/ai-plugins/issues/3687)
- [github/rest-api-description](https://github.com/github/rest-api-description) — GitHub の REST の公開の記述
- [runtime-smoke-cli-versions.md](runtime-smoke-cli-versions.md) — 同じワークフローのスモークと `--help` の突き合わせ
- [ndf-split-tables-toolchain-and-budget.md](ndf-split-tables-toolchain-and-budget.md) — 設定と件数の上限
