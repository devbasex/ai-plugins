# 配る構成の設定と件数の上限: 表を 1 項目 1 ファイルに分け、Python・uv・テストと CI の量を 1 か所の設定で検査する

NDF のコントラクト（`plugins/ndf/contracts/`）のうち同時に書き足される表は、1 項目 1 ファイルの**分割した表**で持つ。NDF が動く Python の下限と
uv のバージョンは 1 つのファイルに置き、CI が回す Python のバージョンとテスト・CI の件数の上限はリポジトリの設定（`.ndf/project.json`）に置く。
`ndf check` の 4 つの規則（`contracts-split`・`toolchain`・`budget`・`experimental`）が、設定と実物の食い違いを Pull Request で落とす。

例: 上限の 1 項目を変えたい Pull Request は `plugins/ndf/contracts/limits/test-run.test.json` だけを書き換える。別の項目を同時に変える
Pull Request と衝突しない。テストの関数が上限 6,000 を超える Pull Request は、`ndf check` が次の形の違反を出して落ちる。

```json
{"rule": "budget", "path": ".ndf/project.json", "line": 646, "detail": "tests 6001 件（上限 6000）。テストの関数・CI のジョブ・使われない依存・置き換えられる自作の件数が、リポジトリの設定の budget の上限を超えた"}
```

**規則の一覧と見る範囲は [`plugins/ndf/src/ndf/checks/README.md`](../../plugins/ndf/src/ndf/checks/README.md) が正である。** この文書は、
決定の理由、設定のコントラクト、常に成り立つ条件、テスト観点を持つ。テストの見本を本物の記述と突き合わせる仕組みは
[ndf-live-samples-verify.md](ndf-live-samples-verify.md) にある。

## 用語

定義は [用語集](../glossary.md) が正である。この文書で使う語は「分割した表」「テストと CI の予算」「安定版と実験版」である。

## 理由

| 困っていたこと | 今の形 |
| --- | --- |
| 上限の表（230 項目）と停止理由の表（36 項目）が 1 ファイルずつで、並行する Pull Request が同じファイルで衝突した | 1 項目 1 ファイル。変えた Pull Request の差分は 1 ファイルに収まる |
| Python の指定が `requires-python` 3.11・CI の `3.x`・`3.10`・`3.12`・hook の 3.9 に分かれ、CI の uv はバージョンの指定が無かった | `plugins/ndf/contracts/data/toolchain.json` が唯一の設定で、ほかの箇所は検査が突き合わせる |
| テストと CI のジョブの数に上限が無かった | `.ndf/project.json` の `budget` が上限を持つ |
| 依存を取れないとき、uv の出力のまま終了コード 1 で終わっていた | 結果 JSON と `deps_missing`（69）で終わる |
| 実験版の保存先が決まっていなかった | `experimental/ndf/`。参照は規則 `experimental` が落とす |

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 分割した表の形と読み手、規則 `contracts-split` | 分割した表に挙げていないコントラクトの表の分割 |
| 配る構成の設定とリポジトリの設定 `toolchain`、規則 `toolchain` | `.python-version`（作らない。手元で使う Python の指定は `requires-python` に従う） |
| テスト・CI のジョブ・使われない依存・ライブラリへ置き換えられる自作の件数の上限（設定 `budget`）と、それを調べる規則 `budget` | 前の件数より増やさない仕組み（[ラチェット](ndf-contract-fact-checks.md)）、CI でのテストの実行時間の計測（ai-plugins の `scripts/ci_test_durations.py`。#4331） |
| 実験版の保存先と規則 `experimental` | 実験版の台帳（[10 系の台帳](../archive/ndf-experiments.md) の後は未定） |
| `plugins/ndf/bin/ndf` の依存の用意 | `start.py` が呼び出し元の環境変数を戻す形（[ndf-entrypoint-caller-env.md](ndf-entrypoint-caller-env.md)） |

## 仕様

### 分割した表

| 表 | 保存先 | 項目の単位 | 件数（2026-11-24） |
| --- | --- | --- | ---: |
| `limits` | `plugins/ndf/contracts/limits/<方針の名前>.json` | 上限の方針 | 187 |
| `causes` | `plugins/ndf/contracts/causes/<停止理由>.json` | 停止理由 | 37 |

- 分ける表の一覧は `plugins/ndf/contracts/domains/split-tables.json` が持つ。この一覧自身は分けない
- **ファイル名（拡張子 `.json` を除く）が項目のキーである。** ファイルの中身は項目の値だけを持つ。表そのものの値（`owner`）は `_meta.json` に置く。
  `_` で始まるファイルは項目として読まない
- `causes` の項目は、読み手がファイル名を `kind` として足す（一覧の `key_field`）。`limits` は足さない
- 分ける前の 1 ファイル（`limits.json`・`causes.json`）は置かない

| 読み手 | 読むファイル |
| --- | --- |
| `contracts.load_split(rel)` | 表のディレクトリの全件。キーの並びはファイル名の昇順 |
| `contracts.split_keys(rel)` | ディレクトリの一覧だけ（項目は読まない） |
| `contracts.load_item(rel, key)`・`limits.policy`・`causes.row` | その項目のファイルだけ。1 プロセスに 1 回 |
| `causes.has` | ディレクトリの一覧だけ |
| `hooks/tables.py` の `policy`・`policy_value` | hook が使う関数。標準ライブラリだけで動き、渡された名前の方針のファイルだけを読む |

hook は 1 回の起動で読む項目のファイルを、PreToolUse と Stop で 6 つ以下、SessionStart で 20 以下にする。`budget_for` は予算の 1 項目だけを引き、
全件を読まない。

読み手は次を誤りにする（CLI は `config_broken` で 2、hook は `TableError`）。

- ディレクトリが無い・項目のファイルが JSON のオブジェクトでない・同じキーが 1 つのファイルに 2 つある
- `_` で始まらないのに拡張子が小文字の `.json` でないファイルがある
- 大文字と小文字だけが違うキーが 2 つある
- コントラクトのファイルがプラグインの外を指すリンクである、コントラクトの下にディレクトリのリンクがある

### 配る構成の設定とリポジトリの設定

NDF 自身が動く条件は配るコントラクトが持ち、そのリポジトリの CI が何で回すか・量の上限をいくつにするかはリポジトリの設定が持つ。
ai-plugins の値を利用先のリポジトリへ持ち込まないためである。

| キー | 置くファイル | 意味 |
| --- | --- | --- |
| `python.floor` | `contracts/data/toolchain.json` | NDF が動く Python の下限（3.11）。`requires-python` が許す最も古い正式リリースが `3.11.0` と同じであることを検査する |
| `python.hook_floor` | 同上 | 依存の環境が無いときに hook が動くシステムの python3 の下限（3.9）。`hooks/boot.py` が読む |
| `uv.version`・`uv.installer_sha256` | 同上 | ランチャーが入れる uv のバージョンとインストーラの sha256 |
| `toolchain.workflows` | `.ndf/project.json` | 検査が見るワークフローのグロブ |
| `toolchain.python_ci` | 同上 | `setup-python` の `python-version` に書いてよい値 |
| `toolchain.python_compat` | 同上 | 対応の下限を確かめる手順だけに書いてよい値（`versions`）と、書いてよいワークフロー（`workflows`） |
| `toolchain.plugin_pyprojects` | 同上 | `requires-python` と使われない依存を見る `pyproject.toml` |
| `toolchain.launchers` | 同上 | `UV_VERSION=`・`UV_INSTALLER_SHA256=` を突き合わせるランチャー |
| `budget.tests`・`ci_jobs`・`unused_deps`・`replaceable` | 同上 | 件数の上限。キーが無い項目は上限と比べない |
| `budget.dep_modules`・`dep_exempt` | 同上 | 依存の名前と import の名前の対応、import に現れない依存の例外と理由 |

設定のキーごとに独立して効く。`toolchain` も `budget` も無いリポジトリでは、規則 `toolchain` は何も読まず、規則 `budget` は件数を
`metrics.budget` に出すだけである。設定の値は `.ndf/project.json` と `plugins/ndf/contracts/data/toolchain.json` が正で、ここにはコピーしない。

**テストの件数の上限 `budget.tests` は NDF の機能として残す。** 実行時間を測る仕組みを持たないリポジトリが、量の上限を置く手段だからである。
ai-plugins はテストの量を件数でなく CI の実行時間で見るので、`.ndf/project.json` に `budget.tests` を置かない（#4331 の決定 5）。
テストの件数は今と同じく `metrics.budget.tests` に出るが、Pull Request は件数で落ちない。

`main` への push で起動するジョブ `pytest (所要)` は、テストケースごとの実行秒数の合計に上限を設けて警告する。CI の開始から終了までの経過時間は比べない。
各回の集計は、その回のテストケースの実行秒数を合計した値で、並列に実行した分割テストの秒数も合算する。設定した回数分の集計の中央値が上限を超えると警告する。
直前の実行からの増加は見ないので、上限以下での増加は警告しない。

このジョブは、すべての push で動くわけではない。`main` への push で `Python tests` ワークフローが起動するのは、`.github/workflows/pytest.yml` の
`on.push.paths` に当たるファイルを変えたときだけである。`docs/` 配下の文書だけを変更した push では、ワークフローは起動せず、集計も残らない。
`plugins/` 配下の文書（`plugins/ndf/README.md` など）は起動対象に含まれる。ワークフローが起動しても、6 つの分割テスト（`pytest (0/6)`〜`pytest (5/6)`）と
NDF の ok 出力の検査（`pytest (NDF の ok の出力)`）がすべて成功しなければ、`pytest (所要)` は実行されない。

実行されると、今回の集計を作って判定した後に、今回の集計を出力ファイルへ書く。判定は次の順に行う（`scripts/ci_test_durations.py` の `cmd_trend`）。

1. 今回の集計が無効な場合は、過去の集計を検索せず、上限との比較も行わない。テスト結果の欠落や古い試行の成果物など、集計を無効とした理由を警告に表示する
2. 今回の集計が有効なら、`main` への push で成功した過去の実行を、実行番号の降順に調べて有効な集計を探す。
   再実行した実行では、集計の `attempt` が実行の最新の試行番号（`run_attempt`）と一致するものだけを採用する
3. 今回を含めて、設定ファイル `scripts/ci-test-duration.json` の `runs` 回分がそろえば、その中央値を同じファイルの上限 `limit_seconds` と比べ、超えた場合に警告を表示する
4. 回数が足りない場合は判定を保留し、そろった回数と探索を打ち切った理由を注記に表示する

判定の後に今回の集計を書くのは、有効でも無効でも同じである。書いた集計は成果物として保存され、後の実行が過去の集計として読む。

過去の集計の探索は、必要な回数がそろう前でも、次のどれかに当たると打ち切る（`past_totals`）。

| 打ち切る条件 | 中身 |
| --- | --- |
| 保存期間 | 次に調べる実行が、成果物の保存期間（`RETENTION_DAYS`）より前に作られた |
| ページの上限 | 実行の一覧と成果物の一覧を合わせて、`PAGE_LIMIT` ページを取得した |
| ZIP の取得の上限 | 集計の ZIP を、必要な過去の回数の 2 倍まで取得した |
| 時間の上限 | ジョブの最初の手順で記録した時刻（`CI_TEST_DURATION_STARTED`）から、ジョブの持ち時間（`JOB_BUDGET`）から成果物の保存に残す時間（`UPLOAD_RESERVE`）を引いた秒数が過ぎた |
| 候補が尽きた・一覧を取れない | 実行の一覧を最後まで調べた、または一覧の取得に失敗した |

上限を超えたという警告が出ないことは、上限以下と判定したことを意味しない。判定を保留した回と今回の集計が無効だった回にも、その警告は出ない。

### 規則 `toolchain`

次を違反にする。

- `workflows` に当たるファイルの `setup-python` に `python-version` が無い、または `python_ci` に無い値を書く。
  `python_compat.versions` の値は `python_compat.workflows` に当たるファイルだけに書ける
- `setup-uv` に `version` が無い、または設定のバージョンと違う
- `run` のコマンドが `setup-uv` 以外で uv を入れる（`pip`・`pipx` の `install uv`、`curl`・`wget` での `astral.sh/uv` の取得）。`run` の本文は
  bash の構文木にし、制御構造と `$(…)` の中のコマンドを見て、ヒアドキュメントの本文・コメント・`echo` の引数は見ない
- `plugin_pyprojects` の `requires-python` が許す最も古い Python 3 の正式リリースが下限の `.0` と違う（PEP 440 の条件としてパッチまで評価する）
- `launchers` の `UV_VERSION=` か `UV_INSTALLER_SHA256=` が無い、または設定と違う

### 規則 `budget`

| 数えるもの | 数え方 |
| --- | --- |
| `tests` | テストのファイル（`contracts/domains/test-files.json` の見分け）の、最上位とクラスの中の `test_` で始まる関数の定義 |
| `ci_jobs` | `toolchain.workflows` に当たるファイルの `jobs` のキー（matrix の展開は数えない） |
| `unused_deps` | `pyproject.toml` の直接の実行時の依存のうち、隣の `uv.lock` にあり、同じディレクトリの `src/` に import が無く、`dep_exempt` にも無いもの |
| `replaceable` | 置き換えの規則（`contracts/replaceable/`。1 規則 1 ファイル）に当たる製品のコードの箇所。規則の `exempt` のファイルは数えない |

置き換えの規則は 3 つで、`fcntl.flock` の直接の呼び出し（`filelock`）、テーブルの列の `" | "` の join（`tabulate`）、sleep を持つ繰り返しの
打ち直し（`tenacity`）である。件数は違反の有無にかかわらず結果 JSON の `metrics.budget` に出る。`--only budget-ci-jobs` は CI のジョブの数だけを比べ、
プラグインのファイルを読まない。

### 規則 `experimental`

実験版は `experimental/ndf/`（リポジトリの根。`contracts/domains/locations.json` の項目 `experimental`）に置き、
`python3 experimental/ndf/<名前>.py` で呼ぶ。Claude Code も Codex も `plugins/ndf/` を丸ごと配るため、その外に置けば配る仕組みに除外を足さずに済む。
プラグインの根の下で検査が集めたファイルの本文に保存先のパスが現れれば違反にする。`README.md`・`CHANGELOG.md`・`docs/` と、
保存先の表の該当の値、規則の実装とそのテストは数えない。

### 規則の当て方

- 4 つの規則は既にある必須チェック `script-structure-check` の `ndf check` の中で走る。必須チェックは増えない
- 差分が NDF の検査の入力に当たらずワークフローか設計レビューの項目の表に当たる Pull Request では、`ndf check` を 1 回だけ打つ。
  ワークフローに当たれば `--only toolchain --only budget-ci-jobs`、表に当たれば `--only review-items` を並べる
- 途中の規則が止まっても、止まる前の指摘は結果 JSON の `items` に残り、`config_broken`（2）で終わる

### 依存の用意（`plugins/ndf/bin/ndf`）

`exec` の前に `uv sync --frozen --no-dev --inexact` を 1 回打ち、`exec uv run --no-sync` へ渡す。同期の経路は 1 起動に 1 回である。
`--inexact` は lock に無い追加のパッケージを消さないために付ける。`UV_NO_SYNC` が真（`1`・`true`・`yes`・`y`・`on`・`t`、大文字小文字を問わない）なら
同期を飛ばす。

| 起きたこと | 結果 |
| --- | --- |
| uv が無く、入れられない | 結果 JSON（`stopped`・`deps_missing`）と終了コード 69 |
| 同期に失敗した（ネットワークが無い・相手の障害） | 同上。`summary` に uv の `error:` を載せる |
| 同期できたが `start.py` が依存の欠けを見つけた | 同上（[終了コードの表](../../plugins/ndf/docs/contracts/exit-codes.md)） |

## 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 一覧に挙げた表は 1 項目 1 ファイルで、分ける前の 1 ファイルが無い | `contracts-split` が落ちる |
| I2 | `toolchain.json` と、`requires-python`・ワークフロー・ランチャーの指定が一致する | `toolchain` が落ちる |
| I3 | 4 つの件数が `budget` の上限を超えない | `budget` が落ちる |
| I4 | 既定で動くものが実験版の保存先を参照しない | `experimental` が落ちる |
| I5 | 依存を用意できないとき、結果 JSON と `deps_missing`（69）で終わる | `tests/bin/test_start.py` が落ちる |

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 表を項目ごとのファイルに分ける。分けるのは一覧に挙げた表だけ | 区分ごとに 20〜30 ファイルへ分ける形では、同じ区分の別の項目を変える Pull Request どうしの衝突が残る。すべての表を分けるとファイルが 600 を超える |
| 設定を配るものと配らないものに分ける | 利用先のリポジトリへ ai-plugins の CI のバージョンと上限を持ち込まないため |
| 使われない依存を `ndf check` の中で数える | deptry を使うと、例外の設定が 2 か所に分かれる |
| `exec` の前に同期を 1 回打つ | `exec` の後に uv が落ちると、結果 JSON を出せない。同期済みなら 1 回 15 ミリ秒（2026-11-23 の実測） |
| 上限に現在の件数の余裕を置くのは `tests` と `ci_jobs` だけ | 使われない依存と置き換えられる自作は増やしてよいものではない。減らす作業は #4330 が持つ |
| ai-plugins の設定から `budget.tests` を外し、規則は残す（#4331） | 件数の上限は機能を足すたびに上げ直しになり、遅いテストも見つけられない。規則は設定にキーがあるときだけ効くので、残しても使わないリポジトリに影響しない |

## 運用

- uv のバージョンを上げる Pull Request は、`plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py` の `UV_VERSION` に加えて
  `plugins/ndf/contracts/data/toolchain.json` とワークフローの `setup-uv` の `version` も直す。Renovate の Pull Request が後の 2 つを
  直さない件は #4322 が持つ

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| 分割した表の読みが 1 ファイルの時と同じ結果になる・誤りの形 | `plugins/ndf/tests/contracts/test_split.py`・`test_causes.py`・`test_limits.py` |
| 分割の形の検査 | `plugins/ndf/tests/checks/test_contracts_split.py` |
| hook の 1 起動で読む項目の数 | `plugins/ndf/tests/domains/hooks/` の`test_budget.py` |
| 指定の食い違いの検査 | `plugins/ndf/tests/checks/test_toolchain.py`・`tests/contracts/test_toolchain.py` |
| 件数の数え方と上限 | `plugins/ndf/tests/checks/test_budget.py` |
| 実験版の参照の検査 | `plugins/ndf/tests/checks/test_experimental.py` |
| 依存を用意できないときの結果 JSON と 69 | `plugins/ndf/tests/bin/test_start.py` |

## 関連リンク

- [issue #3687](https://github.com/devbasex/ai-plugins/issues/3687)
- [ndf-limits-declaration-and-retry-policy.md](ndf-limits-declaration-and-retry-policy.md) — 上限の値の引き方
- [ndf-deps-missing-exit-code.md](ndf-deps-missing-exit-code.md) — 終了コード 69 を選んだ理由
- [dependency-updates-renovate-and-supply-chain.md](dependency-updates-renovate-and-supply-chain.md) — 依存の更新
