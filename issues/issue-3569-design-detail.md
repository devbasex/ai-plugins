# #3569 の詳細設計: 閉じた子の 10 本の機能を `ndf` の副命令で呼ぶ

設計文書 [issues/issue-3569-design.md](issue-3569-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。次のものは [issues/issue-3569-design-commands.md](issue-3569-design-commands.md) にあり、ここでは写さない。

- 旧い副命令ごとの行き先
- 新しい副命令の引数・結果・終了コードの契約

この文書のパスは、断りが無ければ `plugins/ndf-next/` からの相対パスである。旧いプラグインのパスは `plugins/ndf/` から書く。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 移す | 旧いプラグインの関数の振る舞い（入力・判定・書くファイルの置き場と形）を変えずに、新しいプラグインのモジュールへ書き直すこと。旧いプラグインのファイルは変えない |
| 公開の関数 | モジュールの外から呼ばれる関数。「関数の仕様」に書くのは公開の関数だけである |
| 非公開の関数 | 公開の関数だけが呼ぶ関数。旧い実装の非公開の関数（名前が `_` で始まるもの）を移したもので、名前は「クラスと関数の構造」に置く |
| 旧い置き場 | 旧いプラグインが状態を書く置き場。入れ替えの前は、旧いプラグインと新しいプラグインの両方が同じ旧い置き場を読み書きする（[issue-3569-design-commands.md の「旧い実装と並ぶ間の状態の書き手」](issue-3569-design-commands.md#旧い実装と並ぶ間の状態の書き手) ） |
| 結果 JSON | 副命令が標準出力の最後の 1 行に出す、`tool`・`status`・`summary`・`items`・`metrics` を持つ JSON（`ndf.contracts.result`） |

**関数の粒度について。** 移す旧い実装は、旧い 6 本のスクリプトと、それが読む部品を合わせて 6,000 行を超える（2026-11-15 に `wc -l` で数えた）。非公開の関数をすべて表へ並べると、この文書が 1,000 行に収まらない。そのため、責務・失敗の扱い・副作用が決まる公開の関数だけを「関数の仕様」以降の表に書き、非公開の関数は旧い関数の名前と行き先だけを「クラスと関数の構造」に置く。非公開の関数は旧い関数と同じ責務と失敗の扱いを持つ。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── domains/env.json
│   ├── limits.json
│   └── schemas/io/
│       ├── project.result.schema.json *
│       ├── statusline.result.schema.json *
│       ├── materials.result.schema.json *
│       ├── relay.result.schema.json
│       └── <副命令>.args.schema.json（生成）
├── docs/contracts/{commands.md,env.md}（生成）
├── src/ndf/
│   ├── cli.py
│   ├── checks/children.json
│   ├── parity/{features.json,trial/<日付>.json *}
│   └── domains/
│       ├── project/ *
│       │   ├── __init__.py
│       │   ├── cli.py
│       │   ├── check.py
│       │   ├── measure_repo.py
│       │   ├── measure_ci.py
│       │   ├── measure_flow.py
│       │   └── write.py
│       ├── statusline/ *
│       │   ├── __init__.py
│       │   ├── cli.py
│       │   ├── settings.py
│       │   └── statusline.sh
│       ├── materials/ *
│       │   ├── __init__.py
│       │   ├── cli.py
│       │   └── collect.py
│       └── relay/
│           ├── cli.py
│           ├── registry.py
│           ├── notice.py *
│           ├── account_registry.py *
│           ├── account_login.py *
│           ├── account_metered.py *
│           ├── context_policy.py *
│           ├── context_records.py *
│           └── context_report.py *
└── tests/domains/
    ├── project/{test_project_cli.py,test_project_measure.py} *
    ├── statusline/test_statusline_cli.py *
    ├── materials/test_materials_cli.py *
    └── relay/{test_notice.py,test_account_registry.py,test_account_metered.py,test_context_policy.py,test_context_report.py} *
```

**依存の向きは一方向で、循環は無い。** 依存は次の順に一方向へ進む。

1. 副命令の表（`cli.py`）
2. 各パッケージの `cli.py`
3. 判定と測定のモジュール
4. 既存の部品（`ndf.contracts`・`ndf.adapters`・`ndf.domains.records`・`ndf.domains.worktree.declaration`）

3 つの新しいパッケージ（`project`・`statusline`・`materials`）は互いに読まない。`relay` の中の読みは次のとおりである。

- `cli.py` は `notice`・`account_*`・`context_*` を読む
- `account_login` と `account_metered` は `account_registry` を読む
- `context_report` と `context_policy` は `context_records` を読む

逆向きの読みと、`account_*` と `context_*` の間の読みは無いため、循環は無い。新しいモジュールは旧いプラグインのモジュールを import しない（旧いプラグインのファイルの置き場を知らない）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | 副命令の表に `project`・`statusline`・`materials` の 3 行を足す | 無し |
| `plugins/ndf-next/src/ndf/domains/project/__init__.py` | 新設 | パッケージの説明（旧い `project-decl.py` の移し先であること）だけを持つ | 無し |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | 新設 | `ndf project` の 5 つの下位の命令の引数を読み、判定・測定・書き出しを呼んで結果 JSON を出す | `project.check`・`project.measure_*`・`project.write`・`ndf.contracts` |
| `plugins/ndf-next/src/ndf/domains/project/check.py` | 新設 | 宣言の新しさの判定と、判定の入力の指紋を持つ（旧い `check` と `project_lib/fingerprint.py`） | `ndf.domains.records.files`・`ndf.domains.records.jsonfile`・`ndf.adapters.git` |
| `plugins/ndf-next/src/ndf/domains/project/measure_repo.py` | 新設 | リポジトリの言語・テスト・サービス・リリース・指示書・素材を測る（旧い `project_lib/measure_repo.py`・`measure_materials.py`） | `ndf.adapters.git` |
| `plugins/ndf-next/src/ndf/domains/project/measure_ci.py` | 新設 | CI の定義とその実行の時間、課題の置き場を測る（旧い `project_lib/measure_ci.py`・`ci_tests.py`） | `ndf.adapters.github` |
| `plugins/ndf-next/src/ndf/domains/project/measure_flow.py` | 新設 | ブランチの使い方を測る（旧い `project_lib/measure_branch_flow.py`） | `ndf.adapters.git`・`ndf.domains.worktree.declaration` |
| `plugins/ndf-next/src/ndf/domains/project/write.py` | 新設 | 測定の結果と答えを宣言のキーへ取り込み、書き出しとずれの比較を行う（旧い `project_lib/merge.py`・`compose.py`・`secret.py`） | `ndf.domains.records.files`・`ndf.domains.records.jsonfile`・`ndf.domains.worktree.declaration` |
| `plugins/ndf-next/src/ndf/domains/statusline/__init__.py` | 新設 | パッケージの説明だけを持つ | 無し |
| `plugins/ndf-next/src/ndf/domains/statusline/cli.py` | 新設 | `ndf statusline` の 4 つの下位の命令を受け、設定の操作を呼んで結果 JSON を出す | `statusline.settings`・`ndf.contracts` |
| `plugins/ndf-next/src/ndf/domains/statusline/settings.py` | 新設 | `settings.json` の `statusLine` と控え・表示の本体の置き換えを、排他の中で行う（旧い `statusline-switch.sh`） | `ndf.domains.relay.signals`（`claude_config_root`・`replace_text`） |
| `plugins/ndf-next/src/ndf/domains/statusline/statusline.sh` | 新設 | 表示の本体。旧い `scripts/statusline.sh` と同じバイト列で、`set`・`ensure` が置く元になる | 無し |
| `plugins/ndf-next/src/ndf/domains/materials/__init__.py` | 新設 | パッケージの説明だけを持つ | 無し |
| `plugins/ndf-next/src/ndf/domains/materials/cli.py` | 新設 | `ndf materials` の引数を受け、材料を集めて結果 JSON と下書きを出す | `materials.collect`・`ndf.contracts` |
| `plugins/ndf-next/src/ndf/domains/materials/collect.py` | 新設 | 課題の本文・ファイルの一覧・設計文書からモードの判定の材料を組む（旧い `mode-materials.py`） | `ndf.adapters.github`・`ndf.adapters.git`・`ndf.adapters.markdown` |
| `plugins/ndf-next/src/ndf/domains/relay/cli.py` | 変更 | `notice`・`stop --all`・`account` の 9 つ・`context` の 4 つの引数とハンドラを足す | `relay.notice`・`relay.account_*`・`relay.context_*`・既存の `relay.hub`・`relay.registry` |
| `plugins/ndf-next/src/ndf/domains/relay/registry.py` | 変更 | 旧い状態の根を求める処理を `legacy_state_root` に切り出し、`legacy_root` と `context_records` が共有する | 無し |
| `plugins/ndf-next/src/ndf/domains/relay/notice.py` | 新設 | 呼び手の claude がラッパーの直接の子かを判定し、カットポイントのアナウンスの 1 文を組む（旧い `relay_lib/mark.py` の `notice_lines`・`proc.py`） | `relay.registry`・`relay.signals`・psutil |
| `plugins/ndf-next/src/ndf/domains/relay/account_registry.py` | 新設 | 旧い置き場のアカウントの登録の読み書き・排他・一覧・枠・削除を持つ（旧い `lib/claude_accounts.py`・`lib/claude_account_dir.py` の登録の部分） | filelock |
| `plugins/ndf-next/src/ndf/domains/relay/account_login.py` | 新設 | アカウントごとの設定ディレクトリで OAuth のログインを動かし、登録する（旧い `relay_lib/login.py`） | `relay.account_registry` |
| `plugins/ndf-next/src/ndf/domains/relay/account_metered.py` | 新設 | 従量の接続（Bedrock・API キー）の宣言・確かめ・月の額・使い方を持つ（旧い `relay_lib/bedrock.py`・`api_keys.py`・`lib/claude_api_keys.py`） | `relay.account_registry`・`ndf.adapters.http` |
| `plugins/ndf-next/src/ndf/domains/relay/context_policy.py` | 新設 | 再起動の方針の設定（`.ndf/context.json`）から適用値を解決する（旧い `lib/context_policy.py`・`legacy_values.py`） | `ndf.domains.records.files`・`ndf.domains.records.jsonfile` |
| `plugins/ndf-next/src/ndf/domains/relay/context_records.py` | 新設 | 旧い置き場の判定の記録の追記・読み取りと、見込みの較正を持つ（旧い `lib/context_decisions.py`・`context_calibrate.py`） | `relay.registry`・`relay.signals` |
| `plugins/ndf-next/src/ndf/domains/relay/context_report.py` | 新設 | 判定の記録と使用量の台帳から作業ごとの量を集計する（旧い `context-policy.py` の `report_work` まわり） | `relay.context_records`・`ndf.domains.records.usage` |
| `plugins/ndf-next/contracts/domains/env.json` | 変更 | 新しく読む環境変数（`NDF_ACCOUNTS_DIR`・`NDF_SHARED_CONFIG_DIR`・`NDF_CONTEXT_DIR`・`NDF_ACCOUNT_CHECK_INTERVAL`）の行を足す | 無し |
| `plugins/ndf-next/contracts/limits.json` | 変更 | 測定の締め切り・アカウントと statusline の排他の待ち・外部コマンドの上限の行を足す | 無し |
| `plugins/ndf-next/contracts/schemas/io/project.result.schema.json` | 新設 | `ndf project` の結果の `items`・`metrics` の形 | `result.schema.json` |
| `plugins/ndf-next/contracts/schemas/io/statusline.result.schema.json` | 新設 | `ndf statusline` の結果の形 | `result.schema.json` |
| `plugins/ndf-next/contracts/schemas/io/materials.result.schema.json` | 新設 | `ndf materials` の結果の形 | `result.schema.json` |
| `plugins/ndf-next/contracts/schemas/io/relay.result.schema.json` | 変更 | 足した下位の命令の `items`・`metrics` の形を足す | `result.schema.json` |
| `plugins/ndf-next/src/ndf/parity/features.json` | 変更 | 10 行の `to`・`not_built` と 7 行を直す（[issue-3569-design-commands.md の「機能の対応表の変更」](issue-3569-design-commands.md#機能の対応表の変更) ） | 無し |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | S1e の行を足し、T2 の `needs` に S1e を足す（設計文書の決定 6） | 無し |
| `plugins/ndf-next/tests/domains/project/test_project_cli.py` | 新設 | `ndf project` の判定・書き出し・ずれ・スキーマの単体テスト | `project` |
| `plugins/ndf-next/tests/domains/project/test_project_measure.py` | 新設 | 測定の 3 モジュールの単体テスト | `project` |
| `plugins/ndf-next/tests/domains/statusline/test_statusline_cli.py` | 新設 | `ndf statusline` の単体テスト | `statusline` |
| `plugins/ndf-next/tests/domains/materials/test_materials_cli.py` | 新設 | `ndf materials` の単体テスト | `materials` |
| `plugins/ndf-next/tests/domains/relay/test_notice.py` | 新設 | アナウンスと `stop --all` の単体テスト | `relay` |
| `plugins/ndf-next/tests/domains/relay/test_account_registry.py` | 新設 | 登録・一覧・枠・削除・OAuth の登録の単体テスト | `relay` |
| `plugins/ndf-next/tests/domains/relay/test_account_metered.py` | 新設 | 従量の接続の単体テスト | `relay` |
| `plugins/ndf-next/tests/domains/relay/test_context_policy.py` | 新設 | 方針の解決・記録・較正の単体テスト | `relay` |
| `plugins/ndf-next/tests/domains/relay/test_context_report.py` | 新設 | 作業ごとの集計の単体テスト | `relay` |

次のファイルは、関数を持たないためこの表に並べない。どれも設計文書の「設計の結果」の触るファイルにある。

- 生成のファイル（`<副命令>.args.schema.json`・`docs/contracts/commands.md`・`docs/contracts/env.md`）
- 試走の記録（`src/ndf/parity/trial/<日付>.json`）
- 本文の 49 ファイル

## クラスと関数の構造

型の関係は設計文書の「構造」の節を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/cli.py` | モジュール | `COMMANDS`（変更。3 行を足す） |
| `src/ndf/domains/project/cli.py` | モジュール | `main`（新設）。内側: `_parser`・`_check`・`_measure`・`_write`・`_drift`・`_schema`（旧い `cmd_*` と `build_parser`） |
| `src/ndf/domains/project/check.py` | モジュール・`Judged`（NamedTuple） | `judge`・`fingerprint`（新設）。内側: `_load_decl`・`_read_decl_json`・`_declared_flow`（旧い同名の関数） |
| `src/ndf/domains/project/measure_repo.py` | モジュール・`Measured`（NamedTuple） | `measure`（新設）。内側: 旧い `measure_repo.py` の項目ごとの関数と `measure_materials` |
| `src/ndf/domains/project/measure_ci.py` | モジュール | `measure`（新設）。内側: 旧い `measure_ci.py` の関数と `ci_tests.py` の関数 |
| `src/ndf/domains/project/measure_flow.py` | モジュール | `measure`（新設）。内側: 旧い `measure_branch_flow.py` の関数 |
| `src/ndf/domains/project/write.py` | モジュール・`Row`・`Written`・`Drift`（NamedTuple） | `plan_writes`・`apply`・`drift`（新設）。内側: `_merge_worktree_part`・`_declare_ci_tests`・`_same_but_time`・`_gitignore_note`・`mask`（旧い同名の関数と `secret.mask`） |
| `src/ndf/domains/statusline/cli.py` | モジュール | `main`（新設） |
| `src/ndf/domains/statusline/settings.py` | モジュール・`Paths`・`Switched`（NamedTuple） | `paths`・`locked`・`ensure`・`status`・`set_ndf`・`restore`（新設）。内側: `_read_settings`・`_write_settings`・`_place_body` |
| `src/ndf/domains/materials/cli.py` | モジュール | `main`（新設） |
| `src/ndf/domains/materials/collect.py` | モジュール・`Gathered`（NamedTuple） | `issue_materials`・`gather`・`draft`・`write_draft`（新設）。内側: `raw_candidates`・`exists_in`・`looks_new`・`is_test`・`tests_of`・`issue_kind`・`public_interface`・`design_files`（旧い同名の関数） |
| `src/ndf/domains/relay/cli.py` | モジュール | `main`・`_parser`・`HANDLERS`（変更）。`_stop_all`・`_notice`・`_account_registry`・`_context`（新設） |
| `src/ndf/domains/relay/registry.py` | モジュール | `legacy_state_root`（新設）。`legacy_root`（変更。中で `legacy_state_root` を呼ぶ。シグネチャ・責務・失敗の扱いは変えない） |
| `src/ndf/domains/relay/notice.py` | モジュール・`Position`・`Notice`（NamedTuple） | `position`・`lines`（新設）。内側: `_first_claude_ancestor` |
| `src/ndf/domains/relay/account_registry.py` | モジュール・`Account`（dataclass） | `store_dir`・`account_dir`・`locked`・`load`・`save`・`list_rows`・`set_capacity`・`remove`（新設）。内側: 旧い `claude_accounts.py` の `_read`・`_write`・`_tighten`・`_secure`・`make_store`・`_sweep_old_locks` と `claude_account_dir.py` の設定ディレクトリの用意 |
| `src/ndf/domains/relay/account_login.py` | モジュール・`Added`（NamedTuple） | `add`（新設）。内側: 旧い `login.py` の `_auth`・`_identity`・`check_name`・`start_login`・`_wait_exit`・`_register` |
| `src/ndf/domains/relay/account_metered.py` | モジュール・`Target`（NamedTuple） | `add_bedrock`・`add_api_key`・`set_monthly`・`set_use`・`check`（新設）。内側: 旧い `bedrock.py` の `_aws`・`profiles`・`region`・`models`・`classify_failure`・`verify` と `api_keys.py` の `_read_key`・`_verify`・`_amount`・`_weekly_at` |
| `src/ndf/domains/relay/context_policy.py` | モジュール・`Resolved`（NamedTuple） | `resolve`（新設）。内側: 旧い `context_policy.py` の値の解決と `legacy_values.py` の読み替え |
| `src/ndf/domains/relay/context_records.py` | モジュール | `context_dir`・`append`・`read`・`calibrate`（新設）。内側: 旧い `context_decisions.py` の行の組み立てと `context_calibrate.py` の較正の計算 |
| `src/ndf/domains/relay/context_report.py` | モジュール | `report`・`load_ledger`（新設）。内側: 旧い `context-policy.py` の `work_windows`・`clip_windows`・`session_parts`・`_sum_sessions`・`_restarts_by_reason`・`_rework`・`work_scope` |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `project.cli.main` | `src/ndf/domains/project/cli.py` | 新設 | `main(argv: list[str]) -> int` | 下位の命令を振り分け、結果 JSON を 1 行出して終了コードを返す | 副命令の表が状態の置き場を用意した | 標準出力の最後の行が結果 JSON で、終了コードは契約の表の値である | 標準出力・標準エラー |
| `check.judge` | `src/ndf/domains/project/check.py` | 新設 | `judge(main_dir: Path, force: bool) -> Judged` | 宣言が新しい・無い・古い・壊れているのどれかを決める | `main_dir` はメインディレクトリ | `Judged.kind` が `fresh`・`missing`・`stale`・`broken` のどれかで、理由を持つ | `.ndf/project.json` と指紋の入力のファイルを読む |
| `check.fingerprint` | `src/ndf/domains/project/check.py` | 新設 | `fingerprint(main_dir: Path) -> str` | 解析器の版と、判定の入力のファイルの中身から指紋を作る | 同上 | 同じ入力と同じ版なら同じ文字列である | ファイルを読む・`git ls-files` |
| `measure_repo.measure` | `src/ndf/domains/project/measure_repo.py` | 新設 | `measure(root: Path, deadline: float) -> dict[str, Measured]` | 宣言の項目のうちリポジトリの中だけで測れるものを測る | `deadline` は単調時計の秒 | 項目ごとに値か `None`（不明）と出所を持つ。締め切りを過ぎた項目は `None` | ファイルを読む・`git` |
| `measure_ci.measure` | `src/ndf/domains/project/measure_ci.py` | 新設 | `measure(root: Path, deadline: float, repo: str \| None) -> dict[str, Measured]` | CI の定義・実行の時間・課題の置き場を測る | 同上 | 同上。GitHub の名前が分からなければ CI の実行の時間は `None` | ファイルを読む・GitHub の API |
| `measure_flow.measure` | `src/ndf/domains/project/measure_flow.py` | 新設 | `measure(root: Path, deadline: float) -> Measured` | ブランチの使い方（起点・本番・流れ）を測る | 同上 | ブランチの流れの値か `None` を持つ | `git` |
| `write.plan_writes` | `src/ndf/domains/project/write.py` | 新設 | `plan_writes(main_dir: Path, measured: Mapping[str, Measured], answers: Mapping[str, Any]) -> list[Row]` | 測定と答えから、キーごとに書く・保つ・食い違い・不明・秘密の形のどれかを決める | 測定の結果の形が `Measured` である | 人が書いた値を書き換える行が無い。秘密の形の値は伏せてある | 既にある宣言を読む |
| `write.apply` | `src/ndf/domains/project/write.py` | 新設 | `apply(main_dir: Path, rows: list[Row], dry_run: bool) -> Written` | 書く行を宣言（`.ndf/project.json`・`.ndf/worktree.json`）へ書く | `rows` は `plan_writes` の戻り値 | `dry_run` なら何も書かない。そうでなければ宣言が一時ファイルからの置き換えで書かれている | ファイルを書く |
| `write.drift` | `src/ndf/domains/project/write.py` | 新設 | `drift(main_dir: Path, measured: Mapping[str, Measured]) -> list[Drift]` | 宣言のブランチの運用と、測った使い方のずれを並べる | 同上 | ずれた項目ごとに宣言の値と測った値を持つ | 既にある宣言を読む |
| `statusline.cli.main` | `src/ndf/domains/statusline/cli.py` | 新設 | `main(argv: list[str]) -> int` | 下位の命令を振り分け、結果 JSON を出す | 同上 | 同上 | 標準出力 |
| `settings.paths` | `src/ndf/domains/statusline/settings.py` | 新設 | `paths(environ: Mapping[str, str]) -> Paths` | `settings.json`（symlink なら参照先）・控え・排他・表示の本体の置き場を求める | 無し | 4 つのパスを持つ。控えと排他は `settings.json` の実体の隣にある | symlink を読む |
| `settings.locked` | `src/ndf/domains/statusline/settings.py` | 新設 | `locked(p: Paths, wait: float) -> ContextManager[None]` | 旧い実装と同じ排他のファイルに排他のロックを取る | 排他のファイルの親がある | 中の処理の間、旧い `flock` と新しい書き手のどちらも同時に書かない | 排他のファイルを作る |
| `settings.ensure` | `src/ndf/domains/statusline/settings.py` | 新設 | `ensure(p: Paths) -> Switched` | `statusLine` が無ければ NDF の表示を設定し、NDF の表示なら本体だけを今の版に置き直す | 排他の中で呼ぶ | `statusLine` が他の設定なら何も書かない（`action: kept`） | ファイルを書く |
| `settings.status` | `src/ndf/domains/statusline/settings.py` | 新設 | `status(p: Paths) -> Switched` | 今の `statusLine` と控えの有無を読む | 無し | 何も書かない | ファイルを読む |
| `settings.set_ndf` | `src/ndf/domains/statusline/settings.py` | 新設 | `set_ndf(p: Paths) -> Switched` | 今の値を控えて NDF の表示へ切り替える | 排他の中で呼ぶ | 控えがあり、`statusLine.command` が `bash ~/.claude/ndf-statusline.sh` である | ファイルを書く |
| `settings.restore` | `src/ndf/domains/statusline/settings.py` | 新設 | `restore(p: Paths) -> Switched` | 控えから元の値へ戻す。控えが無ければ `statusLine` を消す | 排他の中で呼ぶ | 控えが消え、`statusLine` が控えの値か無い | ファイルを書く・消す |
| `materials.cli.main` | `src/ndf/domains/materials/cli.py` | 新設 | `main(argv: list[str]) -> int` | 材料を集めて結果 JSON を出し、`--out` があれば下書きを書く | 同上 | 同上 | 標準出力 |
| `collect.issue_materials` | `src/ndf/domains/materials/collect.py` | 新設 | `issue_materials(number: int, body: str, files: list[str], designs: list[tuple[str, list[str]]]) -> dict[str, Any]` | 1 つの課題の種類・公開インタフェースの変更・触るファイルの候補とそのテストを組む | `files` は追跡されたファイルの一覧 | 旧い `issue_materials` と同じ入力で同じ辞書を返す | 無し |
| `collect.gather` | `src/ndf/domains/materials/collect.py` | 新設 | `gather(root: Path, numbers: list[int], hub: GitHub) -> Gathered` | 課題の本文・ファイルの一覧・設計文書を読み、課題ごとの材料と読めなかった課題を返す | `numbers` が 1 件以上 | 読めた課題は `items`、読めなかった課題は理由つきで `gaps` にある | GitHub の API・`git ls-files`・ファイルを読む |
| `collect.draft` | `src/ndf/domains/materials/collect.py` | 新設 | `draft(items: list[dict[str, Any]]) -> dict[str, Any]` | 判定の根拠のファイルの下書き（`row`・`mode`・`condition` は空）を組む | 無し | `ndf loop sprint` が読む形である | 無し |
| `collect.write_draft` | `src/ndf/domains/materials/collect.py` | 新設 | `write_draft(path: Path, data: Mapping[str, Any]) -> None` | 下書きを一時ファイルからの置き換えで書く | 無し | `path` に下書きがある | ファイルを書く |
| `relay.cli.main` | `src/ndf/domains/relay/cli.py` | 変更 | `main(argv: list[str]) -> int`（変えない） | 足した下位の命令もハンドラへ振り分け、足した部品の例外を `invalid` か `precondition` の結果 JSON へ変える | 同上 | 同上 | 標準出力 |
| `relay.cli._stop_all` | `src/ndf/domains/relay/cli.py` | 新設 | `_stop_all(ctx: hub.Context) -> int` | 動いている新しいラッパーのすべてへ、名前つきの停止（`hub.stop`）を依頼する | 名前を同時に渡していない | 依頼したラッパーごとに 1 件の `items` と件数の `metrics.stopped` を出す | リレーハブへの依頼 |
| `notice.position` | `src/ndf/domains/relay/notice.py` | 新設 | `position(environ: Mapping[str, str], start_pid: int) -> Position` | 呼び手の claude とラッパーの位置（直接の子・置き場が無い・終わっている・直接の子でない）を決める | 無し | `Position.kind` が 4 つのどれかである | プロセスの表と排他のファイルを読む |
| `notice.lines` | `src/ndf/domains/relay/notice.py` | 新設 | `lines(pos: Position, quiet: float) -> Notice` | 位置と静かな時間からアナウンスの 1 文を組む | 無し | 旧い `notice_lines` と同じ位置と秒数で同じ文を返す | 無し |
| `registry.legacy_state_root` | `src/ndf/domains/relay/registry.py` | 新設 | `legacy_state_root(environ: Mapping[str, str]) -> Path` | 旧いプラグインの状態の根（`NDF_STATE_HOME` → `XDG_STATE_HOME/ndf` → `~/.local/state/ndf`）を求める | 無し | `legacy_root(environ)` は `legacy_state_root(environ) / "relay"` と同じ値である | 無し |
| `account_registry.store_dir` | `src/ndf/domains/relay/account_registry.py` | 新設 | `store_dir(environ: Mapping[str, str]) -> Path` | 旧い置き場（`NDF_ACCOUNTS_DIR` → `<共有の設定ディレクトリ>/ndf/accounts`）を求める | 無し | 旧い `claude_accounts.store_dir` と同じ環境で同じパスを返す | 無し |
| `account_registry.locked` | `src/ndf/domains/relay/account_registry.py` | 新設 | `locked(store: Path, name: str, wait: float) -> ContextManager[None]` | アカウントごとの排他（`.locks/<名前>.lock`）を取る | `name` が名前の形に合う | 旧い書き手と同じパスで排他を持つ | 排他のファイルを作る |
| `account_registry.load` | `src/ndf/domains/relay/account_registry.py` | 新設 | `load(store: Path, name: str) -> Account \| None` | アカウントの `account.json` を読む | 無し | 無ければ `None`。読めなければ `AccountError` | ファイルを読む |
| `account_registry.save` | `src/ndf/domains/relay/account_registry.py` | 新設 | `save(store: Path, account: Account) -> None` | アカウントの `account.json` を権限 0600 で書く | 排他の中で呼ぶ | 旧い実装が読めるキーだけを持つ | ファイルを書く |
| `account_registry.list_rows` | `src/ndf/domains/relay/account_registry.py` | 新設 | `list_rows(store: Path) -> list[dict[str, Any]]` | 登録したアカウント・従量の接続・API キーを 1 件ずつ並べる | 無し | キーの値と認可コードを含まない | ファイルを読む |
| `account_registry.set_capacity` | `src/ndf/domains/relay/account_registry.py` | 新設 | `set_capacity(store: Path, name: str, five: float \| None, seven: float \| None) -> dict[str, Any]` | アカウントの 5 時間と週の枠の宣言を書く | `name` が登録済み | 枠が `account.json` にあり、`-` の枠は消えている | ファイルを書く |
| `account_registry.remove` | `src/ndf/domains/relay/account_registry.py` | 新設 | `remove(store: Path, target: str) -> dict[str, Any]` | アカウント・従量の接続・API キーの登録を外す | 無し | 外した登録の置き場が無い | ファイル・ディレクトリを消す |
| `account_login.add` | `src/ndf/domains/relay/account_login.py` | 新設 | `add(store: Path, name: str \| None, code: str \| None, environ: Mapping[str, str]) -> Added` | アカウントの設定ディレクトリで `claude auth login` を動かし、本人の確かめができたら登録する | `claude` が `PATH` にある | 登録したか、認可コードが要る（`Added.needs_code`）かのどちらかである | 外部コマンド（`claude auth`）・ファイルを書く・ネットワーク（claude が行う） |
| `account_metered.add_bedrock` | `src/ndf/domains/relay/account_metered.py` | 新設 | `add_bedrock(store: Path, profile: str \| None, region: str \| None, model: str \| None, yes: bool) -> dict[str, Any]` | Bedrock の接続を確かめて従量の接続の宣言（`metered.json`）を書く | `aws` が `PATH` にある | 確かめに通ったときだけ宣言が権限 0600 で書かれている | 外部コマンド（`aws`）・ファイルを書く |
| `account_metered.add_api_key` | `src/ndf/domains/relay/account_metered.py` | 新設 | `add_api_key(store: Path, name: str, account: str, key: str, monthly: float \| None) -> dict[str, Any]` | API キーを確かめて保存する | `key` は標準入力かファイルから読んだ値 | 確かめに通ったときだけ保存され、結果にキーの値が無い | ネットワーク（Anthropic の API）・ファイルを書く |
| `account_metered.set_monthly` | `src/ndf/domains/relay/account_metered.py` | 新設 | `set_monthly(store: Path, name: str, usd: float) -> dict[str, Any]` | API キーの月の額の宣言を書く | `name` が保存済みのキー | 月の額が宣言にある | ファイルを書く |
| `account_metered.set_use` | `src/ndf/domains/relay/account_metered.py` | 新設 | `set_use(store: Path, mode: str, weekly_at: float \| None) -> dict[str, Any]` | API キーへ切り替える使い方と閾値を書く | `mode` が `off`・`always`・`near-limit` のどれか | 使い方が宣言にある | ファイルを書く |
| `account_metered.check` | `src/ndf/domains/relay/account_metered.py` | 新設 | `check(store: Path, target: str) -> dict[str, Any]` | 従量の接続か API キーが呼べるかを確かめる | 無し | 状態を記録しない | 外部コマンド（`aws`）かネットワーク |
| `context_policy.resolve` | `src/ndf/domains/relay/context_policy.py` | 新設 | `resolve(root: Path, policy: str) -> Resolved` | 設定と既定値から、再起動の方針の適用値と出所を決める | `policy` が `current` か `trial` | 設定が壊れていれば現行の方式の値を返し、`config_error` を持つ | `.ndf/context.json` を読む |
| `context_records.context_dir` | `src/ndf/domains/relay/context_records.py` | 新設 | `context_dir(environ: Mapping[str, str]) -> Path` | 旧い置き場（`NDF_CONTEXT_DIR` → `<旧い状態の根>/context`）を求める | 無し | 旧い `context_decisions.context_state_dir` と同じ環境で同じパスを返す | 無し |
| `context_records.append` | `src/ndf/domains/relay/context_records.py` | 新設 | `append(root: Path \| None, row: Mapping[str, Any], environ: Mapping[str, str]) -> Path` | 判定の記録へ 1 行を追記する | `row` の理由か印が決められた値である | 1 回の書き込みで 1 行が足されている | ファイルへ追記 |
| `context_records.read` | `src/ndf/domains/relay/context_records.py` | 新設 | `read(root: Path \| None, environ: Mapping[str, str]) -> list[dict[str, Any]]` | 判定の記録を読み、読めない行を飛ばして `at` の順に並べる | 無し | 戻り値の行はどれも JSON の object である | ファイルを読む |
| `context_records.calibrate` | `src/ndf/domains/relay/context_records.py` | 新設 | `calibrate(root: Path \| None, days: int, environ: Mapping[str, str]) -> dict[str, Any]` | 直近の判定の記録から再起動の見込みを較正して書く | `days` が 1 以上 | 見込みが一時ファイルからの置き換えで書かれ、標本の数を返す | ファイルを読む・書く |
| `context_report.load_ledger` | `src/ndf/domains/relay/context_report.py` | 新設 | `load_ledger(environ: Mapping[str, str]) -> list[dict[str, Any]]` | 新しいプラグインの使用量の台帳を読む | 無し | 読めなければ空の一覧を返す | ファイルを読む |
| `context_report.report` | `src/ndf/domains/relay/context_report.py` | 新設 | `report(work: str, rows: list[dict[str, Any]], ledger: list[dict[str, Any]], span: tuple[float, float] \| None) -> dict[str, Any]` | 1 つの作業の区分ごとの token・秒・費用・再起動・自動圧縮・品質・復元の量を集計する | `rows` は `context_records.read` の戻り値 | 取れない値は `None` で、旧い `report_work` と同じ入力で同じ値を返す | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `project.cli.main` | 使い方の誤り | 知らない下位の命令・必須の引数が無い | argparse の `SystemExit` | 止める | `stopped`（種類 `invalid`）・終了コード 2 | 無し。引数を直して再実行できる |
| `project.cli.main` | 捕まえていない例外 | 部品の不具合 | 副命令の表（`ndf.cli`） | 呼び出し元へ返す | `stopped`（種類 `unknown`） | 無し。書き出しは一時ファイルからの置き換えのため、途中の宣言は残らない |
| `check.judge` | 宣言が読めない | JSON でない・object でない | `jsonfile.read_object` の `JsonFileError` | 呼び出し元へ返す（`kind: broken`） | `failed` ではなく判定として結果を出し、終了コード 2 | 無し。宣言を直すか消して再実行できる |
| `check.judge` | 判定の入力を読めない | 権限が無い・git の外 | `OSError`・`git` の終了コード | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 無し。再実行できる |
| `check.fingerprint` | 入力のファイルを読めない | 権限が無い・読む間に消えた | `OSError` | 呼び出し元へ返す（`check.judge` が終了コード 1 に変える） | 同上 | 無し |
| `measure_repo.measure` | 締め切りを過ぎた | 大きなリポジトリ | 項目ごとに単調時計と `deadline` を比べる | 既定値で続ける（その項目は `None`） | `items` の値が `null`、`metrics.unknown` に数える。終了コード 0 | 無し。`--budget` を大きくして再実行できる |
| `measure_repo.measure` | `git` が失敗した | git の外・`git` が無い | `git.run_git` の終了コード | 既定値で続ける（その項目は `None`） | 同上 | 無し |
| `measure_ci.measure` | GitHub の API に届かない | トークンが無い・ネットワーク・上限 | `GitHubError` | 既定値で続ける（実行の時間は `None`） | 同上。理由を `warnings` に 1 行出す | 無し。再実行できる |
| `measure_flow.measure` | ブランチの一覧を読めない | `git` が失敗した | `git.run_git` の終了コード | 既定値で続ける（`None`） | 同上 | 無し |
| `write.plan_writes` | 既にある宣言が壊れている | JSON でない | `JsonFileError` | 止める | `stopped`（種類 `unreadable`）・終了コード 2 | 何も書かない。宣言を直して再実行できる |
| `write.plan_writes` | 答えが取り込み方の表に合わない | 型・値の範囲が違う | 宣言のスキーマ（`contracts/schemas/declarations/project.schema.json`）の検証 | 止める | `stopped`（種類 `failed`）・終了コード 1。合わなかったキーを `items` に並べる | 何も書かない。答えを直して再実行できる |
| `write.apply` | 宣言を書けない | 権限が無い・容量 | `OSError` | 止める | `stopped`（種類 `failed`）・終了コード 1 | 一時ファイルを消す。元の宣言は残る。再実行できる |
| `write.drift` | 測定の結果を読めない | `--measure` のファイルが無い・壊れている | `JsonFileError` | 止める | `stopped`（種類 `failed`）・終了コード 1 | 無し |
| `write.drift` | 宣言が壊れている | JSON でない | `JsonFileError` | 止める | `stopped`（種類 `unreadable`）・終了コード 2 | 無し |
| `statusline.cli.main` | 使い方の誤り | 知らない下位の命令 | argparse | 止める | `stopped`（種類 `invalid`）・終了コード 2 | 無し |
| `settings.paths` | symlink の参照先が無い | 壊れた symlink | `Path.resolve(strict=False)` の後に親の有無を見る | 呼び出し元へ返す | `stopped`（種類 `unreadable`）・終了コード 2 | 無し |
| `settings.locked` | 排他を待ちきれない | 旧い `flock` か別のセッションが長く持つ | `filelock.Timeout` | 止める（`ensure` だけは既定値で続ける） | `set`・`restore` は `stopped`（種類 `failed`）・終了コード 1。`ensure` は `action: skipped`・終了コード 0 | 何も書かない。再実行できる |
| `settings.ensure` | `settings.json` を読めない | JSON でない | `json.JSONDecodeError` | 既定値で続ける（何もしない） | `action: skipped`・理由を `warnings`・終了コード 0（SessionStart を止めない） | 何も書かない |
| `settings.status` | `settings.json` を読めない | JSON でない・権限が無い | `OSError`・`json.JSONDecodeError` | 止める | `stopped`（種類 `unreadable`）・終了コード 2 | 無し |
| `settings.set_ndf` | `settings.json` を読めない | 同上 | 同上 | 止める | 同上 | 何も書かない |
| `settings.set_ndf` | 書けない | 権限が無い | `OSError` | 止める | `stopped`（種類 `failed`）・終了コード 1 | 控えを書いた後に `settings.json` を書けなければ、控えを消して元の状態に戻す。再実行できる |
| `settings.restore` | `settings.json` を読めない | 同上 | 同上 | 止める | `stopped`（種類 `unreadable`）・終了コード 2 | 何も書かない |
| `settings.restore` | 書けない | 権限が無い | `OSError` | 止める | `stopped`（種類 `failed`）・終了コード 1 | 控えは消さずに残す。再実行できる |
| `materials.cli.main` | 使い方の誤り | `--issue` が無い | argparse | 止める | `stopped`（種類 `invalid`）・終了コード 2 | 無し |
| `collect.gather` | 本文を読めない課題がある | 無い番号・権限・ネットワーク | `GitHubError` | 既定値で続ける（その課題を `gaps` へ） | `failed`・終了コード 1。`gaps` に課題と理由 | 無し。再実行できる |
| `collect.gather` | ファイルの一覧を読めない | git の外 | `git.run_git` の終了コード | 止める | `stopped`（種類 `precondition`）・終了コード 3 | 無し |
| `collect.write_draft` | 下書きを書けない | 権限が無い | `OSError` | 止める | `stopped`（種類 `failed`）・終了コード 1 | 一時ファイルを消す |
| `relay.cli.main` | 足した部品が使い方の誤りを送出した | 名前の形が違う・登録の無い名前 | `account_registry.AccountError`・`account_metered.MeteredError` | 止める | `stopped`（種類 `invalid`）・終了コード 2 | 無し |
| `relay.cli.main` | `stop` に名前と `--all` を同時に渡した | 使い方の誤り | argparse の排他のグループ | 止める | `stopped`（種類 `invalid`）・終了コード 2 | 無し |
| `relay.cli._stop_all` | 停止を依頼できないラッパーがある | リレーハブに届かない・ハブが断った | `hub.stop` の `Outcome.ok` が偽 | 既定値で続ける（ほかのラッパーへは依頼する） | `failed`・終了コード 1。断られたラッパーを理由つきで `items` に並べる | 無し。再実行できる（依頼は冪等） |
| `notice.position` | プロセスの表を読めない | 権限が無い・プロセスが消えた | `psutil.Error` | 既定値で続ける（`kind: not-child`） | 外の文を出す・終了コード 0 | 無し |
| `notice.position` | ラッパーの状態を読めない | 状態のファイルが壊れている | `registry.read_state` が `None` | 既定値で続ける（`kind: not-running`） | 同上 | 無し |
| `account_registry.locked` | 排他を待ちきれない | 旧い書き手か別のセッションが持つ | `filelock.Timeout` | 呼び出し元へ返す（`AccountLockTimeout`） | `stopped`（種類 `failed`）・終了コード 1。何を待ったかを `summary` に書く | 何も書かない。再実行できる |
| `account_registry.load` | `account.json` が壊れている | JSON でない | `json.JSONDecodeError` | 呼び出し元へ返す（`AccountError`） | `stopped`（種類 `invalid`）・終了コード 2 | 無し。`account remove` で外して登録し直せる |
| `account_registry.save` | 書けない | 権限が無い・置き場の権限が 0700 でない | `OSError`・`_secure` の偽 | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 一時ファイルを消す |
| `account_registry.list_rows` | 1 件の登録が壊れている | JSON でない | `json.JSONDecodeError` | 既定値で続ける（その行の状態を `broken` にする） | 終了コード 0。壊れた行を `warnings` に 1 行 | 無し |
| `account_registry.set_capacity` | 登録の無い名前 | 名前の誤り | `load` が `None` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 無し |
| `account_registry.set_capacity` | 書けない | 同上 | `OSError` | 呼び出し元へ返す | 同上 | 一時ファイルを消す |
| `account_registry.remove` | 登録が無い | 名前の誤り | 置き場の有無 | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 無し |
| `account_registry.remove` | 消せない | 権限が無い・動いている claude が使う | `OSError`・排他の `Timeout` | 呼び出し元へ返す | 同上 | 消し終えなかった置き場を `items` に書く。再実行できる |
| `account_login.add` | 認可コードが要る | ブラウザの無い端末 | `claude auth login` の出力に URL がある | 呼び出し元へ返す（`needs_code`） | `stopped`（種類 `precondition`）・終了コード 3。URL と `--code` の渡し方を `summary` に書く。認可コードは出さない | 登録の途中の状態（`.pending-<名前>/`）を残す。`--code` を付けて再実行できる |
| `account_login.add` | ログインに失敗した | コードの誤り・期限切れ | `claude auth` の終了コード | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 登録の途中の状態を消す。最初から再実行できる |
| `account_login.add` | `claude` が無い | `PATH` に無い | `shutil.which` | 止める | `stopped`（種類 `precondition`）・終了コード 3 | 無し |
| `account_metered.add_bedrock` | `aws` が無い・呼べない | 未導入・資格情報が無い | `shutil.which`・`classify_failure` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1。失敗の分類を `summary` に書く | 宣言を書かない。再実行できる |
| `account_metered.add_bedrock` | 書けない | 権限が無い | `OSError` | 呼び出し元へ返す | 同上 | 一時ファイルを消す |
| `account_metered.add_api_key` | キーの確かめに失敗した | 無効なキー・ネットワーク | HTTP の状態・`httpx.HTTPError` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1。キーの値を出さない | 保存しない。再実行できる |
| `account_metered.add_api_key` | 書けない | 権限が無い | `OSError` | 呼び出し元へ返す | 同上 | 一時ファイルを消す |
| `account_metered.set_monthly` | キーが無い・額が負 | 名前と値の誤り | `_amount`・置き場の有無 | 呼び出し元へ返す（`MeteredError`） | `stopped`（種類 `invalid`）・終了コード 2 | 無し |
| `account_metered.set_use` | 書けない | 権限が無い | `OSError` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 一時ファイルを消す |
| `account_metered.check` | 呼べない | 資格情報・ネットワーク | `verify` の戻り値 | 呼び出し元へ返す | `failed`・終了コード 1。失敗の分類を `items` に書く | 無し。状態を記録しない |
| `context_policy.resolve` | 設定が壊れている | JSON でない・型が違う | `JsonFileError`・型の確かめ | 既定値で続ける（現行の方式の値） | 終了コード 0。`items` の `config_error` と `warnings` に理由 | 無し |
| `context_records.append` | 書けない | 権限が無い・容量 | `OSError` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 途中の行を残さない（1 回の `write` で 1 行）。再実行できる |
| `context_records.read` | 読めない行がある | 途中で切れた行 | `json.JSONDecodeError` | 既定値で続ける（その行を飛ばす） | 終了コード 0。飛ばした行数を `metrics.skipped` に出す | 無し |
| `context_records.read` | 記録のファイルを読めない | 権限が無い | `OSError` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 無し |
| `context_records.calibrate` | 書けない | 権限が無い | `OSError` | 呼び出し元へ返す | `stopped`（種類 `failed`）・終了コード 1 | 一時ファイルを消す。前の見込みは残る |
| `context_report.load_ledger` | 台帳を読めない | 無い・壊れている | `OSError`・`json.JSONDecodeError` | 既定値で続ける（空の一覧） | 終了コード 0。台帳の分の値は `null` | 無し |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check.judge` | 宣言が無い | `missing`・終了コード 3 |
| `check.judge` | 宣言が読めない | `broken`・終了コード 2 |
| `check.judge` | 宣言の解析器の版が今の版より古い、または指紋が違う | `stale`・終了コード 3 |
| `check.judge` | 版と指紋が同じで `--force` | `stale`（理由「手動の再解析」）・終了コード 3 |
| `check.judge` | 版と指紋が同じで `--force` なし | `fresh`・終了コード 0 |
| `write.plan_writes` | 宣言に人が書いた値があり、測った値と同じ | `kept` |
| `write.plan_writes` | 宣言に人が書いた値があり、測った値と違う | `mismatch`（書かない） |
| `write.plan_writes` | 解析が書いた値がある、または値が無く、測った値がある | `written` |
| `write.plan_writes` | 測った値が `None` で答えも無い | `unknown`（書かない） |
| `write.plan_writes` | 値が秘密の形（`secret.mask` が当たる） | `secret`（伏せて書かない） |
| `write.plan_writes` | 答えがあり、取り込み方の表に合う | 答えの値で `written`（測った値より先） |
| `settings.ensure` | `statusLine` が無い | 本体を置き、NDF の表示を設定する（`set`） |
| `settings.ensure` | `statusLine.command` が NDF の表示で、本体の中身が今の版と違う | 本体だけを置き直す（`updated`） |
| `settings.ensure` | `statusLine.command` が NDF の表示で、本体が今の版と同じ | 何もしない（`kept`） |
| `settings.ensure` | `statusLine` が他の設定 | 何もしない（`kept`） |
| `settings.ensure` | 排他を取れない・`settings.json` を読めない | 何もしない（`skipped`） |
| `settings.restore` | 控えがあり、中に `statusLine` がある | その値へ戻し、控えを消す |
| `settings.restore` | 控えがあり、中に `statusLine` が無い | `statusLine` を消し、控えを消す |
| `settings.restore` | 控えが無い | `statusLine` を消す |
| `notice.position` | `NDF_RELAY_DIR` が無いか、ディレクトリでない | `no-dir` |
| `notice.position` | ラッパーの排他（`relay.lock`）が持たれていない | `not-running` |
| `notice.position` | 親をたどって最初に当たる claude の親の pid が、ラッパーの状態の `pid` と違う | `not-child` |
| `notice.position` | 同じ | `relay`（`metrics.inside: true`） |
| `notice.lines` | `relay` で静かな時間が有限 | 「<秒> 秒のアイドルの後に自動で切り替わる」の文 |
| `notice.lines` | `relay` で静かな時間が有限でない | 自動で切り替えない旨と手で切り替える手順の文 |
| `notice.lines` | `not-running`・`not-child`・`no-dir` | 理由ごとの外の文（旧い `notice_lines` と同じ文面） |
| `relay.cli._stop_all` | 動いている新しいラッパーが 0 本 | `ok`・`metrics.stopped: 0` |
| `relay.cli._stop_all` | すべての依頼が通った | `ok` |
| `relay.cli._stop_all` | 1 本以上の依頼が断られた | `failed`・終了コード 1 |
| `context_policy.resolve` | 設定が無い | 既定値（出所 `default`） |
| `context_policy.resolve` | 設定があり読める。`policy` が `trial` で `trial` の値がある | 試行の値（出所 `trial`） |
| `context_policy.resolve` | 設定があり読める。それ以外 | 設定の値（出所 `config`）。無いキーは既定値 |
| `context_policy.resolve` | 設定が壊れている | 現行の方式の既定値と `config_error` |

- 繰り返しの終わる条件: `notice.position` の親のたどりは pid が 1 以下になるか 64 世代で終わる（旧い `is_direct_child` と同じ）。`measure_*` は項目の並びの終わりか締め切りで終わる。`relay.cli._stop_all` は `registry.running` の一覧の終わりで終わる
- 計算量の注意: `context_report.report` は判定の記録と台帳の行の数に比例し、作業ごとに全行を走査する（旧いと同じ）。`--all` は作業の数だけ走査を繰り返すため、作業の数と行の数の積になる。旧い実装と同じ形のまま移し、速さの改善はこの課題で行わない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `COMMANDS` の 3 行 | 定数 | `src/ndf/cli.py` | `"project": "ndf.domains.project.cli"`・`"statusline": "ndf.domains.statusline.cli"`・`"materials": "ndf.domains.materials.cli"` |
| `ANALYZER` | 定数 | `src/ndf/domains/project/check.py` | 3（旧い `project_lib.ANALYZER` と同じ。上げると宣言が古いと判定される） |
| `ITEM_KEYS` | 定数 | `src/ndf/domains/project/check.py` | 旧い `project_lib.ITEM_KEYS` の 11 項目 |
| `Measured` | 型 | `src/ndf/domains/project/measure_repo.py` | `name: str`・`value: Any \| None`・`source: str` |
| `Row` | 型 | `src/ndf/domains/project/write.py` | `key: str`・`kind: str`（`written`・`kept`・`mismatch`・`unknown`・`secret`）・`value: Any` |
| `project.measure` | 設定値（上限の表） | `contracts/limits.json` | 120 秒（旧い `--budget` の既定）。`--budget` が先 |
| 宣言のパス | 定数（ファイルの表） | `contracts/domains/ndf-files.json` の `project`（既存） | `.ndf/project.json`。`records.files.path(root, "project")` で引く |
| `NDF_COMMAND` | 定数 | `src/ndf/domains/statusline/settings.py` | `bash ~/.claude/ndf-statusline.sh` |
| 控え・排他のファイルの名前 | 定数 | `src/ndf/domains/statusline/settings.py` | `.ndf-statusline-backup.json`・`.ndf-statusline.lock`（旧いと同じ） |
| `statusline.lock` | 設定値（上限の表） | `contracts/limits.json` | 10 秒 |
| `Position` | 型 | `src/ndf/domains/relay/notice.py` | `kind: str`・`wrapper_pid: int \| None`・`child_pid: int \| None` |
| `ANCESTOR_LIMIT` | 定数 | `src/ndf/domains/relay/notice.py` | 64 |
| 静かな時間 | 設定値（上限の表・既存） | `contracts/limits.json` の `relay.quiet` | 5 秒。アナウンスの秒数はこの値だけから作る |
| 旧い状態の根 | 関数 | `src/ndf/domains/relay/registry.py` の `legacy_state_root` | `NDF_STATE_HOME` → `XDG_STATE_HOME/ndf` → `~/.local/state/ndf`。旧い名前 `ndf` は既存の `identity.FINAL_NAME` から引く |
| アカウントの旧い置き場 | 関数 | `src/ndf/domains/relay/account_registry.py` の `store_dir` | `NDF_ACCOUNTS_DIR` → `<NDF_SHARED_CONFIG_DIR か CLAUDE_CONFIG_DIR か ~/.claude>/ndf/accounts` |
| `relay.account-lock` | 設定値（上限の表） | `contracts/limits.json` | 旧い `claude_accounts.lock_timeout` の秒数を写す（実装で旧い値を読んで決める） |
| `relay.account-auth` | 設定値（上限の表） | `contracts/limits.json` | 旧い `login.py` の `claude auth` の上限の秒数 |
| 方針の記録の旧い置き場 | 関数 | `src/ndf/domains/relay/context_records.py` の `context_dir` | `NDF_CONTEXT_DIR` → `<legacy_state_root>/context` |
| 理由の値・印の値 | 定数 | `src/ndf/domains/relay/context_records.py` | 旧い `context_decisions` の 7 つの理由と `引継ぎ開始`・`復元完了`。argparse の `choices` はこの定数から作る |
| 新しく読む環境変数 | 設定値（環境変数の表） | `contracts/domains/env.json` | `NDF_ACCOUNTS_DIR`・`NDF_SHARED_CONFIG_DIR`・`NDF_CONTEXT_DIR`・`NDF_ACCOUNT_CHECK_INTERVAL`（`child` の列は `pass`） |

旧い状態の根は `registry.legacy_state_root` の 1 か所だけが求める。既存の `registry.legacy_root` はこの関数に `relay` を足した値を返す形に書き換え、同じ規則を 2 か所に持たない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 旧い `relay.py account` と新しい `ndf relay account` | アカウントの旧い置き場の `<名前>/account.json`・`metered.json`・API キーのファイル | `account_registry.locked` が `.locks/<名前>.lock` に filelock を取る。旧い実装と同じパスのため、互いに待つ。書くのは一時ファイルからの置き換え | `set_capacity`・`set_monthly`・`set_use` は同じ値で何度打っても同じ結果。`add` は途中で切れると `.pending-<名前>/` が残り、`--code` を付けて再開できる |
| 旧い `context-policy.py record`・hook と、新しい `ndf relay context record` | 判定の記録（`decisions/<リポジトリ>.decisions.jsonl`） | 排他を取らず、1 回の `write` で 1 行を追記する（`O_APPEND`）。読み手が `at` で並べ直す | 同じ行を 2 回打つと 2 行になる（旧いと同じ）。途中で切れた行は読み手が飛ばす |
| 旧い `calibrate` と新しい `calibrate` | 見込み（`estimates/<リポジトリ>.cost-estimates.json`） | 一時ファイルからの置き換え。後に書いた方が残る | 同じ記録から作るため冪等 |
| 旧い `statusline-switch.sh` と新しい `ndf statusline`、SessionStart の `ensure` の重なり | `settings.json`・控え・表示の本体 | `settings.locked` が `.ndf-statusline.lock` に `fcntl.flock` の排他を取る（旧い `flock` コマンドと同じロックの種類） | `ensure`・`set` は 2 回目以降に同じ状態で終わる。`restore` は控えを消した後は `statusLine` を消すだけになる |
| 旧い `project-decl.py write` と新しい `ndf project write` | `.ndf/project.json`・`.ndf/worktree.json` | 一時ファイルからの置き換え。後に書いた方が残る | 同じ測定と答えから同じ宣言になる |
| `ndf relay stop --all` と、リレーハブの別の依頼 | リレーハブの依頼の置き場 | 既存の `hub.submit` の排他に任せる | 停止の依頼は冪等。止まった後の依頼は「動いていない」で返る |
| `ndf relay notice` | ラッパーの状態のファイル | 読むだけで排他を取らない（`relay.lock` は持たれているかを見るだけ） | 副作用が無いため冪等 |

filelock と `flock` コマンドが同じファイルで互いに待つことは、`test_statusline_cli.py` で確かめる。子プロセスの `flock` が持つ間に、`settings.locked` が待つことを見る。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `project.cli.main` | 判定の種類と理由・書いたキーの件数・食い違いのキーの名前 | 結果 JSON | 秘密の形の値（`secret` の行は伏せた値だけ） |
| `measure_ci.measure` | GitHub に届かなかった理由 | 結果 JSON の `warnings` | トークン |
| `settings.ensure` | 行った操作 | 結果 JSON。`skipped` の理由は `warnings` | 無し |
| `collect.gather` | 読めなかった課題と理由 | 結果 JSON の `gaps` と `metrics.gaps` | 無し |
| `relay.cli._stop_all` | 依頼したラッパーと結果 | 結果 JSON | 無し |
| `notice.lines` | アナウンスの 1 文と `inside` | 結果 JSON の `items`・`metrics` | 無し |
| `account_login.add` | 登録したアカウントの名前・メールアドレスの伏せた形・組織の名前 | 結果 JSON | 認可コード・アクセストークン・リフレッシュトークン（標準エラーとログにも出さない） |
| `account_metered.add_api_key` | 保存したキーの名前と月の額 | 結果 JSON | キーの値（先頭と末尾の数文字も出さない。旧いと同じ） |
| `account_metered.add_bedrock` | プロファイル・地域・モデルの ID | 結果 JSON | AWS の資格情報 |
| `account_registry.list_rows` | 名前・残量・枠・月の額・状態 | 結果 JSON | キーの値・トークン |
| `context_records.append` | 追記した行（理由・作業の識別子・時刻） | 判定の記録と結果 JSON | 無し |
| `context_report.report` | 作業ごとの量 | 結果 JSON の `items` | 会話の本文 |

どの関数も `logging` のハンドラを足さない。副命令の表（`ndf.cli`）の既定のとおり、利用者に見せるのは結果 JSON と標準エラーの理由の行だけである。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `project.cli.main` | 既存の副命令の `main` の形（`ndf.domains.relay.cli.main`）・`ndf.contracts.result` | `grep -rn "def main(argv" src/ndf/domains` | 使う（同じ形で書き、結果 JSON は `result.emit` で出す） |
| `check.judge` | `ndf.domains.records.preflight.declaration_checked` | `grep -rn "project.json\|\"project\"" src/ndf --include=*.py` | 使えない理由: 有無と object かだけを見て、版と指紋を見ない。宣言のパスと読み取りは同じ部品（`records.files.path`・`jsonfile.read_object`）を使う |
| `check.fingerprint` | `ndf.adapters.git.run_git` | `grep -n "^def " src/ndf/adapters/git.py` | 使う（`git ls-files` を呼ぶ） |
| `measure_repo.measure` | 新しいプラグインの言語・テストの判定（`ndf.testing.scoped`） | `grep -rn "languages\|test_duration" src/ndf --include=*.py` | 使えない理由: 宣言を読む側で、リポジトリを測らない。旧い `measure_repo.py` を移す |
| `measure_ci.measure` | `ndf.adapters.github.GitHub`・`ndf.adapters.ci` | `grep -n "^def \|^class " src/ndf/adapters/github.py src/ndf/adapters/ci.py` | 使う（API の呼び出しは `GitHub` に寄せ、`gh` の子プロセスを持ち込まない） |
| `measure_flow.measure` | `ndf.domains.worktree.declaration`（ブランチの流れの宣言の読み手） | `grep -rln "branch_flow" src/ndf` | 使う（宣言の値の型と読み取り）。測る処理は無いため旧い `measure_branch_flow.py` を移す |
| `write.plan_writes` | 宣言のスキーマ `contracts/schemas/declarations/project.schema.json` と jsonschema | `ls contracts/schemas/declarations/`・`grep -rn "Draft202012Validator" src/ndf` | 使う（旧い pydantic のモデルを移さず、スキーマを正本にして jsonschema で検証する） |
| `write.apply` | `ndf.domains.relay.signals.replace_json`（一時ファイルからの置き換え） | `grep -n "def replace_json\|def replace_text" src/ndf/domains/relay/signals.py` | 使う |
| `write.drift` | `ndf.domains.worktree.declaration.load` | 同上 | 使う（宣言の側の値を読む） |
| `statusline.cli.main` | 既存の副命令の `main` の形 | `project.cli.main` と同じ | 使う |
| `settings.paths` | `ndf.domains.relay.signals.claude_config_root` | `grep -n "def claude_config_root" src/ndf/domains/relay/signals.py` | 使う（`CLAUDE_CONFIG_DIR` と `~/.claude` の解決） |
| `settings.locked` | filelock・`signals.held`（`fcntl.flock`） | `grep -rn "def held\|from filelock" src/ndf` | `signals.held` を使う。旧い `flock` コマンドと同じ `flock` の種類で待つためで、filelock の `FileLock` は Linux で `fcntl.flock` を使うが、待ちの形を既存の関数に揃える |
| `settings.ensure` | 無し（`statusLine` を扱う部品が無い） | `grep -rn "statusLine" src/ndf` | 新しく書く（旧い `statusline-switch.sh` の `ensure` の分岐を移す） |
| `settings.status` | 同上 | 同上 | 新しく書く |
| `settings.set_ndf` | `signals.replace_text`（本体の置き換え）・`signals.replace_json` | 同上 | 使う |
| `settings.restore` | 同上 | 同上 | 使う |
| `materials.cli.main` | 既存の副命令の `main` の形 | 同上 | 使う |
| `collect.issue_materials` | `ndf.adapters.markdown`（見出しと節の読み取り） | `grep -n "^def " src/ndf/adapters/markdown.py` | 使う（旧い `md` の見出しの読み取りの代わり） |
| `collect.gather` | `ndf.adapters.github.GitHub.issue_body` | `grep -n "def issue_body" src/ndf/adapters/github.py` | 使う |
| `collect.draft` | `ndf loop sprint` が読む判定の根拠の形（`ndf.domains.loop`） | `grep -rn "condition" src/ndf/domains/loop --include=*.py` | 使う（形を写さず、同じキーを組む） |
| `collect.write_draft` | `signals.replace_json` | 上と同じ | 使う |
| `relay.cli._stop_all` | `registry.running`・`hub.stop` | `grep -n "def running\|def stop" src/ndf/domains/relay/registry.py src/ndf/domains/relay/hub.py` | 使う（新しいラッパーを名前ごとに止める。停止シグナルファイルを新しく作らない） |
| `notice.position` | `registry.read_state`・`registry.lock_busy`・`signals.relay_dir`・psutil | `grep -n "def read_state\|def lock_busy" src/ndf/domains/relay/registry.py`・`grep -n psutil pyproject.toml` | 使う（ラッパーの状態の `pid` と親のたどりで判定し、ラッパーに子の pid のファイルを足さない） |
| `notice.lines` | 無し | `grep -rn "カットポイント" src/ndf --include=*.py` | 新しく書く（旧い `notice_lines` の文面を移す） |
| `registry.legacy_state_root` | `registry.legacy_root` | `grep -n "def legacy_root" -A8 src/ndf/domains/relay/registry.py` | 使う（中の規則を切り出して共有する） |
| `account_registry.store_dir` | `relay.accounts.accounts_dir`（指定と使用率の置き場） | `grep -n "def accounts_dir" src/ndf/domains/relay/accounts.py` | 使えない理由: 新しい状態の置き場の `relay/accounts` を指し、旧い登録の置き場（`identity` の旧い置き場の表の `config-accounts`。扱いは `KEEP`）と別である |
| `account_registry.locked` | filelock（`ndf.domains.records.jsonl.lock` と同じ使い方） | `grep -rn "from filelock" src/ndf` | 使う |
| `account_registry.load` | `ndf.domains.records.jsonfile.read_object` | `grep -n "def read_object" src/ndf/domains/records/jsonfile.py` | 使う |
| `account_registry.save` | `signals.replace_text`（`mode` を渡せる） | `grep -n "def replace_text" src/ndf/domains/relay/signals.py` | 使う（権限 0600 で置き換える） |
| `account_registry.list_rows` | `relay.accounts.usages`（使用率のキャッシュ） | `grep -n "def usages" src/ndf/domains/relay/accounts.py` | 使う（残量の列はこのキャッシュから読む） |
| `account_registry.set_capacity` | 無し（枠の宣言の部品が無い） | `grep -rn "capacity" src/ndf/domains/relay` | 新しく書く |
| `account_registry.remove` | 無し | 同上 | 新しく書く |
| `account_login.add` | `ndf.adapters.claude`（claude の起動の組み立て） | `grep -n "^def " src/ndf/adapters/claude.py` | 使う（`claude` の解決）。`auth login` の対話は無いため旧い `login.py` を移す |
| `account_metered.add_bedrock` | 無し（`aws` を呼ぶ部品が無い） | `grep -rn "\"aws\"" src/ndf --include=*.py` | 新しく書く（旧い `bedrock.py` を移す） |
| `account_metered.add_api_key` | `ndf.adapters.http` | `grep -n "^def \|^class " src/ndf/adapters/http.py` | 使う（キーの確かめの HTTP の呼び出し） |
| `account_metered.set_monthly` | `signals.replace_text` | 上と同じ | 使う |
| `account_metered.set_use` | 同上 | 同上 | 使う |
| `account_metered.check` | `add_bedrock`・`add_api_key` の確かめの非公開の関数 | 同じモジュール | 使う |
| `context_policy.resolve` | `ndf.domains.records.files.path(root, "context")`・`jsonfile.read_object` | `grep -n "\"context\"" contracts/domains/ndf-files.json` | 使う（設定のパスと読み取り） |
| `context_records.context_dir` | `registry.legacy_state_root` | 上と同じ | 使う |
| `context_records.append` | `signals.append_jsonl`・`records.jsonl.append` | `grep -n "def append_jsonl\|def append" src/ndf/domains/relay/signals.py src/ndf/domains/records/jsonl.py` | `signals.append_jsonl` を使う（排他を取らない追記で、旧いと同じ取り方になる） |
| `context_records.read` | `ndf.domains.records.jsonl.parse_lines` | `grep -n "def parse_lines" src/ndf/domains/records/jsonl.py` | 使う（読めない行を飛ばして数える） |
| `context_records.calibrate` | 無し（較正の計算の部品が無い） | `grep -rn "calibrat" src/ndf` | 新しく書く（旧い `context_calibrate.py` を移す） |
| `context_report.load_ledger` | `ndf.domains.records.usage.ledger_path` | `grep -n "def ledger_path" src/ndf/domains/records/usage.py` | 使う（新しいプラグインの台帳を読む） |
| `context_report.report` | `ndf.domains.records.usage.report` | 同上 | 使えない理由: 期間の集計で、作業と区分（引継ぎの前後）で分けない。旧い `report_work` を移す |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `project.cli.main` | 5 つの下位の命令がどれも結果 JSON を 1 行出す | `--help` が終了コード 0 | 知らない下位の命令で `invalid`・2。例外で `unknown` |
| `check.judge` | 版と指紋が同じなら `fresh`・0 | `--force` で `stale`・3 | 無いと `missing`・3、壊れていると `broken`・2、git の外で 1 |
| `check.fingerprint` | 同じ入力で同じ値 | 入力のファイルが 0 件 | 読めないファイルで `OSError` が返る |
| `measure_repo.measure` | 言語とテストのコマンドを測る | 締め切り 0 秒で全項目が `None` | git の外で `None` が並び、終了コード 0 |
| `measure_ci.measure` | CI の定義を読む | GitHub の名前が無い | `GitHubError` で実行の時間が `None`、`warnings` に 1 行 |
| `measure_flow.measure` | 起点と本番のブランチを測る | ブランチが 1 本だけ | git が失敗して `None` |
| `write.plan_writes` | 測った値を `written` にする | 人の値と同じなら `kept` | 人の値と違えば `mismatch`、秘密の形は `secret`、答えがスキーマに合わなければ 1、宣言が壊れていれば 2 |
| `write.apply` | 宣言が置き換わる | `--dry-run` で何も書かない | 書けない置き場で 1、元の宣言が残る |
| `write.drift` | ずれた項目を並べる | ずれ 0 件 | 測定の結果が壊れていれば 1、宣言が壊れていれば 2 |
| `statusline.cli.main` | 4 つの下位の命令が結果 JSON を出す | 引数なし | 知らない下位の命令で `invalid`・2 |
| `settings.paths` | `CLAUDE_CONFIG_DIR` の下を指す | `settings.json` が symlink なら参照先の隣に控えを置く | 壊れた symlink で 2 |
| `settings.locked` | 排他の中で書く | 子プロセスの `flock` が持つ間は待つ | 待ちの上限で `Timeout` |
| `settings.ensure` | 無ければ設定する | NDF の表示で本体が同じなら `kept` | 他の設定なら `kept`、壊れた `settings.json` で `skipped`・0 |
| `settings.status` | 今の値と控えの有無を出す | `settings.json` が無い | 壊れた `settings.json` で 2 |
| `settings.set_ndf` | 控えて切り替える | 旧い版で作った控えがある | 壊れた `settings.json` で 2、書けなければ 1 で控えが残らない |
| `settings.restore` | 控えへ戻す | 控えが無ければ `statusLine` を消す | 壊れた `settings.json` で 2、書けなければ 1 で控えが残る |
| `materials.cli.main` | 材料を集めて結果を出す | `--out` なし | `--issue` なしで `invalid`・2 |
| `collect.issue_materials` | 旧い実装の固定の入力と同じ辞書になる | 触るファイルの候補が 0 件 | 本文が空 |
| `collect.gather` | 課題ごとに 1 件 | 課題が 1 件 | 読めない課題は `gaps` に入り 1、git の外で 3 |
| `collect.draft` | `row`・`mode`・`condition` が空の下書き | 課題が 1 件 | 無し |
| `collect.write_draft` | 下書きが書かれる | 既にあるファイルを置き換える | 書けない置き場で 1 |
| `relay.cli.main` | 足した下位の命令が振り分けられる | `stop <名前>` は今の振る舞いのまま | 名前と `--all` の同時指定で 2、`AccountError` で 2 |
| `relay.cli._stop_all` | 動いている 2 本へ依頼して `stopped: 2` | 0 本で `ok`・0 | 1 本が断られて 1 |
| `notice.position` | 直接の子で `relay` | 間に bash が挟まっても `relay` | 置き場が無い・排他が持たれていない・親が違うで 3 つの外、`psutil.Error` で `not-child` |
| `notice.lines` | 有限の秒数の文 | 静かな時間が有限でない | 外の 3 つの理由の文が旧い文面と同じ |
| `registry.legacy_state_root` | `NDF_STATE_HOME` があればそれ | 変数が無ければ `~/.local/state/ndf` | `legacy_root` が同じ環境で `/relay` を足した値 |
| `account_registry.store_dir` | `NDF_ACCOUNTS_DIR` があればそれ | `NDF_SHARED_CONFIG_DIR` が `CLAUDE_CONFIG_DIR` より先 | 無し |
| `account_registry.locked` | 排他の中で書く | 旧い実装と同じパス | 待ちの上限で `AccountLockTimeout`・1 |
| `account_registry.load` | 登録を読む | 無ければ `None` | 壊れた `account.json` で `AccountError`・2 |
| `account_registry.save` | 権限 0600 で書く | 旧い実装が読めるキーだけ | 置き場の権限が 0700 でなければ 1 |
| `account_registry.list_rows` | アカウント・接続・キーが並ぶ | 登録 0 件 | 壊れた 1 件が `broken` で、ほかは並ぶ。キーの値が出ない |
| `account_registry.set_capacity` | 枠を書く | `-` で枠を消す | 登録の無い名前で 1 |
| `account_registry.remove` | 登録を外す | `metered` を外す | 登録が無ければ 1、消せなければ 1 で残りを `items` に |
| `account_login.add` | 偽の `claude` で登録が書かれる | 認可コードが要るとき 3 で `.pending-<名前>/` が残る | ログインの失敗で 1 と途中の状態が消える、`claude` が無ければ 3。どの出力にも認可コードが無い |
| `account_metered.add_bedrock` | 偽の `aws` で宣言が書かれる | `--yes` で確かめの問いを飛ばす | 資格情報の誤りで 1 と分類、書けなければ 1 |
| `account_metered.add_api_key` | 偽の HTTP の応答で保存される | `--monthly` なし | 無効なキーで 1 と保存されない。出力にキーの値が無い |
| `account_metered.set_monthly` | 月の額を書く | 0 ドル | キーが無い・負の額で 2 |
| `account_metered.set_use` | 使い方を書く | `--weekly-at` なし | 書けなければ 1 |
| `account_metered.check` | 呼べれば 0 | 状態のファイルが変わらない | 呼べなければ 1 |
| `context_policy.resolve` | 設定の値と出所 | 設定が無ければ既定値 | 壊れた設定で現行の値と `config_error`・0 |
| `context_records.context_dir` | `NDF_CONTEXT_DIR` があればそれ | 無ければ旧い状態の根の `context` | 無し |
| `context_records.append` | 1 行が足される | 旧い実装が書いた行の後に足す | 書けなければ 1 |
| `context_records.read` | `at` の順に並ぶ | 記録が無ければ空 | 途中で切れた行を飛ばし `skipped` に数える、読めなければ 1 |
| `context_records.calibrate` | 見込みが書かれ標本の数を返す | 標本 0 件 | 書けなければ 1 で前の見込みが残る |
| `context_report.load_ledger` | 台帳の行を返す | 台帳が無ければ空 | 壊れた台帳で空 |
| `context_report.report` | 旧い実装の固定の入力と同じ値 | 期間の指定で区間を切る | 台帳が空なら台帳の分が `None` |
