# #3613 の詳細設計: ndf-next の直訳調の語を業界の語へ置き換える

設計文書 [issue-3613-design.md](issue-3613-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語の対応（どの旧い語をどの語へ置き換えるか）は設計文書の「対応表」が持ち、ここでは写さない。

この文書が決めるのは次の 3 つである。

- 禁止語の規則（`plugins/ndf-next/src/ndf/checks/words.py`）が単独で照合する廃止した語を読むための、関数の形と失敗の扱い
- 識別子の改名で、どのファイル・モジュール・関数がどの名前になるか
- 状態のデータベースの列を改名したときに、旧い形の保存先と表をどう扱うか

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除。新設と削除の対は改名を表す）:

```text
docs/glossary/glossary.json
docs/glossary.md
plugins/ndf-next/
├── contracts/
│   ├── domains/
│   │   ├── ndf-files.json
│   │   ├── places.json            -
│   │   └── locations.json         *
│   ├── facts/answers.json
│   └── schemas/
│       ├── declarations/          -（19 ファイル）
│       └── ndf-settings/          *（同じ 19 ファイル）
├── docs/contracts/
│   ├── declarations.md            -
│   └── ndf-settings.md            *（ndf check --write-spec が作る）
├── src/ndf/
│   ├── identity.py
│   ├── checks/
│   │   ├── README.md
│   │   ├── rules.json
│   │   ├── words.py
│   │   ├── smells.py
│   │   ├── marks.py               -
│   │   └── suppressions.py        *
│   ├── core/
│   │   ├── durable.py
│   │   ├── durable_sweep.py
│   │   ├── durable_ledger.py      -
│   │   └── durable_runs.py        *
│   ├── progress/
│   │   ├── model.py
│   │   ├── run.py
│   │   └── store.py
│   └── domains/
│       ├── crossreview/drive.py
│       ├── records/usage.py
│       ├── records/mvv_decide.py
│       ├── relay/marks.py         -
│       ├── relay/markers.py       *
│       ├── release/declaration.py -
│       ├── release/ndf_settings.py *
│       ├── worktree/declaration.py -
│       ├── worktree/ndf_settings.py *
│       ├── worktree/ledger.py     -
│       └── worktree/allocations.py *
└── tests/
    ├── checks/test_words.py
    ├── checks/test_marks.py       -
    ├── checks/test_suppressions.py *
    └── core/test_durable_store.py
```

依存の向きは今のまま変えない。禁止語の規則は用語集のデータと規則のデータ（`rules.json`）を読むだけで、ほかのモジュールから読まれるのは規則のランナー（`checks/run.py`）からだけである。改名はモジュールの名前を変えるだけで、import の向きを足さない。新しく足す漢字の範囲の定数は `words.py` に置き、今の NDF（`plugins/ndf/`）のモジュールを読まない。循環は生じない。

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
| `plugins/ndf-next/src/ndf/identity.py` | 変更 | 固定の語の表のキー `declaration_dir` を `ndf_settings_dir` へ改める | 無し |
| `plugins/ndf-next/src/ndf/core/durable.py` | 変更 | チェックアウトの保存先の形の番号を 2 に上げ、`durable_runs` を読む | `plugins/ndf-next/src/ndf/core/durable_runs.py` |
| `plugins/ndf-next/src/ndf/core/durable_ledger.py` | 削除 | `durable_runs.py` へ移す | — |
| `plugins/ndf-next/src/ndf/core/durable_runs.py` | 新設 | PostgreSQL のときに起動した実行のキーを表 `ndf_runs` へ書き、片付けで読んで消す（`durable_ledger.py` の中身を改名して移す） | 無し |
| `plugins/ndf-next/src/ndf/core/durable_sweep.py` | 変更 | 実行の一覧を読む関数の名前を改める | `plugins/ndf-next/src/ndf/core/durable_runs.py` |
| `plugins/ndf-next/src/ndf/progress/store.py` | 変更 | 列 `table_id` を `machine_id` へ、表の版を 2 へ改め、版 1 の PostgreSQL の表を見つけたら止める | `plugins/ndf-next/src/ndf/core/durable.py`、`plugins/ndf-next/src/ndf/progress/model.py` |
| `plugins/ndf-next/src/ndf/progress/model.py` | 変更 | ステートマシンの ID を作る関数 `table_id` を `machine_id` へ改める | 無し |
| `plugins/ndf-next/src/ndf/progress/run.py` | 変更 | 承認ゲートの回答の値 `revise` を `request_changes` へ改める | `plugins/ndf-next/contracts/facts/answers.json` |
| `plugins/ndf-next/src/ndf/domains/relay/marks.py` | 削除 | `markers.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/relay/markers.py` | 新設 | 中継が子へ入力を送る前に立てるマーカーのファイルを書き、hook が消す（`marks.py` の中身を改名して移す） | 無し |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 削除 | `ndf_settings.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | 新設 | リリースの設定（`.ndf/release.json`）を読み、設定した手順を流す | `plugins/ndf-next/contracts/domains/ndf-files.json` |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 削除 | `ndf_settings.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/worktree/ndf_settings.py` | 新設 | worktree の運用の設定（`.ndf/worktree.json` と個人設定）を読む | `plugins/ndf-next/contracts/domains/ndf-files.json` |
| `plugins/ndf-next/src/ndf/domains/worktree/ledger.py` | 削除 | `allocations.py` へ移す | — |
| `plugins/ndf-next/src/ndf/domains/worktree/allocations.py` | 新設 | テスト環境の割り当て表（`worktree-registry.json`）を排他の中で読み書きする | 無し |
| `plugins/ndf-next/src/ndf/domains/records/usage.py` | 変更 | 使用量ログのパスを返す関数の名前を `usage_log_path` へ改める | 無し |
| `plugins/ndf-next/src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の改訂の兆候の欄 `revise` を `revision_due` へ改める | 無し |
| `plugins/ndf-next/src/ndf/domains/crossreview/drive.py` | 変更 | 指摘の記録の型 `FindingLedger` を `FindingJournal` へ改める | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | 列のキー `place` を `location` へ、`kind` の値 `declaration` を `ndf_settings` へ、`ledger` を含む ID を改める（`ask-ledger` を除く） | `plugins/ndf-next/contracts/domains/locations.json` |
| `plugins/ndf-next/contracts/domains/places.json` | 削除 | `locations.json` へ移す | — |
| `plugins/ndf-next/contracts/domains/locations.json` | 新設 | 保存先の表。ID `relay-marks` を `relay-markers`、パス `relay/marks` を `relay/markers` へ改める | 無し |
| `plugins/ndf-next/contracts/facts/answers.json` | 変更 | 承認ゲートの回答の ID `revise` を `request_changes`、ラベルを「指摘を添えて変更を要求する」へ改める | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/` | 削除 | `ndf-settings/` へ移す | — |
| `plugins/ndf-next/contracts/schemas/ndf-settings/` | 新設 | 設定のスキーマ 19 ファイル。受けるキーは変えない（I6） | 無し |
| `plugins/ndf-next/docs/contracts/declarations.md` | 削除 | `ndf check --write-spec` が `ndf-settings.md` を作り、旧い文書は残さない | — |
| `plugins/ndf-next/docs/contracts/ndf-settings.md` | 新設 | 設定のスキーマから作る契約の文書 | `plugins/ndf-next/contracts/schemas/ndf-settings/` |
| `plugins/ndf-next/tests/checks/test_words.py` | 変更 | 単独で照合する廃止した語の単体テストを足す | `plugins/ndf-next/src/ndf/checks/words.py` |
| `plugins/ndf-next/tests/checks/test_marks.py` | 削除 | `test_suppressions.py` へ移す | — |
| `plugins/ndf-next/tests/checks/test_suppressions.py` | 新設 | 抑制コメントのテスト（`test_marks.py` の中身を改名して移す） | `plugins/ndf-next/src/ndf/checks/suppressions.py` |
| `plugins/ndf-next/tests/core/test_durable_store.py` | 変更 | 形の番号 1 の保存先が退避されることを確かめる | `plugins/ndf-next/src/ndf/core/durable.py` |
| `plugins/ndf-next/src/` | 変更 | 上の行に無いモジュールの識別子・コメント・docstring・出力の文を、対応表どおりに置き換える | 無し |
| `plugins/ndf-next/tests/` | 変更 | 上の行に無いテストの識別子と期待値を、改名に合わせて置き換える。改名したモジュールのテストのファイル名も改める（「改名の一覧」） | 無し |
| `plugins/ndf-next/contracts/` | 変更 | 上の行に無い契約の JSON の説明と、hook が読んでよいモジュールの表（`contracts/hooks/imports.json`）のモジュール名を改める | 無し |
| `plugins/ndf-next/skills/` | 変更 | Skill の本文を対応表どおりに置き換える。`skills/worktree/references/declaration.md` は `ndf-settings.md` へ改名する | 無し |
| `plugins/ndf-next/agents/` | 変更 | エージェントの本文を対応表どおりに置き換える | 無し |
| `plugins/ndf-next/bin/` | 変更 | 起動のスクリプトのコメントと出力の文を置き換える | 無し |
| `plugins/ndf-next/hooks/` | 変更 | hook の設定の説明を置き換える | 無し |

### 改名の一覧

ファイル配置の表の「改名」の行と、ディレクトリの行に含めた改名の内訳である。設計文書の決定 6 の基準で改名するものだけを並べる。

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
| 承認ゲートの回答の値 `revise`・`REVISE` | `request_changes`・`REQUEST_CHANGES` | 契約の値・定数 |
| MVV の改訂の兆候の欄 `Signals.revise`・結果の `metrics.revise` | `revision_due` | 型の欄・結果のキー |
| テスト `test_durable_ledger.py`・`test_crossreview_ledger.py`・`test_declarations.py` | `test_durable_runs.py`・`test_crossreview_journal.py`・`test_ndf_settings.py` | テストのファイル |

**改名だけの関数は「関数の仕様」に書かない。** 引数の型・責務・失敗の扱いを変えず、名前と引数の名前だけを変えるためである（`detailed-design.md` の「書く範囲」）。呼び出し元は `git grep -w <旧い名前>` が 0 件になるまで直す（受け入れ条件 6）。

**改名しない名前**: `.ndf/mvv.json` のキー `revise_after`（利用者の設定。決定 4）、Factory のサービスが読む `ask-ledger`（I8）、表 `ndf_runs`（英語として通じる）、中継の関数 `mark`・`take`（動詞として通じる）。同等性の表（`src/ndf/parity/features.json`）が今の NDF のファイル名（`revise-note.py` など）を引いた箇所も、今の NDF の名前なので変えない（前提 5）。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/checks/words.py` | モジュール `words` | `BannedWords`（新設）、`banned_words`（変更）、`_words_from`（変更）、`_standalone_from`（新設）、`pattern`（変更）、`_part`（変更）、`check`（変更）、`_check_source`・`_examples`（変えない） |
| `plugins/ndf-next/src/ndf/checks/suppressions.py` | モジュール `suppressions` | `Suppression`・`read_suppressions`・`suppression_problem`・`Applied`・`apply`（すべて改名だけ） |
| `plugins/ndf-next/src/ndf/core/durable.py` | モジュール `durable` | 定数 `STORE_FORMAT` を 2 へ。関数は変えない |
| `plugins/ndf-next/src/ndf/core/durable_runs.py` | モジュール `durable_runs` | `RunRow`・`_runs`（改名だけ）、`_register_run`・`_forget_run`（変えない） |
| `plugins/ndf-next/src/ndf/progress/store.py` | モジュール `store` | `ensure_schema`（変更）、`read_machine`・`create_machine`・`read_events`・`read_definition`・`run_lock` ほか（改名と引数の名前だけ） |
| `plugins/ndf-next/src/ndf/progress/model.py` | モジュール `model` | `machine_id`（改名だけ） |
| `plugins/ndf-next/src/ndf/domains/relay/markers.py` | モジュール `markers` | `MarkerError`・`marker_path`（改名だけ）、`mark`・`take`（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/allocations.py` | クラス `Allocations` | `allocations_path`（改名だけ）、`Allocations` のメソッド（変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/ndf_settings.py` | クラス `NdfSettings` | `NdfSettingsError`（改名だけ）、`load`・`shared_file`・`local_file`（変えない） |
| `plugins/ndf-next/src/ndf/domains/release/ndf_settings.py` | クラス `NdfSettings` | `NdfSettingsError`（改名だけ）、`load`・`run_step`・`production_admin`（変えない） |
| `plugins/ndf-next/src/ndf/domains/records/mvv_decide.py` | クラス `Signals` | 欄 `revision_due`（改名だけ） |
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

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `banned_words` | 用語集の設定か用語集のデータが JSON として読めない | ファイルの破損・用語集の設定の `source` が無いパスを指す | `read_json`（今の `_read`） | 呼び出し元へ返す（今と同じ） | `ndf check` が「用語集を読めない」の指摘を出し、終了コード 1 | 後始末は無い。ファイルを直して打ち直せる |
| `banned_words` | 用語集の項目の `deprecated_standalone` が文字列の配列でない | 用語集の手での編集の誤り | `_standalone_from` の中で配列として読むとき | 呼び出し元へ返す。用語集の構造の検査（`glossary.py check` の `schema`）が同じ誤りを先に落とすため、ここで別の形の検査を持たない | `ndf check` が型の誤りで止まり、終了コード 1 | 後始末は無い。用語集を直して打ち直せる |
| `check` | 用語集の読み込みの失敗 | `banned_words` の失敗 | `banned_words` | 呼び出し元へ返す（規則のランナーが規則ごとの失敗を指摘にする今の扱い） | 上の `banned_words` の行と同じ | 上の `banned_words` の行と同じ |
| `ensure_schema` | 表の版が 1 の PostgreSQL の表がある | 列を改名する前の ndf-next が作った表を、同じ接続先で使い続けた | `_read_version` の戻り値が `SCHEMA_VERSION` より小さく、`store.path` が `None` | 止める（`durable.StoreBroken` を送出する）。表は書き換えない | 結果 JSON の `status` が `stopped`、理由の語は `store_broken`。本文に「進行表の表の版 1 は列の名前が古い。表 `ndf_progress_*` を消して作り直す」と、パスワードを隠した接続先を出す。終了コードは今の `stopped` と同じ | DB は変えていない。利用者が表を消せば打ち直せる |
| `ensure_schema` | SQLite で版を上げる前のバックアップが取れない | 保存先が書き込めない・容量が無い | `_backup_or_warn`（今と同じ） | 警告を残して続ける（今と同じ） | 結果 JSON の警告に理由が載る。終了コードは変わらない | 今と同じ |

**SQLite の保存先では、版 1 の表は `ensure_schema` まで届かない。** チェックアウトの保存先の形の番号を 2 に上げたため、`durable` が版 1 の保存先を `<名前>.v1-<日付>` へ退避し、空の保存先で表を作り直す（今の `_retire_store` の経路）。`ensure_schema` が版 1 を見るのは PostgreSQL の接続先だけである。

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

**単独の語の条件を、語の先頭か末尾が漢字かに依らず両側に付ける。** 今の NDF の用語の検査（`glossary_words.standalone_part`）は、先頭が漢字なら前に、末尾が漢字なら後ろにだけ条件を付ける。ndf-next の対応表の「問い」は末尾がひらがなで、今の NDF の形では「問い合わせ」に当たる。対応表が「問い合わせ」を外すと決めたため（W12）、両側に付ける。両側に付けても「版の記録」「段を足す」のように前後が漢字でない出現には当たる。

- 繰り返しの終わる条件: `check` はプラグインの下の文字のファイルを 1 回ずつ読み、各ファイルの行を 1 回ずつ見る（今と同じ）
- 計算量の注意: 語の数は、廃止した語の登録で今の数十から数百へ増える。正規表現は語を `|` で並べた 1 つにまとめ、ファイルの行ごとに 1 回だけ当てる（今と同じ）。語ごとにファイルを読み直さない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `BannedWords` | 型（凍結した dataclass。`reasons: dict[str, str]`・`standalone: frozenset[str]`） | `plugins/ndf-next/src/ndf/checks/words.py` | 用語集の設定が無いときは両方とも空 |
| `KANJI` | 定数（正規表現の文字クラスの中身） | `plugins/ndf-next/src/ndf/checks/words.py` | `"㐀-䶿一-鿿豈-﫿々"`（今の NDF の `glossary_words.KANJI` と同じ字の範囲。ndf-next は今の NDF のモジュールを読まないため、ndf-next の中ではここだけに持つ） |
| 単独の語の理由の文 | 定数（文の形） | `plugins/ndf-next/src/ndf/checks/words.py` | `廃止した語（単独の出現）。「<置き換え先の語>」と書く` |
| `STORE_FORMAT` | 定数 | `plugins/ndf-next/src/ndf/core/durable.py` | `2`（今は 1） |
| `SCHEMA_VERSION` | 定数 | `plugins/ndf-next/src/ndf/progress/store.py` | `2`（今は 1） |
| `FIXED_WORDS["ndf_settings_dir"]` | 定数の表のキー | `plugins/ndf-next/src/ndf/identity.py` | `".ndf"`（値は変えない） |
| `words.examples` の足す行 | 設定値 | `plugins/ndf-next/src/ndf/checks/rules.json` | 単独で照合する廃止した語を例として挙げるファイル（`skills/requirements-design/references/glossary-format.md` の「字で持」など）と、同等性の表（`src/ndf/parity/features.json`）。行ごとにファイル・語・理由を書く。件数は実装の時点の禁止語の規則の出力で決める |
| 兆候の表のキー `suppress` | 設定値のキー | `plugins/ndf-next/src/ndf/checks/rules.json` | 値（`boundary`・`none` など）は変えない |

承認ゲートの回答の値（`request_changes`）の置き場は `contracts/facts/answers.json` だけである。`progress/run.py` の定数は、そこから引く今の形（`_answer("...")`）を保つ。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 禁止語の規則（`ndf check`）を複数のレーンが同時に打つ | 用語集のデータと ndf-next のファイル（読むだけ） | 取らない（読むだけで書かない） | 何度打っても同じ結果。途中で止めても何も残らない |
| 形の番号を上げた後に、複数のプロセスが同じチェックアウトの保存先を初めて開く | チェックアウトの保存先（`state/`）と `format.json` | 今の保存先のロック（`state.lock`）と保守のロック（`maint_lock`）。退避は排他の中で 1 回だけ行う（今の `_retire_store` の経路） | 退避の後は形の番号 2 の `format.json` があるため、打ち直しても退避を繰り返さない |
| 同じ PostgreSQL の接続先を、改名の前と後の ndf-next が同時に使う | 表 `ndf_progress_*` | 今の表の用意の排他（`PG_SCHEMA_KEY` の advisory lock） | 改名の後の ndf-next は版 1 の表を見て止まり、表を変えない。改名の前の ndf-next は今のまま動く |
| 用語集を変える Pull Request と、ndf-next を触る別のレーンの Pull Request が並ぶ | `docs/glossary/glossary.json` と ndf-next のファイル | Git のマージ（排他は取らない） | 廃止した語の登録が先にマージされると、別のレーンの旧い語の行を CI が落とす。そのレーンは置き換えてから打ち直す（設計文書の「未確認のまま残ること」の 3） |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check` | 当たった語ごとに、ファイル・行・「「<語>」: <理由>」 | `ndf check` の結果 JSON の指摘（`banned-word`） | 行の本文（語と理由だけを出す。今と同じ） |
| `ensure_schema` | 版 1 の表で止まった理由と、パスワードを隠した接続先 | 結果 JSON の `stopped` の本文（例外の文） | 接続 URL のパスワード（`render_as_string(hide_password=True)`。今の版が新しいときと同じ） |
| `ensure_schema` | SQLite のバックアップが取れなかった理由 | ログの `warning` と結果 JSON の警告（今と同じ） | 無し（パスと例外の文だけ） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_standalone_from` | 今の NDF の `glossary_words.standalone_part`・`word_pattern` | `grep -n "standalone" plugins/ndf/scripts/lib/glossary_words.py` | 使えない。ndf-next は今の NDF のモジュールを読まず（決定 3）、片側だけに条件を付ける形は「問い合わせ」に当たる（「処理の手順」） |
| `_standalone_from` | ndf-next の `_words_from` | `src/ndf/checks/words.py` を読む | 使えない。`_words_from` は禁止語の設定を受けて混ぜるため、`deprecated_standalone` だけを読む関数を分ける。語 → 理由を作る形は同じにする |
| `_standalone_from` | ndf-next の中の漢字の判定 | `git grep -n "4e00\|一-龥\|CJK" -- plugins/ndf-next/src` | 無い。漢字の範囲の定数は `words.py` に新しく置く |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `banned_words` | 用語集の設定と用語集があると、廃止した語・単独の語・禁止語の理由がそろい、単独の語が `standalone` にある | 用語集の設定が無いと両方とも空 | 用語集が JSON でないと「用語集を読めない」で止まる |
| `_words_from` | 「副命令」を `deprecated` に持つ項目から、「副命令」の理由に置き換え先の語が入る（受け入れ条件 8） | 同じ旧い語を 2 つの項目が持つと、先に出る項目の語が理由になる | — |
| `_standalone_from` | 「版」を `deprecated_standalone` に持つ項目から、「版」の理由が単独の出現の文になる | 欄を持たない項目しかないと空 | — |
| `pattern` | 「版」を単独の語に持つと、「版の記録」に当たる（受け入れ条件 9） | 「開発版」「出版」「版数」に当たらない。「問い合わせ」「枠組み」「目印」に当たらない。行の先頭と末尾の「版」に当たる | 単独の条件を外すと「開発版」に当たって落ちる |
| `_part` | 単独の語は両側に漢字でない条件が付く | ASCII の語を単独に持っても、単独の条件が先に当たる | — |
| `check` | Skill の本文の「副命令」の 1 行に指摘が 1 件ある（受け入れ条件 8） | `words.examples` に挙げたファイルの単独の語は指摘にならない | 用語集の読み込みの失敗は `banned_words` の行と同じ |
| `ensure_schema` | 表が無い接続先で表を作り、版 2 を書く | 版 2 の表では何もしない | 版 1 の表を持つ接続先（PostgreSQL の代わりに `store.path` を `None` にした SQLite の接続で模す）で `StoreBroken` を送出し、表の版も列も変えない |
| `STORE_FORMAT` を使う保存先の用意 | 形の番号 2 の保存先はそのまま使う | 形の番号 1 の保存先は `<名前>.v1-<日付>` へ退避され、新しい保存先が作られる（`test_durable_store.py` の今のテストが番号 `STORE_FORMAT - 1` で確かめる） | — |
