# #3615 の詳細設計: 設定したリリースの形とバージョンの保存先で、承認資料・リリースブランチ・タグを組む

設計文書 [issue-3615-design.md](issue-3615-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。決定の番号（決定 1〜14）は [issue-3615-design-decisions.md](issue-3615-design-decisions.md) を指す。不変条件の番号（I1〜I9）とドメインイベントの番号（E1〜E9）は、設計文書の「ドメインモデル」を指す。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| リリースの部品 | `plugins/ndf-next/src/ndf/domains/release/` の下のモジュール |
| 保存先 | バージョンの保存先（設定の `branch_strategy.release.version_store`）。種類は `plugin`・`json`・`pubspec`・`tag`・`none` |
| 単位 | バージョンの単位（設計文書の用語）。保存先 `plugin` ではプラグイン 1 つ、ほかではリポジトリ全体の 1 つ |
| 書式 | 名前の書式（タグ・リリースブランチ・CHANGELOG の見出し） |
| 範囲の起点・範囲の終点・前のリリースの起点 | 設計文書の用語のとおり |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/workflows/
└── ndf-release-reusable.yml *
docs/
├── glossary.md
└── glossary/glossary.json
plugins/ndf/
├── scripts/data/branch-strategy.schema.json
├── scripts/release_lib/land_read.py
└── skills/development-workflow/schemas/project.schema.json
plugins/ndf-next/
├── contracts/
│   ├── domains/release-forms.json *
│   ├── domains/test-files.json
│   └── schemas/ndf-settings/project.schema.json
├── docs/contracts/ndf-settings.md
├── skills/release/
│   ├── assets/workflows/ndf-release.yml *
│   └── references/{release-workflow.md *, release-steps.md}
├── src/ndf/
│   ├── adapters/git.py
│   ├── checks/children.json
│   └── domains/
│       ├── crossrefactor/tests_decide.py
│       └── release/
│           ├── __init__.py
│           ├── forms_decide.py *
│           ├── names_decide.py *
│           ├── since_decide.py *
│           ├── version_store.py *
│           ├── units.py *
│           ├── assemble.py  changelog_decide.py  changes_decide.py  cli.py
│           ├── gather.py  included_decide.py  land.py  materials_decide.py
│           └── ndf_settings.py  plugins.py  record.py  tagging.py  version_decide.py
└── tests/domains/
    ├── crossrefactor/test_scope_tests.py
    └── release/
        ├── test_release_forms.py *  test_release_names.py *  test_release_since.py *
        ├── test_release_version_store.py *  test_release_repos.py *
        └── test_release_assemble.py  test_release_decide.py  test_release_files.py  test_release_materials.py
```

依存の向きは一方向で、循環は無い。

| モジュール | 読むもの | 読まないもの |
| --- | --- | --- |
| 判断のモジュール（名前が `_decide.py` で終わるもの） | ほかの判断のモジュール・コントラクトの表だけ | git・GitHub・ファイル |
| 外部との接続のモジュール（`version_store`・`units`・`gather`・`assemble`・`tagging`・`land`・`record`） | 判断のモジュール・`adapters/git.py`・`adapters/github.py` | — |
| `cli` | 外部との接続のモジュール | — |
| `version_store` | `names_decide`（タグの読み戻し）・`plugins` | `gather` |
| `gather` | `version_store`・`since_decide` | `assemble` |

どのモジュールも `cli` を読まない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | 新設 | リリースの形の表から形の行を引き、扱わない形を止め、承認資料の届き方の 1 行を作る | `ndf.contracts.facts`・`release-forms.json` |
| `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | 書式からタグ・リリースブランチ・CHANGELOG の見出しを作り、名前から単位とバージョンを読み戻す | `version_decide` |
| `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | 新設 | 前のリリースの起点の候補から 1 つを選び、範囲の起点と終点をリリース方式から決める | `route_decide` |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | 保存先のインタフェースと 5 つのアダプター、設定から 1 つを選ぶ `load` | `names_decide`・`plugins`・`adapters/git.py`・`core/branch_strategy.py` |
| `plugins/ndf-next/src/ndf/domains/release/units.py` | 新設 | 本番ブランチの HEAD から選んだスプリントを順にマージした先頭を、一時の作業場所か手元のブランチで組む | `adapters/git.py`・`names_decide` |
| `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | 範囲・変化・含む PR・出し済みのバージョン・前のリリースの起点を、引数のリモート名と保存先で読む | `version_store`・`since_decide`・`changes_decide`・`included_decide`・`names_decide` |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | リリース方式に合わせて範囲を決め、保存先と書式でリリースブランチを組んで PR を作る | `gather`・`units`・`version_store`・`names_decide`・`forms_decide`・`changelog_decide` |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | 出したバージョンを保存先から求め、書式のタグと GitHub Release を作る。別のコミットを指すタグで止まる | `version_store`・`names_decide`・`changelog_decide`・`forms_decide` |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | 引数 `--units`・`--pr` とリモート名の解決と停止理由の対応を足す | 段階のモジュール |
| `plugins/ndf-next/src/ndf/domains/release/version_decide.py` | 変更 | バージョンの計算だけを持ち、名前を作る関数を `names_decide` へ渡す。計画に範囲の起点を足す | `semver` |
| `plugins/ndf-next/src/ndf/domains/release/changelog_decide.py` | 変更 | 見出しの書式でセクションを作る・探す・読む | `names_decide` |
| `plugins/ndf-next/src/ndf/domains/release/changes_decide.py` | 変更 | リリース対象外の既定をテストのファイルの表から作り、設定の形を足して分ける。リポジトリの根の単位を扱う | `ndf.contracts.facts`・`pathspec` |
| `plugins/ndf-next/src/ndf/domains/release/included_decide.py` | 変更 | リリースブランチを書式で見分け、リリースの単位のマージの件名を見分ける | 無し |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | 変更 | `undistributed` を既定へ足す追加の形として読む | `changes_decide` |
| `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | 変更 | 単位ごとの「前 → 次」と範囲の起点・届き方の行を出し、バージョンを持たない保存先ではバージョンの行を出さない | `forms_decide` |
| `plugins/ndf-next/src/ndf/domains/release/plugins.py` | 変更 | ref のプラグインの定義のバージョンを読む関数を `gather` から移す | `adapters/git.py` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | リリースブランチの名前を書式で読み、JSON の位置を持たない保存先でバージョンの候補を空にする | `names_decide` |
| `plugins/ndf-next/src/ndf/domains/release/record.py` | 変更 | リリースブランチの名前を書式で読む | `names_decide` |
| `plugins/ndf-next/src/ndf/domains/release/__init__.py` | 変更 | 段階とオーナーの表に新設の 5 モジュールを足す | 無し |
| `plugins/ndf-next/src/ndf/adapters/git.py` | 変更 | 遅れて読む名前 `ORIGIN`（モジュールの `__getattr__`）を消す | 無し |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/tests_decide.py` | 変更 | テストのファイルの表の役割 `test` の行だけでテストのファイルを見分ける | `ndf.contracts.facts` |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | 子 `B2`（#3615）の行と、S2 の `needs` の `B2` を足す | 無し |
| `plugins/ndf-next/contracts/domains/release-forms.json` | 新設 | リリースの形 6 行（`id`・`label`・`reach`・`supported`・`why`）。`owner` は `D2（contracts/domains。行はリリースの形を足す子が足す。初版は #3615）` | 無し |
| `plugins/ndf-next/contracts/domains/test-files.json` | 変更 | 列 `role` と行 `python-conftest` を足す | 無し |
| `plugins/ndf-next/contracts/schemas/ndf-settings/project.schema.json` | 変更 | `form` の値 2 つ・保存先 `pubspec`・`tag`・キー `branch`・`changelog_heading`・規則 R11 | 無し |
| `plugins/ndf-next/docs/contracts/ndf-settings.md` | 変更 | 設定のキーの説明に同じ変更を書く | 無し |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | ndf-next のスキーマのコピー（決定 11） | 無し |
| `plugins/ndf/scripts/data/branch-strategy.schema.json` | 変更 | ndf-next のスキーマのコピー（決定 11） | 無し |
| `plugins/ndf/scripts/release_lib/land_read.py` | 変更 | `plugin`・`json` 以外の保存先でバージョンの候補を空にする | 無し |
| `.github/workflows/ndf-release-reusable.yml` | 新設 | `workflow_call` で `assemble`・`merge`・`publish` を流す | `plugins/ndf-next/bin/ndf` |
| `plugins/ndf-next/skills/release/assets/workflows/ndf-release.yml` | 新設 | 利用者が置く呼び出し側のテンプレート | `.github/workflows/ndf-release-reusable.yml` |
| `plugins/ndf-next/skills/release/references/release-workflow.md` | 新設 | テンプレートの置き方・権限・秘密の名前の決め方・ほかの CI での並び | 無し |
| `plugins/ndf-next/skills/release/references/release-steps.md` | 変更 | サブコマンドの引数 `--units`・`--pr` と停止理由を足す | 無し |
| `plugins/ndf-next/tests/domains/release/test_release_forms.py` | 新設 | リリースの形の判断の単体テストと、表の行の `id` の集合とスキーマの `release.form` の列挙が一致するかの確認 | `forms_decide`・`ndf.contracts.load_json` |
| `plugins/ndf-next/tests/domains/release/test_release_names.py` | 新設 | 名前の判断の単体テスト | `names_decide` |
| `plugins/ndf-next/tests/domains/release/test_release_since.py` | 新設 | 起点の判断の単体テスト | `since_decide` |
| `plugins/ndf-next/tests/domains/release/test_release_version_store.py` | 新設 | 5 つのアダプターを一時リポジトリで確かめる | `version_store` |
| `plugins/ndf-next/tests/domains/release/test_release_repos.py` | 新設 | 代表のリポジトリの設定（Web・Flutter・Swift・業務文書・nd-factory のコピー）で `materials`・`assemble`・`publish` を一時リポジトリで流す。リリースの単位の組み立てもここで確かめる | `cli`・`units` |
| `plugins/ndf-next/tests/domains/release/test_release_decide.py` | 変更 | バージョンの計算・変化・含む PR・CHANGELOG のテストを新しいシグネチャへ合わせる | 判断のモジュール |
| `plugins/ndf-next/tests/domains/release/test_release_files.py` | 変更 | 設定の `undistributed` を足す形へ書き換える（`test_ndf_settings_parse_reads_every_key`） | `ndf_settings` |
| `plugins/ndf-next/tests/domains/release/test_release_assemble.py` | 変更 | ai-plugins の設定での組み立てとタグ（I9）・リモート名・タグの指す先の比べ（I7）・リリースブランチの重なり（I6） | `assemble`・`tagging` |
| `plugins/ndf-next/tests/domains/release/test_release_materials.py` | 変更 | 承認資料の届き方の行・範囲の起点・バージョンの行の有無 | `materials_decide` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_scope_tests.py` | 変更 | `conftest.py` をテストのファイルに数えない | `tests_decide` |
| `docs/glossary/glossary.json` | 変更 | 設計文書の用語の表の 7 語を足す・直す | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | `docs/glossary/glossary.json` |

## クラスと関数の構造

型の関係は設計文書の「構造」のセクションのクラス図を指し、ここでは描き直さない。

| ファイル | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | モジュール | 新設: `Form`・`FormUnsupported`・`form_of`・`reach_line` |
| `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | `NameFormats` と モジュール | 新設: `NameFormats`（`tag_of`・`version_of_tag`・`branch_of`・`version_of_branch`・`is_release_branch`・`heading_of`・`heading_key`）・`FormatMismatch`・`formats_of`・`highest_tag`・`_pattern` |
| `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | モジュール | 新設: `Span`・`SpanKind`・`kind_of`・`prev_release`・`since_of`・`until_of` |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | `VersionStore`（Protocol）と 5 つのクラス | 新設: `Unit`・`VersionStore`・`StoreError`・`PluginStore`・`JsonStore`・`PubspecStore`・`TagStore`・`NoneStore`（それぞれ `units`・`read`・`write`・`paths`）・`KINDS`・`load`・`tags_at`・`pubspec_version`・`pubspec_replace` |
| `plugins/ndf-next/src/ndf/domains/release/units.py` | モジュール | 新設: `UnitsError`・`merge_subject`・`build`・`scratch` |
| `plugins/ndf-next/src/ndf/domains/release/gather.py` | モジュール | 変更: `fetch`・`included`・`shipped`・`changes`・`plan_bumps`。新設: `fetch_tags`・`introduced`・`prev_releases`。削除: `_production_version`（`plugins.version_at` へ移す） |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `Target` と モジュール | 変更: `Target`（`remote`・`units`・`pr` を足す）・`_span`・`open_releases`・`_write`・`_commit_and_push`・`_facts`・`collect`・`assemble`。新設: `NoReleasePr` |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | モジュール | 変更: `_tag`・`_released`・`publish`。新設: `TagMoved`・`_released_by_branch` |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | モジュール | 変更: `_target`・`_target_args`・`cmd_assemble`・`cmd_materials`・`cmd_publish`・`_ERRORS`。新設: `_remote` |
| `plugins/ndf-next/src/ndf/domains/release/version_decide.py` | `Planned` と モジュール | 変更: `Planned`（`since` を足す）。削除: `BRANCH_PREFIX`・`TAG_SEPARATOR`・`branch_of`・`version_of_branch`・`tag_of`・`version_of_tag`（`names_decide` へ移す） |
| `plugins/ndf-next/src/ndf/domains/release/changelog_decide.py` | モジュール | 変更: `block`・`insert`・`notes`・`_span`。削除: `changelog_heading`・`changelog_key`（`NameFormats.heading_of`・`heading_key` へ移す） |
| `plugins/ndf-next/src/ndf/domains/release/changes_decide.py` | モジュール | 変更: `_under`・`split`・`changes`。新設: `default_undistributed`。削除: `DEFAULT_UNDISTRIBUTED` |
| `plugins/ndf-next/src/ndf/domains/release/included_decide.py` | モジュール | 変更: `_excluded`・`number_of`・`numbers`。新設: `is_unit_merge` |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | `NdfSettings` と モジュール | 変更: `NdfSettings.undistributed` の既定・`_parse` |
| `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | `Facts` と モジュール | 変更: `Facts`（`form`・`versioned` を足す）・`render`・`_plugins`（`_versions` へ改名） |
| `plugins/ndf-next/src/ndf/domains/release/plugins.py` | モジュール | 新設: `version_at` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | `Lander` と モジュール | 変更: `version_rows`・`Lander.land` |
| `plugins/ndf-next/src/ndf/domains/release/record.py` | モジュール | 変更: `released`・`write`（書式を渡す） |
| `plugins/ndf-next/src/ndf/adapters/git.py` | モジュール | 削除: `__getattr__` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/tests_decide.py` | モジュール | 変更: `is_test_file` |
| `plugins/ndf/scripts/release_lib/land_read.py` | モジュール | 変更: `store_version` |

## 関数の仕様

型の略: `Git` は `adapters.git.Git`、`GitHub` は `adapters.github.GitHub`、`Strategy` は `core.branch_strategy.Strategy`。

| 関数 | ファイル | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `forms_decide.form_of` | `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | 新設 | `form_of(name: str) -> Form` | 設定の形の名前から表の行を引き、扱わない形なら止める | 表 `release-forms` が読める | 返る行の `supported` は真 | 無し |
| `forms_decide.reach_line` | `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | 新設 | `reach_line(form: Form) -> str` | 承認資料の届き方の 1 行を作る | `form` は `form_of` が返した行 | 行は `- 届き方: <label>: <reach>` の形 | 無し |
| `names_decide.formats_of` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `formats_of(release: Mapping[str, Any]) -> NameFormats` | 設定の `release` のセクションから書式を作り、無いキーに既定を入れる | セクションはスキーマを通った値 | `branch`・`changelog_heading` は既定か設定の値。`tag` は設定の値（`None` を含む） | 無し |
| `NameFormats.tag_of` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `tag_of(self, unit: str, version: str) -> str \| None` | 単位と基底のバージョンからタグの名前を作る | `version` は SemVer | `tag` が `None` なら `None`。ほかは置き換えの語をすべて埋めた名前 | 無し |
| `NameFormats.version_of_tag` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `version_of_tag(self, unit: str, name: str) -> str \| None` | タグの名前がその単位の書式に全体一致すればバージョンを返す | 無し | `tag_of(unit, v)` の結果を渡すと `v` の基底が返る（I3）。合わなければ `None` | 無し |
| `NameFormats.branch_of` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `branch_of(self, version: str) -> str` | リリースブランチの名前を作る | `version` は SemVer | 名前は `branch` の `{version}` を埋めたもの | 無し |
| `NameFormats.version_of_branch` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `version_of_branch(self, name: str) -> str` | リリースブランチの名前からバージョンを読む | 無し | `branch_of(v)` を渡すと `v` が返る | 無し |
| `NameFormats.is_release_branch` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `is_release_branch(self, name: str) -> bool` | 名前がリリースブランチの書式に合うかを返す | 無し | 合えば真 | 無し |
| `NameFormats.heading_of` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `heading_of(self, unit: str, version: str, date: str) -> str` | CHANGELOG の見出しを作る | `date` は `YYYY-MM-DD` | 見出しは `## ` で始まる | 無し |
| `NameFormats.heading_key` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `heading_key(self, unit: str, version: str) -> str` | 見出しの書式から `{date}` 以降を除いた、セクションを探す先頭を作る | 無し | `heading_of(u, v, d)` は返す値で始まる | 無し |
| `names_decide.highest_tag` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `highest_tag(formats: NameFormats, unit: str, tags: Iterable[str]) -> tuple[str, str] \| None` | タグの一覧から、その単位の書式に合い接尾辞の無い最も大きいバージョンの (タグ, バージョン) を選ぶ | 無し | 合うタグが無いか `tag` が `None` なら `None` | 無し |
| `names_decide._pattern` | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | 新設 | `_pattern(fmt: str, unit: str) -> re.Pattern[str]` | 書式の置き換えの語の外をエスケープし、語を形の正規表現に置き換えた全体一致の式を作る | `fmt` は `{version}` を含む | 式は名前付きの組 `version` を持つ | 無し |
| `since_decide.kind_of` | `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | 新設 | `kind_of(strategy: Strategy) -> SpanKind` | リリース方式と承認の対象から範囲の取り方を 1 つ決める | 設定はスキーマを通った値 | 4 つの値（`base`・`trunk`・`units`・`pr`）のどれか | 無し |
| `since_decide.prev_release` | `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | 新設 | `prev_release(tagged: str \| None, introduced: str \| None, root_commit: str) -> str` | 前のリリースの起点の候補から、タグ・バージョンの変わり目・最初のコミットの順に 1 つを選ぶ（決定 6） | `root_commit` は空でない | 最初の `None` でない候補を返す | 無し |
| `since_decide.since_of` | `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | 新設 | `since_of(kind: SpanKind, production_head: str, prev: str \| None, fork_point: str \| None) -> str` | 範囲の起点を範囲の取り方から決める（I4） | `trunk`・`units` では `prev` が空でない。`pr` では `fork_point` が空でない | `base` は本番ブランチの HEAD、`trunk`・`units` は `prev`、`pr` は `fork_point` | 無し |
| `since_decide.until_of` | `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | 新設 | `until_of(kind: SpanKind, base_head: str, production_head: str, units_head: str \| None, pr_head: str \| None) -> str` | 範囲の終点を範囲の取り方から決める | `units` では `units_head`、`pr` では `pr_head` が空でない | 設計文書の「処理の流れ」の終点の表のとおり | 無し |
| `version_store.load` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `load(root: Path, strategy: Strategy, repo_name: str) -> VersionStore` | 設定の保存先の種類から 1 つのアダプターを作る（I2） | `root` は範囲の終点を取り出した作業場所 | 返るアダプターの `kind` は設定の種類 | 無し |
| `version_store.tags_at` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `tags_at(git: Git, ref: str) -> list[str]` | `ref` から辿れるタグの名前を並べる | タグを手元へ取得済み（`gather.fetch_tags`） | 名前の一覧（順は不定） | 外部コマンド `git tag --list --merged` |
| `version_store.pubspec_version` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `pubspec_version(text: str) -> str \| None` | `pubspec.yaml` の最上位の `version:` の行の `+` の前を読む | 無し | 行が無ければ `None` | 無し |
| `version_store.pubspec_replace` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `pubspec_replace(text: str, new: str) -> str` | 最上位の `version:` の行だけを置き換え、数のビルド番号を 1 つ上げる（決定 3） | 行が 1 つある | ほかの行は 1 文字も変わらない。数でないビルド番号はそのまま残る | 無し |
| `PluginStore.units` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `units(self) -> list[Unit]` | マーケットプレイスの登録のプラグインを単位にする | 作業場所に登録がある | 登録の順 | ファイル（登録の読み） |
| `PluginStore.read` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `read(self, git: Git, ref: str, unit: Unit) -> str \| None` | `ref` のプラグインの定義のバージョンを読む | `ref` が解決できる | 定義が無ければ `None` | 外部コマンド `git ls-tree`・`git show` |
| `PluginStore.write` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | 今の `plugins.bump` で定義・登録の説明・`files` を書き換える | 作業場所はリリースブランチ | 書き換えたパスを返す | ファイル（書き換え） |
| `PluginStore.paths` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `paths(self) -> frozenset[str]` | 書き換えてよいパス（登録・定義・`files`）を返す | 無し | 空でない | ファイル（登録の読み） |
| `JsonStore.units` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `units(self) -> list[Unit]` | リポジトリ全体の 1 単位を返す | 無し | 1 件。名前は `release.plugin` かリポジトリの名前、`dir` は空 | 無し |
| `JsonStore.read` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `read(self, git: Git, ref: str, unit: Unit) -> str \| None` | `ref` の `path` の `pointer` の値を読む | 無し | ファイルが無ければ `None` | 外部コマンド `git show` |
| `JsonStore.write` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | `path` の `pointer` と `files` を今の規則で書く | ファイルがあり値が文字列 | 書き換えたパスを返す | ファイル（書き換え） |
| `JsonStore.paths` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `paths(self) -> frozenset[str]` | `path` と `files` のパスを返す | 無し | 空でない | 無し |
| `PubspecStore.read` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `read(self, git: Git, ref: str, unit: Unit) -> str \| None` | `ref` の `path` を `pubspec_version` で読む | 無し | ファイルか行が無ければ `None` | 外部コマンド `git show` |
| `PubspecStore.write` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | `path` を `pubspec_replace` で書く | ファイルに行がある | `[path]` を返す | ファイル（書き換え） |
| `TagStore.read` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `read(self, git: Git, ref: str, unit: Unit) -> str \| None` | `ref` から辿れるタグのうち書式に合う最も大きいバージョンを読む | タグを取得済み | 合うタグが無ければ `None` | 外部コマンド `git tag --list --merged` |
| `TagStore.write` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | 何も書かない | 無し | 空を返す | 無し |
| `NoneStore.read` | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | `read(self, git: Git, ref: str, unit: Unit) -> str \| None` | バージョンを持たないことを返す | 無し | 常に `None` | 無し |
| `units.merge_subject` | `plugins/ndf-next/src/ndf/domains/release/units.py` | 新設 | `merge_subject(unit: str, branch: str) -> str` | リリースの単位のマージの件名を作る（10 系と同じ形） | 無し | `Merge <unit> into <branch>` | 無し |
| `units.build` | `plugins/ndf-next/src/ndf/domains/release/units.py` | 新設 | `build(git: Git, remote: str, production: str, units: Sequence[str], branch: str) -> str` | 本番ブランチの HEAD から detached HEAD で、選んだスプリントを `--no-ff` で順にマージし先頭の SHA を返す（決定 8） | 作業場所に変更が無い。`units` が空でない | HEAD は組んだ先頭。既に入っているスプリントは飛ばす | 外部コマンド `git fetch`・`git checkout`・`git merge` |
| `units.scratch` | `plugins/ndf-next/src/ndf/domains/release/units.py` | 新設 | `scratch(git: Git, remote: str, production: str, units: Sequence[str], branch: str) -> ContextManager[tuple[Path, str]]` | 一時の作業場所を作って `build` を流し、抜けるときに外す | 無し | 抜けた後に一時の作業場所もブランチも残らない | ファイル（一時ディレクトリ）・外部コマンド `git worktree add`・`git worktree remove` |
| `gather.fetch` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | `fetch(git: Git, remote: str, name: str) -> None` | 引数のリモートのブランチを取得する（I8） | 無し | `<remote>/<name>` が手元にある | 外部コマンド `git fetch` |
| `gather.fetch_tags` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 新設 | `fetch_tags(git: Git, remote: str) -> None` | 引数のリモートのタグを手元へ取得する | 無し | リモートのタグが手元にある | 外部コマンド `git fetch --tags` |
| `gather.introduced` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 新設 | `introduced(git: Git, store: VersionStore, unit: Unit, production_ref: str) -> str \| None` | 本番ブランチの履歴で、今のバージョンが保存先に初めて入ったコミットを求める（10 系の `untagged_since`） | 無し | 保存先がファイルを持たない（`paths()` が空）か今のバージョンが無ければ `None` | 外部コマンド `git log`・`git show` |
| `gather.prev_releases` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 新設 | `prev_releases(git: Git, store: VersionStore, formats: NameFormats, units: Sequence[Unit], production_ref: str) -> dict[str, str]` | 単位ごとの前のリリースの起点を求める（決定 6） | タグを取得済み | すべての単位が値を持つ | 外部コマンド `git tag`・`git log`・`git rev-list` |
| `gather.included` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | `included(git: Git, since: str, until: str, production: str, formats: NameFormats) -> list[int]` | 範囲の first-parent の PR と、リリースの単位のマージの第 1 親..第 2 親の PR を合わせる（決定 8） | 無し | 番号は小さい順で重なりが無い | 外部コマンド `git log` |
| `gather.shipped` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | `shipped(git: Git, remote: str, store: VersionStore, formats: NameFormats, units: Sequence[Unit], production_ref: str) -> dict[str, str]` | 単位ごとの出し済みのバージョン（本番ブランチの保存先とリモートのタグの大きい方）を求める | 無し | バージョンの無い単位は入らない | 外部コマンド `git ls-remote`・`git show` |
| `gather.changes` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | `changes(git: Git, since_by_unit: Mapping[str, str], until: str, units: Sequence[Unit], decl: NdfSettings) -> list[Change]` | 単位ごとに、その単位の起点からの範囲で変化を分ける（決定 6） | 起点がすべての単位にある | 変化のある単位だけを名前の順で返す | 外部コマンド `git diff` |
| `gather.plan_bumps` | `plugins/ndf-next/src/ndf/domains/release/gather.py` | 変更 | `plan_bumps(git: Git, store: VersionStore, ref: str, units: Sequence[Unit], change_list: Sequence[Change], decl: NdfSettings, since_by_unit: Mapping[str, str], level: str, channel: str) -> list[Planned]` | バージョンを上げる単位と次のバージョンと範囲の起点を決める（I5） | 無し | 保存先 `none` では空。`tag` で今のバージョンが無ければ `0.0.0` から上げる | 外部コマンド `git show`・`git tag` |
| `plugins.version_at` | `plugins/ndf-next/src/ndf/domains/release/plugins.py` | 新設 | `version_at(git: Git, ref: str, plugin: Plugin) -> str \| None` | `ref` のプラグインの定義（先頭）のバージョンを読む（`gather._production_version` を移す） | 無し | 定義が無ければ `None` | 外部コマンド `git ls-tree`・`git show` |
| `assemble._span` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `_span(git: Git, target: Target, kind: SpanKind, hub: GitHub) -> tuple[str, str \| None]` | 要るブランチを取得し、範囲の終点（と `pr` の分岐点）を決める | `pr` では `target.pr` がある | 終点は解決できる ref | 外部コマンド `git fetch`・`git merge-base`・ネットワーク（PR の読み） |
| `assemble.open_releases` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `open_releases(hub: GitHub, target: Target, formats: NameFormats) -> list[int]` | 本番ブランチ宛てで head がリリースブランチの書式に合うかベースブランチの開いている PR を並べる | 無し | 番号は小さい順 | ネットワーク（PR の一覧） |
| `assemble._write` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `_write(root: Path, decl: NdfSettings, facts: Facts, store: VersionStore, formats: NameFormats) -> None` | 保存先でバージョンを書き、見出しの書式で CHANGELOG を書き、設定の手順を流す | 作業場所はリリースブランチ | 書き換えたファイルは保存先の `paths()` と CHANGELOG と手順の `writes` の中 | ファイル（書き換え）・外部コマンド（設定の手順） |
| `assemble._commit_and_push` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `_commit_and_push(git: Git, remote: str, branch: str, message: str) -> str` | 1 つのコミットにして引数のリモートへ送る。差分が無ければ空のコミットにする | 無し | リモートに `branch` があり、返す SHA を指す | 外部コマンド `git add`・`git commit`・`git push` |
| `assemble._facts` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `_facts(root: Path, hub: GitHub, target: Target, strategy: Strategy, since: str \| None, until: str) -> Facts` | 作業場所から承認資料の材料を読む | 作業場所の HEAD は `until` | `planned` の各行に範囲の起点がある。バージョンを持つ保存先で上げるものが無ければ `NothingToRelease` | 外部コマンド（git）・ネットワーク（PR の題と CI） |
| `assemble.collect` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `collect(root: Path, hub: GitHub, target: Target, strategy: Strategy) -> Facts` | ブランチを残さずに承認資料の材料を組む。`units` は一時の作業場所で組む | 形は扱う形 | 手元のブランチ・作業場所は呼ぶ前と同じ | 外部コマンド（git）・ファイル（一時ディレクトリ）・ネットワーク |
| `assemble.assemble` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `assemble(root: Path, hub: GitHub, target: Target, strategy: Strategy) -> Built` | リリース方式に合わせてリリースブランチを組み、PR を作る | 形は扱う形 | リモートにリリースブランチと PR がある。`pr` の取り方では `NoReleasePr` | 外部コマンド（git）・ネットワーク（PR の作成と本文） |
| `tagging._tag` | `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | `_tag(hub: GitHub, tag: str, revision: str) -> Made` | タグが無ければ作り、あれば指す先を比べる（決定 12） | 無し | タグは `revision` を指す | ネットワーク（ref の読みと作成） |
| `tagging._released` | `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | `_released(git: Git, store: VersionStore, hub: GitHub, formats: NameFormats, revision: str) -> list[tuple[Unit, str]]` | そのコミットで出したバージョンを保存先から求める（決定 2） | 無し | 接尾辞の無いバージョンだけを返す | 外部コマンド（git）・ネットワーク（`_released_by_branch` を通る） |
| `tagging._released_by_branch` | `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 新設 | `_released_by_branch(hub: GitHub, formats: NameFormats, revision: str) -> str \| None` | コミットに結び付いたマージ済みの PR の head の名前をブランチの書式で読む | 無し | 結び付いた PR が無いか書式に合わなければ `None` | ネットワーク（コミットの PR の読み） |
| `tagging.publish` | `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | `publish(root: Path, hub: GitHub, revision: str, strategy: Strategy) -> list[Made]` | 形を確かめ、出したバージョンのタグと（設定が真なら）GitHub Release を作る | 形は扱う形 | 書式が `None` なら空。作るタグと Release は 1 件ずつ結果に並ぶ | ネットワーク（ref と Release の作成）・ファイル（CHANGELOG の読み） |
| `cli._remote` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 新設 | `_remote(args: argparse.Namespace) -> str` | メインディレクトリの設定からリモート名を 1 度だけ求める（決定 9） | 無し | 空でない名前 | ファイル（`.ndf/worktree.json` の読み） |
| `cli._target` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `_target(args: argparse.Namespace, remote: str) -> Target` | 引数とリモート名から組み立ての指定を作る | 無し | `Target.remote` は空でない | 無し |
| `cli.cmd_assemble` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_assemble(args: argparse.Namespace) -> dict` | 設定を読み、形を確かめて組み立てを呼ぶ | 無し | 結果 JSON の `outputs.branch` は書式の名前 | 外部コマンド（git）・ネットワーク |
| `cli.cmd_materials` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_materials(args: argparse.Namespace) -> dict` | 設定を読み、形を確かめて承認資料を組む | 無し | `--out` があればそのファイルに資料がある | ファイル（`--out` の書き込み）・外部コマンド（git）・ネットワーク |
| `cli.cmd_publish` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_publish(args: argparse.Namespace) -> dict` | 設定を読み、タグと GitHub Release を作る | 無し | `items` の `name` は書式の名前 | ネットワーク |
| `version_decide.Planned` | `plugins/ndf-next/src/ndf/domains/release/version_decide.py` | 変更 | `Planned(plugin: str, current: str, next: str, since: str)` | 単位ごとのバージョンアップの計画に範囲の起点を持たせる | 無し | `since` は空でない SHA | 無し |
| `changelog_decide.block` | `plugins/ndf-next/src/ndf/domains/release/changelog_decide.py` | 変更 | `block(heading: str, entries: Sequence[Entry]) -> list[str]` | 渡された見出しでセクションの行を作る | `heading` は `## ` で始まる | 見出し・空行・PR ごとの行・空行 | 無し |
| `changelog_decide.insert` | `plugins/ndf-next/src/ndf/domains/release/changelog_decide.py` | 変更 | `insert(text: str, key: str, new: Sequence[str]) -> str` | `key` で始まる見出しのセクションを置き換えるか、最初のセクションの前へ差し込む | 無し | ほかのセクションは 1 行も変わらない | 無し |
| `changelog_decide.notes` | `plugins/ndf-next/src/ndf/domains/release/changelog_decide.py` | 変更 | `notes(text: str, key: str) -> str \| None` | `key` で始まる見出しのセクションの本文を読む | 無し | 無ければ `None` | 無し |
| `changes_decide.default_undistributed` | `plugins/ndf-next/src/ndf/domains/release/changes_decide.py` | 新設 | `default_undistributed() -> tuple[str, ...]` | テストのファイルの表のすべての行から、リリース対象外の gitignore の形を作る（決定 5） | 表が読める | 保存先の行は `<名前>/`、名前の行は `<形>` | 無し |
| `changes_decide.split` | `plugins/ndf-next/src/ndf/domains/release/changes_decide.py` | 変更 | `split(paths: Iterable[str], unit: str, directory: str, extra: Sequence[str]) -> Change` | 既定と `extra` を合わせた形で、単位の下の変化を分ける。`directory` が空なら根の下のすべて | 無し | 各パスは 2 つの組のどちらか一方にだけ入る | 無し |
| `changes_decide.changes` | `plugins/ndf-next/src/ndf/domains/release/changes_decide.py` | 変更 | `changes(paths_by_unit: Mapping[str, Sequence[str]], unit_dirs: Mapping[str, str], extra: Sequence[str]) -> list[Change]` | 単位ごとに渡された範囲のパスを分ける | 2 つの写像のキーが同じ | 変化のある単位だけを名前の順 | 無し |
| `included_decide.number_of` | `plugins/ndf-next/src/ndf/domains/release/included_decide.py` | 変更 | `number_of(subject: str, skipped: Collection[str], is_release: Callable[[str], bool]) -> int \| None` | 件名 1 行の PR の番号を、リリースブランチと `skipped` からのマージを除いて読む | 無し | PR のマージでなければ `None` | 無し |
| `included_decide.numbers` | `plugins/ndf-next/src/ndf/domains/release/included_decide.py` | 変更 | `numbers(subjects: Iterable[str], skipped: Collection[str], is_release: Callable[[str], bool]) -> list[int]` | 件名の並びから含む PR の番号を集める | 無し | 小さい順で重なりが無い | 無し |
| `included_decide.is_unit_merge` | `plugins/ndf-next/src/ndf/domains/release/included_decide.py` | 新設 | `is_unit_merge(subject: str, is_release: Callable[[str], bool]) -> bool` | 件名がリリースの単位のマージ（`Merge <スプリント> into <リリースブランチ>`）かを見分ける | 無し | リリースブランチの部分が書式に合うときだけ真 | 無し |
| `ndf_settings._parse` | `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | 変更 | `_parse(raw: dict) -> NdfSettings` | `undistributed` を既定へ足す追加の形として読む | 無し | キーが無ければ `undistributed` は空 | 無し |
| `materials_decide.render` | `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | 変更 | `render(facts: Facts) -> str` | 届き方の行と、単位ごとの「前 → 次」と範囲の起点を出す | 無し | `versioned` が偽ならバージョンのセクションを出さない | 無し |
| `land.version_rows` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `version_rows(strategy: Strategy, show: Callable[[str], str \| None]) -> list[tuple[str, str]]` | `plugin`・`json` 以外の保存先で候補を空にする（決定 11） | 無し | `pubspec`・`tag`・`none` では空 | 無し |
| `Lander.land` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `land(self, number: int) -> Landed` | リリースブランチの名前を書式で読む | 無し | 書式に合わない head では今と同じく止まる | ネットワーク（PR の読みとマージ） |
| `record.released` | `plugins/ndf-next/src/ndf/domains/release/record.py` | 変更 | `released(hub: GitHub, number: int, production: str, formats: NameFormats) -> tuple[Pull, Record]` | リリースの PR の head の名前を書式で読んでバージョンを決める | 無し | 書式に合わなければ `NotReleased` | ネットワーク（PR の読み） |
| `tests_decide.is_test_file` | `plugins/ndf-next/src/ndf/domains/crossrefactor/tests_decide.py` | 変更 | `is_test_file(path: str) -> bool` | 表の役割 `test` の行（列が無い行は `test`）だけで見分ける | 無し | `conftest.py` は偽 | 無し |
| `land_read.store_version` | `plugins/ndf/scripts/release_lib/land_read.py` | 変更 | `store_version(strategy, show) -> str \| None` | `plugin`・`json` 以外の保存先で `None` を返す（決定 11） | 無し | `pubspec`・`tag` で読み出しの例外が出ない | 無し |

次のメソッドは、上の同じ名前のメソッドと同じ形で、値だけが決定 2 の表の行に従う（`TagStore.paths`・`NoneStore.paths` は空）。

- `PluginStore`: `paths`
- `JsonStore`: `units`・`paths`
- `TagStore`: `units`・`paths`
- `NoneStore`: `units`・`write`・`paths`
- `PubspecStore`: `units`・`paths`

`cli._target_args` は引数 `--units`（`nargs="+"`）と `--pr`（`int`）を足すだけで、ここに書かない。

## 異常系の処理

停止理由は結果 JSON の `stopped` の種類である（`cli._ERRORS` の表が例外から引く）。終了コードは `result.emit` の規則のまま（`ok` は 0、`stopped` は種類ごとの値）。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `forms_decide.form_of` | 扱わない形 | 形の行の `supported` が偽（`desktop`） | 表の行を引いた直後 | 止める（`FormUnsupported`） | `precondition`。形の名前・表のパス・`why` を出す（I1） | 何も書いていない。設定を直せば再実行できる |
| `forms_decide.form_of` | 表に無い形 | スキーマを通らない設定（手で書いた値） | 表の行を引くとき | 止める（`FormUnsupported`） | `precondition`。取れる形の一覧を出す | 同上 |
| `NameFormats.version_of_branch` | 書式に合わない | head がリリースブランチでない | 全体一致の判定 | 呼び出し元へ返す（`FormatMismatch`。`VersionError` の子） | 呼び出し元の扱いに従う（`land` は今と同じ止まり方、`record` は `not_applicable`） | 何も書いていない |
| `version_store.load` | 表に無い種類 | 設定の `kind` が `KINDS` に無い | `KINDS` を引くとき | 止める（`StoreError`） | `precondition`。取れる種類を出す（I2） | 何も書いていない。設定を直せば再実行できる |
| `version_store.tags_at` | git が失敗する | `ref` が無い・リポジトリが壊れている | `Git.run` の終了コード | 呼び出し元へ返す（`GitError`） | `precondition`。git の標準エラーを出す | 何も書いていない |
| `PluginStore.units` | 登録が読めない | `.claude-plugin/marketplace.json` が無いか壊れている | `plugins.load` | 止める（`PluginsError`） | `precondition`（今と同じ） | 何も書いていない |
| `PluginStore.paths` | 登録が読めない | 同上 | `plugins.load` | 止める（`PluginsError`） | `precondition` | 何も書いていない |
| `PluginStore.read` | 定義が JSON として読めない | 本番ブランチの定義が壊れている | `json.loads` | 止める（`PluginsError`） | `precondition`。ref とパスを出す（今の `_production_version` と同じ） | 何も書いていない |
| `PluginStore.write` | 書き換えられない | 定義の 2 本のバージョンが違う・`files` の位置が無い | `plugins.bump` | 止める（`PluginsError`） | `precondition` | リリースブランチは送る前なので残らない。作業場所の変更は `assemble` の作業場所ごと捨てる |
| `JsonStore.read` | 位置が文字列でない | `pointer` の値が無いか文字列でない | 位置の解決の後 | 止める（`StoreError`） | `precondition`。パスと位置を出す | 何も書いていない |
| `JsonStore.write` | 位置が文字列でない | 同上 | 書き換えの前 | 止める（`StoreError`） | `precondition` | 送る前に止まるのでリモートに何も残らない |
| `PubspecStore.read` | `version:` の行が無い | 最上位に行が無い | `pubspec_version` が `None` | 既定で続ける（`None`。初めてのリリースとして `0.0.0` から上げる） | 承認資料の「前」が `無し` になる | 無し |
| `PubspecStore.write` | `version:` の行が無いか 2 つある | ファイルの形が違う | `pubspec_replace` の前の数え | 止める（`StoreError`） | `precondition`。パスと行数を出す | 送る前に止まる |
| `TagStore.read` | git が失敗する | タグの一覧が読めない | `tags_at` | 呼び出し元へ返す（`GitError`） | `precondition` | 何も書いていない |
| `units.build` | 衝突する | 選んだスプリントどうしか本番ブランチとぶつかる | `git merge` の終了コード | 止める（`UnitsError`）。`git merge --abort` の後に投げる | `precondition`。ぶつかったスプリントと標準出力の末尾 400 文字 | 作業場所は中断の前の状態。衝突を人が解いた後に再実行できる |
| `units.build` | スプリントが無い | `--units` のブランチがリモートに無い | `git fetch` の終了コード | 止める（`UnitsError`） | `precondition`。ブランチの名前 | 何も書いていない |
| `units.scratch` | 一時の作業場所を外せない | 中のファイルが別のプロセスに開かれている | `git worktree remove` の終了コード | 既定で続ける（外せなかったパスを結果の `process` に出し、`git worktree prune` を打つ） | 結果は本体の結果のまま。外せなかったパスが `process` に並ぶ | 次の `cleanup` か `git worktree prune` で外れる。再実行できる |
| `gather.fetch` | 取得できない | ブランチが無い・ネットワーク | `fetch_branch` の終了コード | 止める（`GitError`） | `precondition`。リモート名とブランチ名 | 何も書いていない |
| `gather.fetch_tags` | 手元のタグとぶつかる | 同じ名前の手元のタグが別のコミットを指す | `git fetch --tags` の終了コード | 止める（`GitError`） | `precondition`。手元のタグを消すか合わせるよう出す | 手元のタグは書き換えない |
| `gather.introduced` | 履歴が読めない | 本番ブランチが浅い取得で切れている | `git log` の終了コード | 既定で続ける（`None`。次の候補の最初のコミットへ進む） | 承認資料の範囲の起点が最初のコミットになる | 無し。`fetch-depth: 0` で取り直せば変わる |
| `gather.prev_releases` | 最初のコミットが読めない | 本番ブランチが無い | `git rev-list --max-parents=0` | 止める（`GitError`） | `precondition` | 何も書いていない |
| `gather.included` | git が失敗する | 範囲の ref が無い | `Git.run` | 止める（`GitError`） | `precondition` | 何も書いていない |
| `gather.shipped` | リモートのタグが読めない | ネットワーク・権限 | `git ls-remote` の終了コード | 止める（`GitError`） | `precondition` | 何も書いていない |
| `gather.changes` | git が失敗する | 起点の SHA が手元に無い | `changed_files` | 止める（`GitError`） | `precondition` | 何も書いていない |
| `gather.plan_bumps` | 今のバージョンが SemVer でない | 保存先の値が `1.2` など | `version_decide.parse` | 止める（`VersionError`） | `precondition`。値を出す | 何も書いていない |
| `plugins.version_at` | 定義が読めない | 同 `PluginStore.read` | `json.loads` | 止める（`PluginsError`） | `precondition` | 何も書いていない |
| `assemble._span` | `--pr` が無い | `pr` の取り方で引数が無い | 範囲の取り方を決めた直後 | 止める（`AssembleError`） | `precondition`。`--pr` が要ると出す（I4） | 何も書いていない |
| `assemble._span` | `--units` が無い | `units` の取り方で引数が無い | 同上 | 止める（`AssembleError`） | `precondition`。`--units` が要ると出す | 何も書いていない |
| `assemble._span` | PR が本番ブランチ宛てでない | `--pr` の番号の誤り | PR の `base_ref` | 止める（`AssembleError`） | `precondition` | 何も書いていない |
| `assemble.open_releases` | 1 ページで読み切れない | 開いている PR が `PER_PAGE` 件以上 | 件数 | 止める（`AssembleError`。今と同じ） | `precondition` | 何も書いていない |
| `assemble._write` | 設定の手順が落ちる | 手順のコマンドの失敗 | `run_step` | 止める（`StepFailed`） | `check_failed`。手順の名前と標準エラーの末尾 | 送る前に止まる。手元の作業場所の変更は workflow のクリーンアップで消える |
| `assemble._write` | 保存先の書き換えの失敗 | 上の `*Store.write` の行 | `store.write` | 止める（例外をそのまま） | 上の行の出力 | 送る前に止まる |
| `assemble._commit_and_push` | 送れない | 権限・同じ名前のブランチが先に作られた | `git push` の終了コード | 止める（`GitError`） | `precondition`。リモート名とブランチ名 | リモートに何も残らない。同じブランチが先にあれば I6 の止まり方へ進む |
| `assemble._facts` | 上げるものが無い | バージョンを持つ保存先で変化がリリース対象外だけ | 計画が空 | 止める（`NothingToRelease`） | `not_applicable`。範囲を出す | 何も書いていない |
| `assemble.collect` | 扱わない形 | `form_of` の行 | `cli` が先に呼ぶ | 止める | `form_of` の行のとおり | 何も書いていない |
| `assemble.collect` | 一時の作業場所の失敗 | `units.scratch` の行 | `units.scratch` | 止める（`UnitsError`） | `precondition` | 一時の作業場所は `finally` で外す |
| `assemble.assemble` | リリースの PR を作らない方式 | `pr` の取り方（`production_pr`） | 範囲の取り方を決めた直後 | 止める（`NoReleasePr`） | `not_applicable`。リリース方式と承認の対象を出す | 何も書いていない |
| `assemble.assemble` | 同じ名前のリリースブランチがある | 前の組み立てが残っている | `git ls-remote --heads` | 止める（`AssembleError`） | `precondition`（I6） | 何も書いていない。前の PR を片付けて再実行できる |
| `assemble.assemble` | 別のリリースの PR が開いている | 同じリポジトリの別の組み立て | `open_releases` | 止める（`ReleaseOpen`） | `refused`（今と同じ） | 何も書いていない |
| `assemble.assemble` | PR を作れない | 権限・同じ head の PR | REST の失敗 | 呼び出し元へ返す（`GitHubError`） | `unknown` か `infra_wait` | ブランチはリモートに残る。PR を手で作るかブランチを消して再実行する |
| `tagging._tag` | タグが別のコミットを指す | 前の操作の誤り・保存先 `tag` の衝突 | 既存の ref の `object.sha` と `revision` の比べ | 止める（`TagMoved`） | `refused`。タグ・今の指す先・出したコミット（I7） | タグは動かさない。人が直した後に再実行できる |
| `tagging._released` | 結び付いた PR が無い | 保存先 `tag` で、リリースの PR を通らないコミット | `_released_by_branch` が `None` | 既定で続ける（空） | `publish` が `not_applicable` | 何も書いていない |
| `tagging._released_by_branch` | PR の読みの失敗 | ネットワーク | REST の失敗 | 呼び出し元へ返す（`GitHubError`） | `unknown` か `infra_wait` | 何も書いていない。再実行できる |
| `tagging.publish` | タグの書式が無い | 設定の `tag` が `null` | `formats.tag` | 既定で続ける（空） | `not_applicable`。タグを作らない設定と出す | 何も書いていない |
| `tagging.publish` | 扱わない形 | `form_of` の行 | 先頭 | 止める | `precondition` | 何も書いていない |
| `cli._remote` | 設定の `remote` が壊れている | `.ndf/worktree.json` の値の誤り | `git.remote_of` | 止める（`RemoteError`。`GitError` の子） | `precondition`（I8） | 何も書いていない |
| `cli.cmd_assemble` | 上の各行 | 上の各行 | 段階のモジュール | `_stopped_of` へ渡す | 上の各行 | 上の各行 |
| `cli.cmd_materials` | `--out` に書けない | 書き込み先のディレクトリが無い | `write_text` | 止める（`OSError` は今と同じく捕まえない） | Python の例外と終了コード 1 | 再実行できる |
| `cli.cmd_publish` | 上の各行 | 上の各行 | 段階のモジュール | `_stopped_of` へ渡す | 上の各行 | 上の各行 |
| `Lander.land` | head が書式に合わない | リリースブランチでない PR | `version_of_branch` | 止める（`FormatMismatch`） | `precondition`（今の `VersionError` と同じ種類） | 何も書いていない |
| `record.released` | head が書式に合わない | 同上 | `version_of_branch` | 止める（`NotReleased`） | `not_applicable` | 何も書いていない |

## 処理の手順

範囲の取り方（`since_decide.kind_of`）の判定表:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `since_decide.kind_of` | 承認の対象が `production_pr` | `pr` |
| `since_decide.kind_of` | 承認の対象が `release_pr`・リリース方式が `merge_base_branch` | `base` |
| `since_decide.kind_of` | 承認の対象が `release_pr`・リリース方式が `release_branch` | `units` |
| `since_decide.kind_of` | 承認の対象が `release_pr`・リリース方式が `trunk` | `trunk` |

範囲の起点と終点（`since_decide.since_of`・`until_of`）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `since_decide.since_of` | `base` | 本番ブランチの HEAD |
| `since_decide.since_of` | `trunk`・`units` | 単位ごとの前のリリースの起点 |
| `since_decide.since_of` | `pr` | 本番ブランチと PR の先頭の分岐点 |
| `since_decide.until_of` | `base` | ベースブランチの HEAD |
| `since_decide.until_of` | `trunk` | 本番ブランチの HEAD |
| `since_decide.until_of` | `units` | 選んだスプリントを集めた先頭 |
| `since_decide.until_of` | `pr` | PR の先頭 |

前のリリースの起点（`since_decide.prev_release`）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `since_decide.prev_release` | 書式に合うタグがある | そのタグ（最も大きいバージョン） |
| `since_decide.prev_release` | タグが無い・保存先がファイルを持ち今のバージョンが入ったコミットがある | そのコミット |
| `since_decide.prev_release` | どちらも無い（初めてのリリース・保存先 `none`・`tag` でタグが無い） | 本番ブランチの最初のコミット |

出したバージョンの求め方（`tagging._released`）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `tagging._released` | 保存先がファイルを持つ（`plugin`・`json`・`pubspec`） | コミットとその第 1 親で `read` の値が変わった単位と新しい値 |
| `tagging._released` | 保存先 `tag` | `_released_by_branch` の値。`None` なら空 |
| `tagging._released` | 保存先 `none` | 空 |
| `tagging._released` | 求めた値が接尾辞を持つ | その単位を除く |

タグの扱い（`tagging._tag`）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `tagging._tag` | タグが無い | 作る（`created`） |
| `tagging._tag` | タグがあり `revision` を指す | `exists` |
| `tagging._tag` | タグがあり別のコミットを指す | `TagMoved` で止まる |

`assemble` の分岐:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `assemble.assemble` | `pr` | `NoReleasePr` |
| `assemble.assemble` | `units`・`--units` あり | 手元で `units.build` を流した先頭からリリースブランチを作る |
| `assemble.assemble` | `base`・`trunk` | 範囲の終点からリリースブランチを作る |
| `assemble.assemble` | 保存先 `none`（`merge_base_branch` で `bump` が偽） | 今と同じく `land` がベースブランチから本番への PR を入れるため、組み立ては `not_applicable` |
| `assemble._commit_and_push` | 保存先が書き換えず CHANGELOG のファイルも無い | `git commit --allow-empty` で空のコミット（設計文書の「未確認のまま残ること」の 2 行目） |
| `assemble._write` | チャネルが本番で CHANGELOG のファイルがある | 見出しの書式でセクションを書く |
| `assemble._write` | それ以外 | CHANGELOG を書かない |

`materials_decide.render` の分岐:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `materials_decide.render` | `versioned` が真 | セクション「バージョン」に単位ごとの `<単位>: <前> → <次>（範囲の起点 <短い SHA>）` |
| `materials_decide.render` | `versioned` が偽 | セクション「バージョン」を出さない |
| `materials_decide.render` | いつも | セクション「届き方」に `reach_line` の 1 行 |

- 繰り返しの終わる条件: `gather.introduced` は本番ブランチの保存先のパスを変えたコミットを新しい順に読む。今のバージョンと違う値に当たった時点で止める（10 系の `untagged_since` と同じ）。`units.build` は `--units` の数だけ回る
- 計算量の注意: `gather.introduced` は保存先のパスを変えたコミットだけを `git log -- <パス>` で絞ってから `git show` を打つ。単位ごとに 1 回で、ai-plugins の 12 単位でもタグがあれば打たない（タグが先に決まる）。`gather.shipped` はリモートのタグを `ls-remote` 1 回で読む（今と同じ）

## 内部のデータと定数

| 名前 | 種類 | ファイル | 既定値 |
| --- | --- | --- | --- |
| `Form` | 型（NamedTuple: `id`・`label`・`reach`・`supported`・`why`） | `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | — |
| `forms_decide.TABLE` | 定数 | `plugins/ndf-next/src/ndf/domains/release/forms_decide.py` | `"release-forms"`（表のパスは `contracts` が引く） |
| `NameFormats` | 型（frozen dataclass: `tag: str \| None`・`branch: str`・`changelog_heading: str`） | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | — |
| `names_decide.DEFAULT_BRANCH` | 定数 | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | `"release/v{version}"`（スキーマの `default` と同じ値。スキーマの側は説明だけで、読むのはこの定数） |
| `names_decide.DEFAULT_HEADING` | 定数 | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | `"## [{plugin} {version}] - {date}"` |
| `names_decide.PLACEHOLDERS` | 定数 | `plugins/ndf-next/src/ndf/domains/release/names_decide.py` | `{"version": SemVer の形, "plugin": 単位の名前のエスケープ, "date": "\d{4}-\d{2}-\d{2}"}` |
| `SpanKind` | 型（StrEnum: `base`・`trunk`・`units`・`pr`） | `plugins/ndf-next/src/ndf/domains/release/since_decide.py` | — |
| `Unit` | 型（NamedTuple: `name`・`dir`） | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | `dir` はリポジトリ全体の単位で空文字 |
| `version_store.KINDS` | 定数（種類 → アダプターのクラス） | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | `plugin`・`json`・`pubspec`・`tag`・`none` の 5 行。スキーマの `oneOf` と一致するかをテストが見る |
| `version_store.INITIAL` | 定数 | `plugins/ndf-next/src/ndf/domains/release/version_store.py` | `"0.0.0"`（`read` が `None` の単位を上げるときの起点） |
| `units.MERGE_SUBJECT` | 定数（書式の文字列） | `plugins/ndf-next/src/ndf/domains/release/units.py` | `"Merge {unit} into {branch}"`。`included_decide.is_unit_merge` はこの定数から式を作り、写さない |
| `Target.remote`・`Target.units`・`Target.pr` | 型の欄 | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `remote` は必須、`units` は空の組、`pr` は `None` |
| `Facts.form`・`Facts.versioned` | 型の欄 | `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | 必須 |
| `NdfSettings.undistributed` | 設定値 | `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | 空の組（既定はテストのファイルの表が持ち、ここに写さない） |
| 表 `release-forms` の行 | 設定値 | `plugins/ndf-next/contracts/domains/release-forms.json` | 6 行（決定 1）。スキーマの `release.form` の列挙と一致するかを `test_release_forms.py` が見る |
| 表 `test-files` の列 `role` | 設定値 | `plugins/ndf-next/contracts/domains/test-files.json` | 省けば `test`。行 `python-conftest` だけが `support` |

リモート名は `cli._remote` が 1 度だけ求めて `Target.remote` に入れ、リリースの部品は定数として持たない（I8）。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じリポジトリの 2 つの `assemble`（workflow の 2 回の起動） | リモートのリリースブランチ・本番ブランチ宛ての PR | 再利用可能なワークフローの `concurrency`（グループはリポジトリと本番ブランチ、`cancel-in-progress: false`）。それでも重なれば `open_releases`（`ReleaseOpen`）と同じ名前のブランチの確かめ（I6）が止める | ブランチを送る前に止まれば何も残らない。送った後に PR の作成で落ちれば、ブランチを消して打ち直す |
| 会話のセッションの `materials` と workflow の `assemble` | 手元の作業場所・リモートのブランチ | `materials` は一時の作業場所（`units.scratch`）か detached HEAD で読むだけで、リモートへ書かない | 読むだけなので何度でも打ち直せる |
| `materials` の一時の作業場所と利用者の作業 | `.git/worktrees` | 一時の作業場所は `tempfile.mkdtemp` の名前で作り、名前が重ならない。ブランチを作らない（detached） | 中断で残った作業場所は `git worktree prune` で外れる |
| `publish` の 2 回の起動（本番ブランチへの push と手動） | リモートのタグと GitHub Release | 排他を取らない。タグは指す先を比べ（I7）、同じなら `exists`、Release は有無を見て `exists` を返す | 何度打っても同じ結果。途中で落ちれば足りない分だけを作る |
| `gather.fetch_tags` と利用者の手元のタグ | 手元の `refs/tags` | 強制の取得をしない（`+` を付けない）。ぶつかれば止まる | 手元のタグを書き換えないので再実行できる |
| `merge`（`land`）と `assemble` | リリースの PR | 今のまま（マージキューと証跡の判定） | 今のまま |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cli.cmd_materials` | 承認資料（`--out` か標準出力）と、含む PR の行 | 結果 JSON の `items`・`presentation_path` | トークン・秘密 |
| `cli.cmd_assemble` | リリースブランチの名前・バージョン・PR の番号と URL | 結果 JSON の `outputs` | トークン |
| `cli.cmd_publish` | 作ったか既にあったタグと Release の名前 | 結果 JSON の `items` | トークン |
| `forms_decide.form_of` | 形の名前・表のパス・11.x へ回す理由 | 結果 JSON の `stopped` の本文 | 無し |
| `tagging._tag` | タグ・今の指す先・出したコミット | 結果 JSON の `stopped` の本文 | 無し |
| `units.scratch` | 外せなかった一時の作業場所のパス | 結果 JSON の `process` | 無し |
| `units.build` | ぶつかったスプリントと git の出力の末尾 400 文字 | 結果 JSON の `stopped` の本文 | git の出力に URL の認証情報が入る行（リモートの URL は出さない） |
| `.github/workflows/ndf-release-reusable.yml` | 秘密 `release_token` が渡されなかったときの CI が起動しない旨 | `::warning::`（決定 10） | 秘密の値（`add-mask` 済みのトークンを `echo` しない） |

## 既存の部品の再利用

検索はすべて worktree の根で打った（2026-11-17）。

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `forms_decide.form_of` | `ndf.contracts.facts.rows`・`facts.value` | `rg -n "def rows\|def value" plugins/ndf-next/src/ndf/contracts` | 使う（表を引くだけ） |
| `forms_decide.reach_line` | `materials_decide` の行を作る関数 | `rg -n "def _" plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | 使えない理由: 形の行を受ける関数が無い。新設して `render` から呼ぶ |
| `names_decide.formats_of` | `Strategy.release_plugin`・`branch_strategy.get` | `rg -n "def get\|release_plugin" plugins/ndf-next/src/ndf/core/branch_strategy.py` | 使う（`get` でセクションを読む） |
| `NameFormats.tag_of` | `version_decide.tag_of` | `rg -n "def tag_of" plugins/ndf-next/src` | 使えない理由: 区切り `--v` を決め打つ。こちらへ移して書式で作る |
| `NameFormats.version_of_tag` | `version_decide.version_of_tag`・10 系の `release_tag.latest_release_tag` | `rg -n "def version_of_tag\|def latest_release_tag" plugins` | 使えない理由: 前者は接頭辞の決め打ち、後者は 10 系で ndf-next から読めない。`_pattern` で作る |
| `NameFormats.branch_of` | `version_decide.branch_of` | `rg -n "def branch_of" plugins/ndf-next/src` | 使えない理由: 接頭辞の決め打ち。こちらへ移す |
| `NameFormats.version_of_branch` | `version_decide.version_of_branch` | 同上 | 使えない理由: 同上 |
| `NameFormats.is_release_branch` | `version_decide.BRANCH_PREFIX` の `startswith` | `rg -n "BRANCH_PREFIX" plugins/ndf-next/src` | 使えない理由: 接頭辞の決め打ち（3 か所）。この関数に寄せる |
| `NameFormats.heading_of` | `changelog_decide.changelog_heading` | `rg -n "def changelog_heading" plugins/ndf-next/src` | 使えない理由: 見出しの決め打ち。こちらへ移す |
| `NameFormats.heading_key` | `changelog_decide.changelog_key` | `rg -n "def changelog_key" plugins/ndf-next/src` | 使えない理由: 同上。探し方（先頭一致）はそのまま移す |
| `names_decide.highest_tag` | `version_decide.highest` | `rg -n "def highest" plugins/ndf-next/src` | 使う（バージョンの大小は `highest` で比べる） |
| `names_decide._pattern` | `re.escape`・`semver.Version.is_valid` | 標準ライブラリと LIB2 | 使う |
| `since_decide.kind_of` | `route_decide.route_of` | `rg -n "def route_of" plugins/ndf-next/src` | 使う（`Route.release_pr`・`collects_units` から決め、リリース方式の名前で分岐しない） |
| `since_decide.prev_release` | 10 系の `release_prev.prev_release` | `rg -n "def prev_release" plugins/ndf/scripts` | 使えない理由: 10 系で ndf-next から読めず、git を呼ぶ。順だけを移し、候補は `gather` が集める |
| `since_decide.since_of` | 無し | `rg -n "since" plugins/ndf-next/src/ndf/domains/release` | 新設（今は `assemble._span` が本番ブランチの HEAD に決め打つ） |
| `since_decide.until_of` | 同上 | 同上 | 新設 |
| `version_store.load` | `branch_strategy.load` の `version_store` | `rg -n "version_store" plugins/ndf-next/src` | 使う（設定の値を読む）。選ぶ関数は無いので新設 |
| `version_store.tags_at` | `Git.run` | `rg -n "def run" plugins/ndf-next/src/ndf/adapters/git.py` | 使う |
| `version_store.pubspec_version` | ruamel.yaml（LIB1） | 決定 3 | 使えない理由: 1 行の置き換えに見合わない（決定 3） |
| `version_store.pubspec_replace` | 同上・PRD2 | 決定 3 | 使えない理由: 同上。規則は PRD2 に倣う |
| `PluginStore.units` | `plugins.load` | `rg -n "def load" plugins/ndf-next/src/ndf/domains/release/plugins.py` | 使う |
| `PluginStore.read` | `plugins.version_at`（`gather._production_version` を移したもの） | `rg -n "_production_version" plugins/ndf-next/src` | 使う |
| `PluginStore.write` | `plugins.bump` | `rg -n "def bump" plugins/ndf-next/src` | 使う |
| `PluginStore.paths` | `Plugin.manifests`・`plugins.MARKETPLACE` | 同上 | 使う |
| `JsonStore.units` | 無し | — | 新設（1 行） |
| `JsonStore.read` | `json_pointer.resolve`（`land.read_version` が使う） | `rg -n "json_pointer" plugins/ndf-next/src` | 使う |
| `JsonStore.write` | `plugins._bump_pointer`・`plugins._bump_file` | `rg -n "def _bump_pointer\|def _bump_file" plugins/ndf-next/src` | 使う（公開の名前へ改めずにモジュールの中から呼ぶ。`version_store` は `plugins` の同じパッケージ） |
| `JsonStore.paths` | 無し | — | 新設（設定の値を並べるだけ） |
| `PubspecStore.read` | `pubspec_version` | この表の上の行 | 使う |
| `PubspecStore.write` | `pubspec_replace` | この表の上の行 | 使う |
| `TagStore.read` | `tags_at`・`highest_tag` | この表の上の行 | 使う |
| `TagStore.write` | 無し | — | 新設（空を返す） |
| `NoneStore.read` | 無し | — | 新設（`None` を返す） |
| `units.merge_subject` | 10 系の `strategy_rows.unit_merge_subject` | `rg -n "def unit_merge_subject" plugins/ndf/scripts` | 使えない理由: 10 系で読めない。同じ形をこちらに置く |
| `units.build` | 10 系の `strategy_route._build_release_branch`・`Git.checkout_detached` | `rg -n "def _build_release_branch\|def checkout_detached" plugins` | 前者は移す（rerere の扱いを含む）。後者は使う |
| `units.scratch` | `Git.worktrees`・`cleanup.git_remover` | `rg -n "def worktrees\|def git_remover" plugins/ndf-next/src` | `git_remover` を使う（外すときの手を 1 つにする） |
| `gather.fetch_tags` | `Git.fetch` | `rg -n "def fetch" plugins/ndf-next/src/ndf/adapters/git.py` | 使う（`fetch(remote, "--tags")` の形で呼べないため `run("fetch", "--quiet", "--tags", ...)`） |
| `gather.introduced` | 10 系の `release_prev.untagged_since` | `rg -n "def untagged_since" plugins/ndf/scripts` | 使えない理由: 10 系で読めない。手順を移し、読みは `store.read` を使う |
| `gather.prev_releases` | `since_decide.prev_release`・`introduced`・`highest_tag` | この表の上の行 | 使う |
| `included_decide.is_unit_merge` | 10 系の `strategy_rows.UNIT_MERGE` | `rg -n "UNIT_MERGE" plugins/ndf/scripts` | 使えない理由: 接頭辞 `release/v` の決め打ち。書式の判定 `is_release` を受けて作る |
| `changes_decide.default_undistributed` | `tests_decide.is_test_file`・`range_decide`（保存先の行を読む） | `rg -ln "test-files" plugins/ndf-next/src` | 使えない理由: どちらもパスを 1 つずつ判定し、gitignore の形を返さない。表は同じものを読む（決定 5） |
| `plugins.version_at` | `gather._production_version` | `rg -n "_production_version" plugins/ndf-next/src` | 移す（中身は同じ） |
| `tagging._released_by_branch` | `GitHub.rest`（`/commits/<sha>/pulls`） | `rg -n "def rest" plugins/ndf-next/src/ndf/adapters/github.py` | 使う |
| `cli._remote` | `git.remote_of`・`cli._strategy` のメインディレクトリの求め方 | `rg -n "def remote_of\|def _strategy" plugins/ndf-next/src` | 使う。メインディレクトリの求め方を `_main_dir(args)` に出して `_strategy` と分け合う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `forms_decide.form_of` | 6 つの値のうち扱う 5 つで行が返る | 文書の 4 形が 1 つの値 `document` に当たる | `desktop` で `FormUnsupported`（本文に形の名前と表のパスと理由）。表に無い値で取れる形の一覧 |
| `forms_decide.reach_line` | 行が `- 届き方:` で始まる | — | — |
| 表 `release-forms` とスキーマの一致 | 表の行の `id` の集合が、`contracts/schemas/ndf-settings/project.schema.json` の `release.form` の列挙と一致する（`ndf.contracts.load_json` で両方を読む） | 扱わない形 `desktop` も両方にある | 片方にだけある値を、どちらにあるかを添えて落とす |
| `names_decide.formats_of` | 3 キーを書いた設定でその値 | `branch`・`changelog_heading` が無いとき既定。`tag` が `null` で `None` | — |
| `NameFormats.tag_of` | `v{version}` で `v1.2.3`。`{plugin}--v{version}` で `ndf--v1.2.3` | `1.2.3-dev.1` は基底の `1.2.3` で作る | `tag` が `None` で `None` |
| `NameFormats.version_of_tag` | 作った名前を読み戻すと元のバージョン（I3） | 単位の名前に正規表現の文字（`.`・`+`）を含む | ほかの単位のタグ・書式の前後に文字が付いたタグで `None` |
| `NameFormats.version_of_branch` | `release/v1.2.3` で `1.2.3`。書式 `rel-{version}` で `rel-1.2.3` | — | 書式に合わない名前で `FormatMismatch` |
| `NameFormats.is_release_branch` | 書式の名前で真 | — | `sprint/x` で偽 |
| `NameFormats.heading_of` | `## [{version}] - {date}` で `## [1.2.3] - 2026-11-17` | 既定の書式で今の見出しと同じ（I9） | — |
| `NameFormats.heading_key` | 見出しが返す値で始まる | `{date}` を含まない書式で書式全体 | — |
| `names_decide.highest_tag` | 3 つのタグから最も大きいもの | 接尾辞付きのタグを除く | 合うタグが無いと `None` |
| `since_decide.kind_of` | 判定表の 4 行 | — | — |
| `since_decide.prev_release` | 判定表の 3 行 | タグと変わり目の両方があるときタグ | — |
| `since_decide.since_of` | 判定表の 3 行 | `trunk` で前のリリースの起点が本番ブランチの HEAD と同じでも、そのまま返す（範囲が空でも呼び出し元が `NothingToRelease` にする） | — |
| `since_decide.until_of` | 判定表の 4 行 | — | — |
| `version_store.load` | 5 つの種類でそのクラス | `KINDS` とスキーマの `oneOf` の種類が一致 | 表に無い種類で `StoreError` |
| `version_store.tags_at` | 辿れるタグだけを返す | 別のブランチだけにあるタグを返さない | 無い ref で `GitError` |
| `version_store.pubspec_version` | `version: 1.2.3+45` で `1.2.3` | `+` の無い行・インデントのある `version:`（最上位でない）を読まない | 行が無いと `None` |
| `version_store.pubspec_replace` | `1.2.3+45` を `1.2.4+46` に | 数でないビルド番号はそのまま。コメントの行が 1 文字も変わらない | 行が 2 つで `StoreError` |
| `PluginStore.units` | 登録の順で単位が並ぶ | 根に置いたプラグイン（`dir` が空） | 登録が無いと `PluginsError` |
| `PluginStore.paths` | 登録・定義・`files` が並ぶ | — | 登録が無いと `PluginsError` |
| `PluginStore.read` | ref のバージョン | 定義が無い ref で `None` | 壊れた定義で `PluginsError` |
| `PluginStore.write` | 定義と登録が書き換わる | — | 2 本の定義のバージョンが違うと `PluginsError` |
| `JsonStore.read` | `package.json` の `/version` | ファイルが無い ref で `None` | 値が数で `StoreError` |
| `JsonStore.write` | 同じ位置と `files` が書き換わる | — | 値が無いと `StoreError` |
| `PubspecStore.read` | ref の `pubspec.yaml` のバージョン | ファイルが無い ref で `None` | — |
| `PubspecStore.write` | 行が書き換わる | — | 行が無いと `StoreError` |
| `TagStore.read` | 辿れる最も大きいタグのバージョン | タグが無いと `None` | git の失敗で `GitError` |
| `units.build` | 2 つのスプリントを順にマージした先頭。件名が `merge_subject` の形 | 本番ブランチに入っているスプリントを飛ばす | 衝突で `UnitsError`。作業場所が中断の前に戻る。無いスプリントで `UnitsError` |
| `units.scratch` | 抜けた後に一時の作業場所が無い | 中で例外が出ても外れる | 外せないとき `process` に出して続ける |
| `gather.fetch` | 引数のリモート（`upstream`）から取る（I8） | — | 無いブランチで `GitError` |
| `gather.fetch_tags` | リモートのタグが手元に来る | — | 手元の同じ名前の別のタグで `GitError` |
| `gather.introduced` | 今のバージョンが入ったコミット | 保存先 `tag`・`none` で `None` | 浅い取得で `None` |
| `gather.prev_releases` | 単位ごとに違うタグを起点にする | タグの無い単位は最初のコミット | 本番ブランチが無いと `GitError` |
| `gather.included` | first-parent の PR とリリースの単位の中の PR の和 | リリースブランチからのマージを除く（書式 `rel-{version}` でも） | 無い ref で `GitError` |
| `gather.shipped` | 本番ブランチの値とタグの大きい方 | 値もタグも無い単位を入れない | ls-remote の失敗で `GitError` |
| `gather.changes` | 単位ごとに違う起点から数える | `src/foo.test.ts`・`src/__tests__/bar.ts`・`tests/Unit/FooTest.php` だけの範囲は全部リリース対象外（受け入れ条件 8） | 無い起点で `GitError` |
| `gather.plan_bumps` | 変化のある単位を上げ、`since` を持つ | 保存先 `none` で空。`tag` でタグが無いとき `0.0.0` から | SemVer でない値で `VersionError` |
| `plugins.version_at` | ref の定義のバージョン | 定義の無い ref で `None` | 壊れた定義で `PluginsError` |
| `assemble._span` | 4 つの取り方の終点 | `pr` で分岐点を返す | `--pr`・`--units` の欠けで `AssembleError`。本番宛てでない PR で `AssembleError` |
| `assemble.open_releases` | 書式に合う head の PR を数える | head がベースブランチの PR を数える | `PER_PAGE` 件で `AssembleError` |
| `assemble._write` | 保存先と CHANGELOG と手順が書かれる | 開発版のチャネルで CHANGELOG を書かない | 手順の失敗で `StepFailed` |
| `assemble._commit_and_push` | 引数のリモートへ送る | 差分が無いとき空のコミット | 送れないと `GitError` |
| `assemble._facts` | 単位ごとの起点を持つ計画 | 保存先 `none` で計画が空でも資料を組む | バージョンを持つ保存先で上げるものが無いと `NothingToRelease` |
| `assemble.collect` | Web・Flutter・Swift・業務文書・nd-factory のコピーで `ok`（受け入れ条件 2〜4・12） | 呼ぶ前と後で手元のブランチと作業場所が同じ | `desktop` で `precondition`（受け入れ条件 13）。一時の作業場所の失敗で `UnitsError` |
| `assemble.assemble` | ai-plugins の設定で名前が直す前と同じ（I9）。`json`・`pubspec` で保存先が書き換わる（受け入れ条件 4） | 保存先 `tag` で空のコミットの PR | 同じ名前のブランチで `AssembleError`（I6）。`pr` の取り方で `NoReleasePr` |
| `tagging._tag` | 無ければ `created` | 同じコミットなら `exists` | 別のコミットなら `TagMoved`（I7） |
| `tagging._released` | ファイルを持つ保存先で値の変わった単位 | 接尾辞付きを除く | 保存先 `tag` で結び付いた PR が無いと空 |
| `tagging._released_by_branch` | head の名前のバージョン | 書式に合わない head で `None` | REST の失敗で `GitHubError` |
| `tagging.publish` | `v1.2.3` のタグを作る（受け入れ条件 5） | `tag` が `null` で空 | `desktop` で `FormUnsupported` |
| `cli._remote` | 設定の `remote` | 設定が無いと既定のリモート | 壊れた値で `RemoteError` |
| `cli.cmd_assemble` | `outputs.branch` が書式の名前 | — | 各例外が `_ERRORS` の種類で返る |
| `cli.cmd_materials` | `--out` に資料がある | `--units` を省いた `release_branch` で `precondition` | — |
| `cli.cmd_publish` | `items` の `name` が書式の名前 | — | `TagMoved` が `refused` で返る |
| `changes_decide.default_undistributed` | 表の行の数だけ形がある | `conftest.py`（`support`）を含む | — |
| `changes_decide.split` | 既定と設定の形の両方でリリース対象外に分ける（受け入れ条件 8） | `directory` が空でリポジトリ全体 | — |
| `changes_decide.changes` | 単位ごとに分ける | — | — |
| `changelog_decide.block` | 渡した見出しで始まる | — | — |
| `changelog_decide.insert` | 書式 `## [{version}] - {date}` のセクションを足し、同じセクションを置き換える（受け入れ条件 6） | セクションが 1 つも無いとき末尾 | — |
| `changelog_decide.notes` | 本文を返す | — | 無いと `None` |
| `included_decide.number_of` | 2 通りの件名で番号 | — | リリースブランチからのマージで `None` |
| `included_decide.numbers` | 重なりを除く | — | — |
| `included_decide.is_unit_merge` | `Merge sprint/a into release/v1.2.3` で真 | `Merge origin/sprint/a into ...` で真 | PR のマージで偽 |
| `ndf_settings._parse` | `undistributed` が足す形で読まれる | キーが無いとき空 | 文字列でない値で `NdfSettingsError` |
| `materials_decide.render` | 届き方と「前 → 次」と範囲の起点が出る | `versioned` が偽でバージョンのセクションが無い | — |
| `version_decide.Planned` | `since` を持つ | — | — |
| `land.version_rows` | `plugin`・`json` で今のまま | `pubspec`・`tag`・`none` で空 | — |
| `Lander.land` | 書式の名前の PR を入れる（既存のテスト） | — | 書式に合わない head で止まる |
| `record.released` | 書式の名前からバージョン | — | 書式に合わない head で `NotReleased` |
| `tests_decide.is_test_file` | `test_x.py` で真 | `conftest.py` で偽 | — |
| `land_read.store_version` | `json` で今のまま | `pubspec`・`tag` で `None` | — |
