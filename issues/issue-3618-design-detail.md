# #3618 の詳細設計: 設定の受け口を減らす設計を設計承認の前に見つける

設計文書 [issue-3618-design.md](issue-3618-design.md) の「構成要素」と「入出力の契約」を、ファイルと関数の粒度へ下ろした文書である。決定の番号（決定 1〜12）は [issue-3618-design-decisions.md](issue-3618-design-decisions.md) を指す。不変条件の番号（I1〜I6）は設計文書の「ドメインモデル」を指す。

この文書で使う語は設計文書の「用語」の表と同じである。とくに次の 3 語を使う。

| 語 | この文書での意味 |
| --- | --- |
| 頭の設計文書 | `issues/issue-<番号>-design.md`（文書の種類の契約で種類が `design` のもの）。決定の記録と詳細設計の文書は含まない |
| 代表のリポジトリの設定 | `.ndf/design.json` の `representative_repos`。読み込んだ後の内部の形は「内部のデータと定数」の `RepoSettings` |
| 2 規則 | 設計の検査に足す規則 `repo_scope_missing`（振る舞いの表と事情の欄が無い）と `repo_loss_unrouted`（使えなくなるものが人へ残す判断へ届かない） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.ndf/
├── design.json *                      代表のリポジトリの設定（ai-plugins の値）
├── mvv.md                             MVV 版 8 の本文（M1・M2）
└── mvv.json                           MVV の宣言（版 8 の承認）
docs/
├── glossary/glossary.json             用語の正本
└── glossary.md                        用語集（glossary.py render の生成物）
plugins/ndf/
├── scripts/
│   ├── design-survey.py               設計の検査（2 規則を呼ぶ・schema の出力）
│   ├── data/issue-items.json          起票の項目（policy_split）
│   ├── lib/
│   │   ├── design_repos.py *          設定の読み込みと 2 規則
│   │   └── README.md                  lib の一覧
│   ├── supervise_lib/procedures.py    直しの worker への指示
│   └── tests/
│       ├── test_design_repos.py *     2 規則と設定の単体テスト
│       ├── test_issue_items.py        起票の項目の読み手のテスト
│       └── test_mvv_gate.py           MVV 判定のテスト（P2 を持つ MVV）
└── skills/
    ├── design/
    │   ├── schemas/design.schema.json *   設定のスキーマ（生成物）
    │   └── references/design-template.md  設計文書のテンプレート
    ├── requirements-design/references/spec-template.md
    └── cross-review/
        ├── scripts/review_lib/review_focus.py
        └── docs/08-review-categories.md
plugins/ndf-next/
├── skills/design/references/design-template.md
├── skills/requirements-design/references/spec-template.md
├── skills/cross-review/docs/08-review-categories.md
└── src/ndf/
    ├── parity/fixes.json              直しの棚卸し
    └── checks/children.json           作り直しの子の表
```

依存の向きは一方向で、循環は無い。`design-survey.py` が `lib/design_repos.py` を呼ぶ。`design_repos.py` は `lib/md.py`・`lib/schema.py`・`lib/pathmatch.py`・`lib/deps.py` だけを読む。`design_repos.py` は `design-survey.py` を読まない（人へ残す判断の表の番号は呼ぶ側が切り出して渡す）。テンプレート・観点・データのファイルはコードから読まれる側で、ほかを読まない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/design_repos.py` | 新設 | 代表のリポジトリの設定を読んで形を確かめ、頭の設計文書 1 本に 2 規則を当てて指摘を返す | `lib/md.py`・`lib/schema.py`・`lib/pathmatch.py`・`lib/deps.py` |
| `plugins/ndf/scripts/design-survey.py` | 変更 | 設定を 1 回読み、まとまりの頭の設計文書ごとに 2 規則を呼んで指摘と件数を結果に足す。サブコマンド `schema` で設定のスキーマを出す | `lib/design_repos.py` |
| `plugins/ndf/scripts/lib/README.md` | 変更 | `design_repos.py` の行を「置いてあるもの」の表に足す | 無し |
| `plugins/ndf/skills/design/schemas/design.schema.json` | 新設 | `.ndf/design.json` の JSON Schema。`design_json_schema()` の出力をそのまま置く | 無し |
| `plugins/ndf/scripts/supervise_lib/procedures.py` | 変更 | `survey_steps` の直しの指示に 2 規則の直し方を足す | 無し |
| `plugins/ndf/skills/design/references/design-template.md` | 変更 | 「適用範囲」に、振る舞いの表と「このリポジトリだけの事情」の欄の書き方を置く | 無し |
| `plugins/ndf/skills/requirements-design/references/spec-template.md` | 変更 | 方針を「このリポジトリの運用の決まり」と「作るものの振る舞い」に分ける節（該当時）を置く | 無し |
| `plugins/ndf/scripts/data/issue-items.json` | 変更 | 起票の項目 `policy_split` を足す | 無し |
| `plugins/ndf/skills/cross-review/scripts/review_lib/review_focus.py` | 変更 | `DESIGN_REVIEW_TEMPLATE` に後方互換の 1 行を足す | 無し |
| `plugins/ndf/skills/cross-review/docs/08-review-categories.md` | 変更 | `design` の分類の説明に後方互換の観点を足す | 無し |
| `plugins/ndf/scripts/tests/test_design_repos.py` | 新設 | 設定の読み込み・当てる範囲・2 規則・設計の検査の結果を縛る | `lib/design_repos.py`・`design-survey.py` |
| `plugins/ndf/scripts/tests/test_issue_items.py` | 変更 | `policy_split` が該当時の項目として読めることを縛る | `lib/issue_items.py` |
| `plugins/ndf/scripts/tests/test_mvv_gate.py` | 変更 | P2 の行を持つ MVV で、判定の指示に P2 が入ることと、`boundary` が空でなければ承認ゲートを省かないことを縛る | `mvv-gate.py` |
| `plugins/ndf-next/skills/design/references/design-template.md` | 変更 | 今の NDF のテンプレートと同じ変更 | 無し |
| `plugins/ndf-next/skills/requirements-design/references/spec-template.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/skills/cross-review/docs/08-review-categories.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/src/ndf/parity/fixes.json` | 変更 | スクリプトの分を移す子へ渡す 3 行を足す | 無し |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | 子 G1 の行を足し、S2 の `needs` に G1 を足す | 無し |
| `.ndf/design.json` | 新設 | ai-plugins の代表のリポジトリの 3 種・表を求めるパス・番号 P2 | `plugins/ndf/skills/design/schemas/design.schema.json` |
| `.ndf/mvv.md` | 変更 | MVV 版 8 の本文（改訂の文 M1・M2） | 無し |
| `.ndf/mvv.json` | 変更 | 版 8 の承認の記録（`project-mvv.py approve` が書く） | 無し |
| `docs/glossary/glossary.json` | 変更 | 4 語（受け口・代表のリポジトリ・代表のリポジトリの設定・このリポジトリだけの事情）は設計の工程で足してある。実装で語の意味が実装と食い違ったときだけ直す | 無し |
| `docs/glossary.md` | 変更 | `glossary.json` を直したときだけ `glossary.py render` で作り直す | `docs/glossary/glossary.json` |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/design_repos.py` | モジュール `design_repos` | 新設: `RepoSettingsError`（例外）・`RepoSettings`（型）・`load_settings`・`design_json_schema`・`applies`・`check_repos`。内部の新設: `_models`・`_behavior_table`・`_scope_problems`・`_loss_rows` |
| `plugins/ndf/scripts/design-survey.py` | モジュール `design-survey` | 新設: `repo_items`・`cmd_schema`。変更: `judge_docs`（引数 `settings` を足す）・`cmd_check`（設定を読む）・`build_parser`（サブコマンド `schema`）。名前だけ: `_left_table_rows`・`_result_files`（呼ぶ先が増えるだけで変えない） |
| `plugins/ndf/scripts/supervise_lib/procedures.py` | モジュール `procedures` | 名前だけ: `survey_steps`（直しの指示の文を足すだけで、シグネチャ・責務・失敗の扱いは変えない） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/review_focus.py` | モジュール `review_focus` | 定数だけ: `DESIGN_REVIEW_TEMPLATE`（関数は変えない） |
| `plugins/ndf/scripts/tests/test_design_repos.py` | テストのモジュール | 新設: 「単体テストの観点」の行ごとのテスト |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load_settings` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `load_settings(root: Path) -> RepoSettings \| None` | リポジトリの根の `.ndf/design.json` を読み、形を確かめて内部の形へ直す | `root` はリポジトリの根の絶対パス | ファイルが無いか `representative_repos` が無ければ `None`。あれば `kinds` が空でなく重複が無く、`redline` が `[CPR][1-9][0-9]*` の形の `RepoSettings` を返す。形が合わなければ値を返さない | ファイルの読み取り。ファイルがあるときだけ `deps.require("schema", "pathmatch")`（依存が無ければ uv で起動し直す） |
| `design_json_schema` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `design_json_schema() -> str` | 設定の型から JSON Schema の文字列（末尾の改行つき・キーの順は型の定義の順）を作る | `deps.require("schema")` を呼べる | 同じ型からは同じ文字列が返る。`$schema` は draft 2020-12 | `deps.require("schema")` |
| `applies` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `applies(settings: RepoSettings, touched: set[str] \| None) -> bool` | 頭の設計文書に 2 規則を当てるかを決める | `settings` は `load_settings` の戻り値 | 判定表（「処理の手順」）のとおりの真偽を返す | 無し |
| `check_repos` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `check_repos(path: str, text: str, settings: RepoSettings, left_ids: set[str]) -> list[dict]` | 頭の設計文書 1 本に 2 規則を当て、指摘の並びを返す | `applies` が真を返した文書である。`left_ids` は同じ文書の「人へ残す判断」の表の番号のセルから切り出したレッドラインの番号の集合 | 指摘はそれぞれ `name`（`<パス>:<行>`）・`rule`（2 規則のどちらか）・`text` を持ち、文書の行の順に並ぶ。表と事情の欄がそろい、使えなくなるものの行が無いか番号の行があれば空 | 無し |
| `_scope_problems` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `_scope_problems(text: str, settings: RepoSettings) -> tuple[list[tuple[int, str]], Table \| None]` | 「適用範囲」の節・振る舞いの表・種類ごとの行・事情の欄の欠けを（行, 理由）で並べ、見つけた表を返す | `text` は頭の設計文書の本文 | 欠けが無ければ並びは空。表が見つからなければ 2 つ目は `None` | 無し |
| `_loss_rows` | `plugins/ndf/scripts/lib/design_repos.py` | 新設 | `_loss_rows(table: Table) -> tuple[list[int], list[int]]` | 振る舞いの表の行を「使えなくなるものがある行」と「使えなくなるものが空の行」に分け、文書の中の行番号で返す | `table` は `_behavior_table` が返した表 | セルが `無し` で始まる行はどちらにも入らない | 無し |
| `repo_items` | `plugins/ndf/scripts/design-survey.py` | 新設 | `repo_items(group: list[Doc], settings: RepoSettings \| None) -> list[dict]` | まとまりの頭の設計文書ごとに、当てる範囲を決めて 2 規則を呼ぶ | `group` は `survey_groups` の 1 まとまり | `settings` が `None` なら空。そうでなければ、当てる頭の設計文書ごとの `check_repos` の結果をつないだもの | 無し |
| `judge_docs` | `plugins/ndf/scripts/design-survey.py` | 変更 | `judge_docs(docs: list[Doc], require_detail: bool, settings: RepoSettings \| None = None) -> tuple[list[dict], dict]` | 文書の並びに調査・レッドライン・詳細設計・代表のリポジトリの判定を当て、指摘と件数を返す | 今と同じ | 今の指摘と件数に加え、`metrics` に `repo_scope_missing`・`repo_loss_unrouted` の件数（`settings` が `None` なら 0）がある。今の指摘の並びと件数は変わらない（I3） | 無し |
| `cmd_check` | `plugins/ndf/scripts/design-survey.py` | 変更 | `cmd_check(a: argparse.Namespace) -> None` | 文書を読み、設定を 1 回読んで判定し、結果の 1 行の JSON を出す | 今と同じ | 終了コードの意味は今と同じ（0 / 1 / 2 / 3）。設定が壊れていれば 2 | 標準出力。`load_settings` のファイルの読み取り |
| `cmd_schema` | `plugins/ndf/scripts/design-survey.py` | 新設 | `cmd_schema(a: argparse.Namespace) -> int` | 設定のスキーマを標準出力へ出し、`--out` があればそのパスにも書く | 無し | 標準出力にスキーマがある。`--out` を渡したときはそのファイルの中身が標準出力と同じ | 標準出力。`--out` のファイルの書き込み |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_settings` | 設定を読めない | ファイルの権限・文字コードが UTF-8 でない | `Path.read_text` の `OSError`・`UnicodeDecodeError` | 止める（`RepoSettingsError` を上げる） | `cmd_check` が `status: stopped` と「`.ndf/design.json` を読めない: <理由>」で終了コード 2 | 書いたものは無い。ファイルを直せば再実行できる |
| `load_settings` | JSON として読めない | 書式の誤り | `json.loads` の `JSONDecodeError` | 止める（`RepoSettingsError`。行と列を添える） | 同上（「JSON として読めない: <行>:<列>」）で終了コード 2 | 同上 |
| `load_settings` | 形が合わない | 知らない項目・`version` が 1 でない・`kinds` が空の配列・`redline` の形が違う・`paths` が空の配列（I4） | `schema.load_shape` の `ShapeError` | 止める（`RepoSettingsError`。`ShapeError` の 1 行を添える） | 同上（「`.ndf/design.json` の形が合わない: representative_repos.kinds: 1 個以上にする」など）で終了コード 2 | 同上。推して「設定なし」として進めない |
| `load_settings` | 種類が重なる | `kinds` に同じ名前が 2 つある | 型の検査の後の重なりの確かめ | 止める（`RepoSettingsError`） | 同上（「種類が重なる: <名前>」）で終了コード 2 | 同上 |
| `load_settings` | 依存が入らない | `pydantic`・`pathspec` が無く uv も使えない | `deps.require` | 止める（`deps.require` の既存の扱い） | `deps` の既存の案内と終了コード `EXIT_DEPS_MISSING` | 依存を入れれば再実行できる。設定が無いリポジトリでは呼ばないため起きない（I3） |
| `design_json_schema` | 依存が入らない | `pydantic` が無く uv も使えない | `deps.require` | 止める（既存の扱い） | 同上 | 同上 |
| `cmd_check` | 文書の種類を決められない | 文書の種類の契約を読めない | `repo_items` の中の `doc_kinds.doc_kind` の `DocKindsError` | 止める（`StepError(EXIT_UNREADABLE)`。`detail_items` と同じ扱い） | `status: stopped` と契約の誤りの 1 行で終了コード 2 | 書いたものは無い。契約を直せば再実行できる |
| `cmd_schema` | `--out` に書けない | ディレクトリが無い・権限が無い | `Path.write_text` の `OSError` | 止める（`StepError(EXIT_UNREADABLE)`） | `status: stopped` と「スキーマを書けない: <パス>: <理由>」で終了コード 2 | 書きかけのファイルは残らない（1 回の `write_text`）。パスを直せば再実行できる |

`check_repos`・`applies`・`repo_items`・`judge_docs` は表と節を読めない場合も例外を上げない。節や表が無いことは指摘として返す（規則の判定の一部である）。

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `applies` | `settings.paths` が `None` | 真（リポジトリのすべての頭の設計文書に当てる） |
| `applies` | `paths` があり、`touched` が `None`（設計の結果の表を読めない、または `legacy-refactor` の書式） | 真。触るファイルが分からない設計を外すと、減少を書いた設計が検査を通らずに承認へ届くため、安全側に倒す |
| `applies` | `paths` があり、`touched` のどれかが `pathmatch.path_matches(f, paths)` で当たる | 真 |
| `applies` | `paths` があり、`touched` が空か、どれも当たらない | 偽（今と同じ。I3） |
| `_scope_problems` | 「適用範囲」の `##` の節が無い | 1 件（行 0・「適用範囲の節が無い」）。ほかは見ずに表は `None` |
| `_scope_problems` | 節の中に `### 代表のリポジトリでの振る舞い` が無い | 1 件（適用範囲の見出しの行・「代表のリポジトリでの振る舞いの節が無い」） |
| `_scope_problems` | 小見出しはあるが、見出しに `代表のリポジトリ` と `使えなくなるもの` の列を持つ表が中に無い | 1 件（小見出しの行・「振る舞いの表に 代表のリポジトリ と 使えなくなるもの の列が無い」） |
| `_scope_problems` | 表はあるが、`settings.kinds` のある種類で始まる 1 列目のセルが無い | 種類ごとに 1 件（表の見出しの行・「種類 <名前> の行が無い」） |
| `_scope_problems` | 表に「使えなくなるもの」のセルが空の行がある | 行ごとに 1 件（その行・「使えなくなるものが空」） |
| `_scope_problems` | 節の中に、題が `だけの事情` で終わる `###` の節が無いか、その本文に空白でない行が無い | 1 件（適用範囲の見出しの行・「このリポジトリだけの事情の欄が無い」） |
| `check_repos` | `_scope_problems` の欠けが 1 件以上 | 欠けごとに `repo_scope_missing` の指摘 |
| `check_repos` | 表があり、使えなくなるものがある行が 1 行以上あり、`settings.redline` が `left_ids` に無い（`P2` と `P20` は別の番号として扱う） | 使えなくなるものがある行ごとに `repo_loss_unrouted` の指摘 |
| `check_repos` | 使えなくなるものがある行があり、番号の行もある | `repo_loss_unrouted` は出さない |
| `check_repos` | 表が `None` | `repo_loss_unrouted` は判定しない（`repo_scope_missing` だけを返す） |
| `cmd_check` | `load_settings` が `RepoSettingsError` | 文書の判定より前に終了コード 2 |
| `cmd_check` | 設定が `None` | 今と同じ判定（`repo_items` は空） |

セルの字面は、両端の空白と `` ` ``・`*`・`_` を外してから比べる（`_read_survey` と同じ外し方）。種類の名前との比べ方は前方一致で、`Web サービス・設定なし（…）` は種類 `Web サービス` の行として数える。

- 繰り返しの終わる条件: 表の行・種類・まとまりの文書はどれも有限の並びで、1 回ずつ見る
- 計算量の注意: 文書 1 本ごとに `md.md_sections`・`md.tables` を 1 回ずつ呼ぶ（`md` は内部で字句の解析を覚えておく）。種類の数 × 表の行の数の比べは、種類 3・行 5 程度で問題にならない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `RepoSettings` | 型（`NamedTuple`: `kinds: tuple[str, ...]`・`paths: tuple[str, ...] \| None`・`redline: str`） | `plugins/ndf/scripts/lib/design_repos.py` | — |
| `SETTINGS_PATH` | 定数（根からの相対パス） | `plugins/ndf/scripts/lib/design_repos.py` | `.ndf/design.json` |
| `RULES` | 定数（規則の順。指摘の並びと `metrics` の鍵） | `plugins/ndf/scripts/lib/design_repos.py` | `("repo_scope_missing", "repo_loss_unrouted")` |
| `SCOPE_TITLE`・`BEHAVIOR_TITLE`・`OWN_SUFFIX` | 定数（規則が名指す節の題） | `plugins/ndf/scripts/lib/design_repos.py` | `適用範囲`・`代表のリポジトリでの振る舞い`・`だけの事情` |
| `REPO_COL`・`LOSS_COL`・`NONE_VALUE` | 定数（規則が名指す列と値） | `plugins/ndf/scripts/lib/design_repos.py` | `代表のリポジトリ`・`使えなくなるもの`・`無し` |
| `REDLINE_ID` | 定数（レッドラインの番号の正規表現） | `plugins/ndf/scripts/design-survey.py`（今の唯一の置き場。番号の切り出しは呼ぶ側の `repo_items` が行い、`design_repos.py` へは番号の集合 `left_ids` を渡す。`design_repos.py` は `redline` の形を型の `pattern` で確かめるだけで、この正規表現を写さない） | `(?<![A-Za-z0-9])[CPR][0-9]+(?![0-9])` |
| 設定の型 `DesignSettings`・`RepresentativeRepos` | 型（`schema.Shape`。`version: Literal[1]`・`representative_repos: RepresentativeRepos \| None = None`／`kinds: list[NonEmptyStr]`（1 個以上）・`paths: list[NonEmptyStr] \| None = None`（1 個以上）・`redline: str`（`^[CPR][1-9][0-9]*$`）） | `plugins/ndf/scripts/lib/design_repos.py` の `_models()`（pydantic を読む前に依存を確かめるため、`project_mvv_decl.decl_models` と同じく関数の中で組む） | — |
| 設定のスキーマ | 生成物 | `plugins/ndf/skills/design/schemas/design.schema.json`（正本は上の型。`design-survey.py schema --out` で作り直す） | — |
| ai-plugins の設定の値 | 設定値 | `.ndf/design.json` | `kinds`: `Web サービス`・`モバイルアプリ`・`業務文書だけ`、`paths`: `plugins/ndf/**`・`plugins/ndf-next/**`・`.claude-plugin/marketplace.json`、`redline`: `P2` |
| 後方互換の観点の 1 行 | 定数の文 | `review_focus.py` の `DESIGN_REVIEW_TEMPLATE` の末尾の前（文は決定 6 の 1 行） | — |
| 起票の項目 `policy_split` | データ | `plugins/ndf/scripts/data/issue-items.json` の `boundary` の後・`request` の前 | `name`: `方針の分け方`、`need`: `運用`・`機能` は `該当時`、`不具合`・`調査` は `不要`、`level`: 「依頼の方針を、このリポジトリの運用の決まり（設定の値）と作るものの振る舞いの 2 列の表に分けて書く」、`when.text`: 「依頼に運用の方針（ブランチの運用・リリースの形など）が含まれるとき」、`when.key`: `null` |
| 直しの棚卸しの行 | データ | `plugins/ndf-next/src/ndf/parity/fixes.json` の `fixes` | `fix:3618-survey`（設定の読み込み・2 規則・スキーマ・直しの指示。`to`: `M7`）、`fix:3618-focus`（後方互換の観点。`to`: `M5`）、`fix:3618-items`（起票の項目。`to`: `M6`）。`prs`・`commits` は実装の Pull Request を作った後にその番号とコミットを書く。実装の時点で行き先の子が閉じていれば、追補の単位（`M7+` など）を `to` にする（決定 9） |
| 子 G1 の行 | データ | `plugins/ndf-next/src/ndf/checks/children.json` の `children` | `{"issue": 3618, "packages": [], "needs": ["S1a", "X3"], "order": 9, "lane": "lguard", "closed": false}`。S2 の `needs` は `["T2", "S1d", "G1"]`（決定 12） |
| 直しの指示の文 | 定数の文 | `procedures.py` の `survey_steps` の `fix-survey` の `prompt`（`require_detail` に依らず足す） | 「repo_* の指摘は頭の設計文書の適用範囲を直す: `### 代表のリポジトリでの振る舞い` の表に設定の種類ごとの行を足す・`### <名前>だけの事情` の欄を書く・使えなくなるものがある行は、人へ残す判断の表に設定が名指す番号の行を足す。使えなくなるものを `無し` へ書き換えて指摘を消さない」 |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列の計画の複数のレーンの `survey-check`（同じリポジトリの別の worktree） | 各 worktree の `.ndf/design.json`（読むだけ） | 取らない。設計の検査は読むだけで何も書かない | 同じ入力からは同じ結果になる。中断しても打ち直せばよい |
| `design-survey.py schema --out` と CI のテスト（生成物との比べ） | `plugins/ndf/skills/design/schemas/design.schema.json` | 取らない。書くのは実装の worker が手元で打つときだけで、CI は読むだけ | 同じ型からは同じ文字列になり、打ち直しても中身は変わらない |
| `.ndf/design.json` を置く実装の Pull Request のマージと、進行中のレーンの直しのラウンド | リポジトリの `.ndf/design.json` | 取らない。マージの後に打ち直した検査が設定を読み、表が無ければ終了コード 1 で直しのラウンドへ渡る（決定 11） | 直しの worker が表を足せば次の検査で通る。プランは止まらない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_check` | 2 規則の指摘（`items` の `name`・`rule`・`text`）と件数（`metrics.repo_scope_missing`・`metrics.repo_loss_unrouted`）。要約の文に、指摘のある規則の種類として「代表のリポジトリの振る舞い」を足す | 結果 JSON（`lib/step_result.py` の形の 1 行。標準出力） | 秘密は扱わない。設定の値は種類の名前・パス・番号だけで、要約には写さない |
| `cmd_check` | 設定を読めないときの理由の 1 行 | 結果 JSON の `summary`（`status: stopped`） | 設定のファイルの中身の全文 |
| `cmd_schema` | スキーマの本文。`--out` のときは書いた先のパス | 標準出力（`--out` のときは加えて結果 JSON の 1 行） | 無し |
| `load_settings`・`check_repos`・`applies` | 出さない（戻り値と例外だけ） | — | — |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load_settings` | `lib/schema.py` の `Shape`・`load_shape`・`ShapeError` | `grep -n "^def \|^class " plugins/ndf/scripts/lib/schema.py`（2026-11-15） | 使う。知らない項目を拒む型と、誤りの日本語の 1 行（`too_short`・`string_pattern_mismatch` の理由を持つ）がそろっている |
| `load_settings` | `lib/project_mvv_decl.py` の `decl_problems`（標準ライブラリで形を見る） | `grep -n "schema\|def " plugins/ndf/scripts/lib/project_mvv_decl.py`（2026-11-15） | 使えない。MVV の宣言だけの形を見る関数で、別の設定の形を受けない。型を関数の中で組む形（`decl_models`）だけを倣う |
| `load_settings` | `lib/review_criteria_decl.py` の `_nonempty_globs` | `grep -n "def _nonempty_globs" plugins/ndf/scripts/lib/review_criteria_decl.py`（2026-11-15） | 使えない。内部の関数で、`Shape` の型の `min_length` が同じ確かめを持つ |
| `design_json_schema` | `lib/project_mvv_decl.py` の `mvv_json_schema` | 同上 | 使えない。MVV の型に固定した関数である。`model_json_schema()` に `$schema` を足す同じ書き方で書く |
| `applies` | `lib/pathmatch.py` の `path_matches`（pathspec の包み） | `grep -rln "pathspec" plugins/ndf/scripts/lib`（2026-11-15） | 使う。リポジトリの根から見た git の wildmatch の照らし合わせで、`plugins/ndf/**` が深い階層に当たる |
| `check_repos` | `lib/md.py` の `md_sections`・`section_named`・`tables_in`・`fenced_lines`・`Table.body_line` | `grep -n "^def \|^class " plugins/ndf/scripts/lib/md.py`（2026-11-15） | 使う。節の範囲・表のセル・本文の行番号を読む部品がそろっている（LIB1） |
| `_scope_problems` | `lib/md.py`（同上） | 同上 | 使う |
| `_loss_rows` | `lib/md.py` の `Table`（同上） | 同上 | 使う |
| `repo_items` | `design-survey.py` の `_result_files`（設計の結果の触るファイル）・`_left_table_rows`（人へ残す判断の表の番号のセル）・`doc_kinds.doc_kind` | `sed -n 120,240p plugins/ndf/scripts/design-survey.py`（2026-11-15） | 使う。触るファイルと番号のセルを読む部品は今の検査が持っており、写さずに呼ぶ。番号は `REDLINE_ID.findall` で切り出して `left_ids` にする |
| `cmd_schema` | `project-mvv.py` の `cmd_schema` | `sed -n 419,430p plugins/ndf/scripts/project-mvv.py`（2026-11-15） | 使えない。MVV のスキーマに固定したサブコマンドである。同じ形（標準出力と `--out`）で書く |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_settings` | 3 種・`paths`・`P2` の設定を読み、`RepoSettings` の値が一致する | ファイルが無い → `None`。`{"version": 1}` だけ（`representative_repos` が無い）→ `None`。`paths` を省く → `paths` が `None` | 読めない・JSON の誤り・知らない項目・`kinds` が空の配列・`redline` が `X1`・`paths` が空の配列・種類の重なりのそれぞれで `RepoSettingsError` |
| `design_json_schema` | 出力が公開した `design.schema.json` と一致する（生成物の食い違いを落とす） | 2 回呼んで同じ文字列 | 依存の欠けは `deps` の既存のテストが持つ |
| `applies` | `paths` が `plugins/ndf/**` で触るファイルが `plugins/ndf/scripts/a.py` → 真 | `paths` が `None` → 真。`touched` が `None` → 真。`touched` が空 → 偽。`plugins/mcp/x.json` だけ → 偽 | — |
| `check_repos` | 3 種の行と事情の欄があり、使えなくなるものがすべて `無し` → 空 | 種類で前方一致する行（`Web サービス・設定なし`）を数える。使えなくなるものがある行と、人へ残す判断に `P2` の行がある → 空。`P2` がインラインコードで書かれていても数える | 表が無い・種類の行が 1 つ欠ける・事情の欄が無い・事情の欄が空・使えなくなるものが空 → `repo_scope_missing`。使えなくなるものがある行があり、人へ残す判断に `P2` が無い（`P1` だけ・`P20` だけ）→ `repo_loss_unrouted` |
| `_scope_problems` | `check_repos` の正常の行で欠けが空 | 「適用範囲」の外にある同じ名前の小見出しは数えない | 「適用範囲」の節が無い → 1 件だけで表は `None` |
| `_loss_rows` | `無し` で始まる行（`無し（今と同じ）`）は入らない | `` `無し` `` の記法を外して `無し` として読む | 空のセルは 2 つ目の並びに入る |
| `repo_items` | 頭の設計文書だけに当て、決定の記録と要求のコピーには当てない | 触るファイルが `paths` に当たらない頭の設計文書は表が無くても空 | 文書の種類の契約を読めない → `StepError(EXIT_UNREADABLE)` |
| `judge_docs` | 設定ありで表の無い頭の設計文書 → `repo_scope_missing` の指摘と件数 | `settings` が `None` → 今のテスト（`test_design_survey.py`）の期待値がすべて変わらず、2 規則の件数が 0（I3） | — |
| `cmd_check` | `--file` で #3596 の前の設計と同じ形の合成の文書を渡し、終了コード 1 と `repo_scope_missing`（設計文書の「受け入れ条件 7 の確かめ」の C の形） | 設定が無い根で今と同じ終了コード | 壊れた設定の根で終了コード 2 と `status: stopped`（I4） |
| `cmd_schema` | 標準出力に JSON Schema が出る | `--out` のファイルが標準出力と同じ | 書けない `--out` で終了コード 2 |
