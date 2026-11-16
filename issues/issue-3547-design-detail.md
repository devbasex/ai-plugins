# #3547 の詳細設計: OAuth でログインした gws を認証済みと判定し、試走で 3 行を ndf-next だけで通す

設計文書 [issue-3547-design.md](issue-3547-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。
2 つのサブコマンド（`ndf gws check`・`ndf gws thumbnails`）と google-workspace の Skill の本文は develop にあり、作り直さない。
この文書が扱うのは、認証済みの判定の直し・`system-gdrive.md` の書き換え・判定の文の書き換え・試走の記録への追記と機能の対応表の生成し直しだけである。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設するファイルは無い）:

```text
plugins/ndf-next/
├── src/ndf/domains/records/gws.py                  変更（decide と冒頭の説明）
├── tests/domains/gws/test_check.py                 変更（テストを 1 つ足す）
├── tests/domains/gws/test_thumbnails.py            変更（テストを 1 つ足す）
├── tests/domains/records/test_close.py             変更（既存のテストに行を足す）
├── skills/document-systems/references/system-gdrive.md  変更（Google スライドの行）
├── contracts/domains/env.json                      変更（GOOGLE_WORKSPACE_CLI_TOKEN の行の meaning）
└── docs/contracts/env.md                           変更（env.json から生成し直す）
docs/specifications/ndf-next-gws-subcommands.md     変更（判定の文・I9 の行・テストの表）
scripts/ndf-next-parity/parity/
├── trial/2026-11-16.json                           変更（runs と passed へ追記）
└── features.json                                   変更（trial の列を生成し直す）
```

依存の向きは一方向で、循環は無い。`ndf gws check`・`ndf gws thumbnails`・`ndf records gws-check` は、記録の gws の部品の
`authenticated` を呼ぶ。`authenticated` は `decide` を呼ぶ。`decide` はほかの部品を読まない。`env.md` は `env.json` から
`ndf check --write-spec` が作り、機能の対応表の `trial` の列は試走の記録と ndf-next の本文から試走の判定のコマンドが作る。
`system-gdrive.md` はサブコマンドの名前と google-workspace の Skill の操作の対応表を指すだけで、逆向きの参照は無い。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/records/gws.py` | 変更 | `decide` を I1 の判定（未認証は 2 つの形だけ）へ直し、冒頭の説明の判定の文を同じに直す | 無し（`authenticated`・`auth_status` は変えない） |
| `plugins/ndf-next/tests/domains/gws/test_check.py` | 変更 | ログイン済みの形で `ndf gws check` が `ok`・0・`authenticated` を返すテストを足す。未認証の 3 つの場合のテストは残す | `tests/domains/gws/__init__.py` の `status_rule`・`run_gws` |
| `plugins/ndf-next/tests/domains/gws/test_thumbnails.py` | 変更 | ログイン済みの形で gws が失敗したとき、`ndf gws thumbnails` が 11 でなく `stopped`・1 を返すテストを足す | `tests/domains/gws/__init__.py` の `status_rule`・`run_gws` |
| `plugins/ndf-next/tests/domains/records/test_close.py` | 変更 | `test_gws_client_secret_only_is_not_authenticated` に、ログイン済みの形が真・`environment_variables` の形が真になる行を足す | `ndf.domains.records.gws` |
| `plugins/ndf-next/skills/document-systems/references/system-gdrive.md` | 変更 | 「描画して見る手段」の表の Google スライドの行を、`ndf gws thumbnails <ID> <出力のディレクトリ>` を打つ形にする（設計の決定 3） | google-workspace の Skill の本文（参照だけ） |
| `plugins/ndf-next/contracts/domains/env.json` | 変更 | `GOOGLE_WORKSPACE_CLI_TOKEN` の行の `meaning` を I1・I2 の判定の文にする | 無し |
| `plugins/ndf-next/docs/contracts/env.md` | 変更 | `ndf check --write-spec` で `env.json` から生成し直す。手で書かない | `contracts/domains/env.json` |
| `docs/specifications/ndf-next-gws-subcommands.md` | 変更 | 「認証済みの判定」の段落・関連する仕様の表の行・I9 の行の破れたときの扱い・テストの表の認証済みと未認証の行を I1 に合わせる | 無し |
| `scripts/ndf-next-parity/parity/trial/2026-11-16.json` | 変更 | `runs` に受け入れ条件 20 の (a)・(b) の 2 件、`passed` に 3 行の id を追記する（設計の決定 2）。実装の時点でより新しい日付の記録があれば、その最新の記録へ追記する | 無し |
| `scripts/ndf-next-parity/parity/features.json` | 変更 | `python -m parity.trial.probe --write` で `trial` の列を生成し直す。3 行の `now`・`to` は手で変えない（設計の決定 4） | 試走の記録・ndf-next の本文 |

触らないと決めたファイル（実装の範囲に入れない。消すファイルは無い）:

| パス | 扱い | 理由 |
| --- | --- | --- |
| `scripts/contract-allow/plugins__ndf__skills__google-workspace__scripts__gws-check.py--core-entry.json` | 残す | 今の `plugins/ndf/` の gws-check.py の外れを許す例外で、消すと `python3 scripts/check-ndf-contracts.py` が違反を返す。消すのは今の NDF を外す #3304（設計の決定 4） |
| `features.json` の `entrypoint:skills/google-workspace/scripts/gws-check.py` の行 | 残す（`trial` だけ生成し直す） | `now`・`to` は試走の判定のコマンドが旧い呼び出しを探す手がかりで、`passed` は記録と本文で決まる |
| `features.json` の `entrypoint:skills/google-workspace/scripts/slides-thumbnails.py` の行 | 残す（`trial` だけ生成し直す） | 同上 |
| `features.json` の `skill:document-systems` の行 | 残す（`trial` だけ生成し直す） | `now` は Skill の置き場で、本文の旧い呼び出しが無くなれば `failed` でなくなる |
| `scripts/ndf-next-parity/parity/trial/2026-11-15.json` | 残す | 追記先は最新の記録だけで、過去の記録は書き換えない（I7） |
| `scripts/ndf-next-parity/parity/trial/probe.py`・`scripts/ndf-next-parity/tests/` | 残す | 判定の規則は変えない。3 行は今の規則のまま `passed` になる |
| `plugins/ndf-next/src/ndf/domains/gws/`（`__init__.py`・`check.py`・`thumbnails.py`・`cli.py`・`material.py`・`report_decide.py`） | 残す | `authenticated` を呼ぶだけで判定の値を持たない。I1 の直しは呼び先で効く |
| `plugins/ndf-next/src/ndf/domains/records/cli.py` | 残す | `ndf records gws-check` は `authenticated` を呼ぶだけである |
| `plugins/ndf-next/tests/domains/gws/__init__.py`・`conftest.py` | 残す | `status_rule` が `auth_method` を引数で受けるため、足すテストもこのまま書ける |
| `plugins/ndf-next/skills/google-workspace/SKILL.md` | 残す | 旧いスクリプトの参照は 0 件で、サブコマンドを打つ形が develop にある（受け入れ条件 14） |
| `plugins/ndf-next/contracts/` の 2 つのサブコマンドの引数と結果のスキーマ・`plugins/ndf-next/docs/contracts/commands.md` | 残す | 名前・引数・結果の形は変えない（受け入れ条件 12） |
| `plugins/ndf/` のすべて・`docs/specifications/ndf-google-workspace-gws.md` | 残す | 今の NDF は変えない（受け入れ条件 16） |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。クラスは足さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/records/gws.py` | モジュール `ndf.domains.records.gws` | `decide`（変更）。`auth_status`・`public_keys`・`authenticated`・`GwsError`・`STATUS_KEYS` は変えない |
| `plugins/ndf-next/tests/domains/gws/test_check.py` | テストのモジュール | `test_logged_in_with_client_secret_is_authenticated`（新設） |
| `plugins/ndf-next/tests/domains/gws/test_thumbnails.py` | テストのモジュール | `test_gws_failure_when_logged_in_stops_with_1_not_11`（新設） |
| `plugins/ndf-next/tests/domains/records/test_close.py` | テストのモジュール | `test_gws_client_secret_only_is_not_authenticated`（行を足す。名前は変えない） |

テストの関数は「関数の仕様」に載せず、縛る振る舞いを「単体テストの観点」に書く。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `decide` | `plugins/ndf-next/src/ndf/domains/records/gws.py` | 変更 | `decide(status: Mapping[str, Any], token_in_env: bool) -> bool`（変えない） | `gws auth status` の応答と環境変数のトークンの有無から、認証済みかを I1・I2 の判定で決める | `status` は `auth_status` が返した応答で、`STATUS_KEYS` の 3 つのキーを持つ | `token_in_env` が真なら真。偽なら、`credential_source` が `none` のときと、`credential_source` が `client_secret.json` で `auth_method` が `none` のときだけ偽で、それ以外は真 | 無し |

`decide` で変わるのは判定の式と説明だけで、シグネチャは変えない。読むキーは `credential_source` と `auth_method` の 2 つで、
`client_config_exists` は判定に使わない（結果へ写すのは `public_keys` のまま 3 つ。I4）。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `decide` | `auth_method` のキーが無い応答を受ける | 事前条件を破った呼び出し（`auth_status` は 3 つのキーがそろわない応答を `GwsError` で止めるため、サブコマンドの経路では起きない） | 検出しない（`status.get` が `None` を返す） | 既定値で続ける。`None` は `none` と等しくないため、`client_secret.json` の形でも認証済みになる | 無し（純粋な判断） | 後始末は無い。再実行してよい |
| 試走 (a)（`plugins/ndf-next/bin/ndf gws check`） | `ok` 以外が返る | gws が無い・未認証・応答を読めない | 結果 JSON の `status` | 止める。その行を記録の `passed` に載せず、原因を直してから打ち直す（I8） | サブコマンドの結果 JSON と終了コード（10・11・1・2・3） | 記録へは何も書かない。直した後に打ち直してよい |
| 試走 (b)（`plugins/ndf-next/bin/ndf gws thumbnails <ID> <出力のディレクトリ>`） | `ok` 以外が返る・`metrics.pages` と `p<番号>.png` の数が合わない | 読めないプレゼンテーション・取得の失敗・未認証 | 結果 JSON の `status` と出力のディレクトリのファイルの数 | 止める。`passed` に載せず、原因を直してから打ち直す（I8） | 同上 | 出力のディレクトリは作業ディレクトリの外の一時の置き場にし、確かめた後に消す。打ち直してよい |
| 試走の判定のコマンド（`python -m parity.trial.probe --write`） | 3 行のどれかが `passed` にならない | 本文に `.py` 付きの旧いスクリプトの呼び出しが残る（`failed`）・記録に id が無い（`not_run`） | 出力の行ごとの結果と `git diff scripts/ndf-next-parity/parity/features.json` | 止める。`failed` なら本文を、`not_run` なら記録の追記を直してから打ち直す | 判定の件数の出力と終了コード 0 | `features.json` は打ち直すたびに全行を書き直すため、打ち直してよい |
| 試走の判定のコマンド（2 回目） | 打ち直すと `features.json` に差分が出る | 手で `trial` の列を書いた・develop へ合わせる前の記録で書いた（I6） | `git diff --exit-code scripts/ndf-next-parity/parity/features.json` | 止める。develop へ合わせてから打ち直した結果を採る | `git diff` の差分 | 打ち直してよい |
| `ndf check --write-spec` | `env.md` を生成し直さずにコミットした | `env.json` だけを書き換えた | `plugins/ndf-next/bin/ndf check --base origin/develop` の規則 `spec-docs` | 止める。`--write-spec` を打ってからコミットし直す | 規則 `spec-docs` の違反と終了コード 1 | 打ち直してよい |

## 処理の手順

`decide` の判定表（`token_in_env` が偽のとき。真なら応答に依らず認証済み）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `decide` | `token_in_env` が真 | 真（I2） |
| `decide` | `credential_source` が `none`（`auth_method` に依らない） | 偽 |
| `decide` | `credential_source` が `client_secret.json`、`auth_method` が `none` | 偽（#2067 の形） |
| `decide` | `credential_source` が `client_secret.json`、`auth_method` が `oauth2` | 真（受け入れ条件 1 のログイン済みの形） |
| `decide` | `credential_source` が `environment_variables`（`auth_method` に依らない） | 真（要求の前提 7） |
| `decide` | `credential_source` が `token_env_var` | 真 |
| `decide` | `credential_source` が上のどれでもない値（例 `token_cache`） | 真 |

試走の手順（設計文書の「処理の流れ」の手順を、打つコマンドの粒度へ下ろしたもの）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| 試走 (a) | ログイン済みの実の gws の上で `plugins/ndf-next/bin/ndf gws check` を打ち、`ok`・0・`authenticated` | 記録の `runs` に 1 件を足す。`entrypoint:skills/google-workspace/scripts/gws-check.py` の id を `passed` に足す |
| 試走 (b) | `system-gdrive.md` の行の `ndf` を `plugins/ndf-next/bin/ndf` に置き換えて打ち、`ok`・0・ページ数と `p<番号>.png` の数が一致 | 記録の `runs` に 1 件を足す。`entrypoint:skills/google-workspace/scripts/slides-thumbnails.py` と `skill:document-systems` の id を `passed` に足す |
| 試走 (a)・(b) | どちらかが上の条件を満たさない | その行の id を `passed` に足さない（異常系の処理の行） |
| 試走の判定のコマンド | 作業のブランチを develop の最新へ合わせた後に `--write` を打つ | 3 行が `passed`・`record` が追記した記録。もう一度打って差分が無い |

- 繰り返しの終わる条件: 試走の判定のコマンドを打ち直して `features.json` に差分が出なくなったら終える
- 計算量の注意: 無し（`decide` は定数回の比較。試走の判定のコマンドは対応表の全行を 1 回ずつ読む今の振る舞いのまま）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `STATUS_KEYS` | 定数 | `plugins/ndf-next/src/ndf/domains/records/gws.py` | `("credential_source", "auth_method", "client_config_exists")`（変えない） |
| 未認証の 2 つの形（`credential_source` が `none`・`credential_source` が `client_secret.json` かつ `auth_method` が `none`） | 判定の式 | `decide` の中だけ。ほかのファイルに書き写さない（I3。設計文書の「テスト設計」の検索で縛る） | — |
| `GOOGLE_WORKSPACE_CLI_TOKEN` | 設定値（環境変数の名前） | `authenticated` の中と、環境変数の一覧 `plugins/ndf-next/contracts/domains/env.json`（変えない） | 無し（値は読まず有無だけを見る） |
| 試走の記録の実行の 1 件 | 型 | `scripts/ndf-next-parity/parity/trial/<日付>.json` の `runs` | 既存の要素と同じキー（`what`・`command`・`result`・`observed`）。`command` には本文の行と打ったコマンドを並べ、プレゼンテーションの ID と出力のディレクトリは `<プレゼンテーションの ID>`・`<出力のディレクトリ>` に置き換える |
| 試走の記録の通した行の id | 型 | 同じファイルの `passed` | 3 行の id を末尾へ追記する |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| この課題の実装の担当（計画 p8 のレーン ls1d）と試走 T2 #3411 のレーン | 最新の試走の記録（`trial/<日付>.json`） | 排他は取らない。どちらも追記だけにし、Pull Request の衝突は両方の追記を残して合わせる（設計文書の「処理の流れ」の末尾） | 追記の前に、記録の `passed` に同じ id が無いかを見る。あれば足さない |
| 同上 | `features.json` の `trial` の列 | 排他は取らない。手で合わせず、develop へ合わせた後に `--write` を打ち直した結果を採る（I6） | `--write` は全行を書き直すため、何度打っても同じ入力なら同じ結果になる |
| 同上 | ndf-next の本文（`system-gdrive.md`） | 排他は取らない。試走の判定は合わせた後の本文で打ち直す | 判定は本文を読むだけで、本文を書き換えない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `decide` | 無し（真偽を返すだけ） | — | — |
| `ndf gws check`・`ndf gws thumbnails`（変えない） | 結果 JSON の `metrics` の 3 つのキーと状態 | 結果 JSON | `gws auth status` のほかのキー・トークンの値（I4） |
| 試走 (a)・(b) | 打ったコマンドと結果の要点（`ok`・終了コード・ページ数と保存したファイルの数・`bin/ndf` の解決先） | 試走の記録の `runs` | プレゼンテーションの ID・Google のアカウント・ファイルの名前と中身（I7） |
| 試走の判定のコマンド | 行ごとの試走の結果（`result`・`reason`・`record`） | `features.json` の `trial` の列 | 無し（記録に秘密を書かないため、写す先にも出ない） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `test_logged_in_with_client_secret_is_authenticated` | `status_rule(source, method, client)`・`run_gws`・`fakes` | `grep -n "def status_rule" -A6 plugins/ndf-next/tests/domains/gws/__init__.py` | 使う。`status_rule("client_secret.json", "oauth2")` でログイン済みの応答を作れる |
| `test_gws_failure_when_logged_in_stops_with_1_not_11` | `status_rule`・`run_gws`・`_curl`・既存の `test_gws_failure_stops_with_1_and_shows_the_tail` の組み方 | `sed -n 75,100p plugins/ndf-next/tests/domains/gws/test_thumbnails.py` | 使う。既存のテストの `status_rule("token_cache")` をログイン済みの応答へ替えた形で書ける |
| 試走の記録の追記 | 既存の記録の `runs` の要素の形 | `python3 -c` で `trial/2026-11-16.json` の `runs` のキーを並べた（`what`・`command`・`result`・`observed`） | 使う。同じキーで書く |
| 機能の対応表の生成し直し | `parity/trial/probe.py` の `main --write`・`write_trials` | `sed -n 167,260p scripts/ndf-next-parity/parity/trial/probe.py` | 使う。`trial` の列だけを書き換え、ほかの列と並びを残す |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide`（`test_close.py` の `test_gws_client_secret_only_is_not_authenticated`） | `client_secret.json`・`oauth2` が真。`environment_variables`・`none` が真。`token_cache` が真（今の行） | `client_secret.json`・`none` が偽（今の行）。`none`・`oauth2` が偽。環境変数のトークンがあれば `none` でも真（今の行） | `auth_method` の無い応答の扱いはサブコマンドの経路で起きないため縛らない（異常系の処理の 1 行目） |
| `test_logged_in_with_client_secret_is_authenticated` | `client_secret.json`・`oauth2`・`client_config_exists` が真の応答で、`ok`・0・`authenticated` と 3 つのキーが返る（受け入れ条件 1） | — | 判定を `credential_source` だけで決める形に戻すと落ちる |
| `test_gws_failure_when_logged_in_stops_with_1_not_11` | — | — | ログイン済みの応答で gws のページの取得が失敗したとき、`stopped`・1・`gws-failed` を返し、11 を返さない（受け入れ条件 10）。サムネイルの側に判定を書き写して直し忘れると落ちる |
| `test_unauthenticated_is_gate_11`（変えない） | — | `none`・`none`、`client_secret.json`・`none` の 2 つの形で `gate`・11 のまま（受け入れ条件 2） | `client_secret.json` を一律に認証済みにする形で落ちる |
