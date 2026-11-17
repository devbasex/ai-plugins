# ndf-next: ai-plugins の値を既定から外して宣言で受け、ハードコードと選べる値の減少を CI で止める

## 概要

ndf-next（`plugins/ndf-next/`）の既定値・Skill の本文・同梱物は、ai-plugins だけの値を持たない。ai-plugins の値は
ai-plugins の `.ndf/` の宣言にだけ置き、ほかのリポジトリは宣言を書くか既定のまま使う。同じ混入が再び入ったら CI が落ち、
宣言で選べる値が黙って減っても CI が落ちる。

例: フォークの運用で `.ndf/worktree.json` に `"remote": "upstream"` を書いたリポジトリでは、ndf の push・照合・
差分の基準・PR の取得・既定ブランチの解決が `upstream` を使う。宣言が無ければ `origin` を使う。開発者が ndf-next の
`src/` の Python に `devbasex` を 1 行足した PR を出すと、ハードコードの検査が足したファイルと行と、足せば通る例外リストの
項目のファイル名を出して終了コード 1 で落ちる。

働く範囲:

| 範囲 | 働くもの |
| --- | --- |
| ndf-next を使う利用先のリポジトリすべて | リモート名・文書だけとみなすパス・配分の値の宣言、旧い NDF の登録を外す起動、利用者向けの副命令の一覧 |
| このリポジトリの CI だけ | ハードコードの検査・選べる値の検査・機能の対応表 |

今の NDF（`plugins/ndf/`）の振る舞いは変えない。

## 用語

「ハードコード」「設定で選べる値」「リモート名」「選べる値の基準」「機能の対応表」の定義は [用語集](../glossary.md) にある。
この文書は、`bin/ndf <名前>` の `<名前>` を「副命令」と書く。

## 背景

ndf-next を ai-plugins の中で作り ai-plugins でだけ試してきたため、リモート名 `origin` のハードコード、`.md` だけの
差分を文書と決める判定、ai-plugins の実測の配分、登録名 `ndf@ai-plugins`、`devbasex` の例、機能の対応表の同梱などが
汎用の振る舞いとして入っていた。ハードコードを見つける検査（`scripts/check-skill-repo-assumptions.py`）は Skill の一覧を
`manifests/*-skills.txt` から読むため、一覧を `manifests/skills.json` に持つ ndf-next の Skill を 1 本も走査して
いなかった。

デプロイごとに変わる値はコードから分けて置く（The Twelve-Factor App の Config）。リモート名の既定は git 自身が `origin`
とし、設定と引数で変えられる（`git clone` の `clone.defaultRemoteName`）。ndf-next はこの 2 つに揃える。

## 適用範囲

プロジェクトごとに違うものは、次の宣言で受ける。

| 宣言 | キー | 何を決めるか | 省いたとき |
| --- | --- | --- | --- |
| `.ndf/worktree.json` | `remote` | 取得・照合・差分の基準・送信に使う git のリモートの名前 | `origin`（スキーマの `default`） |
| `.ndf/project.json` | `docs.files`・`docs.behavior` | 文書だけの差分とみなすパスと、その中で振る舞いを決める文書のパス | `docs.files` は `**/*.md`、`docs.behavior` は無し |
| `.ndf/estimate.json` | `allocation_minutes` | cross-refactoring の配分の 1 件あたりの分（種類ごと） | 初期値のファイルの値 |

外す旧い NDF の登録は宣言で受けず、起動のときに導入済みの登録から読む。

代表のリポジトリでの振る舞い:

| 項目 | Web サービス（TypeScript・vitest か jest・develop→main・OpenAPI） | モバイルアプリ（Flutter か Swift） | 業務文書だけ | ai-plugins だけの事情 |
| --- | --- | --- | --- | --- |
| ハードコードの検査 | 何も起きない（このリポジトリの CI だけで働く） | 同左 | 同左 | ndf-next の Skill・`src/`・`contracts/` を走査し、例外リストの外で増えると CI が落ちる |
| リモート名 | 宣言が無ければ `origin`。フォークの運用で `upstream` を書けば取得・照合・差分の基準が `upstream` になる | 同左 | 同左 | 宣言を書かない（`origin` を使う） |
| 文書だけの差分 | 宣言が無ければ `.md` だけの差分で全体テストとリファクタリングを飛ばす。`openapi/*.yaml` を文書にするなら `docs.files` に書く | 宣言が無ければ `.md` だけで飛ばす | `.md` だけの差分が多く、宣言が無くても飛ばす。表計算を文書にするなら `docs.files` に足す | Skill とエージェントの本文（`.md`）は振る舞いなので `docs.behavior` に書いてあり、Skill の変更で全体テストを飛ばさない |
| 配分の値 | 履歴が無い間は初期値で配分する。合わなければ `.ndf/estimate.json` に書く | 同左（ビルドの長いテストなら初期値を上書きする） | cross-refactoring を回さないため何も起きない | 初期値の出所が ai-plugins の実測（#917）であることを、初期値のファイルが持つ |
| 旧い NDF の登録 | 旧い NDF を入れていなければ何も外さない。入れていればその登録名で外す | 同左 | 同左 | `ndf@ai-plugins` も導入済みの登録から見つけて外す |
| リリースの workflow の既定 | 影響しない（ai-plugins の workflow） | 同左 | 同左 | 宣言に `base_branch`・`production_branch` があるため結果は宣言のとおり。無ければ GitHub の既定ブランチ |
| `--admin` のマージ | 迂回の宣言と ruleset がそろわなければ迂回しない | 同左 | 同左 | 迂回の宣言があり、本番ブランチ宛ての PR は迂回でマージする |
| Skill の本文の表と例 | 汎用の表の行に「（ai-plugins）」が無く、例は `<owner>`・`<owner>/<repo>` で書かれる | 同左 | 同左 | — |
| 機能の対応表 | 導入先に対応表と注記が届かない | 同左 | 同左 | 対応表は `scripts/ndf-next-parity/` で開発に使い続ける |
| 開発の副命令 | `ndf --help` に `check`・`manifests` が出ない。hook の登録と起動は変わらない | 同左 | 同左 | CI は `bin/ndf check` を名前で打つ |

## 仕様

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| ハードコードの検査（`scripts/check-skill-repo-assumptions.py`） | ndf-next の形のプラグインの Skill・エージェント・`src/**/*.py`・`contracts/**/*.json`・`hooks/*.py` を走査し、追加の語を当てる |
| 例外リスト（`scripts/repo-assumptions-allow/`） | ndf-next の当たりを、他の課題の受け持ち（種類 `pending`・Issue の番号つき）と違反でないもの（種類 `not-a-violation`）として 1 項目 1 ファイルで持つ |
| 選べる値の検査（`scripts/check-ndf-choices.py`） | 選べる値の基準の集合ごとに出所から今の値を読み、基準と照らす |
| 選べる値の基準（`scripts/ndf-choices.json`） | 集合の出所・値・減らす例外を持つ |
| リモート名を決める部品（`adapters/git.py`） | 宣言の `remote`、無ければスキーマの既定を返す。ndf-next の各部品はリモート名をここから引く |
| テンプレートの差し込み（`contracts/templates/params.json`・`core/route`） | 差し込み `{{remote}}` を、省かれたときリモート名を決める部品から補う |
| 文書だけの判定（`progress/command.py`） | 文書だけの差分の判定を 1 つ持ち、工程の条件とテンプレート `check` の `assess` がこれを使う |
| 配分の値を決める部品（`domains/crossrefactor/allocation_decide.py`） | 初期値のファイルと宣言の `allocation_minutes` を重ねた配分の値を返す |
| 起動の引数を組む部品（`adapters/claude.py`） | `claude plugin list --json` の一覧から外す登録を決める |
| 副命令の表（`cli.py`） | 利用者向けの副命令と NDF 自身の開発の副命令を分け、使い方の表示には利用者向けだけを出す |
| 機能の対応表（`scripts/ndf-next-parity/`） | 対応表・その読み手・試走の判定・そのテストを、リリース対象の外に持つ |

読む向きは、根の `scripts/` の検査と機能の対応表から ndf-next への 1 方向だけである。ndf-next の部品は、検査・例外リスト・
選べる値の基準・機能の対応表を読まない。根の 2 つの検査は ndf-next のファイルを読むだけで、ndf-next のモジュールを import
しない（stdlib だけで動き、ndf-next の依存の環境を要らない）。

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | ndf-next の走査対象のハードコードは、（ファイル・語）ごとに例外リストの件数以下である | 終了コード 1。超えた組のファイル・行・語と、足す項目のファイル名を出す |
| I2 | 例外リストの項目は理由と 1 以上の件数を持ち、件数は実際の当たり数と等しい。種類 `pending` は Issue の番号を持つ | 終了コード 2 |
| I3 | `plugins/ndf/skills` と `plugins/playwright-kit/skills` の走査の対象・当たり・報告行は、ndf-next の走査を足しても変わらない | 単体テストが落ちる |
| I4 | 基準の各集合の値は、減らす例外に載った値を除いて、今のコントラクトにすべてある | 終了コード 1。集合・減った値と、`removed` へ足す 1 行の JSON を出す |
| I5 | 減らす例外は Issue の番号と理由を持ち、その値は今のコントラクトに無い | 終了コード 2 |
| I6 | 今のコントラクトの値は、基準の集合にすべてある | 終了コード 1。足した値と、基準へ足す行を出す |
| I7 | ndf-next の取得・照合・差分の基準・送信が使うリモート名は、宣言の `remote` か、無ければスキーマの既定である | 単体テストが落ちる |
| I7a | リモート名の既定の文字列は `worktree.schema.json` の `/properties/remote/default` の 1 か所だけにある | ほかの場所はハードコードの検査（語 `remote-origin`）が落とす。`default` が 0 か所か 2 か所以上なら終了コード 2 |
| I8 | 文書だけの差分は、変えたファイルが 1 件以上あり、すべて `docs.files` に当たり、`docs.behavior` に 1 件も当たらない差分である | 単体テストが落ちる |
| I8a | `assess` と工程の条件は I8 の 1 つの判定を呼び、判定を別に持たない | 単体テストが落ちる |
| I9 | 配分の値は、種類ごとに宣言の値、無ければ初期値のファイルの値である | 形の誤りは `AllocationError` |
| I10 | 外す登録は、導入済みで有効な登録のうち `@` の前が旧い NDF の名前で、プラグインの根の名前と違うものだけである | 単体テストが落ちる |
| I10a | 導入済みの登録の一覧が読めなければ、外す登録を空にして起動し、読めなかったことを結果に残す | 単体テストが落ちる |
| I11 | `ndf --help` の一覧に `check`・`manifests` が無く、名前で呼べば動く | 単体テストと CI の `bin/ndf check` が落ちる |
| I12 | リリース対象（`plugins/ndf-next/`）に機能の対応表のファイル・その注記・そのリンクが無い | ハードコードの検査（語 `parity-table`）が落ちる |

### ハードコードの検査

ndf-next の形のプラグインは、Skill の一覧を `manifests/skills.json` に持つプラグインである（`is_next_family`）。
`manifests/*-skills.txt` を持つ今の NDF は走査の対象も語も変わらない（I3）。ndf-next の形のプラグインには、今の語に
次の語を足して当てる。

| 語 | 当てる名前 | 当てる場所 |
| --- | --- | --- |
| `repo-name` | `ai-plugins` | Skill の本文・エージェント・`src/`・`contracts/`・`hooks/*.py` |
| `org-name` | `devbasex` | 同上 |
| `marketplace-entry` | `ndf@` | 同上 |
| `parity-table` | 機能の対応表への参照（`parity/features.json`・`機能の対応表`・`ndf-next-parity`、コードでは `ndf.parity` も） | 同上 |
| `remote-origin` | リモート名の文字列（`"origin"`・`origin/`・`remotes/origin`・`remote.origin`・`remote "origin"`） | `src/`・`contracts/`・`hooks/*.py` だけ |

- `remote-origin` は Skill の本文に当てない。本文の git の手順が書く `origin` は、この検査の範囲の外である
- `contracts/` の JSON は、鍵 `default` の値の中の `remote-origin` を走査しない。既定を書く正しい置き場を違反として数えない
  ためである。代わりに `default` の値の当たりを数え、`worktree.schema.json` の `/properties/remote/default` の 1 か所で
  なければ終了コード 2 で落とす（I7a）
- 終了コードは、走査の範囲の欠け（ndf-next の形なのに `src/` か `contracts/` が無い）・読めないファイル・既定の問題・例外リストの
  誤りを 2 として先に返し、それが無く宣言の外の当たりがあれば 1、どれも無ければ 0 である
- `--report` は、ndf-next の行に `src`・`contracts` の本数を足して出す

例外リストの当たりは次の規則で分ける。

| 当たり | 扱い |
| --- | --- |
| 他の課題が受け持つ場所（リリースの部品・CI の落ちたテストの読み方・ndf-next 全体の語の置き換え） | 種類 `pending`、受け持ちの Issue の番号を持つ |
| NDF 自身の公開の置き場（スキーマの `$schema`・`$id` の URL、初期値の出所の URL）、言語の表の正当な行、プラグイン自身の定義の読み手 | 種類 `not-a-violation`、理由を書く |
| それ以外 | 直す。例外リストへ載せない |

既存の検出ツール（detect-secrets・betterer・semgrep）は使わず、今の検査を広げた。今の検査がすでに（ファイル・語）ごとの件数・
理由・受け持ちの Issue の番号を持ち、ほかのツールは名前の語の一覧と件数と Issue の番号を持てないか、依存が大きいためである。
検査の引数・例外リストの項目の形は [ndf-repo-assumptions-check.md](ndf-repo-assumptions-check.md) にある。

### 選べる値の検査

`python3 scripts/check-ndf-choices.py [--root <根>]` は、基準（`scripts/ndf-choices.json`）の集合ごとに出所から今の値を読み、
基準と照らす。

| 集合 | 出所 | 値 |
| --- | --- | --- |
| `branch-flows` | `plugins/ndf-next/contracts/domains/branch-flows.json` の `rows[].id` | `merge-base-branch`・`release-branch` |
| `delivery-kind` | `plugins/ndf-next/contracts/schemas/ndf-settings/project.schema.json` の `/$defs/Delivery/properties/kind/enum` | `auto`・`manual` |
| `release-form` | `plugins/ndf-next/skills/release/references/form-*.md` のファイル名（拡張子と接頭辞 `form-` を外す） | `desktop`・`document`・`mobile`・`package-plugin`・`page`・`procedure`・`service`・`slide`・`spreadsheet` |

| 判定 | 結果 |
| --- | --- |
| 基準にあり、今に無く、`removed` に無い | 減った（I4）。終了コード 1 |
| 基準にあり、今に無く、`removed` にある | 通る |
| `removed` にあり、今にある | 誤った例外（I5）。終了コード 2 |
| 今にあり、基準に無い | 増えた（I6）。終了コード 1 |

- 2 は 1 より先に返す。基準の形の誤り（`removed` の行に Issue の番号か理由が無い・無い集合を指す）と、出所が読めない
  （ファイルが無い・JSON Pointer の先が列挙でない・glob が 0 件）も 2 である。0 件を「全部減った」と読まない
- 基準に無い値の増加も落とすのは、基準が知らない値が後で消えても検査が気づかないためである。値を足す PR は基準の `values`
  にも同じ PR で 1 行足す
- 値を減らす PR は、`removed` に `{"set", "value", "issue", "reason"}` を足す

### リモート名

- `git.remote_of(main_dir)` は `<main_dir>/.ndf/worktree.json` の `remote` を返す。宣言のファイルか `remote` が無ければ
  `git.default_remote()`（スキーマの `default` を 1 回だけ読む）を返す。値が文字列でない・空・`-` で始まる、または宣言が
  JSON として読めなければ `RemoteError`（`GitError` の子）を上げる
- `Git.remote()` は `git worktree list` の先頭をメインディレクトリとして `remote_of` を返す。linked worktree からでも
  メインディレクトリの宣言を読む
- リモート名の値は個人設定（`.ndf/worktree.local.json`）で上書きできない
- テンプレートの `push`・`base-sync`（`parts/steps.json`）と `check` の `assess` は `{{remote}}` を使う。`ndf route` は
  省かれた `remote` を `remote_of(<repo>)` で補い、宣言の誤りは `RouteError` で止まる。既定ブランチは
  `refs/remotes/<リモート名>/HEAD` から読む
- push・照合・差分の基準・PR の取得・既定ブランチ・hook のマージの宛先の照合・リポジトリの識別・計測は、すべて
  `Git.remote()` か `remote_of` から引く
- `adapters/git.py` はリモート名の文字列を持たず、`ndf.contracts`（`jsonschema` を読み込む）を読まない。hook の起動を延ばさない
  ためである
- `git.ORIGIN` はリリースの部品（`domains/release/gather.py`・`assemble.py`）のためにモジュールの `__getattr__` で残り、値は
  `default_remote()` である。この 2 ファイルは宣言の `remote` を読まず既定のリモート名を使う。リリースの部品を作り直す
  #3615 が `git.ORIGIN` を消す

### 文書だけの判定

`ndf progress docs-only --base <ベースブランチ> [--remote <リモート名>]` は、範囲の差分が文書だけかを結果 JSON で返す。

| 条件 | 結果 |
| --- | --- |
| 変えたファイルが 0 件 | `ok`（文書だけとしない） |
| すべて `docs.files` に当たり、`docs.behavior` に 1 件も当たらない | 止まった理由 `not_applicable`。テンプレート `check` の `on_cause` が `review` へ進める |
| 上のどちらでもない | `ok`（`test-all` へ進む） |
| 差分を読めない・ベースブランチが無い・宣言の `remote` が違う | 止まった理由 `precondition` |

- 判定は `command.range_diff_kinds` の `docs_only_diff` で、工程の条件と同じものを使う（I8a）
- `docs.behavior` の既定は空である。空の並びのとき `git diff` を打たない（打つと全体が返り、すべてが振る舞いの文書に当たる）
- 結果 JSON は範囲と件数（変えたファイル・文書・振る舞いの文書）だけを出し、ファイルの名前の一覧を出さない
- リリース承認ゲートの承認を次へ持ち越す判定（`progress/gate/answering.py` の `git_docs_only`）は、`docs.behavior` を宣言して
  いないリポジトリでは文書だけと見なさず、承認を出し直す。どの文書が振る舞いを決めるかが分からないまま持ち越すと、
  Skill の本文の変更が人の承認を経ずに通るためである
- ai-plugins は `.ndf/project.json` の `docs.behavior` に `**/skills/**/*.md`・`**/agents/*.md` を書く。今の NDF の既定と
  同じ値なので、今の NDF の判定は変わらない

### 配分の値

- 初期値のファイル（`skills/cross-refactoring/data/allocation-defaults.json`）は `"initial": true` と、上書きの置き場を説明する
  `summary` と、出所（#917 の実測）を持つ
- `allocation_decide.decide(defaults, declared)` は、初期値の `values` に宣言の `allocation_minutes` を種類ごとに重ねた分を返す。
  宣言が `None` か `allocation_minutes` が無ければ初期値のまま
- 初期値に `initial` が無い・`values` の分が 0 以下・宣言に初期値に無い種類がある、のどれかで `AllocationError` を上げる
- `.ndf/estimate.json` のスキーマは `allocation_minutes` の種類の名前を並べず、0 より大きい数だけを受ける。種類の名前は初期値の
  ファイルの `values` の鍵だけが持つ
- ndf-next の cross-refactoring の配分の表は、まだこの部品を呼ばない（呼び手は #3643）

### 旧い NDF の登録を外す起動

- `claude.list_plugins` は `claude plugin list --json` を 1 回打ち（時間切れ 30 秒、打ち直さない）、各要素が `id` と `enabled` を
  持つ並びを返す。`claude` が無い・終了コードが 0 でない・時間切れ・JSON でない・要素の形が違えば `None` を返す
- `claude.disabled_plugins` は、`enabled` が真で、`id` の `@` の前が旧い NDF の名前（`identity.FINAL_NAME`）で、プラグインの
  根の名前と違う登録を `{<id>: false}` で返す
- `claude.plugin_args` は外す登録があれば `--settings '{"enabledPlugins": {...}}'` を付け、空なら付けない。一覧が `None` なら
  外す登録を空にし、`notice` に理由を入れる
- 知らせは `ndf launch-args` の結果の `items` に `{"kind": "notice", "reason": "導入済みのプラグインの一覧を読めず、旧い NDF を外さずに起動する"}`
  として 1 件載り、`status` は `ok` のまま。worker は `worker run` の結果の `warnings`、Skill の工程は `StageResult.warnings`
  に載せる
- 一覧が読めなくても止めないのは、`claude` の CLI の版の違いだけで Skill を回す起動が全部止まるのを避けるためである。外さずに
  起動したときに起きるのは、旧い NDF が並んで読まれることである
- 一覧は文書化された CLI の出力を読み、導入先のファイル（`installed_plugins.json`）は読まない

### 副命令の一覧

| 副命令 | 一覧（`ndf --help`） | 理由 |
| --- | --- | --- |
| `check` | 出さない | 見る範囲は ndf-next 自身だけで、利用者のリポジトリで使わない |
| `manifests` | 出さない | `render` はプラグインの根のファイルを作り、利用者のリポジトリで使わない |
| `hooks` | 出す | `stats` は利用者の環境の hook の所要を出す |

隠すのは使い方の表示だけで（`cli.DEVELOPER_COMMANDS`）、名前で呼べば動く。CI と名前を入れ替えた一時コピーが `bin/ndf check`
を打つためである。

### 機能の対応表

- 対応表・読み手（`parity/table.py`）・試走の判定（`parity/trial/probe.py`）・テストは `scripts/ndf-next-parity/` にあり、
  リリース対象に無い。Skill とエージェントの冒頭の対応の注記も無い
- 試走の判定は `--plugin-root`（既定はリポジトリの根の `plugins/ndf-next`）を受け、置き場を決め打たない。根が無ければ終了コード 2
- 読み手が `jsonschema` と `ndf.domains.manifests` を import するため、テストは ndf-next の依存の環境で `ndf-next.yml` が流す。根の
  `pytest .` は `conftest.py` の `collect_ignore_glob` で収集から外し、`scripts/ci-heavy-skip.py` の `SELF_TESTED` に入れる
- `ndf check` の規則は対応表を読まない。子の表（`checks/children.json`）の `X3` の `packages` に `ndf.parity` は無い

### リリースの workflow

`.github/workflows/ndf-next-release.yml` は、`.ndf/worktree.json` に `base_branch`・`production_branch` が無いとき、
`github.event.repository.default_branch` を使う。スキーマの「未指定ならリモートの HEAD が指すブランチ」の GitHub 上の値である。

### Skill の本文

- `merged` は管理者の迂回（`--admin`）を使わない側を既定にし、`.ndf/project.json` の `production_merge.admin` の宣言と、本番ブランチの
  ruleset が承認を 1 以上求めることの両方がそろう本番ブランチ宛ての PR だけを迂回の対象として書く
- 汎用の表の行に「（ai-plugins）」を書かない。例は `<owner>`・`<owner>/<repo>` で書く

## データ・設定

| 置き場 | 内容 |
| --- | --- |
| `plugins/ndf-next/contracts/schemas/ndf-settings/worktree.schema.json` | `remote`（文字列。`minLength: 1`・`pattern: ^[^-]`・`default: "origin"`）。`base_branch`・`production_branch` の未指定時の説明は「remote のリモートの HEAD」 |
| `plugins/ndf-next/contracts/schemas/ndf-settings/estimate.schema.json` | `allocation_minutes`（オブジェクト。値は `exclusiveMinimum: 0` の数） |
| `plugins/ndf-next/contracts/schemas/ndf-settings/project.schema.json` | `docs.behavior` は省くと振る舞いの文書は無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | `worktree` の `keys` に `remote`、`estimate` の `keys` に `allocation_minutes` |
| `plugins/ndf-next/contracts/templates/params.json` | 差し込み `remote` |
| `scripts/ndf-choices.json` | `{"version": 1, "sets": [{"id", "source", "values"}], "removed": [{"set", "value", "issue", "reason"}]}`。`source` は `{"rows", "key"}`・`{"enum", "pointer"}`・`{"files", "prefix"}` のどれか |
| `plugins/ndf-next/src/ndf/checks/children.json` | `X4`: `{"issue": 3617, "packages": [], "needs": [], "order": 9, "lane": "lgeneric"}`。`S2` の `needs` に `X4` |
| `.ndf/project.json`（ai-plugins） | `docs.behavior`: `["**/skills/**/*.md", "**/agents/*.md"]` |

## 運用

| ジョブ | 打つもの |
| --- | --- |
| `runtime-plugin-validate.yml` | `python3 scripts/check-skill-repo-assumptions.py` |
| `ndf-next-checks.yml` | `python3 scripts/check-ndf-choices.py`。起動のパスに `scripts/ndf-next-parity/**`・`scripts/check-ndf-choices.py`・`scripts/ndf-choices.json` を持つ |
| `ndf-next.yml` | ndf-next のテストと `scripts/ndf-next-parity/tests`。起動のパスに `scripts/ndf-next-parity/**` を持つ |

- 選べる値の検査の新しいジョブは作らず、`ndf-next-checks` の 1 ステップにしている。新しいジョブには必須のチェックの宣言か
  ruleset の変更が要るためである
- ハードコードの検査の所要は、ndf-next を走査に足す前の実測で 0.56 秒である

## テスト観点

- `--report` が ndf-next の Skill・`src/`・`contracts/` について 0 でない件数を出し、Skill の数が `plugins/ndf-next/skills/` の直下の
  ディレクトリの数と一致すること
- 例外リストの外の語を 1 行足すと終了コード 1 で、そのファイルと行を出すこと。件数を 1 超えると 1、1 少ないと 2、理由の無い項目・
  Issue の番号の無い `pending` で 2 になること
- `plugins/ndf/skills`・`plugins/playwright-kit/skills` の件数と当たりが変わらないこと（I3）
- `description` の `"origin"` は当たり、`"default": "origin"` は当たらないこと。既定が 0 か所・2 か所・別のファイルで 2 になること
- 取り込み方の表から行を 1 つ外すと 1 で外れた値を出し、Issue の番号のある例外を足すと 0、番号の無い例外で 2 になること。
  `delivery` の種類とリリースの形でも同じであること。基準に無い値を足すと 1 になること
- 宣言に `upstream` を書いたリポジトリで push・照合・`assess` の差分の基準・PR の取得・既定ブランチが `upstream` を使い、宣言が
  無いと `origin` を使うこと。`remote` が空・`-` で始まる・数のとき `RemoteError` になること
- `docs.files` を書いたリポジトリで、すべて当たる差分は `not_applicable`、1 つ外れると `ok`、工程の条件も同じ結果になること。
  宣言が無いと Skill の `.md` だけの差分も `not_applicable` になること
- 宣言の `allocation_minutes` が初期値より先に効き、省いた種類は初期値になること。初期値に無い種類・`initial` の無い初期値で
  `AllocationError` になること
- 一覧の `ndf@<別の名前>`（有効）が外れ、自分の名前と無効の登録は外れず、旧い NDF が無ければ `--settings` を付けないこと。一覧が
  読めないと外さずに起動の引数を返し、知らせが 1 件載り `status` が `ok` であること
- `ndf --help` に `check`・`manifests` が無く、`ndf check` が同じ結果を返すこと。hook の登録と `hooks/entry.py` の起動が動くこと
- `plugins/ndf-next/src/ndf/parity/` が無く、移した先の対応表のテストが通ること
- 文言を照合するテストは書かない（`merged` の本文などはレビューで見る）

旧い NDF を別の登録名で入れた環境で `--settings` の `enabledPlugins` がその登録を外すことは、公式の文書に鍵の形の記述が無く、
単体テストでは確かめていない。リリース後テストで、起動の引数と読み込まれたプラグインを見て確かめる。`claude plugin list --json`
の所要時間も未測定である。

## 関連リンク

- [ndf-repo-assumptions-check.md](ndf-repo-assumptions-check.md) — ハードコードの検査の引数・例外リストの項目の形・CI のジョブ
- [ndf-design-representative-repos.md](ndf-design-representative-repos.md) — 代表のリポジトリの種類と、選べる値を減らす差分の扱い
- [ndf-next-declarations-and-io-contracts.md](ndf-next-declarations-and-io-contracts.md) — ndf-next の宣言と入出力の規約
- [ndf-next-ci-failed-tests-junit.md](ndf-next-ci-failed-tests-junit.md) — CI で落ちたテストの読み方（同じ点検の別の受け持ち）
- #3617 — 要求と受け入れ条件
- https://12factor.net/config
- https://git-scm.com/docs/git-clone
- https://code.claude.com/docs/en/plugins/cli-reference
