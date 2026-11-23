# 検査（`ndf check`）

`plugins/ndf/` だけを見る検査である。CI（`.github/workflows/script-structure.yml` の `script-structure-check`）が Pull Request ごとに打つ。

```bash
plugins/ndf/bin/ndf check
```

- 見るファイルを 1 回集め、`.py` の構文木を 1 ファイルにつき 1 回だけ作る。すべての規則が同じ木を読む
- 兆候は初日から 0 件で、基準のファイルも増やさないラチェットも持たない。見つけた兆候はすべて違反になる
- 規則のしきい値と名前の一覧は `rules.json` にあり、規則の関数は値を写さずにそこから読む
- 結果 JSON を標準出力の最後の 1 行に出す。形は結果のコントラクト（`ndf.contracts.result`）で、出す前に確かめる。違反は 1 件につき 1 行を標準エラーへ出す
- 止まったときの停止理由（`cause`）と終了コード:

| 場合 | `status` | `cause.kind` | 終了コード |
| --- | --- | --- | ---: |
| 違反が無い | `ok` | 無し | 0 |
| 違反がある | `stopped` | `check_failed` | 1 |
| 構文の誤りで読めない `.py` がある | `stopped` | `invalid` | 2 |
| 規則のデータ・子の表・用語集・git・PR の事象の JSON が読めない | `stopped` | `config_broken` | 2 |

`cause.detail` は `summary` と同じ文である。違反の一覧は `items`、規則ごとの件数は `metrics.findings` にある。

## 見る範囲

| 範囲 | 当てる規則 |
| --- | --- |
| `src/` の下の `.py` | 兆候・構造・輪・事実の重複・子の表・I1・I3・禁止語・環境変数の読み（spec-io） |
| `tests/` の下の `.py` | 行数の上限・I3・禁止語 |
| `bin/`・`hooks/` のスクリプト | 行数の上限・I1・禁止語 |
| そのほかの文字のファイル（`.md`・`.json` など。`uv.lock` を除く） | 禁止語。`skills/`・`agents/` の `.md` は書かれた呼び出し（spec-skill） |
| コントラクトの表・スキーマ・生成物・`bin/` | 規約の規則（spec-docs・spec-decl・spec-io・spec-skill・spec-public） |

## 兆候の規則

兆候とは、AI が書きがちで、読みにくさや障害の隠れにつながる作りである。次の表の 1 行に当たる箇所を 1 件と数える。

| 規則 | 兆候と数えるもの | 数える行 | 抑制コメント |
| --- | --- | --- | --- |
| `NDF201` | ループ（`for`・`while`・内包表記）の本体の中の外部呼び出し。外部呼び出しは、名前が `calls` にある関数か、根の名前が `modules` にある属性の呼び出し | 呼び出しを直に含む最も内側のループの行。1 つのループで 1 件 | 境界の種類か `上限` |
| `NDF301` | `elif` が 4 本以上続く連鎖 | 連鎖の先頭の `if` | 受けない |
| `PLR0912` | 分岐が 9 を超える関数（ruff の数え方） | `def` の行 | 受けない |
| `PLR1702` | ブロックの入れ子が 3 を超える関数（ruff の数え方） | 入れ子の外側のブロックの行 | 受けない |
| `S110` | 本体が `pass` だけの `except`（型を指定した `except` も数える） | `except` の行 | 境界の種類 |
| `S112` | 本体が `continue` だけの `except`（同上） | `except` の行 | 境界の種類 |
| `BLE001` | `Exception`・`BaseException` を受け、本体で送出し直さない | `except` の行 | 境界の種類 |
| `E722` | 素の `except`（型を書かない `except:`） | `except` の行 | 境界の種類 |
| `NDF401` | 本体が定数の `return` を含み、`pass`・`continue`・定数の `return` だけでできた `except` | `except` の行 | 境界の種類 |
| `NDF402` | `contextlib.suppress(...)` の呼び出し | 呼び出しの行 | 境界の種類 |
| `NDF501` | 右端が空の値（`{}`・`[]`・`()`・`""`・`0`・`None`）の `or` と、同じ値を真偽で見て空の値へ落とす条件式（`x if x else {}`） | 式の行 | 境界の種類 |
| `NDF502` | 位置引数がちょうど 2 つの `.get(k, 既定)`。受け手が `boundary_receivers`（`os.environ`）のものは環境変数という境界そのものなので数えない | 呼び出しの行 | 境界の種類 |
| `NDF701` | `def` の行から最後の行までが 60 行を超える関数 | `def` の行 | 受けない |
| `PLR0913` | 引数が 6 を超える関数（ruff の数え方。メソッドの `self`・`cls` は数えない） | `def` の行 | 受けない |

入れ子の関数・クラス・`lambda` は、外の関数の長さ以外では外の関数に数えず、それぞれ 1 つの関数として数える。

**`S110`・`S112`・`BLE001`・`E722`・`PLR0912`・`PLR1702`・`PLR0913` は ruff が数える**（設計の決定 36。既存の規則で見られるものは自作しない）。`rules.json` で `by` が `ruff` の規則で、しきい値は `ruff.config` にある。見るファイルすべてへ ruff を 1 回だけ起動する（`lint.py`）。ruff には `# noqa` を読ませず（`--ignore-noqa`）、下の抑制コメントは NDF の規則として `suppressions` が当てる。ruff が `except` の兆候を型の式の行へ置いても（複数行の `except (` など）、`lint` が構文木の `except` の行へ揃えてから抑制コメントを当てる。

### 抑制コメント

兆候が境界（外から来る失敗や値を受ける所）にあって直せないときは、行末に抑制コメントを付けて外す。

```python
data = os.environ.get("X", "")  # noqa: NDF502 利用者の入力: 未設定は空として扱う
```

- 形は `# noqa: <規則>[,<規則>] <境界の種類>: <理由>`。境界の種類は `外部コマンド`・`ネットワーク`・`利用者の入力`・`ファイル`
- `NDF201` だけは `# noqa: NDF201 上限: <件数と根拠>` も使える
- 「受けない」の規則には抑制コメントが効かない。直す
- `except` の規則は、受ける型がすべて内部の型（`TypeError`・`KeyError` など）なら抑制コメントが効かない。握りつぶさずに送出する
- 兆候の無い行の抑制コメントは `suppression-unused` の違反になる

## 構造の規則

| 規則 | 違反と数えるもの |
| --- | --- |
| `lines` | 1,000 行を超えるコードのファイル（`.py` と `bin/`・`hooks/` のスクリプト）。ファイルは責務で分け、この上限は分け忘れを拾う安全網として使う。上限に合わせて切らない |
| `layer` | 下の層が上の層を読む import。層は下から `ndf.contracts`・`ndf.adapters`・`ndf.core`・`ndf.progress`・`ndf.domains` |
| `durable-boundary` | `ndf.core.durable` の外で `dbos` を読む import |
| `decide-pure` | 名前が `_decide.py` で終わる判断のモジュールが、副作用のある部品（`os`・`subprocess`・`time`・`ndf.adapters` など）を読む import と、ファイルを触る呼び出し（`open`・`read_text`・`exists` など） |
| `cycle` | import の輪。`src/` の下のモジュールを点、import を辺とする有向グラフの、2 つ以上のモジュールからなる強連結成分を 1 つの輪と数える。関数の中の import（遅延の import）も辺に入れる。輪ごとに 1 件で、辞書順の先頭のファイルに付ける |

## 事実の重複の規則

| 規則 | 違反と数えるもの |
| --- | --- |
| `const-fact` | 2 つ以上のファイルの最上位の大文字の定数が同じ事実を持つ組。種類は `same-str`（4 字以上の同じ文字列）・`same-set`（同じ文字列の集まり）・`same-num`（同じ名前で同じ数）・`subset`（集まりが別のファイルの集まりに含まれる）・`member`（文字列が別のファイルの集まりの要素）・`table-set`（事実の表の `id` を 2 つ以上含む集まり）・`table-str`（事実の表の `id` に等しい 4 字以上の文字列）。`shared_names` が真の表（`id` が他の名前空間と共有する名前）は `table-set`・`table-str` の検証をしない |
| `fact-table` | 事実の表（`contracts/facts/*.json` と `contracts/domains/*.json`。`*.schema.json` を除く）が、`contracts/facts/fact.schema.json`（表の共通の形）に合わない・読めない・行の並び `rows` が無い・行に文字列の `id` が無い・`id` が重なる・表の名前が 2 つの保存先で重なる |

重複は片方をもう片方から読む形へ直す（定数を import する・事実の表から読む）。

## 上限の設定の読み手の規則

| 規則 | 違反と数えるもの |
| --- | --- |
| `limit-readers` | 上限の設定（`contracts/limits.json`）の方針の読み手の指定（`read_by`）と、`src/` のコードが実際に方針を引くモジュールの食い違い。種類は `unread`（指定した読み手がその方針を引いていない。指摘は上限の設定の行）・`undeclared`（方針の `read_by` に無いモジュールが引いている。指摘は呼び出しの行）・`unresolved`（方針の名前を解けない読み口。指摘は呼び出しの行） |

方針を足すときは、引くモジュールの名前を `read_by` に書く。どのコードからも引かれない方針は置けない。

方針を引く関数（読み口）の指定は `rules.json` の `limits` が持つ。

| キー | 中身 |
| --- | --- |
| `readers` | 読み口の完全な名前 → 方針の名前の引数の名前（`arg`）と位置（`index`。メソッドは `self` を数えない）。名前を引数で受けて読み口へ渡す関数・メソッド、名前を欄に持つクラス、引数で受けた読み口（`<関数>.<引数>`）も並べ、呼び手を読み手に数える。引数の既定値の方針は、既定値を書いたモジュールが引く。`template` があれば、引数の値を `{}` に入れた名前を引く |
| `tables` | 方針の表を丸ごと返す名前。参照したモジュールは `whole` に要る（引くモジュールは 1 行ずつ読む読み口を使う） |
| `whole` | 表を受け取るだけで方針を引かないモジュールと、その理由 |
| `dynamic` | 名前を実行時に組み立てる読み手。`at` は組み立てる関数の完全な名前、`expr` は読み口の引数の式の綴り（引数が関数の中の名前なら、その名前に入る式。`for` の変数なら並びの式）。式の綴りか、`template` の形が実際のコードと違えば `unresolved` になる。`vocabulary`（コントラクトのファイルと JSON Pointer。`*` は辞書か並びのすべて）の文字列の値を `template` の `{}` に、`*` が当たったキー（左から数える）を `{1}`・`{2}` … に入れた名前を引く。組み立てた名前のうち上限の設定に無いものは数えない |

名前は、文字列・モジュールの最上位の定数（import の先は 1 階層）・関数の中の代入・並びと辞書の定数・条件式から解く。条件式は枝ごとに見て、引数をそのまま渡す枝だけを呼び手からの受け渡しと扱う。import は関数の囲みごとに解く。

メソッドの読み口は、受け手のクラスを、引数と `self` の欄の注釈・クラスの呼び出し・関数の戻り値の注釈から解けた呼び出しだけを数える。

## 子の表の規則

`child-deps` は、作り直しの子の表のコピー `children.json` と、コードの import・子の順番とレーンを照らす（#3347 の決定 97・IG1・IG2）。表は子ごとに持つサブパッケージ `packages`・先に要る子 `needs`・流す順番 `order`・レーン `lane`・閉じたか `closed` を持ち、共有の表の書き手を `tables` に持つ。SSOT は [issue-3347-design-detail.md](../../../../../issues/issue-3347-design-detail.md) の「実装計画」と、[issue-3395-design.md](../../../../../issues/issue-3395-design.md) の「構成要素」が足して分けた子である。表を変えるときは、SSOT を直す設計 PR を先に通す。

| 違反と数えるもの | 指摘の保存先 |
| --- | --- |
| `src/` のモジュールにオーナーの子が無い（`packages` が最も長く一致する子がオーナー。パッケージの `__init__` を除く） | そのファイルの 1 行目 |
| 子 A のモジュールが、A の `needs` の推移の閉包に無い子のモジュールを読む（IG1）。A の追補 `A+` があれば、`A+` の閉包も A のモジュールが読んでよい | import の行 |
| 閉じていない子が、同じレーン・同じ順番に 3 本以上ある（IG2。上限は `rules.json` の `child_deps.max_per_slot`） | `children.json` の先頭の子の行 |
| 閉じていない子の順番が、閉じていない先に要る子の順番より大きくない（IG2。閉じた子は流し終えたので、その子の順番も、その子を先に要る子の順番も問わない） | `children.json` のその子の行 |
| 同じ表（`tables`）へ書く閉じていない 2 つの子が、どちらも他方の閉包に無い（並行で書く。#3378）。順序を付け、後に流れる子が先の子のマージをマージしてから衝突を解く | `children.json` の後の子の行 |

`children.json` が無い・形が違う・知らない子を指す・`needs` に輪があるときは、検査を止める（`config_broken`）。

## 規約の規則

設定（`.ndf/`）と入出力（サブコマンド・環境変数・受け渡しのファイル・hook・Skill・起動スクリプト）の規約を、コードと SSOT の表から生成し、食い違いを落とす（#3518・#3519）。生成物はコントラクトのページ（`docs/contracts/*.md`）とサブコマンドごとの引数のスキーマ（`contracts/schemas/io/*.args.schema.json`）で、保存先は `rules.json` の `spec.generated` が持つ。コントラクトの保存先（`contracts/`）を持たないプラグインには当てない。

```bash
plugins/ndf/bin/ndf check --write-spec   # 生成物を書き直してから検査する
plugins/ndf/bin/ndf check                     # 書かずに検査する（CI と同じ）
```

| 規則 | 違反と数えるもの |
| --- | --- |
| `spec-docs` | コミットされた生成物が、生成し直した中身と違う・無い・読めない。生成物の保存先に生成されないファイルが残る |
| `spec-decl` | 保存先の表（`contracts/domains/ndf-files.json`）の設定の行が、スキーマ（`contracts/schemas/ndf-settings/<id>.schema.json`）かセクションの表（`<id>.sections.json`）を持たない。スキーマの最上位の非推奨でないキーが表の `keys` と食い違う。`main_keys` が `keys` に無い。キーが型か説明を持たない。注釈 `x-ndf-location`・`x-ndf-read-by`・例が無い。検査を打つリポジトリの `.ndf/` の実物がスキーマに通らない（ファイルと JSON Pointer） |
| `spec-io` | サブコマンドが結果のスキーマ（`contracts/schemas/io/<サブコマンド>.result.schema.json`）を持たない。引数の出所が argparse の取り出しと手書きの引数の表（`contracts/domains/manual-args.json`）の両方か、どちらでもない。`src/` のコードが名前の文字列で読む環境変数が環境変数の表に無い。`bin/` のファイルが起動スクリプトの表（`contracts/domains/launchers.json`）に無い |
| `spec-skill` | 実体のある配る Skill が Skill の入出力の表（`contracts/domains/skill-io.json`）に行を持たない。公開する Skill の行が `args`・`env` を持つ。`skills/`・`agents/` の `.md` に書かれた `ndf <サブコマンド>` の呼び出しが、サブコマンド・下位の命令・引数の名前で規約に当たらない（値の型は見ない） |
| `spec-public` | 公開する面の表（`contracts/domains/entrypoints.json`）の行が規約の項目に当たらない（順番が `caller-first` の行と種類 `spec` の行を除く）。規約の保存先 3 つ（`docs/contracts`・`contracts/schemas/ndf-settings`・`contracts/schemas/io`）を指す種類 `spec` の行がちょうど 1 行ずつでないか、ディレクトリが無い |

サブコマンドの引数は、サブコマンドの `main` を argparse が引数を読む呼び出しで止めて取り出す（`spec_args`）。結果の形は、テストの間に出たすべての結果 JSON を `tests/conftest.py` が結果のコントラクトと結果のスキーマで確かめる。

## 禁止語の規則

`banned-word` は、用語集が廃止した語（各語の `deprecated`）と禁止語（用語集の設定 `.ndf/glossary.json` の `check.banned.words`）を、プラグインの下の文字のファイルの行から拾う。用語集の保存先は用語集の設定の `source` から求める。ASCII の語とカタカナだけの語は、同じ字種の語の内側に当てない。避ける語の例として廃止した語を挙げる文書は、`rules.json` の `words.examples` にファイル（プラグインの根からのパス）・外す語・理由を書き、そのファイルのその語だけを外す。

## 不変条件

| 規則 | 違反と数えるもの |
| --- | --- |
| `I1` | `src/` の `.py` の文字列の定数（docstring を除く）と、`bin/`・`hooks/` のスクリプトのコメントでない行が、自分の名前を持つ箇所（下の「I1 の名前の集合」） |
| `I3` | 自分のパッケージ・標準ライブラリ・`pyproject.toml` が定義した依存の外のモジュールを読む import |

旧いプラグイン（10 系）と並べて作った間の規則 `I2`（子の PR が旧いプラグインを変えない）・`port`（旧いプラグインの直しを移す）と、`I3` の旧いプラグインのパスの文字は、入れ替え（#3304）で旧いプラグインが無くなったため外した。

### I1 の名前の集合

I1 は `plugin.json` の `name` とディレクトリ名から名前を求め、`identity.FINAL_NAME`（`ndf`）と合わせて当てる。入れ替え（#3304）の後はどれも `ndf` だが、名前を変えても同じ箇所を当てる。

| 検証 | 名前 |
| --- | --- |
| 文字列そのもの（完全一致） | `plugin.json` の `name`・`identity.FINAL_NAME`・`identity.FIXED_WORDS` の語 |
| パスの一部（`/<名前>`・`<名前>/`）と前置き（`<名前>:`） | `plugin.json` の `name`・`identity.FINAL_NAME`・ディレクトリ名 |

- Python のパッケージの形（`src/ndf`・`ndf.` で始まるモジュール名）は検証しない。パッケージ名はプラグイン名から切り離した固定の名前である
- 設定の保存先 `.ndf`・エントリポイントの名前 `ndf`（`bin/ndf`）・工程の定義の名前空間 `ndf` は、プラグイン名から求めない固定の語で、`identity.FIXED_WORDS` に置く。コードはこの表から引き、I1 は `identity.py` の `FIXED_WORDS` と `FINAL_NAME` の定義の行だけを通す

### I1 と Skill 本文の前置き

I1 は Skill 本文（`skills/`）とエージェント定義（`agents/`）を見ない。Skill 本文は前置き（`<プラグイン名>:`）を `identity` から求められず、文字で書くほかないためである。コードが前置きを要るときは `identity.load().prefix` から求め、I1 がその文字を落とす。

