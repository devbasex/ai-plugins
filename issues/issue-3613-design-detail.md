# #3613 の詳細設計: ndf-next の直訳調の語を業界の語へ置き換え、利用者の設定のキーを改名する

設計文書 [issue-3613-design.md](issue-3613-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語の対応（どの旧い語をどの語へ置き換えるか）は設計文書の「対応表」が、設定のキーと値の対応は設計文書の「データ構造」の「利用者の設定のキーと値の改名」が持ち、ここでは写さない。

この文書が決めるのは次の 4 つである。

- 禁止語の規則（`plugins/ndf-next/src/ndf/checks/words.py`）が単独で照合する廃止した語を読むための、関数の形と失敗の扱い
- 識別子の改名で、どのファイル・モジュール・関数がどの名前になるか
- 状態のデータベースの列を改名したときに、旧い形の保存先と表をどう扱うか
- 設定のキーの改名表を読む関数・移行のコマンド・旧いキーで止まる読む側の、関数の形と失敗の扱い

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除。新設と削除の対は改名を表す）:

```text
docs/glossary/glossary.json
docs/glossary.md
plugins/ndf-next/
├── contracts/
│   ├── domains/
│   │   ├── branch-flows.json
│   │   ├── ndf-files.json
│   │   ├── ndf-settings-renames.json  *
│   │   ├── places.json                -
│   │   └── locations.json             *
│   ├── facts/answers.json
│   ├── hooks/imports.json
│   └── schemas/
│       ├── declarations/              -（19 ファイル）
│       └── ndf-settings/              *（同じ 19 ファイル。worktree と mvv のスキーマはキーを改名）
├── docs/contracts/
│   ├── declarations.md                -
│   └── ndf-settings.md                *（ndf check --write-spec が作る）
├── skills/
│   ├── worktree/schemas/worktree.schema.json
│   └── development-workflow/schemas/mvv.schema.json
├── src/ndf/
│   ├── cli.py
│   ├── identity.py
│   ├── checks/
│   │   ├── README.md
│   │   ├── rules.json
│   │   ├── words.py
│   │   ├── smells.py
│   │   ├── spec_docs.py
│   │   ├── spec_model.py
│   │   ├── marks.py                   -
│   │   └── suppressions.py            *
│   ├── core/
│   │   ├── durable.py
│   │   ├── durable_sweep.py
│   │   ├── durable_ledger.py          -
│   │   └── durable_runs.py            *
│   ├── progress/
│   │   ├── gate/checks.py
│   │   ├── model.py
│   │   └── store.py
│   └── domains/
│       ├── crossreview/drive.py
│       ├── records/cli.py
│       ├── records/mvv.py
│       ├── records/mvv_decide.py
│       ├── records/preflight.py
│       ├── records/usage.py
│       ├── relay/marks.py             -
│       ├── relay/markers.py           *
│       ├── release/declaration.py     -
│       ├── release/ndf_settings.py    *
│       ├── settings/__init__.py       *
│       ├── settings/renames.py        *
│       ├── settings/cli.py            *
│       ├── worktree/declaration.py    -
│       ├── worktree/ndf_settings.py   *
│       ├── worktree/ledger.py         -
│       ├── worktree/allocations.py    *
│       ├── worktree/merged.py
│       └── worktree/merged_decide.py
└── tests/
    ├── checks/test_words.py
    ├── checks/test_marks.py           -
    ├── checks/test_suppressions.py    *
    ├── core/test_durable_store.py
    ├── domains/test_branch_flows.py
    └── domains/settings/              *（test_renames.py・test_cli.py）
```

### 依存の向き

依存の向きは、層の並びに従う。層の並びは `rules.json` の `structure.layers` が持つ。順は `ndf.contracts` → `ndf.adapters` → `ndf.core` → `ndf.progress` → `ndf.domains` である。下の層は上の層を読まない。

新設する `ndf.domains.settings` が読むのは次の 3 つだけである。

- `ndf.contracts`（`facts`・`schema_errors`・`result`）
- 同じ層の `ndf.domains.records.files`（設定ファイルのパスを解く）
- 同じ層の `ndf.domains.worktree.atomic`（半端なファイルを残さない書き込み）

改名表を読む関数（`settings/renames.py`）は、`ndf.domains.records.files` と `ndf.contracts.facts` のほかを読まない。そのため、次の読む側がこれを読んでも循環は生じない（`renames.py` は読む側のどれも import しない）。

- `worktree/ndf_settings.py`・`records/mvv.py`・`records/preflight.py`
- `checks/spec_docs.py`

`ndf.checks` は層の並びに入らず、今も `ndf.domains` 以外の層を読んでいる。`spec_docs.py` が `ndf.domains.settings.renames` を読むのは、この規則が許す範囲である。承認ゲート（`ndf.progress.gate`）は `ndf.domains` より下の層なので、改名表を読まない（「既存の部品の再利用」の最後の行）。禁止語の規則は用語集のデータと規則のデータ（`rules.json`）を読むだけで、新しく足す漢字の範囲の定数は `words.py` に置き、今の NDF（`plugins/ndf/`）のモジュールを読まない。

### ファイルごとの役割

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `docs/glossary/glossary.json` | 変更 | 置き換え先の語の項目を足し、旧い語を廃止した語・単独で照合する廃止した語へ登録する | 無し |
| `docs/glossary.md` | 変更 | 用語集のデータから `glossary.py render` で作り直す | `docs/glossary/glossary.json` |
| `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | 廃止した語・単独で照合する廃止した語・禁止語を、ndf-next の文字のファイルの行から拾う | `plugins/ndf-next/src/ndf/identity.py`、`plugins/ndf-next/src/ndf/checks/rules.json` |
| `plugins/ndf-next/src/ndf/checks/rules.json` | 変更 | 例として許す箇所（`words.examples`）を足し、兆候の表のキー `mark` を `suppress` へ改める | 無し |
| `plugins/ndf-next/src/ndf/checks/README.md` | 変更 | 禁止語の規則の説明に、単独で照合する廃止した語の扱いを足す | 無し |
| `plugins/ndf-next/src/ndf/checks/marks.py` | 削除 | `suppressions.py` へ移す | — |
| `plugins/ndf-next/src/ndf/checks/suppressions.py` | 新設 | 抑制コメント（`# noqa: <規則> <境界の種類>: <理由>`）を読み、兆候を外せるかを決める（`marks.py` の中身を改名して移す） | `plugins/ndf-next/src/ndf/checks/rules.json` |
| `plugins/ndf-next/src/ndf/checks/smells.py` | 変更 | `suppressions` を読むように import と呼び出しの名前を改める | `plugins/ndf-next/src/ndf/checks/suppressions.py` |
| `plugins/ndf-next/src/ndf/checks/spec_docs.py` | 変更 | 規約の規則 spec-decl が、旧いキーの残る実物を改名表で変換してからスキーマと突き合わせる | `plugins/ndf-next/src/ndf/domains/settings/renames.py` |
| `plugins/ndf-next/src/ndf/checks/spec_model.py` | 変更 | 設定のスキーマのディレクトリ（`DECL_SCHEMA_DIR`）を `schemas/ndf-settings` へ、`kind` の値を `ndf_settings` へ改める | `plugins/ndf-next/contracts/schemas/ndf-settings/` |
| `plugins/ndf-next/src/ndf/identity.py` | 変更 | 固定の語の表のキー `declaration_dir` を `ndf_settings_dir` へ改める | 無し |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | サブコマンドの表（`COMMANDS`）に `settings` → `ndf.domains.settings.cli` を足す | `plugins/ndf-next/src/ndf/domains/settings/cli.py` |
| `plugins/ndf-next/src/ndf/core/durable.py` | 変更 | チェックアウトの保存先の形の番号を 2 に上げ、`durable_runs` を読む | `plugins/ndf-next/src/ndf/core/durable_runs.py` |
| `plugins/ndf-next/src/ndf/core/durable_ledger.py` | 削除 | `durable_runs.py` へ移す | — |
| `plugins/ndf-next/src/ndf/core/durable_runs.py` | 新設 | PostgreSQL のときに起動した実行のキーを表 `ndf_runs` へ書き、片付けで読んで消す（`durable_ledger.py` の中身を改名して移す） | 無し |
| `plugins/ndf-next/src/ndf/core/durable_sweep.py` | 変更 | 実行の一覧を読む関数の名前を改める | `plugins/ndf-next/src/ndf/core/durable_runs.py` |
| `plugins/ndf-next/src/ndf/progress/store.py` | 変更 | 列 `table_id` を `machine_id` へ、表の版を 2 へ改め、版 1 の PostgreSQL の表を見つけたら止める | `plugins/ndf-next/src/ndf/core/durable.py`、`plugins/ndf-next/src/ndf/progress/model.py` |
| `plugins/ndf-next/src/ndf/progress/model.py` | 変更 | ステートマシンの ID を作る関数 `table_id` を `machine_id` へ改める | 無し |
| `plugins/ndf-next/src/ndf/progress/gate/checks.py` | 変更 | MVV の設定のパスを引く `mvv_decls` が、表 `ndf-files` の `kind` の値 `ndf_settings` と列 `location` で引く | `plugins/ndf-next/contracts/domains/ndf-files.json` |
| `plugins/ndf-next/src/ndf/domains/settings/__init__.py` | 新設 | 利用者の設定のキーの改名を持つパッケージの説明（責務とモジュールの表） | 無し |
| `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 新設 | 改名表を読み、設定の中身から旧いキーの一覧・変換した中身・JSON Patch の操作の列・止まる理由の文を作る。ファイルを読み書きしない | `plugins/ndf-next/contracts/domains/ndf-settings-renames.json`、`plugins/ndf-next/src/ndf/domains/records/files.py` |
| `plugins/ndf-next/src/ndf/domains/settings/cli.py` | 新設 | `ndf settings migrate` を受け、差分とハッシュを示すか、承認した差分を書く | `plugins/ndf-next/src/ndf/domains/settings/renames.py`、`plugins/ndf-next/src/ndf/domains/worktree/atomic.py` |
| `plugins/ndf-next/src/ndf/domains/relay/marks.py` | 削除 | `markers.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/relay/markers.py` | 新設 | 中継が子へ入力を送る前に立てるマーカーのファイルを書き、hook が消す（`marks.py` の中身を改名して移す） | 無し |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 削除 | `ndf_settings.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | 新設 | リリースの設定（`.ndf/release.json`）を読み、設定した手順を流す（`declaration.py` の中身を改名して移す） | `plugins/ndf-next/contracts/domains/ndf-files.json` |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 削除 | `ndf_settings.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/worktree/ndf_settings.py` | 新設 | worktree の運用の設定（`.ndf/worktree.json` と個人設定）を読み、旧いキーが残れば止まる | `plugins/ndf-next/contracts/domains/ndf-files.json`、`plugins/ndf-next/src/ndf/domains/settings/renames.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/merged.py` | 変更 | スプリントの開始時の記録（`branch_flow` のコピー）に旧いキーが残れば止まる | `plugins/ndf-next/src/ndf/domains/settings/renames.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/merged_decide.py` | 変更 | 取り込み方の表の行を、キー `release_strategy` と列 `release_strategy` で引く | `plugins/ndf-next/contracts/domains/branch-flows.json` |
| `plugins/ndf-next/src/ndf/domains/worktree/ledger.py` | 削除 | `allocations.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/worktree/allocations.py` | 新設 | テスト環境の割り当て表（`worktree-registry.json`）を排他の中で読み書きする（`ledger.py` の中身を改名して移す） | 無し |
| `plugins/ndf-next/src/ndf/domains/records/mvv.py` | 変更 | MVV の設定を読む 2 つの関数が旧いキーで止まる。MVV 判定の関数と記録の ID を `mvv_validation` の系列へ改める | `plugins/ndf-next/src/ndf/domains/settings/renames.py` |
| `plugins/ndf-next/src/ndf/domains/records/mvv_decide.py` | 変更 | MVV 判定の定数・関数と、改訂の兆候の欄 `revise` を改める（設計文書の決定 16） | 無し |
| `plugins/ndf-next/src/ndf/domains/records/preflight.py` | 変更 | 手順 0 の MVV の確かめが、旧いキーの残る MVV の設定で止まる | `plugins/ndf-next/src/ndf/domains/settings/renames.py` |
| `plugins/ndf-next/src/ndf/domains/records/cli.py` | 変更 | 改訂の兆候の結果のキー `metrics.revise` を `revision_due` へ改める | `plugins/ndf-next/src/ndf/domains/records/mvv.py` |
| `plugins/ndf-next/src/ndf/domains/records/usage.py` | 変更 | 使用量ログのパスを返す関数の名前を `usage_log_path` へ改める | 無し |
| `plugins/ndf-next/src/ndf/domains/crossreview/drive.py` | 変更 | 指摘の記録の型 `FindingLedger` を `FindingJournal` へ改める | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | 列のキー `place` を `location` へ、`kind` の値 `declaration` を `ndf_settings` へ、`ledger` を含む ID（`ask-ledger` を除く）と `mvv-vet` を改める。改名表の行を足す | `plugins/ndf-next/contracts/domains/locations.json` |
| `plugins/ndf-next/contracts/domains/ndf-settings-renames.json` | 新設 | 設定のキーの改名表。列は `file`・`old`・`new`・`values`（「内部のデータと定数」） | `plugins/ndf-next/contracts/domains/ndf-files.json` |
| `plugins/ndf-next/contracts/domains/branch-flows.json` | 変更 | 列 `promotion` を `release_strategy` に、行の id と値を設計文書の表のとおりに改める | 無し |
| `plugins/ndf-next/contracts/domains/places.json` | 削除 | `locations.json` へ移す | — |
| `plugins/ndf-next/contracts/domains/locations.json` | 新設 | 保存先の表。ID `relay-marks` を `relay-markers`、パス `relay/marks` を `relay/markers` へ改める | 無し |
| `plugins/ndf-next/contracts/facts/answers.json` | 変更 | 承認ゲートの回答のラベルの「差し戻す」を「変更を要求する」へ置き換える。ID `revise` は変えない（設計文書の決定 11） | 無し |
| `plugins/ndf-next/contracts/hooks/imports.json` | 変更 | hook が読んでよいモジュールの `ndf.domains.relay.marks` を `ndf.domains.relay.markers` へ改める | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/` | 削除 | `ndf-settings/` へ移す | — |
| `plugins/ndf-next/contracts/schemas/ndf-settings/` | 新設 | 設定のスキーマ 19 ファイル。`worktree.schema.json` と `mvv.schema.json` は新しいキーと値だけを受ける。ほかの 17 ファイルは移すだけ | 無し |
| `plugins/ndf-next/skills/worktree/schemas/worktree.schema.json` | 変更 | Skill の中のスキーマのコピーを、契約のスキーマと同じキーと値にする | 無し |
| `plugins/ndf-next/skills/development-workflow/schemas/mvv.schema.json` | 変更 | Skill の中のスキーマのコピーを、契約のスキーマと同じキーと `$defs` の名前（`MvvValidation`）にする | 無し |
| `plugins/ndf-next/docs/contracts/declarations.md` | 削除 | `ndf check --write-spec` が `ndf-settings.md` を作り、旧い文書は残さない | — |
| `plugins/ndf-next/docs/contracts/ndf-settings.md` | 新設 | 設定のスキーマから作る契約の文書 | `plugins/ndf-next/contracts/schemas/ndf-settings/` |
| `plugins/ndf-next/tests/checks/test_words.py` | 変更 | 単独で照合する廃止した語の単体テストを足す | `plugins/ndf-next/src/ndf/checks/words.py` |
| `plugins/ndf-next/tests/checks/test_marks.py` | 削除 | `test_suppressions.py` へ移す | — |
| `plugins/ndf-next/tests/checks/test_suppressions.py` | 新設 | 抑制コメントのテスト（`test_marks.py` の中身を改名して移す） | `plugins/ndf-next/src/ndf/checks/suppressions.py` |
| `plugins/ndf-next/tests/core/test_durable_store.py` | 変更 | 形の番号 1 の保存先が退避されることを確かめる | `plugins/ndf-next/src/ndf/core/durable.py` |
| `plugins/ndf-next/tests/domains/test_branch_flows.py` | 変更 | 設定の `branch_flow` を取り込み方の表で照らすテストを、キー `release_strategy` と新しい行の id にする | `plugins/ndf-next/contracts/domains/branch-flows.json` |
| `plugins/ndf-next/tests/domains/settings/` | 新設 | 改名表とスキーマの突き合わせ（I9）・移行のコマンド（I10）・読む側の止まり方（I6）の単体テスト | `plugins/ndf-next/src/ndf/domains/settings/` |
| `plugins/ndf-next/src/` | 変更 | 上の行に無いモジュールの識別子・コメント・docstring・出力の文を、対応表どおりに置き換える | 無し |
| `plugins/ndf-next/tests/` | 変更 | 上の行に無いテストの識別子と期待値を、改名に合わせて置き換える。改名したモジュールのテストのファイル名も改める（「改名の一覧」） | 無し |
| `plugins/ndf-next/contracts/` | 変更 | 上の行に無い契約の JSON の説明を置き換える | 無し |
| `plugins/ndf-next/skills/` | 変更 | Skill の本文を対応表どおりに置き換え、設定のキーの例を新しい名前にする。`skills/worktree/references/declaration.md` は `ndf-settings.md` へ改名する | 無し |
| `plugins/ndf-next/agents/` | 変更 | エージェントの本文を対応表どおりに置き換える | 無し |
| `plugins/ndf-next/bin/` | 変更 | 起動のスクリプトのコメントと出力の文を置き換える | 無し |
| `plugins/ndf-next/hooks/` | 変更 | hook の設定の説明を置き換える | 無し |

**`release-steps release` の引数 `--promotion` は、ndf-next のコードに無い。** `git grep -n -- '--promotion' -- plugins/ndf-next/src` は 0 件である。出現は、Skill の本文の次の箇所だけである（2026-11-16）。

- `skills/release/references/release-steps.md` の 124・135 行目
- `skills/development-workflow/references/production-merge.md` の 66 行目

設計文書の「入出力の契約」の `--release-strategy release_branch` は、この Skill の本文の書き換えで満たす。引数を ndf-next の `release-steps` に作るのは、その機能を移す課題が行う。

### 改名の一覧

ファイル配置の表の「改名」の行と、ディレクトリの行に含めた改名の内訳である。設計文書の決定 6 と決定 16 の基準で改名するものだけを並べる。設定のキーと値の改名は設計文書の「データ構造」の表が持つ。

| 旧い名前 | 新しい名前 | 種類 |
| --- | --- | --- |
| `checks/marks.py`・`Mark`・`MARK`・`read_marks`・`mark_problem`・引数 `marks` | `checks/suppressions.py`・`Suppression`・`SUPPRESSION`・`read_suppressions`・`suppression_problem`・引数 `suppressions` | モジュール・型・定数・関数 |
| `rules.json` の兆候の表のキー `mark` | `suppress` | 規則のデータのキー |
| `relay/marks.py`・`MarkError`・`mark_path` | `relay/markers.py`・`MarkerError`・`marker_path`（関数 `mark`・`take` は変えない） | モジュール・型・関数 |
| 保存先の ID `relay-marks`・ファイルの ID `relay-mark` | `relay-markers`・`relay-marker` | 契約の ID |
| `durable_ledger.py`・`LedgerRow`・`_ledger` | `durable_runs.py`・`RunRow`・`_runs` | モジュール・型・関数 |
| `worktree/ledger.py`・`Ledger`・`ledger_path`（worktree） | `worktree/allocations.py`・`Allocations`・`allocations_path` | モジュール・型・関数 |
| `records/usage.py` の `ledger_path` | `usage_log_path` | 関数 |
| `FindingLedger`・欄 `ledger` | `FindingJournal`・欄 `journal` | 型 |
| ファイルの ID `backlog-ledger`・ファイル名 `ledger.json` | `backlog-journal`・`journal.json` | 契約の ID・ファイル名 |
| ファイルの ID `usage-ledger-file`・保存先の ID `usage-ledger` | `usage-log-file`・`usage-log` | 契約の ID |
| `release/declaration.py`・`worktree/declaration.py`・`Declaration`・`DeclarationError` | `release/ndf_settings.py`・`worktree/ndf_settings.py`・`NdfSettings`・`NdfSettingsError` | モジュール・型 |
| 固定の語のキー `declaration_dir` | `ndf_settings_dir` | 定数の表のキー |
| `contracts/domains/places.json`・列のキー `place`・関数と変数の `place`・`places` | `contracts/domains/locations.json`・`location`・`location`・`locations` | 契約・識別子 |
| `progress/model.py` の `table_id`・列 `table_id`・引数 `table_id` | `machine_id` | 関数・列・引数 |
| 表 `ndf_progress_tables`・`TABLES`・`read_table`・`create_table` | `ndf_progress_machines`・`MACHINES`・`read_machine`・`create_machine` | 表・定数・関数 |
| MVV の改訂の兆候の欄 `Signals.revise`・結果の `metrics.revise` | `revision_due` | 型の欄・結果のキー |
| 取り込み方の表の列 `promotion`・`merged_decide._flow_row` の引数 `promotion` | `release_strategy` | 契約の列・引数 |
| 取り込み方の表の行の id `promote-whole`・`promote-selected` | `merge-base-branch`・`release-branch` | 契約の ID |
| `vet_sprint`・`vet_key`・`VET_SPRINT`・`last_vet`・スキーマの `$defs` の `MvvVet` | `mvv_validate_sprint`・`mvv_validation_key`・`MVV_VALIDATION_SPRINT`・`last_mvv_validation`・`MvvValidation` | 関数・定数・変数・スキーマの型 |
| 記録の ID `mvv-vet`・ファイル名 `mvv-vet.jsonl` | `mvv-validation`・`mvv-validation.jsonl` | 契約の ID・ファイル名 |
| テスト `test_durable_ledger.py`・`test_crossreview_ledger.py`・`test_declarations.py` | `test_durable_runs.py`・`test_crossreview_journal.py`・`test_ndf_settings.py` | テストのファイル |

**改名だけの関数は「関数の仕様」に書かない。** 引数の型・責務・失敗の扱いを変えず、名前と引数の名前だけを変えるためである（`detailed-design.md` の「書く範囲」）。呼び出し元は `git grep -w <旧い名前>` が 0 件になるまで直す（受け入れ条件 6）。

**改名しない名前**は次のとおりである。

- 承認ゲートの回答の値 `revise`・`REVISE`・`ndf progress answer` の引数（Factory のサービスが読む記録の値。I8・決定 11）
- Factory のサービスが読む `ask-ledger`（I8）
- `.ndf/mvv.json` のキー `revise_after`（要求の前提 6）
- 言語モデルの判定の答えの欄 `verdict` と値 `follow`（決定 16）
- 表 `ndf_runs`（英語として通じる）
- 中継の関数 `mark`・`take`（動詞として通じる）

同等性の表（`src/ndf/parity/features.json`）が今の NDF のファイル名（`revise-note.py` など）を引いた箇所も、今の NDF の名前なので変えない（前提 5）。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/checks/words.py` | モジュール `words` | `BannedWords`（新設）、`banned_words`（変更）、`_words_from`（変更）、`_standalone_from`（新設）、`pattern`（変更）、`_part`（変更）、`check`（変更）、`_check_source`・`_examples`（変えない） |
| `plugins/ndf-next/src/ndf/checks/suppressions.py` | モジュール `suppressions` | `Suppression`・`read_suppressions`・`suppression_problem`・`Applied`・`apply`（すべて改名だけ） |
| `plugins/ndf-next/src/ndf/checks/spec_docs.py` | モジュール `spec_docs` | `_real_errors`（変更）、`decl_findings`（変えない） |
| `plugins/ndf-next/src/ndf/core/durable.py` | モジュール `durable` | 定数 `STORE_FORMAT` を 2 へ。関数は変えない |
| `plugins/ndf-next/src/ndf/core/durable_runs.py` | モジュール `durable_runs` | `RunRow`・`_runs`（改名だけ）、`_register_run`・`_forget_run`（変えない） |
| `plugins/ndf-next/src/ndf/progress/store.py` | モジュール `store` | `ensure_schema`（変更）、`read_machine`・`create_machine`・`read_events`・`read_definition`・`run_lock` ほか（改名と引数の名前だけ） |
| `plugins/ndf-next/src/ndf/progress/model.py` | モジュール `model` | `machine_id`（改名だけ） |
| `plugins/ndf-next/src/ndf/progress/gate/checks.py` | モジュール `checks` | `mvv_decls`（引く列と値の名前だけ） |
| `plugins/ndf-next/src/ndf/domains/settings/renames.py` | モジュール `renames` | `Rename`・`LegacyKey`（新設の型）、`load`・`legacy_keys`・`migrate`・`legacy_detail`（新設）、`_pointers`（新設の内部の関数） |
| `plugins/ndf-next/src/ndf/domains/settings/cli.py` | モジュール `cli` | `main`・`plan`・`apply`（新設）、`_targets`・`_digest`（新設の内部の関数） |
| `plugins/ndf-next/src/ndf/domains/relay/markers.py` | モジュール `markers` | `MarkerError`・`marker_path`（改名だけ）、`mark`・`take`（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/allocations.py` | クラス `Allocations` | `allocations_path`（改名だけ）、`Allocations` のメソッド（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/ndf_settings.py` | クラス `NdfSettings` | `load`（変更）、`NdfSettingsError`（改名だけ）、`shared_file`・`local_file`・`read_object`（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/merged.py` | モジュール `merged` | `read_flow_record`（変更）、`after_merge`（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/merged_decide.py` | モジュール `merged_decide` | `_flow_row`・`sprint_keep`（引く列とキーの名前だけ） |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | クラス `NdfSettings` | `NdfSettingsError`（改名だけ）、`load`・`run_step`・`production_admin`（変えない） |
| `plugins/ndf-next/src/ndf/domains/records/mvv.py` | モジュール `mvv` | `decl_at`・`worktree_version`（変更）、`mvv_validate_sprint`（改名だけ） |
| `plugins/ndf-next/src/ndf/domains/records/mvv_decide.py` | モジュール `mvv_decide` | `mvv_validation_key`・`MVV_VALIDATION_SPRINT`・`Signals.revision_due`（改名だけ） |
| `plugins/ndf-next/src/ndf/domains/records/preflight.py` | モジュール `preflight` | `mvv_checked`（変更）、`mvv_state`（変えない） |
| `plugins/ndf-next/src/ndf/domains/crossreview/drive.py` | クラス `FindingJournal` | 改名だけ |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `banned_words` | `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | `banned_words(repo_root: Path, config_name: str) -> BannedWords` | 用語集の設定と用語集から、禁止する語の理由と単独で照合する語の集合を作る | `repo_root` はリポジトリの根 | 用語集の設定が無ければ `reasons` も `standalone` も空。あれば `standalone` のすべての語が `reasons` のキーにある | 用語集の設定（`.ndf/<config_name>`）と用語集のデータを読む |
| `_words_from` | `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | `_words_from(config: dict[str, Any], glossary: dict[str, Any]) -> dict[str, str]` | 廃止した語（`deprecated`）と禁止語から、語 → 理由を作る | `glossary` は `terms` を持つ | 同じ廃止した語を複数の項目が持つときは、用語集で先に出る項目の語を理由に書く。禁止語は「禁止語」の理由で上書きする | 無し |
| `_standalone_from` | `plugins/ndf-next/src/ndf/checks/words.py` | 新設 | `_standalone_from(glossary: dict[str, Any]) -> dict[str, str]` | 単独で照合する廃止した語（`deprecated_standalone`）から、語 → 理由を作る | `glossary` は `terms` を持つ | 欄を持たない項目は飛ばす。同じ語を複数の項目が持つときは、先に出る項目の語を理由に書く | 無し |
| `pattern` | `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | `pattern(words: Mapping[str, str], standalone: AbstractSet[str] = frozenset()) -> re.Pattern[str] \| None` | 長い語から当てる正規表現を作り、単独の語にだけ前後が漢字でない条件を付ける | `standalone` は `words` のキーの部分集合 | `words` が空なら `None`。単独の語は前後が漢字の出現に当たらない | 無し |
| `_part` | `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | `_part(word: str, standalone: bool) -> str` | 1 つの語の正規表現の断片を作る | `word` は空でない | 単独の語は、字種に依らず前後が漢字でない条件が付く。それ以外は今の規則（ASCII・カタカナ）のまま | 無し |
| `check` | `plugins/ndf-next/src/ndf/checks/words.py` | 変更 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | プラグインの下の文字のファイルから、禁止する語を行ごとに拾う | `rules` は `words` の節を持つ | 例として許した語を除き、当たった語ごとに `banned-word` の指摘が 1 件ある | `banned_words` を通して用語集の設定と用語集のデータを読む |
| `ensure_schema` | `plugins/ndf-next/src/ndf/progress/store.py` | 変更 | `ensure_schema(store: Store, now: datetime) -> None`（変えない） | 表が無ければ作り、表の版を上げる。版 1 の PostgreSQL の表を見つけたら止める | `store` は開いた状態の DB | 戻ったときは表の版が `SCHEMA_VERSION` で、列が `machine_id` の形 | DB の表を作る・版を書く。SQLite で版を上げる前にバックアップを取る |
| `renames.load` | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 新設 | `load() -> tuple[Rename, ...]` | 改名表の行を、表の並びのまま型にして返す | 契約の表の置き場が決まっている（`ndf.contracts.use_dir` か既定） | 戻り値の並びは表の行の並びで、親のキーの行が子のキーの行より前にある | 契約の表（`ndf-settings-renames.json`）を読む（`facts.rows` のキャッシュを通す） |
| `legacy_keys` | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 新設 | `legacy_keys(file: str, data: Mapping[str, Any]) -> list[LegacyKey]` | 設定の今の中身に実在する旧いキーを、配列の要素ごとの位置で返す。各行の `old` を中身にそのまま当て、親を改めた後の位置で書いた子の行は、親を移した後の中身でだけ当たる | `file` は表 `ndf-files` の ID。`data` は JSON のオブジェクト | 戻り値の `old` はどれも `data` の中に実在する位置（`/versions/0/vet`）で、表の並び・配列の添字の順に並ぶ。表に無い `file` なら空 | 無し（`load` を通して契約の表を読む） |
| `migrate` | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 新設 | `migrate(file: str, data: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]` | 旧いキーを新しいキーへ、旧い値を新しい値へ変えた中身と、JSON Patch の操作の列を返す | `legacy_keys` と同じ | 変換した中身に `legacy_keys` が 0 件。改名したキーは同じオブジェクトの中の元の並びの位置に置く。表に無いキーと、`values` に無い値は変わらない。操作の列はキーの改名が `move`、値の対応が `replace` で、当てる順に並ぶ。`move` と `replace` の直前には、元の位置の今の値を持つ `test` を置く。`data` は変えない（深いコピーを返す） | 無し |
| `legacy_detail` | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 新設 | `legacy_detail(file: str, data: Mapping[str, Any], root: Path) -> Optional[str]` | 旧いキーが残れば、止まる理由の文（ファイル・旧いキーの位置 → 新しいキーの位置・打つコマンド）を返す | `legacy_keys` と同じ。`root` は移行のコマンドへ渡すリポジトリの根 | 旧いキーが無ければ `None`。あれば文に旧いキーのすべての位置と `ndf settings migrate --root <root>` がある | 無し |
| `plan` | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | 新設 | `plan(root: Path) -> dict[str, Any]` | 改名表に当たる設定ファイルごとの差分と、差分のハッシュを結果 JSON にする | `root` はディレクトリ | 書き換えるものが無ければ `status: ok`。あれば `status: gate` で、`items` にファイルごとの差分、`metrics.digest` に差分のハッシュ、`next` に `--apply <差分のハッシュ>` を付けたコマンドがある。ファイルは 1 バイトも変わらない | `root` の下の `.ndf/` の設定ファイルを読む |
| `apply` | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | 新設 | `apply(root: Path, digest: str) -> dict[str, Any]` | 今の差分のハッシュが渡したハッシュと一致し、変換した中身が新しいスキーマに通るときだけ書く | `plan` と同じ。`digest` は `plan` が返した 64 桁の 16 進 | `status: ok` のとき、差分のあったファイルはどれも変換した中身で、新しいスキーマに通り、`legacy_keys` が 0 件。`stopped` のときはどのファイルも変わらない | `root` の下の `.ndf/` の設定ファイルを読み、差分のあるファイルを書き換える |
| `main` | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | 新設 | `main(argv: list[str]) -> int` | `ndf settings migrate --root <リポジトリ> [--apply <差分のハッシュ>]` を解き、結果 JSON を 1 行出して終了コードを返す | `argv` はサブコマンドの名前の後ろの引数 | 標準出力に結果 JSON が 1 行あり、終了コードは結果の状態と `cause` の種類から `exit-codes.json` が決める値（`ok` は 0、`gate` は 10） | 標準出力へ書く。`plan` か `apply` の副作用 |
| `ndf_settings.load` | `plugins/ndf-next/src/ndf/domains/worktree/ndf_settings.py` | 変更 | `load(main_dir: Path) -> Optional[NdfSettings]`（型の名前だけ変わる） | メインディレクトリの worktree の設定を読み、旧いキーが残れば止まる | `main_dir` はメインディレクトリ | 共有設定が無ければ `None`。戻り値の設定に旧いキーは無い | 共有設定と個人設定を読む |
| `read_flow_record` | `plugins/ndf-next/src/ndf/domains/worktree/merged.py` | 変更 | `read_flow_record(path: Optional[Path]) -> Optional[dict[str, Any]]`（変えない） | スプリントの開始時の記録を読み、`branch_flow` の旧いキーが残れば止まる | 無し | `path` が `None` なら `None`。戻り値に旧いキーは無い | `path` のファイルを読む |
| `decl_at` | `plugins/ndf-next/src/ndf/domains/records/mvv.py` | 変更 | `decl_at(root: Path, ref: str) -> dict[str, Any]`（変えない） | 参照にある MVV の設定を読み、旧いキーが残れば止まる | `root` は git のリポジトリ | 戻り値に旧いキーは無い | `git show` を打つ |
| `worktree_version` | `plugins/ndf-next/src/ndf/domains/records/mvv.py` | 変更 | `worktree_version(root: Path) -> Optional[int]`（変えない） | worktree の MVV の設定の現行の版を返し、旧いキーが残れば止まる | 無し | 旧いキーの残る設定から版を返さない | MVV の設定のファイルを読む |
| `mvv_checked` | `plugins/ndf-next/src/ndf/domains/records/preflight.py` | 変更 | `mvv_checked(root: Path) -> pd.Checked`（変えない） | MVV の状態を確かめ、旧いキーの残る設定・本文の不一致・読めない設定で止まる | 無し | 旧いキーが残れば `precondition` の `stopped` で、理由の文は `legacy_detail` の文 | MVV の設定と本文のファイルを読む |
| `_real_errors` | `plugins/ndf-next/src/ndf/checks/spec_docs.py` | 変更 | `_real_errors(decl: DeclarationSpec, path: Path) -> list[str]`（引数の型の名前は spec_model の改名に従う） | `.ndf/` の実物を、旧いキーがあれば改名表で変換してからスキーマで確かめる | `decl.id` は表 `ndf-files` の ID | 旧いキーそのものは誤りにしない。変換で直らない誤りは今と同じ形（`/<位置>: <文>`）で返す | 実物のファイルを読む |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `banned_words` | 用語集の設定か用語集のデータが JSON として読めない | ファイルの破損・用語集の設定の `source` が無いパスを指す | `read_json`（今の `_read`） | 呼び出し元へ返す（今と同じ） | `ndf check` が「用語集を読めない」の指摘を出し、終了コード 1 | 後始末は無い。ファイルを直して打ち直せる |
| `banned_words` | 用語集の項目の `deprecated_standalone` が文字列の配列でない | 用語集の手での編集の誤り | `_standalone_from` の中で配列として読むとき | 呼び出し元へ返す。用語集の構造の検査（`glossary.py check` の `schema`）が同じ誤りを先に落とすため、ここで別の形の検査を持たない | `ndf check` が型の誤りで止まり、終了コード 1 | 後始末は無い。用語集を直して打ち直せる |
| `check` | 用語集の読み込みの失敗 | `banned_words` の失敗 | `banned_words` | 呼び出し元へ返す（規則のランナーが規則ごとの失敗を指摘にする今の扱い） | 上の `banned_words` の行と同じ | 上の `banned_words` の行と同じ |
| `ensure_schema` | 表の版が 1 の PostgreSQL の表がある | 列を改名する前の ndf-next が作った表を、同じ接続先で使い続けた | `_read_version` の戻り値が `SCHEMA_VERSION` より小さく、`store.path` が `None` | 止める（`durable.StoreBroken` を送出する）。表は書き換えない | 結果 JSON の `status` が `stopped`、理由の語は `store_broken`。本文に「ステートマシンの表の版 1 は列の名前が古い。表 `ndf_progress_*` を消して作り直す」と、パスワードを隠した接続先を出す。終了コードは今の `stopped` と同じ | DB は変えていない。利用者が表を消せば打ち直せる |
| `ensure_schema` | SQLite で版を上げる前のバックアップが取れない | 保存先が書き込めない・容量が無い | `_backup_or_warn`（今と同じ） | 警告を残して続ける（今と同じ） | 結果 JSON の警告に理由が載る。終了コードは変わらない | 今と同じ |
| `renames.load` | 改名表が無い・形が違う | 契約の表の編集の誤り | `facts.rows` の表の形の検査（`FactsError`） | 呼び出し元へ返す。契約の表の誤りは CI の `ndf check`（facts の規則）と単体テスト（I9）が先に落とす | 呼び手のサブコマンドが `stopped`（種類 `unknown`。`ndf.cli` が捕まえない例外を変える今の扱い）、終了コードは `exit-codes.json` の `unknown` の行 | 後始末は無い。表を直して打ち直せる |
| `legacy_keys` | 改名表の `old` が `*` の位置で配列でない値に当たる | 利用者の設定の `versions` が配列でない | `_pointers` が `*` を広げるとき | 当たらない位置として飛ばす（旧いキーに数えない）。形の誤りは読む側とスキーマが今と同じく扱う | 無し（旧いキーが無いとして返す） | 後始末は無い |
| `plan` | 設定が JSON として読めない・オブジェクトでない | 利用者の設定の破損 | `_targets` の `jsonfile.read_object`（`JsonFileError`） | 止める。ほかのファイルの差分も出さない（一部だけの差分に承認を求めない） | `stopped`（種類 `invalid`、行き先は conductor）。本文にファイルのパスと誤りの位置（`JSONDecodeError` の行と列）。終了コード 2 | 何も書いていない。設定を直して打ち直せる |
| `plan` | 設定ファイルが読めない（権限・入出力の誤り） | 利用者の環境 | `_targets` の `read_text`（`OSError`） | 止める | `stopped`（種類 `invalid`）。本文にパスと理由。終了コード 2 | 何も書いていない。打ち直せる |
| `apply` | 渡したハッシュが今の差分のハッシュと一致しない | 差分を示した後に設定が変わった・別のリポジトリのハッシュを渡した・書き換えるものが無い | `apply` の中で `_digest` を作り直して比べるとき | 止める。1 バイトも書かない | `stopped`（種類 `refused`、行き先は人）。本文に「示した差分と今の差分が違う。`ndf settings migrate --root <root>` で差分を示し直し、承認を取り直す」。終了コード 1 | 何も書いていない。示し直して承認を取り直せば打ち直せる |
| `apply` | 変換した中身が新しいスキーマに通らない | 利用者の設定に、改名と関わらない誤りがある | `contracts.schema_errors` | 止める。どのファイルも書かない | `stopped`（種類 `refused`）。本文にファイルとスキーマの誤りの位置と文。終了コード 1 | 何も書いていない。誤りを直し、示し直して承認を取り直す |
| `apply` | 読み込みの失敗 | `plan` の 2 行と同じ | `_targets` | 止める | `plan` の 2 行と同じ | `plan` の 2 行と同じ |
| `apply` | 書き換えの途中で書けない | 容量が無い・権限が無い | `atomic.write_text`（`OSError`） | 止める。書き終えたファイルは戻さない | `stopped`（種類 `invalid`）。本文に書けなかったパスと、書き終えたファイルの一覧。終了コード 2 | 1 ファイルの中は書き終えたか元のままのどちらか（一時ファイルの付け替え）。打ち直すと、書き終えたファイルの差分は 0 件になり、残りの差分の新しいハッシュが要る（示し直して承認を取り直す） |
| `main` | 引数の誤り（`migrate` が無い・`--root` が無い・`--apply` の値が 64 桁の 16 進でない） | 打つ側の誤り | argparse と `main` の形の確かめ | 止める | `stopped`（種類 `invalid`）。終了コード 2 | 後始末は無い。打ち直せる |
| `main` | `plan`・`apply` の失敗 | 上の各行 | `plan`・`apply` | 結果 JSON をそのまま出す | 上の各行と同じ | 上の各行と同じ |
| `ndf_settings.load` | 旧いキーが残る | 移行のコマンドを打っていない | `load` の中で、共有設定を読んだ直後に `legacy_detail("worktree", data, main_dir)` が文を返す | 止める（`NdfSettingsError` を送出する）。既定の行へ倒さない | 呼び手の `worktree` のサブコマンドが `stopped`（種類 `precondition`、行き先は人）。本文は `legacy_detail` の文。終了コード 3 | 何も書いていない。移行のコマンドを打てば打ち直せる |
| `ndf_settings.load` | 共有設定が読めない・版が違う | 今と同じ | 今と同じ | 今と同じ（`NdfSettingsError`） | 今と同じ（`precondition`、終了コード 3） | 今と同じ |
| `read_flow_record` | 記録の `branch_flow` のコピーに旧いキーが残る | 改名の前の ndf-next が書いた開始時の記録を渡した | `legacy_detail("worktree", {"branch_flow": 記録}, path.parent)` が文を返す | 止める（`NdfSettingsError`）。旧いキーを読まずに既定の行（スプリントブランチを残さない行）へ倒すと、残すはずのブランチを消すため | `ndf worktree merged` が `stopped`（種類 `precondition`）。本文は `legacy_detail` の文。終了コード 3 | 後片付けを始める前に止まり、何も消していない。記録のキーを書き換えれば打ち直せる |
| `decl_at` | 旧いキーが残る | 参照のブランチの MVV の設定を移行していない | `legacy_detail("mvv", raw, root)` が文を返す | 止める（`MvvError` を送出する） | `ndf records mvv signals` が `stopped`（種類 `precondition`）。本文は `legacy_detail` の文。終了コード 3 | 何も書いていない。移行してマージすれば打ち直せる |
| `decl_at` | 参照の設定が読めない | 今と同じ | 今と同じ | 今と同じ（`MvvError`） | 今と同じ（`precondition`） | 今と同じ |
| `worktree_version` | 旧いキーが残る | worktree の MVV の設定を移行していない | `legacy_detail("mvv", raw, root)` が文を返す | 止める（`MvvError`） | `ndf records mvv answer` が `stopped`（種類 `precondition`）。本文は `legacy_detail` の文。終了コード 3 | 改訂の兆候の記録を書く前に止まり、何も書いていない。移行すれば打ち直せる |
| `worktree_version` | 設定が読めない | 今と同じ | 今と同じ | 今と同じ（`MvvError`） | 今と同じ | 今と同じ |
| `mvv_checked` | 旧いキーが残る | MVV の設定を移行していない | 設定を読んだ直後の `legacy_detail("mvv", decl, root)` | 止める（`precondition` の `stopped` を返す）。本文の確かめより先に当てる | `ndf records preflight` が `stopped`（種類 `precondition`）。本文は `legacy_detail` の文。終了コード 3 | 何も書いていない。移行すれば打ち直せる |
| `mvv_checked` | 設定が読めない・本文が承認の記録と違う | 今と同じ | `mvv_state` | 今と同じ（`precondition` の `stopped`） | 今と同じ | 今と同じ |
| `_real_errors` | 実物が JSON として読めない | 今と同じ | 今と同じ | 今と同じ（誤りの文を返す） | `ndf check` の spec-decl の指摘、終了コード 1 | 今と同じ |
| `_real_errors` | 変換した後もスキーマに通らない | 改名と関わらない誤り | `Draft202012Validator.iter_errors` | 誤りの文を返す（今と同じ）。位置は変換した後の中身の位置で書く | `ndf check` の spec-decl の指摘、終了コード 1 | 後始末は無い。実物を直して打ち直せる |

**SQLite の保存先では、版 1 の表は `ensure_schema` まで届かない。** チェックアウトの保存先の形の番号を 2 に上げたため、`durable` が版 1 の保存先を `<名前>.v1-<日付>` へ退避し、空の保存先で表を作り直す（今の `_retire_store` の経路）。`ensure_schema` が版 1 を見るのは PostgreSQL の接続先だけである。

**旧いキーで止まる読む側は、設計文書の構成要素の表の「設定を読む側」より 1 つ多い。** スプリントの開始時の記録（`read_flow_record`）は `branch_flow` のコピーを持ち、`merged_decide.sprint_keep` が取り込み方を引く。コピーの旧いキーを読まないと、キーが無いとして既定の行（`keeps_sprint` が偽）を使い、選んで集めるリリース方式で残すべきスプリントブランチを消す。設計文書の I6 の「旧いキーが残る設定を読むと止まる」をこの記録にも当てる。

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_part` | 語が単独で照合する語 | `(?<![<漢字>])<語>(?![<漢字>])`（ASCII・カタカナの判定より先に当てる） |
| `_part` | 単独でなく、語が ASCII だけ | 今の規則（前後が英数字と `_` でない） |
| `_part` | 単独でなく、語がカタカナだけ | 今の規則（前後がカタカナでない） |
| `_part` | 上のどれでもない | 語をそのまま当てる（今の規則） |
| `_words_from` | 同じ語が 2 つの項目の `deprecated` にある | 先に出る項目の語を理由にする（`dict.setdefault`） |
| `_words_from` | 同じ語が `deprecated` と禁止語の両方にある | 禁止語の理由で上書きする（今と同じ） |
| `banned_words` | 同じ語が `deprecated` と `deprecated_standalone` の両方にある | 理由は `deprecated` の側を使い、`standalone` に入れる（単独の条件が付く） |
| `ensure_schema` | 版が `SCHEMA_VERSION` より大きい | `StoreFormatNewer` で止める（今と同じ） |
| `ensure_schema` | 版が `SCHEMA_VERSION` と同じ | 何もしない（今と同じ） |
| `ensure_schema` | 版が無い（表が無い） | 表を作り、版を書く（今と同じ） |
| `ensure_schema` | 版が `SCHEMA_VERSION` より小さく、SQLite | バックアップを取ってから表を用意する（今と同じ。形の番号の退避で、この組み合わせは起こらない） |
| `ensure_schema` | 版が `SCHEMA_VERSION` より小さく、PostgreSQL | `StoreBroken` で止める（新しい分岐） |
| `migrate` | 行の `old` の位置にキーがあり、同じオブジェクトに `new` の名前のキーが無い | キーの名前を変え、元の並びの位置に置く。`move` の操作を 1 つ足す |
| `migrate` | 行の `old` の位置にキーがあり、同じオブジェクトに `new` の名前のキーもある | 旧いキーを消さずに残し、操作を足さない（利用者が手で両方を書いた設定を機械が選ばない）。`legacy_keys` は旧いキーを返し続け、読む側は止まる。`apply` は変換した後も旧いキーが残るため `refused` で止まる |
| `migrate` | 行に `values` があり、改名した位置の値が `values` のキーにある | 値を置き換え、`replace` の操作を 1 つ足す |
| `migrate` | 行に `values` があり、値が `values` のキーに無い | 値を変えない（新しいスキーマの検査が誤りを示す） |
| `migrate` | 行の `old` の位置にキーが無い | 何もしない |
| `_pointers` | `old` に `*` がある | 親の位置の値が配列なら、要素ごとに `*` を添字へ広げる。配列でなければ位置を返さない |
| `_pointers` | 親のキーの行（`/versions/*/vet`）を当てた後の子のキーの行（`/versions/*/vet/verdict`） | 子の行の `old` は親を改めた後の位置で読む（改名表の子の行の `old` は `/versions/*/validation/verdict` と書く。設計文書の「データ構造」の行の並びの決まり）。`legacy_keys` は行を当てずに今の中身へ `old` を当てるため、未移行の中身では親の行だけが当たる |
| `plan` | 改名表に当たる設定ファイルが 1 つも無い（`.ndf/` が無いリポジトリを含む） | `status: ok`、「書き換えるものが無い」 |
| `plan` | ファイルはあるが、どれも差分が 0 件 | `status: ok`、「書き換えるものが無い」 |
| `plan` | 差分が 1 件以上 | `status: gate`、差分とハッシュ |
| `apply` | 差分が 0 件 | ハッシュが一致しないとして `refused`（空の差分に承認は無い） |
| `apply` | ハッシュが一致し、変換した中身がどのファイルもスキーマに通り、旧いキーが残らない | 差分のあるファイルだけを書き、書いたファイルの一覧を `items` に出す |
| `apply` | ハッシュが一致し、どれかのファイルがスキーマに通らないか旧いキーが残る | `refused`。どのファイルも書かない（書く前にすべてを確かめる） |
| `legacy_detail` | 旧いキーが無い | `None` |
| `legacy_detail` | 旧いキーが 1 つ以上 | `<設定ファイルの相対パス> に旧いキーが残る: <old> → <new>（、で並べる）。ndf settings migrate --root <root> で差分を示し、承認してから --apply で書き換える` |

**単独の語の条件を、語の先頭か末尾が漢字かに依らず両側に付ける。** 今の NDF の用語の検査（`glossary_words.standalone_part`）は、先頭が漢字なら前に、末尾が漢字なら後ろにだけ条件を付ける。ndf-next の対応表の「問い」は末尾がひらがなで、今の NDF の形では「問い合わせ」に当たる。対応表が「問い合わせ」を外すと決めたため（W12・決定 17）、両側に付ける。両側に付けても「版の記録」「`段を足す`」のように前後が漢字でない出現には当たる。

**差分のハッシュは、差分を並べた JSON の sha256 を取った 64 桁の 16 進である。** 並べる値は、ファイルの ID の順に並べた `[{"file": <ID>, "path": <相対パス>, "patch": <操作の列>}]` である。これを、キーを並べ替えた区切りの空白の無い JSON にする（`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`）。 操作の列が同じなら、打つマシンと時刻に依らず同じハッシュになる。操作の列は `move` と `replace` ごとに元の位置の今の値を持つ `test` を含むため、示した後に移す値（`vet` の `at` など）を変えるとハッシュが変わり、`apply` は `refused` で止まる。`--root` の絶対パスはハッシュに入れない（同じリポジトリの別の clone で示した差分も、中身が同じなら同じハッシュになる）。

**書くときの JSON の形は、インデント 2・`ensure_ascii=False`・末尾の改行 1 つにする。** 今の NDF と人が書いた `.ndf/` の実物（このリポジトリの `.ndf/worktree.json`）はインデント 2 である。インデントの違うファイルは書き換えの後にインデント 2 へそろい、差分に空白の行が出る。差分の JSON Patch には空白の変化が載らない。そのため `plan` は、差分の各ファイルの `items` に「インデントがそろう」を 1 行添える。添えるのは、`json.dumps(data, indent=2, ensure_ascii=False) + "\n"` と今の中身が違うときである。

- 繰り返しの終わる条件: `check` はプラグインの下の文字のファイルを 1 回ずつ読み、各ファイルの行を 1 回ずつ見る（今と同じ）。`migrate` は改名表の行を 1 回ずつ当て、`*` は配列の要素の数だけ広げる
- 計算量の注意: 禁止語の語の数は、廃止した語の登録で今の数十から数百へ増える。正規表現は語を `|` で並べた 1 つにまとめ、ファイルの行ごとに 1 回だけ当てる（今と同じ）。語ごとにファイルを読み直さない。改名表は 4 行、設定の `versions` は MVV の改訂の回数（数十）で、計算量の注意は無い

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `BannedWords` | 型（凍結した dataclass。`reasons: dict[str, str]`・`standalone: frozenset[str]`） | `plugins/ndf-next/src/ndf/checks/words.py` | 用語集の設定が無いときは両方とも空 |
| `KANJI` | 定数（正規表現の文字クラスの中身） | `plugins/ndf-next/src/ndf/checks/words.py` | `"㐀-䶿一-鿿豈-﫿々"`（今の NDF の `glossary_words.KANJI` と同じ字の範囲。ndf-next は今の NDF のモジュールを読まないため、ndf-next の中ではここだけに持つ） |
| 単独の語の理由の文 | 定数（文の形） | `plugins/ndf-next/src/ndf/checks/words.py` | `廃止した語（単独の出現）。「<置き換え先の語>」と書く` |
| `STORE_FORMAT` | 定数 | `plugins/ndf-next/src/ndf/core/durable.py` | `2`（今は 1） |
| `SCHEMA_VERSION` | 定数 | `plugins/ndf-next/src/ndf/progress/store.py` | `2`（今は 1） |
| `FIXED_WORDS["ndf_settings_dir"]` | 定数の表のキー | `plugins/ndf-next/src/ndf/identity.py` | `".ndf"`（値は変えない） |
| `words.examples` の足す行 | 設定値 | `plugins/ndf-next/src/ndf/checks/rules.json` | 同等性の表（`src/ndf/parity/features.json`）が今の NDF の文言を引いた箇所だけ（前提 5）。行ごとにファイル・語・理由を書く。件数は実装の時点の禁止語の規則の出力で決める。単独で照合する廃止した語を例として挙げる行は外さず、用語集に登録していない語の例へ書き換える（設計文書の「処理の流れ」の 1） |
| 兆候の表のキー `suppress` | 設定値のキー | `plugins/ndf-next/src/ndf/checks/rules.json` | 値（`boundary`・`none` など）は変えない |
| 改名表の列 | 契約の表の列の定義 | `plugins/ndf-next/contracts/domains/ndf-settings-renames.json` | `file`（string。表 `ndf-files` の ID）・`old`（string。JSON Pointer。`*` は配列のすべての要素）・`new`（string。JSON Pointer。`old` と最後の 1 階層だけが違う）・`values`（object。`optional: true`。旧い値 → 新しい値） |
| 改名表の行 | 契約の表の行 | `plugins/ndf-next/contracts/domains/ndf-settings-renames.json` | 設計文書の「データ構造」の「利用者の設定のキーと値の改名」の 4 行。行の id は `worktree-release-strategy`・`mvv-validation`・`mvv-validation-result`・`mvv-validation-checked-at`。名前を変えない `accepted_unknown` は親の行で移り、行を持たない。子の行の `old` は親を改めた後の位置（`/versions/*/validation/verdict`） |
| `Rename` | 型（凍結した dataclass。`id`・`file`・`old`・`new`・`values: Mapping[str, str]`） | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | `values` は行に無ければ空 |
| `LegacyKey` | 型（凍結した dataclass。`file`・`old`・`new`。`old` と `new` は添字を広げた位置） | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | 無し |
| `RENAMES` | 定数（契約の表の名前） | `plugins/ndf-next/src/ndf/domains/settings/renames.py` | `"ndf-settings-renames"` |
| `DIGEST` | 定数（`--apply` の値の形） | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | `re.compile(r"[0-9a-f]{64}")` |
| `TOOL` | 定数（結果 JSON の tool の名前） | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | `"settings"` |
| 書くときのインデント | 定数 | `plugins/ndf-next/src/ndf/domains/settings/cli.py` | `2` |

**旧いキーの名前（`promotion`・`vet`・`verdict`・`at`）と旧い値（`whole`・`selected`・`follow`）を文字として持つ場所は、改名表だけである。** `renames.py`・`settings/cli.py`・`spec_docs.py`・読む側は、旧いキーの名前を改名表から引き、コードに書かない。テストの入力は改名表の `old` と `values` のキーから組み立てる（設計文書の決定 13）。`git grep -w promotion -- plugins/ndf-next` は改名表と、移行のコマンド・止める検査のテストの説明の行だけに当たる。

承認ゲートの回答の値（`revise`）の置き場は `contracts/facts/answers.json` だけで、この変更では ID を変えない（決定 11）。変えるのはラベルの日本語の語だけである。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 禁止語の規則（`ndf check`）を複数のレーンが同時に打つ | 用語集のデータと ndf-next のファイル（読むだけ） | 取らない（読むだけで書かない） | 何度打っても同じ結果。途中で止めても何も残らない |
| 形の番号を上げた後に、複数のプロセスが同じチェックアウトの保存先を初めて開く | チェックアウトの保存先（`state/`）と `format.json` | 今の保存先のロック（`state.lock`）と保守のロック（`maint_lock`）。退避は排他の中で 1 回だけ行う（今の `_retire_store` の経路） | 退避の後は形の番号 2 の `format.json` があるため、打ち直しても退避を繰り返さない |
| 同じ PostgreSQL の接続先を、改名の前と後の ndf-next が同時に使う | 表 `ndf_progress_*` | 今の表の用意の排他（`PG_SCHEMA_KEY` の advisory lock） | 改名の後の ndf-next は版 1 の表を見て止まり、表を変えない。改名の前の ndf-next は今のまま動く |
| 用語集を変える Pull Request と、ndf-next を触る別のレーンの Pull Request が並ぶ | `docs/glossary/glossary.json` と ndf-next のファイル | Git のマージ（排他は取らない） | 廃止した語の登録が先にマージされると、別のレーンの旧い語の行を CI が落とす。そのレーンは置き換えてから打ち直す（設計文書の「未確認のまま残ること」の 3） |
| `ndf settings migrate --apply` と、同じリポジトリの `.ndf/` を書く今の NDF（MVV の承認）か人の編集 | `.ndf/worktree.json`・`.ndf/mvv.json` | 取らない。`apply` は書く直前に差分を作り直してハッシュを比べ、示した後の変更を書かない。作り直しから書き込みまでの間の変更は防がない（入れ替えはスプリントの境で、入れ替えを進める人が 1 人で打つ前提。設計文書の「入出力の契約」の機械が読む見出しの行と同じ前提） | 1 ファイルの中は一時ファイルの付け替えで、書き終えたか元のままのどちらか。書き終えた後に打ち直すと差分が 0 件で `ok` になる（`plan` の場合）。途中のファイルで止まったときの扱いは「異常系の処理」の `apply` の書けない行 |
| ndf-next の読む側（`worktree`・`records`）と `ndf settings migrate --apply` | `.ndf/` の設定ファイル | 取らない | 読む側は書き換えの前の中身を読めば止まり、後の中身を読めば進む。一時ファイルの付け替えのため、書きかけの中身を読まない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check` | 当たった語ごとに、ファイル・行・「「<語>」: <理由>」 | `ndf check` の結果 JSON の指摘（`banned-word`） | 行の本文（語と理由だけを出す。今と同じ） |
| `ensure_schema` | 版 1 の表で止まった理由と、パスワードを隠した接続先 | 結果 JSON の `stopped` の本文（例外の文） | 接続 URL のパスワード（`render_as_string(hide_password=True)`。今の版が新しいときと同じ） |
| `ensure_schema` | SQLite のバックアップが取れなかった理由 | ログの `warning` と結果 JSON の警告（今と同じ） | 無し（パスと例外の文だけ） |
| `plan` | ファイルごとの ID・相対パス・JSON Patch の操作の列・旧いキーの位置の数・インデントがそろうか | 結果 JSON の `items`（`kind: settings-file`）。差分のハッシュは `metrics.digest`、承認の後に打つコマンドは `next` | 設定の中身の全文（差分の操作の列だけを出す。MVV の本文のハッシュなど、改名と関わらない値を出さない） |
| `apply` | 書いたファイルの ID・相対パス・当てた操作の数 | 結果 JSON の `items` | 設定の中身の全文 |
| `apply` | ハッシュの不一致・スキーマの誤り | 結果 JSON の `stopped` の本文。スキーマの誤りは位置と文 | 渡されたハッシュの値の全文（先頭 12 桁だけを出し、示し直しを促す） |
| `ndf_settings.load`・`read_flow_record`・`decl_at`・`worktree_version`・`mvv_checked` | `legacy_detail` の文 | 呼び手のサブコマンドの結果 JSON の `stopped` の本文 | 設定の中身（キーの位置だけを出す） |
| `_real_errors` | 変換した後のスキーマの誤り | `ndf check` の spec-decl の指摘 | 旧いキーが残ること（spec-decl の指摘にしない。設計文書の決定 13） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_standalone_from` | 今の NDF の `glossary_words.standalone_part`・`word_pattern` | `grep -n "standalone" plugins/ndf/scripts/lib/glossary_words.py` | 使えない。ndf-next は今の NDF のモジュールを読まず（決定 3）、片側だけに条件を付ける形は「問い合わせ」に当たる（「処理の手順」） |
| `_standalone_from` | ndf-next の `_words_from` | `src/ndf/checks/words.py` を読む | 使えない。`_words_from` は禁止語の設定を受けて混ぜるため、`deprecated_standalone` だけを読む関数を分ける。語 → 理由を作る形は同じにする |
| `_standalone_from` | ndf-next の中の漢字の判定 | `git grep -n "4e00\|一-龥\|CJK" -- plugins/ndf-next/src` | 無い。漢字の範囲の定数は `words.py` に新しく置く |
| `renames.load` | ndf-next の契約の表の読み込み `ndf.contracts.facts.rows` | `grep -n "^def " plugins/ndf-next/src/ndf/contracts/facts.py`（`rows`・`row`・`value` がある） | 使う。表の形の検査（列の型・`optional`）とキャッシュも `facts` が持つ |
| `legacy_keys` | jsonpointer（LIB5 の依存） | 設計文書の LIB5 の行 | 使わない。依存が増え、`*` を広げる処理はどちらにしても自前で要る。位置の各階層（参照トークン）は `/` で割り、`~1`・`~0` の戻し（RFC 6901）だけを書く。改名表のキーに `/` と `~` が無いため、戻しは単体テストで 1 件だけ確かめる |
| `migrate` | jsonpatch（LIB5） | `https://pypi.org/pypi/jsonpatch/json`（設計文書の LIB5） | 使わない（設計文書の決定 12）。操作は `move` と `replace` の 2 つで、キーを元の並びの位置へ置く処理は jsonpatch の `move`（末尾に足す）では書けない |
| `migrate` | ndf-next の `worktree/ndf_settings.py` の `_merge`（深い併合） | `grep -n "def _merge" -A10 plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 使えない。2 つの値を重ねる関数で、キーの名前を変えない |
| `legacy_detail` | ndf-next の `ndf.domains.records.files.relpath` | `cat plugins/ndf-next/src/ndf/domains/records/files.py` | 使う。設定ファイルの相対パスを表 `ndf-files` と保存先の表から引く。パスを写さない |
| `legacy_detail` | ndf-next の `ndf.contracts.result.command`（そのまま打てるコマンドの組み立て） | `grep -n "def command" plugins/ndf-next/src/ndf/contracts/result.py` | 文の中のコマンドには使わない（文字の文で返すため）。`plan` の `next` の組み立てに使う |
| `plan` | ndf-next の `ndf.contracts.schema_errors`（スキーマの誤りの一覧） | `grep -n "^def " plugins/ndf-next/src/ndf/contracts/__init__.py` | 使わない。`plan` は差分を示すだけで、スキーマの確かめは書く前の `apply` が行う |
| `apply` | ndf-next の `ndf.contracts.schema_errors`（スキーマの誤りの一覧） | `grep -n "^def " plugins/ndf-next/src/ndf/contracts/__init__.py` | 使う。新しいスキーマ（`schemas/ndf-settings/<ID>.schema.json`）への確かめに使い、jsonschema を直接組まない |
| `plan` | ndf-next の `ndf.domains.records.jsonfile.read_object` | `grep -n "^def " plugins/ndf-next/src/ndf/domains/records/jsonfile.py` | 使う。JSON のオブジェクトとして読めないときの誤りの形を今の読む側とそろえる |
| `apply` | ndf-next の `ndf.domains.records.jsonfile.read_object` | 同上 | 使う（`plan` と同じ読み込みの関数 `_targets` を通す） |
| `apply` | ndf-next の `ndf.domains.worktree.atomic.write_text` | `cat plugins/ndf-next/src/ndf/domains/worktree/atomic.py` | 使う。一時ファイルへ書いてから付け替え、書きかけを残さない。`gws/material.py` もこの部品を読んでいる |
| `apply` | ndf-next の `ndf.core.plan.files.write_json` | `sed -n 1,30p plugins/ndf-next/src/ndf/core/plan/files.py` | 使わない。状態の保存先の記録の書き込みで、インデントを付けず、利用者の設定の形（インデント 2）に合わない |
| `main` | ndf-next の `ndf.cli` のサブコマンドの表と、使い方の誤りを結果 JSON にする処理 | `grep -n "COMMANDS" -A30 plugins/ndf-next/src/ndf/cli.py` | 使う。`COMMANDS` に 1 行足すだけで、argparse の終了コード 2 を `invalid` にする処理は `ndf.cli` が持つ |
| `main` | ndf-next の `ndf.contracts.result.stopped` と原因の種類の表 | `grep -n "def stopped" plugins/ndf-next/src/ndf/contracts/result.py`、`grep -n '"refused"\|"invalid"\|"precondition"' plugins/ndf-next/contracts/causes.json` | 使う。原因の種類と終了コードは `causes.json` と `exit-codes.json` が決め、新しい種類を足さない |
| （読む側の止める検査） | 承認ゲート `ndf.progress.gate.checks` が MVV の設定を読むか | `grep -rn "mvv" plugins/ndf-next/src/ndf/progress/gate/*.py`（出現は `mvv_decls`・`check_mvv_decl` だけで、設定のパスを引いて変えたファイルと比べる。中身を読まない） | 止める検査を足さない。承認ゲートは `ndf.domains` より下の層で、改名表を読む関数を読めない。中身を読まないため旧いキーで誤った判断をしない。変えるのは表を引く列と値の名前だけ |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `banned_words` | 用語集の設定と用語集があると、廃止した語・単独の語・禁止語の理由がそろい、単独の語が `standalone` にある | 用語集の設定が無いと両方とも空 | 用語集が JSON でないと「用語集を読めない」で止まる。`deprecated_standalone` が配列でないと型の誤りで止まる |
| `_words_from` | 「副命令」を `deprecated` に持つ項目から、「副命令」の理由に置き換え先の語が入る（受け入れ条件 8） | 同じ旧い語を 2 つの項目が持つと、先に出る項目の語が理由になる | — |
| `_standalone_from` | 「版」を `deprecated_standalone` に持つ項目から、「版」の理由が単独の出現の文になる | 欄を持たない項目しかないと空 | — |
| `pattern` | 「版」を単独の語に持つと、「版の記録」に当たる（受け入れ条件 9） | 「開発版」「出版」「版数」に当たらない。「問い合わせ」「枠組み」「目印」に当たらない。行の先頭と末尾の「版」に当たる | 単独の条件を外すと「開発版」に当たって落ちる |
| `_part` | 単独の語は両側に漢字でない条件が付く | ASCII の語を単独に持っても、単独の条件が先に当たる | — |
| `check` | Skill の本文の「副命令」の 1 行に指摘が 1 件ある（受け入れ条件 8） | `words.examples` に挙げたファイルの語は指摘にならない | 用語集の読み込みの失敗は `banned_words` の行と同じ |
| `ensure_schema` | 表が無い接続先で表を作り、版 2 を書く | 版 2 の表では何もしない | 版 1 の表を持つ接続先（PostgreSQL の代わりに `store.path` を `None` にした SQLite の接続で模す）で `StoreBroken` を送出し、表の版も列も変えない。バックアップが取れないと警告を残して続ける |
| `STORE_FORMAT` を使う保存先の用意 | 形の番号 2 の保存先はそのまま使う | 形の番号 1 の保存先は `<名前>.v1-<日付>` へ退避され、新しい保存先が作られる（`test_durable_store.py` の今のテストが番号 `STORE_FORMAT - 1` で確かめる） | — |
| `renames.load` | 4 行を表の並びで返し、親のキーの行が子の行より前にある。新しいキーの位置がどれも新しいスキーマにあり、旧いキーの位置がどれも無く、`old` と `new` が同じ行が無い（I9。スキーマの `properties` を位置の階層ごとにたどる。`*` は `items`） | `values` の無い行は空の対応を持つ | 表の形を壊した写し（`ndf.contracts.use_dir` で差し替えた置き場）で `FactsError` を送出する |
| `legacy_keys` | 旧いキーを持つ worktree の設定（改名表の `old` と `values` のキーから組み立てる）で 1 件、`versions` が 2 つで両方に `vet` を持つ未移行の MVV の設定で、要素ごとの親の位置（`/versions/0/vet`・`/versions/1/vet`）の 2 件を返す。`validation` の下に `verdict` と `at` が残る MVV の設定で、子の行の位置の 2 件を返す | 新しいキーだけの設定で 0 件。表に無い `file` で 0 件。`versions` が空の配列で 0 件。階層に `~1` を含む位置を戻せる | `versions` が配列でない設定で例外を出さずに 0 件 |
| `migrate` | 旧いキーの worktree の設定と MVV の設定が、要求の受け入れ条件 12 の値になり、新しいスキーマに通り、`legacy_keys` が 0 件。操作の列が `test`・`move`・`replace` だけで、`move` と `replace` の直前に元の位置の今の値を持つ `test` がある | 改名したキーがオブジェクトの中の元の並びの位置にある。表に無いキー（`revise_after`・`reviews`・`body`）と `values` に無い値は変わらない。入力の `data` が変わらない | 同じオブジェクトに旧いキーと新しいキーの両方があると、旧いキーを残して操作を足さない |
| `legacy_detail` | 旧いキーの残る設定で、ファイルの相対パス・すべての `old` → `new`・`ndf settings migrate --root <root>` を含む文を返す | 旧いキーの無い設定で `None` | — |
| `plan` | 旧いキーを持つ 2 つの設定のリポジトリで `status: gate`、ファイルごとの差分、64 桁のハッシュ、`--apply` 付きの `next` を返し、2 つのファイルのバイト列が変わらない | `.ndf/` の無いリポジトリと移行済みのリポジトリで `status: ok`。同じ中身を 2 回示すと同じハッシュ。インデント 4 の設定で「インデントがそろう」の行が出る | JSON でない設定で `invalid`（終了コード 2）、ほかのファイルの差分を出さない。読めないファイルで `invalid` |
| `apply` | `plan` が返したハッシュで打つと 2 つのファイルが受け入れ条件 12 の値になり、新しいスキーマに通る。もう一度 `plan` を打つと差分が 0 件（I10） | 書いたファイルがインデント 2・末尾の改行 1 つ。差分の無いファイルは書かない（更新時刻が変わらない） | `plan` の後に設定を変えてから打つと `refused`（終了コード 1）で、どのファイルも変わらない。変える値が移すだけの値（`vet` の `at`）でも同じ。差分が 0 件のときに打つと `refused`。改名と関わらない誤り（型の違う値）を持つ設定で `refused` になり、どのファイルも変わらない。2 つ目のファイルの書き込みを失敗させると `invalid` で、書き終えたファイルの一覧が本文にある |
| `main` | `migrate --root <dir>` で結果 JSON を 1 行出し、差分が無ければ終了コード 0、差分があれば 10（gate） | `--apply` の値が大文字の 16 進でも小文字にそろえない（`invalid`） | `migrate` の無い引数・`--root` の無い引数・63 桁の `--apply` で `invalid`（終了コード 2） |
| `ndf_settings.load` | 新しいキーの設定を読み、`branch_flow.release_strategy` が `release_branch` の設定で取り込み方の表の `release-branch` の行に当たる | `branch_flow` の無い設定は既定の行（今と同じ） | 旧いキーの残る共有設定で `NdfSettingsError` を送出し、文に旧いキーと新しいキーの位置と移行のコマンドがある。`ndf worktree` のサブコマンドが `precondition`（終了コード 3）で止まる（受け入れ条件 13） |
| `read_flow_record` | 新しいキーの記録を読む | `path` が `None` で `None` | 旧いキーの残る記録で `NdfSettingsError`。`ndf worktree merged` がブランチを 1 つも消さずに `precondition` で止まる |
| `decl_at` | 新しいキーの参照の設定を読む | — | 旧いキーの残る参照の設定で `MvvError`、`ndf records mvv signals` が `precondition` で止まる |
| `worktree_version` | 新しいキーの設定の現行の版を返す | — | 旧いキーの残る設定で `MvvError`、`ndf records mvv answer` が記録を書かずに `precondition` で止まる |
| `mvv_checked` | 承認済みの新しいキーの設定で `ok` | 設定が無いと `absent` で `ok`（今と同じ） | 旧いキーの残る設定で、本文が承認の記録と一致していても `precondition` で止まる |
| `_real_errors` | 旧いキーを持つ実物（改名表の `old` と `values` のキーから組み立てる。テストの中に旧いキーの名前を書かない）で誤りを返さない（設計文書の決定 13） | 新しいキーの実物で誤りを返さない（今と同じ） | 旧いキーと型の違う値を両方持つ実物で、型の誤りだけを返す |
