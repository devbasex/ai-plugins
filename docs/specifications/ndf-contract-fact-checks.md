# コントラクトの値の検査: 使われない上限・コードに重ねて書いた値・増えた重複・対応先の無い指摘を `ndf check` が落とす

NDF は、上限の秒数・状態の種類・入出力の形などの値を JSON のデータ（`plugins/ndf/contracts/`。**コントラクト**と呼ぶ）に 1 か所だけ書き、
Python のコードはそこから読む。`ndf check` は CI で次の 4 つを調べ、見つかれば Pull Request を落とす。

| 見つけるもの | 規則 |
| --- | --- |
| 設定してあるのに、どのコードも使っていない上限 | `limit-readers` |
| コントラクトにある値を、コードにもう一度書いた定数 | `const-fact` |
| コピーで生まれた重複（同じ名前の関数・同じ文字列）の件数が、保存した基準と違う変更 | `copy-dup` |
| 過去の設計レビューの指摘のうち、どの作業で対応するかが書かれていないもの | `review-items` |

例: worker（NDF が 1 つの作業を任せて起動する AI のプロセス）の実行時間の上限 `supervise.worker-run`（3,600 秒）は `ndf.core.worker` が使う。この上限のファイルに、使うモジュールとして
誤って `ndf.core.durable` を書くと、`ndf check` は次の 2 件を出して終了コード 1 で終わる。
出力の「方針」は上限の 1 項目、「読み手」はその上限を使うモジュール、「引く」は値を読むことを指す。

```json
[{"rule": "limit-readers", "path": "plugins/ndf/contracts/limits/supervise.worker-run.json", "line": 4, "detail": "unread: 方針 supervise.worker-run の読み手 ndf.core.durable がこの方針を引いていない"},
 {"rule": "limit-readers", "path": "plugins/ndf/src/ndf/core/worker.py", "line": 270, "detail": "undeclared: 方針 supervise.worker-run を引く ndf.core.worker が方針の read_by に無い"}]
```

規則ごとの数え方・検査するファイル・終了コードは [`plugins/ndf/src/ndf/checks/README.md`](../../plugins/ndf/src/ndf/checks/README.md) にある。
この文書は、データの形、常に成り立つ条件、設計の理由、テスト観点を扱う。記録のキーの名前を調べる規則 `internal-names` は
[ndf-internal-data-versions.md](ndf-internal-data-versions.md) にある。

## コンテキスト

この文書の用語は、用語集の区分 `ndf-code-structure`（NDF のコードの構造）のものを使う。検査するのは `plugins/ndf/` だけで、NDF を導入したリポジトリでは働かない。

## 用語

用語集にある語の定義は [用語集](../glossary.md) が正である。この文書では次の意味で使う。

| 語 | 意味 |
| --- | --- |
| 上限の設定 | タイムアウト・再試行の回数・待ち時間などを集めたデータ（`contracts/limits/`） |
| 方針 | 上限の設定の 1 項目。`contracts/limits/` の 1 ファイルで、ファイル名が方針の名前になる |
| 読み手 | 方針の値を使うモジュール |
| ラチェット | 件数が保存した基準と違う変更を落とす検査。増えたら減らし、減ったら基準を下げると通る |
| 値を取り出す関数 | 方針の名前を受け取って値を返す関数（`ndf.contracts.limits` の `seconds`・`value` など） |
| 対応先 | 設計レビューの指摘に対応する作業（`routed_to`） |

## 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| `contracts/limits/<方針の名前>.json` の `read_by` | その方針を使うモジュールの名前を持つ（用語集の「読み手の指定」） |
| `src/ndf/checks/limits_readers.py` | `read_by` と、コードを解析して求めた実際の読み手を比べる |
| `src/ndf/checks/facts.py` | コントラクトの値と同じ値を持つ定数を見つける |
| `src/ndf/checks/copy_dup.py` | 複数のファイルにある同じ名前の関数と同じ文字列を数える |
| `src/ndf/checks/ratchet.py`・`ratchet.json` | 重複の件数を、保存してある基準の件数と比べる |
| `src/ndf/checks/spec_render.py`・`spec_docs.py` | 指摘の一覧表から `children.json` の `review_items` を生成し、生成し直した内容との差と対応先の無い指摘を見つける |
| `ndf.contracts.load_schema`・`schema_enum` | コードの定数の値を JSON Schema から読む。1 つのプロセスで同じスキーマを読むのは 1 回だけ |
| `src/ndf/checks/rules.json` | 規則のしきい値と、検査から除く値とその理由 |

## 理由

| 困っていたこと | 今の形 |
| --- | --- |
| 使われていない上限を見分けられなかった。例: `supervise.worker-step`（1,800 秒）は使われず、worker の上限はコードの定数（3,600 秒）で決まっていた | 方針は、使うモジュールを `read_by` に必ず書く。使うモジュールが無い方針は置けない |
| JSON Schema の `enum` や上限の数と同じ値が、コードの定数にも書かれていた | `const-fact` が、コードの定数をスキーマの `enum`・上限の数とも比べる。辞書の定数は、キーに加えて値の集まりも比べる |
| AI が書きがちな問題のある作り 7 種類（一覧は [ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md) の「兆候の規則」）のうち、コピーによる重複だけ検査が無かった | 規則 `copy-dup` を足し、件数が保存した基準と違う変更を落とす |
| 設計レビューの指摘の一覧表に対応先を書き忘れても、気づく手段が無かった | 規則 `review-items` |

## 仕様

### 上限を使うモジュールの指定（`read_by`）

| 項目 | 型 | 必須 | 意味 |
| --- | --- | --- | --- |
| `read_by` | 文字列（`^ndf(\.[a-z_][a-z0-9_]*)+$`）の並び。1 つ以上・重複なし | 必須（`contracts/schemas/limits.schema.json`） | その方針を使うモジュールの名前。検査だけが読み、コードからは値として取り出せない |

| 違反の種類 | 意味 |
| --- | --- |
| `unread` | `read_by` に書いたモジュールが、その方針を使っていない |
| `undeclared` | `read_by` に無いモジュールが、その方針を使っている |
| `unresolved` | どの方針を使うかを解析できない。名前を実行時に組み立てる箇所も、下の `dynamic` に書いていなければこれになる |

文字列の検索では調べない。`hook.budget.` に変数をつないで名前を作るコードがあり、検索では使われている方針も「使われていない」と出るためである。
代わりにコードの構文木を解析する。解析に要る情報は `rules.json` の `limits` に書く。

| キー | 書くもの |
| --- | --- |
| `readers` | 値を取り出す関数と、方針の名前を受け取る引数 |
| `dynamic` | 名前を実行時に組み立てる箇所。組み立てる式と、名前に入る値の一覧があるコントラクトのファイルとその中の位置（JSON Pointer）を書く。その一覧から作れる名前だけを「使っている」と数える |
| `tables`・`whole` | 方針の全件を返す関数と、全件を受け取るだけで値を使わないモジュール |

hook（Claude Code と Codex が決まった時点で呼ぶ NDF の処理）の実行時間の予算は、`contracts/hooks/runtimes.json` のイベントごとの `budget` の値を使って `hook.budget.<budget の値>` の方針を読む。
ランタイムによって予算が違うイベントは、違う値（Claude Code は `stop`、Codex は `stop.codex`）を持つ。

### コントラクトと同じ値を持つ定数（`const-fact`）

| 違反の種類 | 見つけるもの |
| --- | --- |
| `enum-set`・`enum-subset` | 手書きのスキーマの `enum`（文字列が 2 つ以上）と同じ集まり、またはその一部を持つ定数。コードの引数の定義から生成したスキーマ（`facts.generated_schemas`。`schemas/io/*.args.schema.json`）は、元がコードなので比べない |
| `limit-num` | 名前が上限・回数・待ちを表し、上限の設定に同じ数がある定数 |
| `stale` | 検査から除く一覧（`facts.limit_constants`）にあるのに、その定数が無いか、同じ数の方針が無くなった行 |

ほかの種類は [ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md) の「事実の重複の規則」にある。コードの辞書の定数は、
キーの集まりと値の集まりの両方を、ほかのファイルの定数・表・スキーマと比べる。値の集まりの違反は `<定数の名前>.values` と表示する。

`limit-num` は、定数の名前に含まれる語によって、方針のどの値と比べるかを決める（`facts.limit_kinds`）。ほかの値とは比べない。

| 定数の名前に含まれる語 | 比べる方針の値 |
| --- | --- |
| `TIMEOUT`・`WAIT`・`SECONDS`・`GRACE` | `seconds`・`timeout`・`deadline` |
| `INTERVAL` | `interval`・`max_interval` |
| `MAX`・`LIMIT`・`CAP`・`KEEP`・`BUDGET`・`DAYS` | `count` |
| `RETRY`・`ATTEMPT` | `attempts` |

偶然同じ数になった定数や、外部の仕様で決まる定数は、`facts.limit_constants` に 1 つずつ書いて除く。行は `name`（`<モジュール>.<定数>`）・
`kind`（`external` は外部の仕様で決まる値、`implementation` は実装の都合で決めた値）・`reason`（空にできない）を持つ。形が違えば検査は
`config_broken` で止まる。名前のパターンや値の範囲でまとめて除くことはできない。

### 見つかった定数の直し方

値をどこに置くかの基準は、[`refactoring` の「分岐をデータ化」](../../plugins/ndf/skills/refactoring/SKILL.md#分岐をデータ化) にある。

| 値 | 直し方 | 例 |
| --- | --- | --- |
| スキーマの `enum` にある語の一覧 | `contracts.schema_enum(<スキーマ>, <JSON Pointer>)` で読む。列挙型が要れば、読んだ値から `StrEnum` を作る | `contracts/chosen.py` の `Chooser` |
| スキーマの `enum` の一部 | スキーマの `$defs` に分けて書き、その `enum` を読む | `context.schema.json` の `$defs/mvv_pace` を読む `progress/model.py` の `MVV_PACES` |
| 値と表示名の組 | スキーマの `oneOf` に `const`（値）と `title`（表示名）を並べ、そこから読む。`title` が無ければ値を表示する | `notice.schema.json` の `kind` を読む `adapters/notify.py` の `KIND_LABELS` |
| 複数のモジュールが使う語と、その対応 | 表（`contracts/domains/`）に置き、そこから `StrEnum` を作る | `ci-verdicts`・`claude-failures`・`approval-states` |
| 1 つのモジュールだけが使う語の一覧 | `StrEnum` | `relay/context_thresholds.py` の `Verdict`・`Reason` |
| 運用で決める上限・回数・待ち時間 | 上限の設定の方針にする | `supervise.worker-run` を読む `core/worker.py` の `work_timeout` |

`pace` は開発の進め方で、`fast` と `auto` では、設計とリリースの承認ゲートに限り、プロジェクトの判断基準（MVV: Mission・Vision・Value）による判定で承認を省けることがある。
省くには、承認済みの MVV とそのハッシュの一致、レッドライン（人の承認を省いてはいけない変更）に触れないこと、判定の記録などの条件がすべて要る。条件の正本は
[`pace.md` の「MVV 判定」](../../plugins/ndf/skills/development-workflow/references/pace.md#mvv-判定) である。`MVV_PACES` は `fast` と `auto` の 2 つだけを持つ。
`normal` を含む `pace` の全体から作ると、通常の進め方でも承認を省いてしまう。

### コピーで増えた重複（`copy-dup`）

次の 2 つを数える。今のコードには重複が残っていて 0 件にできないので、件数を `ratchet.json` に保存した基準と比べる。

- 2 つ以上のファイルの最上位にある、同じ名前の関数（`check`・`main` は除く。`copy_dup.allow_names`）
- 4 つ以上のファイルに出る、8〜80 字の同じ文字列（docstring は除く）

| 件数と基準 | 結果 |
| --- | --- |
| 同じ | 通る |
| 件数が多い（重複を増やした） | 落ちる |
| 件数が少ない（重複を減らした） | 落ちる。`ndf check --write-spec` が基準を今の件数へ下げるので、変わった `ratchet.json` も同じ Pull Request に含める |
| 基準そのものを、変更前より大きくした | 落ちる。CI は `--ratchet-base HEAD^1` を渡して変更前の基準を読む |

`ratchet.json` は、規則ごとに基準の件数（`max`）・測った日（`measured`）・そのときのコミット（`commit`）を持つ。件数を基準と比べるのは `copy-dup`
だけで、ほかの規則は 1 件でも違反があれば落ちる。

### 設計レビューの指摘の対応先（`review-items`）

NDF を 11.0 へ作り直したときの設計レビューの指摘 182 件について、一覧表 [issue-1142-design-review-routing.md](../../issues/issue-1142-design-review-routing.md)
が、どの作業で対応するかを記録している。`ndf check --write-spec` は、この表から、作り直しの作業の一覧 `src/ndf/checks/children.json` の
`review_items` を生成する（作り直しを分けた 1 件ずつの作業を「子」と呼ぶ）。

| キー | 中身 |
| --- | --- |
| `id` | 指摘の番号 |
| `text_sha` | 表の「要旨」の sha256 の先頭 12 文字。要旨が書き換わったかを見分ける |
| `routed_to` | 対応する作業の並び。先頭の作業が責任を持つ |
| `order` | 表の「順番」。対応する作業に着手する順番。無ければ `null` |
| `kind` | 表の「新設か追補か」。新しい作業・完了した作業への追加・着手前の作業への追加などの区分 |
| `requirement_line` | 表の「要求の行」。指摘の根拠になる要求仕様の行番号。無ければ `null` |
| `release` | 表の「版」。`11.0.0` か、その後の `11.x` |

次を違反にする。

- 対応先が空の指摘
- 11.0 より後に回した指摘（版が `11.x`）のうち、もう 1 つの表（[issue-3388-design-routing.md](../../issues/issue-3388-design-routing.md)）に
  行が無いか、その行の対応先が `Z<数>`（作業の記号）・`#<Issue 番号>`・`作らない` のどれでもないもの
- 表から生成し直した内容と違う `children.json`

2 つの表か `children.json` のパスがリポジトリの外に出るか、途中にシンボリックリンクがあるときは、読み書きせずに `config_broken` で止まる。

### 結果 JSON の `items` の欄

サブコマンドが出す結果 JSON の `items` の要素は、どれも任意の次の欄を持てる（`contracts/schemas/result.schema.json` の `$defs/item`）。
ほかの欄を持っていてもよい。

| 欄 | 型 | 意味 |
| --- | --- | --- |
| `lens` | 文字列（1 字以上） | 項目を見つけた観点 |
| `category` | 文字列（1 字以上） | 分類 |
| `severity` | 重大度の表（`contracts/domains/severities.json`）の `id` | 重大度。値が表にあるかは `result.errors` が確かめる |
| `acceptance` | 文字列の並び | 関わる受け入れ条件（`<Issue 番号>:<条件の番号>`） |

### CI で打つコマンド

| Pull Request が変えたファイル | 打つコマンド |
| --- | --- |
| NDF の検査が読むファイル（`NDF_PATHS`） | `ndf check --ratchet-base HEAD^1` |
| `NDF_PATHS` に含まれない、指摘の一覧表（`REVIEW_ITEMS_PATHS`） | `ndf check --only review-items` |
| `NDF_PATHS` に含まれない、ワークフロー（`TOOLCHAIN_PATHS`） | `ndf check --only toolchain --only budget-ci-jobs`（Python と uv のバージョンの指定と、CI のジョブの数の上限） |
| `NDF_PATHS` に含まれるファイルは変えず、指摘の一覧表とワークフローの両方 | `ndf check --only review-items --only toolchain --only budget-ci-jobs`（1 回の起動で 3 つの規則を渡す） |

どのファイルが当たるかは `.github/workflows/script-structure.yml` にある。

## 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 上限の設定のすべての方針が、使うモジュールを 1 つ以上書いている | スキーマの検証が落ちる（`config_broken`） |
| I2 | `read_by` と、実際に方針を使うモジュールが一致する | `limit-readers` が落ちる |
| I3 | スキーマの `enum` や上限の数と同じ値を持つ定数は、理由を書いて除いたものだけである | `const-fact` が落ちる |
| I4 | 重複の件数は基準と同じで、基準は変更前より大きくない | `copy-dup` が落ちる |
| I5 | 設計レビューの指摘はすべて対応先を持ち、`review_items` は表と一致する | `review-items` が落ちる |
| I6 | 上限の設定を検証するのは 1 つのプロセスで 1 回だけである | `test_limits_cached.py` が落ちる |
| I7 | `plugins/ndf/docs/contracts/` の生成した文書は、コントラクトと一致する | 規則 `spec-docs` が落ちる |

## エラー処理

| 起きたこと | 結果 JSON の `status`・`cause.kind` と終了コード |
| --- | --- |
| 違反がある | `stopped`・`check_failed`・終了コード 1 |
| 構文の誤りで読めない `.py` がある | `stopped`・`invalid`・終了コード 2。重複の件数は比べず、基準も下げない |
| 上限の設定・スキーマ・`rules.json`・`ratchet.json`・指摘の一覧表を読めない。`--ratchet-base` のコミットを読めない | `stopped`・`config_broken`・終了コード 2 |
| `ratchet.json` に書けない | `stopped`・`unknown`・終了コード 1 |

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 方針のファイルに、使うモジュールを書かせる | 名前を組み立てて読むコードがあるため、文字列の検索では使われている方針 107 個を「使われていない」と誤った（223 個中。2026-11-23 の実測）。実行時の記録を集計すると、テストで通らない経路の方針を見落とす |
| 使われていない方針は削除し、使うコードを後から足さない | `supervise.worker-step` を使うコードを足すと、3,600 秒まで動けた worker が 1,800 秒で止まるようになる |
| 定数の検査は新しい規則を作らず、`const-fact` を広げる | 種類ごとに規則を分けると、同じ問題を 3 つの規則が報告する。定数とデータの一致をテストで確かめる形は、同じ値が 2 か所に残る |
| 検査から除く値は、1 つずつ理由を書く | まとめて除くと、後から足された同じ値の定数も通ってしまう |
| 件数を基準と比べるのは重複だけにし、基準は規則ごとの件数で持つ | 0 件を保っている規則に基準を置くと、違反を許すことになる。違反の行の一覧やファイルごとの件数は、行の移動やファイルの分割だけで変わる |
| 重複は外部のツールを使わず `ndf check` で数える | jscpd は Node.js への依存が増え、数え方も違う。リポジトリの `scripts/check-script-structure.py` は `plugins/ndf/` を調べない |
| `items` の 4 つの欄は任意にし、ほかの欄も許す | `items` の要素はサブコマンドごとに違う欄を持つ。必須にするか欄を限ると、今の結果が検証を通らなくなる |

## テスト観点

| 観点 | テスト（`plugins/ndf/tests/` の下） |
| --- | --- |
| `read_by` と実際の読み手の食い違い（両方向）、解析できない箇所、実行時に組み立てる名前 | `checks/test_limits_readers.py` |
| `enum` と同じ集まり、上限と同じ数、除く一覧、辞書の値 | `checks/test_facts_enum.py`・`test_facts_limits.py`・`test_facts_dict.py` |
| スキーマに値を足すと定数も増えること、スキーマを 1 回だけ読むこと | `contracts/test_schema_constants.py` |
| 重複を数える境界（3 と 4 ファイル、7 と 8 字、80 と 81 字、docstring） | `checks/test_copy_dup.py` |
| 件数が基準と同じ・多い・少ない、基準を大きくした場合 | `checks/test_ratchet.py` |
| 一覧表からの生成、対応先の無い指摘、対応先の形、シンボリックリンク | `checks/test_review_items.py` |
| 検証が 1 回だけであること、生成した文書の 1 文字の違い | `contracts/test_limits_cached.py`・`checks/test_spec_docs.py` |
| `items` の 4 つの欄 | `contracts/test_result_items.py` |

## 関連リンク

- [issue #3684](https://github.com/devbasex/ai-plugins/issues/3684) — 使われない上限・重複・設計レビューの指摘の検査
- [issue #3667](https://github.com/devbasex/ai-plugins/issues/3667) — コントラクトと同じ値を持つ定数の検査
- [ndf-internal-data-versions.md](ndf-internal-data-versions.md) — 記録の型と、キーの名前の検査
- [ndf-limits-declaration-and-retry-policy.md](ndf-limits-declaration-and-retry-policy.md) — 上限の値の読み方
- [ndf-split-tables-toolchain-and-budget.md](ndf-split-tables-toolchain-and-budget.md) — 1 項目 1 ファイルの表と、ほかの `ndf check` の規則
- [ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md) — 事実の表と、問題のある作りの検査
