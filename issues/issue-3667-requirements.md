## 何が起きたか

2026-11-16 に利用者が並列の計画 p6 のオーガナイザーへ「ndf-next で定数ってどれぐらい残ってる？『定数よりデータ』っていう原則は守られてる？」と問い、オーガナイザーが NDF のコード（今の `plugins/ndf/src/`）を AST で数えた。#1142 の要求（`issues/issue-1142-requirements.md` 216 行）は「定数からデータへ」を決めている。中身は、同じ事実を複数の定数に持たせて一致をテストで縛る作りをやめ、事実を 1 か所のデータ（宣言・契約の JSON・表）に置き、読む側はそこから引くことである。数えた結果、この原則が守られていない形が 2 つ残り、NDF 自身の検査（規則 `const-fact`・`fact-table`）がその範囲を見ていないので、2 つともすり抜けていると分かった。

## 種類

機能追加（「定数からデータへ」の原則はあるが、スキーマの `enum` と上限の設定を照合する検査と、値の置き場の基準は今の SSOT に無い）

## 今の姿

値の置き場は、2026-11-16 に利用者と決めた次の基準で決める（構文ではなく値の種類で決める）。

| 種類 | 例 | 置き場 |
| --- | --- | --- |
| 事実・方針 | 工程・状態の種類・閾値・上限 | **データ**（契約の JSON・上限の設定・`.ndf/` の宣言）。コードは読むだけ |
| 語の集まり | `"current"`・`"trial"` などの状態や種類の名前 | **`StrEnum`**。値がスキーマか事実の表にあれば、そこから組み立てる |
| 外部の仕様・実装の都合 | HTTP の 405・curl の終了コード 28・出力を読む正規表現・ポーリングの間隔 | **定数のまま**。使うモジュールかアダプターの中に置く。設定にしない |

この基準に照らした今の状態は次のとおりである（2026-11-23、`main` 33670cad1 で数え直した）。

| 項目 | 今の数と実例 | 数え方 |
| --- | --- | --- |
| スキーマの `enum` を写した定数 | 4 個。`KIND_LABELS`（`src/ndf/adapters/notify.py:114`）・`MVV_PACES`（`src/ndf/progress/model.py:27`）・`SIZES`（`src/ndf/domains/gws/thumbnails.py:30`）・`INTERFACE_VALUES`（`src/ndf/domains/materials/collect.py:32`） | 各ファイルを読んで位置を確かめた |
| 一致を縛るテスト | 2 本。`tests/adapters/test_notify_sender.py:134`・`tests/contracts/test_chosen.py:54` | 同上 |
| 名前が上限・回数・待ちを表す数の定数 | 53 個 | `src/` を AST で読み、最上位の大文字の名前への数の代入のうち、名前に `MAX`・`LIMIT`・`TIMEOUT`・`WAIT`・`RETRY`・`DAYS`・`SECONDS`・`INTERVAL`・`ATTEMPT`・`GRACE`・`KEEP`・`BUDGET`・`CAP` を含むものを数えた |
| `StrEnum` | 15 クラス（11 ファイル） | `grep -rn "class .*StrEnum" src/ndf` |
| `const-fact` の照合の相手 | 事実の表（`contracts/facts/` と `contracts/domains/`）の `id` だけ。辞書の定数はキーだけを読む（`src/ndf/checks/facts.py:47` の `lit` が `ast.Dict` のキーだけを集める）。スキーマの `enum` と上限の設定の値は照合しない | `src/ndf/checks/facts.py` を読んだ |

`WORK_TIMEOUT`（3,600 秒、`src/ndf/core/worker.py:79`）と上限の設定の `supervise.worker-step`（1,800 秒）の食い違いは、同じ事実の不一致ではなく**上限の設定の方に読み手が無い**ことによる（`git grep -n "worker-step" plugins/ndf/src` が 0 件）。worker の上限は `WORK_TIMEOUT` だけが決めている。

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| スキーマの `enum` の写し | 4 個の定数がスキーマの `enum` と同じ文字列の並びを持ち、2 本のテストが一致を縛る | 定数はスキーマ（か事実の表）から組み立て、一致を縛るテストは無い |
| 方針の閾値 | 20 個前後がコードに直に書かれる | `contracts/limits.json` の項目になり、コードは上限の設定から読む |
| `const-fact` の照合 | 事実の表の `id` だけ・辞書はキーだけ | 手書きのスキーマの `enum`・上限の設定の数・辞書の値も照合する |
| 値の置き場の基準 | 書かれていない | 事実・方針はデータ、語の集まりは `StrEnum`、外部の仕様と実装の都合は定数、と開発の規約の 1 か所に書かれる |
| 語の集まり | 文字列の定数のまま | `StrEnum`。値がスキーマか事実の表にあればそこから組み立てる |

## なぜ困るか

- スキーマと定数に同じ事実があると、片方だけを直したときに食い違い、一致を縛るテストが落ちるまで気づけない。テストは事実を 1 か所にする代わりにならない（MVV の Value 8・11）
- 閾値がコードに散ると、上限を変えるときに直す場所を探し回り、上限の設定と食い違う値が残る（例: `WORK_TIMEOUT` の 3,600 秒と上限の設定の `supervise.worker-step` の 1,800 秒）
- 検査が見ない範囲の重複は、AI が足しても誰も気づかない

## 目的

- NDF のコードに、スキーマと上限の設定の写しと方針の閾値が残らず、残れば `const-fact` が指摘する状態にする

## 対象範囲

含む:
- 値の置き場の基準を、NDF の開発の規約（Skill の `refactoring` の「分岐をデータ化」か、`plugins/ndf/` の開発ガイド）の 1 か所に書く
- 語の集まりの文字列の定数を `StrEnum` へ置き換える。値がスキーマか事実の表にあるものは、そこから組み立てる
- スキーマの `enum` を写した 4 個の定数を、スキーマ（か事実の表）から読む形へ変え、一致を縛るテスト 2 本を消す
- 方針の閾値を `contracts/limits.json` へ移し、コードはそこから読む。`WORK_TIMEOUT` と `supervise.worker-step` が同じ事実かを確かめ、同じなら 1 つにする
- `const-fact` の照合の相手に、手書きのスキーマの `enum`・上限の設定の数・辞書の値を加える

含まない:
- 外部の仕様の値と実装の都合の値（ポーリングの間隔など）
- 生成されるスキーマ（`*.args.schema.json`）と一致する定数
- 10 系のコード（`plugins/ndf/scripts/`。11.0 の入れ替えで置き換わった）

## 前提

- 前提 1: 値の置き場の基準（今の姿の「4. 置き場の基準」）は、2026-11-16 に利用者と決めた方向として起票者が書いたものであり、この要求はその基準に従う

## 受け入れ条件

- [ ] 1. スキーマの `enum` を写した定数（11 系で残る 4 個: `KIND_LABELS`・`MVV_PACES`・`SIZES`・`INTERFACE_VALUES`）がコードの文字列の並びを持たず、スキーマか事実の表から読む
- [ ] 2. `tests/adapters/test_notify_sender.py` と `tests/contracts/test_chosen.py` の、定数とスキーマの一致を縛るアサーションが無い
- [ ] 3. 方針の閾値と分けた定数が `contracts/limits.json` の項目になり、コードは上限の設定から読む。どれを方針の閾値と分けたかの一覧を PR の本文に載せる
- [ ] 4. `const-fact` が、手書きのスキーマの `enum` と同じ集まり（か部分集合）の定数を指摘する。合成した最小のスキーマと定数で、指摘が出るときと出ないときの両方をテストで確かめる
- [ ] 5. `const-fact` が、上限の設定の値と同じ数で、名前が上限・回数・待ちを表す定数を指摘する（テスト）
- [ ] 6. `const-fact` が、辞書の定数の値の集まりも照合する（テスト）
- [ ] 7. 値の置き場の基準（事実・方針はデータ、語の集まりは `StrEnum`、外部の仕様と実装の都合は定数）が NDF の開発の規約に 1 か所で書かれ、#3664 の詳細設計の雛形がそこを指す
- [ ] 8. 同じ語の集まりを表す文字列の定数（例: `src/ndf/domains/relay/context_thresholds.py` の `REASONS`・`CONTINUE`・`DENY`・`RESTART`）が `StrEnum` になる。置き換えた一覧を PR の本文に載せる
- [ ] 9. 検査を広げた後に `ndf check` が `plugins/ndf/` で違反 0 件

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（閾値の値は同じまま置き場が変わる。`WORK_TIMEOUT` を 1 つにするときは値が変わりうる） |
| データ | `contracts/limits.json` に閾値の項目が増える |
| 既存の振る舞い | `const-fact` の指摘が増える。閾値を変えるときに触るのが上限の設定の 1 か所になる。語の集まりが `StrEnum` になり、打ち間違いを型で止められる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf -q -n 4` |
| 静的解析・型検査 | `ndf check --only const-fact --only fact-table` と `ndf check`（`plugins/ndf/` で違反 0 件） |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `const-fact` が照合の相手を事実の表の `id` に絞ったのは、スキーマの `enum` と上限の設定が事実の表の形をしていないからである（確かめていない） | 本文の「起票者の推定」 | 採らない（原因の推定で、条件ではない。受け入れ条件 4〜6 は推定によらず照合を広げる） |
| S2 | 閾値をコードに残したのは、上限の設定に置く基準が書かれていないからである（確かめていない） | 本文の「起票者の推定」 | 受け入れ条件 7（基準を書く） |
| S3 | 語の集まりの文字列の定数を `StrEnum` にする | 本文の 4 の表（利用者と決めた方向として起票者が書いた） | 受け入れ条件 8 |
| S4 | 方針の閾値は 20 個前後で、どれが方針かは目で分けた推定である | 本文の「2. 方針の閾値がコードにある」 | 受け入れ条件 3（分けた一覧を PR に載せる） |

## リクエスト（原文）

> ndf-next で定数ってどれぐらい残ってる？『定数よりデータ』っていう原則は守られてる？

（書き手: 利用者、日付: 2026-11-16。起票者の本文の「由来」が引用したもの）

<details><summary>起票した時点の本文（2026-11-19 の棚卸しで直したもの）</summary>

> 2026-11-19 の棚卸しで 11 系の箇所へ書き直した。
>
> ## 目的
>
> #1142 の要求（`issues/issue-1142-requirements.md` 216 行）は「定数からデータへ」を決めている。中身は、同じ事実を複数の定数に持たせて一致をテストで縛る作りをやめ、事実を 1 か所のデータ（宣言・契約の JSON・表）に置き、読む側はそこから引くことである。
>
> 11 系の NDF（`plugins/ndf/`）の `src/` で、この原則が守られていない形が 2 つ残っている。NDF 自身の検査（規則 `const-fact`・`fact-table`）がその範囲を見ていないので、2 つともすり抜けている。この 2 つの形を無くし、検査の照合の範囲を広げる。
>
> **守られている点（2026-11-16 時点）:**
>
> - 最上位の定数は 1,326 個（274 ファイル、1,000 行あたり 22.6 個）。今の `plugins/ndf/scripts/` は 3,275 個で、1,000 行あたり 32.8 個
> - `ndf check --only const-fact --only fact-table` は、1,099 ファイルで違反 0 件
> - 同じ文字列を 2 つ以上のファイルで定数にしている組は 0 件
>
> ### 1. スキーマの `enum` をコードの定数に写している（6 個）
>
> 手書きのスキーマの `enum` と同じ値の並びを、コードの定数にも持っている。
>
> | 定数 | スキーマ |
> | --- | --- |
> | `src/ndf/adapters/notify.py:114` `KIND_LABELS`（キー） | `contracts/schemas/notice.schema.json` の `properties.kind` |
> | ~~`src/ndf/domains/release/declaration.py:33` `STAGES`~~ → 11 系の `src/ndf/domains/release/ndf_settings.py:40` はスキーマの `enum` から読む（`STAGES = tuple(_STEP_SCHEMA["stage"]["enum"])`）。対象から外れた | — |
> | ~~`src/ndf/domains/worktree/declaration.py:47` `START_CHANNELS`~~ → 11 系に無い（`git grep -n START_CHANNELS -- plugins/ndf/src` → 0 件） | — |
> | `src/ndf/progress/model.py:27` `MVV_PACES`（`domains/loop/sprint_decide.py:18` が import する） | `contracts/workflow/context.schema.json` の `properties.pace`（部分集合） |
> | `src/ndf/domains/gws/thumbnails.py:30` `SIZES` | `contracts/schemas/io/gws.result.schema.json` の `metrics.size` |
> | `src/ndf/domains/materials/collect.py:32` `INTERFACE_VALUES` | `contracts/schemas/io/materials.result.schema.json` の `public_interface.value`（部分集合） |
>
> このうち 2 個は、原則がやめると決めた「一致をテストで縛る」形そのものである。
>
> - `tests/adapters/test_notify_sender.py:134`（`set(notify.KIND_LABELS) == set(schema[...]["enum"])`）
> - `tests/contracts/test_chosen.py:54`（`tuple(schema[...]["enum"]) == chosen.CHOOSERS`）
>
> 引数のスキーマ（`*.args.schema.json`）と一致する定数も 5 個あった。こちらは `ndf check --write-spec` がコードから生成するので、重複に当たらない。
>
> ### 2. 方針の閾値がコードにある
>
> 上限・回数・待ちを表す名前の数の定数は 62 個ある。そのうち 20 個前後が方針の閾値で、`contracts/limits.json`（値 502 個）に置かずコードに直接書いている（目で分けた推定）。例:
>
> | 定数 | 値 | 何の閾値か |
> | --- | ---: | --- |
> | `src/ndf/domains/crossreview/drive.py:38` `DEFAULT_MAX_ROUNDS` | 3 | cross-review のラウンドの上限 |
> | `src/ndf/domains/crossrefactor/flaky_decide.py:21` `MAX_ATTEMPTS` | 2 | フレーキーなテストの打ち直し |
> | `src/ndf/core/worker.py:79` `WORK_TIMEOUT` | 3,600 | worker の上限 |
> | `src/ndf/core/plan/runner.py:53` `ANSWER_WAIT_SECONDS` | 86,400 | 答えの待ち |
> | `src/ndf/core/plan/model.py:27` `LIMIT_PER_STEP` | 3 | ステップごとの上限 |
> | `src/ndf/core/durable_sweep.py:27` `KEEP_DAYS`・`src/ndf/testing/records.py:17` `KEEP_DAYS` | 14・30 | 記録の保存日数 |
>
> - 残りの 40 個前後は外部の仕様の値（HTTP の 405・curl の終了コード 28）と、実装の都合（ポーリングの間隔・表示の行数）である
> - `WORK_TIMEOUT` の 3,600 秒は、`limits.json` の `supervise.worker-step`（worker のステップの上限の既定、1,800 秒）と食い違って見える。同じ事実を指すかは確かめていない
>
> ### 3. 検査が見ない範囲
>
> `src/ndf/checks/facts.py` の `const-fact` は、次の 4 つを見ていない。上の 1 と 2 はここをすり抜けている。
>
> - 照合の相手は事実の表（`contracts/facts/` の 2 本と `contracts/domains/` の 18 本）の `id` だけ。スキーマの `enum` と `limits.json` を見ない
> - 辞書の定数はキーだけを見て、値を見ない（`lit` の `ast.Dict` の扱い）
> - 同じファイルの中の重複を見ない（どの組も「2 つ以上のファイルにまたがる」ものだけ）
> - 入れ子の表は読まない（`lit` が `None` を返す）。コードの中の表（辞書と入れ子）は 166 個あり、事実の表を写したものが混ざっているかは 1 つずつは確かめていない
>
> ### 4. 置き場の基準（利用者と決めた方向、2026-11-16）
>
> 値の置き場は、モジュールの定数かクラスの属性かという構文ではなく、**値の種類**で決める。
>
> | 種類 | 例 | 置き場 |
> | --- | --- | --- |
> | 事実・方針 | 工程・状態の種類・閾値・上限 | **データ**（契約の JSON・`limits.json`・`.ndf/` の宣言）。コードは読むだけ |
> | 語の集まり | `"current"`・`"trial"` などの状態や種類の名前（文字列の定数 552 個の多く） | **`StrEnum`**。値がスキーマか事実の表にあれば、そこから組み立てる（今の Enum は 16 個） |
> | 外部の仕様・実装の都合 | HTTP の 405・curl の終了コード 28・出力を読む正規表現・ポーリングの間隔 | **定数のまま**。使うモジュールかアダプターの中に置く。設定にしない |
>
> この基準を採る理由は次の 3 つ。
>
> - **定数をクラスやメソッドのフィールドへ移しても、事実がコードにあることは変わらない。** メソッドの中の値は `const-fact` が見ないので、重複が見えなくなる
> - **設定は、利用者が書き換えてよい公開の契約である。** スキーマ・検証・文書・移行が要る。変えると壊れる外部の仕様の値まで出すと、「変えてよい」という誤った印になる
> - **ndf-next の作りはデータ＋関数である。** モジュールの関数は 3,753 個、クラスは 542 個で、クラスの多くは dataclass（219 個）や NamedTuple・TypedDict などの値の器である
>
> ## 対象範囲
>
> - 含む:
>   - 4 の基準を、NDF の開発の規約（Skill の `refactoring` の「分岐をデータ化」か、`plugins/ndf/` の開発ガイド）に書く
>   - 語の集まりの文字列の定数を `StrEnum` へ置き換える。値がスキーマか事実の表にあるものは、そこから組み立てる
>   - 1 の 6 個の定数を、スキーマ（か事実の表）から読む形へ変える。一致を縛るテスト 2 本を消す
>   - 2 の方針の閾値を `contracts/limits.json` へ移し、コードはそこから読む。`WORK_TIMEOUT` と `supervise.worker-step` が同じ事実かを確かめ、同じなら 1 つにする
>   - 3 の `const-fact` の照合の相手に、手書きのスキーマの `enum`・`limits.json` の数・辞書の値を加える
> - 含まない:
>   - 外部の仕様の値と実装の都合の値（ポーリングの間隔など）
>   - 生成されるスキーマ（`*.args.schema.json`）と一致する定数
>   - 今の `plugins/ndf/`（11.0 の入れ替えで置き換わる）
>
> ## 受け入れ条件
>
> - [ ] 1 の定数（11 系で残る 4 個: `KIND_LABELS`・`MVV_PACES`・`SIZES`・`INTERFACE_VALUES`）がコードの文字列の並びを持たず、スキーマか事実の表から読む
> - [ ] `tests/adapters/test_notify_sender.py:134` と `tests/contracts/test_chosen.py:54` の、定数とスキーマの一致を縛るアサーションが無い
> - [ ] 2 で方針の閾値と分けた定数が `contracts/limits.json` の項目になり、コードは `limits.json` から読む。どれを方針の閾値と分けたかの一覧を PR の本文に載せる
> - [ ] `const-fact` が、手書きのスキーマの `enum` と同じ集まり（か部分集合）の定数を指摘する。合成した最小のスキーマと定数で、指摘が出るときと出ないときの両方をテストで確かめる
> - [ ] `const-fact` が、`limits.json` の値と同じ数で、名前が上限・回数・待ちを表す定数を指摘する（テスト）
> - [ ] `const-fact` が、辞書の定数の値の集まりも照合する（テスト）
> - [ ] 4 の基準（事実・方針はデータ、語の集まりは `StrEnum`、外部の仕様と実装の都合は定数）が NDF の開発の規約に 1 か所で書かれ、#3664 の詳細設計の雛形がそこを指す
> - [ ] 同じ語の集まりを表す文字列の定数（例: `src/ndf/domains/relay/context_thresholds.py` の `REASONS`・`CONTINUE`・`DENY`・`RESTART`）が `StrEnum` になる。置き換えた一覧を PR の本文に載せる
> - [ ] 検査を広げた後に `ndf check` が `plugins/ndf/` で違反 0 件
>
> ## 影響
>
> ndf-next の 6 個の定数と 20 個前後の閾値の置き場が変わり、`const-fact` の指摘が増える。閾値を変えるときに触るのが `limits.json` の 1 か所になる。語の集まりが `StrEnum` になり、打ち間違いを型で止められる。
>
> ## 起票者の推定
>
> `const-fact` は「2 つ以上のファイルの定数に同じ事実を持つ」ことを止める規則として作られ、照合の相手を事実の表の `id` に絞った。スキーマの `enum` と `limits.json` は事実の表の形をしていないので、照合の外に置かれたと見る。閾値をコードに残したのは、`limits.json` に置く基準（どの値が方針で、どれが実装の都合か）が書かれていないからだと見る。どちらも確かめていない。
>
> ## 由来
>
> 並列の計画 p6 のオーガナイザーが調べた（2026-11-16）。利用者の問いは「ndf-next で定数ってどれぐらい残ってる？『定数よりデータ』っていう原則は守られてる？」。
>
> - 数え方: `src/` の各ファイルを AST で読み、モジュールの最上位の大文字の名前への代入を数えた。スキーマの `enum` との照合は、`contracts/` の JSON の文字列の `enum`（105 個）と、定数の文字列の集まりの一致・部分集合で見た
> - 関連する課題: #3664（詳細設計の「内部のデータと定数」の欄と、定数の検査）
>
> ## 11.0.0 の扱い（2026-11-16 の確かめ）
>
> **残す（11.x）。** ndf-next の作りを「定数からデータへ」の原則へ寄せ、`const-fact` の照合の範囲を広げる改善で、今のプラグインの機能の不具合ではない（受け付けの規則 `scripts/ndf-next-parity/README.md` の「作り直しの間に起票された不具合の受け付け」の手順 1）。
>
> - 11.0.0 の条件（#1142 の受け入れ条件の 7 行）のどれも、この Issue の振る舞いを求めない。本文のとおり `ndf check --only const-fact --only fact-table` は今の範囲で違反 0 件で、X1 の検査の受け入れ条件は満たしている
> - `WORK_TIMEOUT`（3,600 秒）と `limits.json` の `supervise.worker-step`（1,800 秒）の食い違いが同じ事実の不一致と分かれば、それは不具合として分けて 11.0.0 へ入れる
> - 行き先の子は無い。11.0.0 を出した後に、X1 の検査の改善（#3664 の詳細設計の雛形と一緒）として新しく計画する
>
> ## 11 系での確かめ（2026-11-19、main 2e43af7bc）
>
> 本文のパスは入れ替えの前の `plugins/ndf-next/` からの相対である。入れ替えの後は `plugins/ndf/` からの相対で同じ位置にある（下の表以外）。
>
> | 本文の定数 | 11 系 |
> | --- | --- |
> | `KIND_LABELS` | `plugins/ndf/src/ndf/adapters/notify.py:114`。テストの一致の縛りも残る（`plugins/ndf/tests/adapters/test_notify_sender.py:134`） |
> | `STAGES` | 直っている。`plugins/ndf/src/ndf/domains/release/ndf_settings.py:40` がスキーマから読む |
> | `START_CHANNELS` | 無い（`git -C /work/ai-plugins grep -n START_CHANNELS -- plugins/ndf/src` → 0 件） |
> | `MVV_PACES` | `plugins/ndf/src/ndf/progress/model.py:27` へ移った |
> | `SIZES`・`INTERFACE_VALUES` | `plugins/ndf/src/ndf/domains/gws/thumbnails.py:30`・`plugins/ndf/src/ndf/domains/materials/collect.py:32` |
> | `CHOOSERS` の縛り | `plugins/ndf/tests/contracts/test_chosen.py:54` に残る |
> | 閾値の例（`DEFAULT_MAX_ROUNDS`・`MAX_ATTEMPTS`・`WORK_TIMEOUT`・`ANSWER_WAIT_SECONDS`・`LIMIT_PER_STEP`・`KEEP_DAYS`） | すべて同じ値で残る。文脈の閾値（旧 `context_policy.py` の `CURRENT_LIMIT` ほか）は 11.0.21 で上限の設定（`contracts/limits.json` の `context.decide-from`・`context.limit`）へ移った（#3995）。`WORK_TIMEOUT`（3,600 秒、`plugins/ndf/src/ndf/core/worker.py:79`）と `limits.json` の `supervise.worker-step`（1,800 秒、174 行）の食い違いも残る |
>
> 最上位の定数の数（1,326 個）と `ndf check --only const-fact --only fact-table` の違反 0 件は、入れ替えの後に数え直していない（未確認）。
>
>

</details>

（書き手: 起票者（並列の計画 p6 のオーガナイザーの調査と 2026-11-19 の棚卸し）、日付: 2026-11-16・2026-11-19）

