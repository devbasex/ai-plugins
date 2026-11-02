# リリース後テスト: 利用者の設定ディレクトリのプラグインを変えずに確かめ、変わったら止まる（#2401）

## 目的

- **今どうなっているか**: リリース後テストの worker が、導入の確認のスクリプトとは別に手で導入を試した。その操作で、利用者の Claude Code に入っていた ndf が 10.27.7 から 10.27.10 へ黙って上がった（2026-11-02 15:30 JST）。手順書にも起動指示にも禁止が無く、変わったことを機械で知る仕組みも無い
- **誰に効くか**: NDF のスプリントを回す利用者（同じマシンで別の開発ループを動かしている利用者を含む）
- **変えると何が成り立つか**: worker は利用者の設定ディレクトリで導入の操作を打たない。打たれて変わったら、まとめのプランが振り返りへ進まずに止まり、変わったプラグインと前後の版を人へ示す

## 適用範囲

- **働く範囲**: NDF を使うどのリポジトリでも働く。変えるのは 3 つである。リリース後テストの手順書（`release-verification` の Skill）・まとめのプランのリリース後テストのステップ・導入の確認のスクリプト（`release-verification-steps.py`）
- **プロジェクトごとに違うもの**: 無し。利用者の設定ディレクトリの場所は環境変数（`CLAUDE_CONFIG_DIR`・`CODEX_HOME`）と既定の場所（`~/.claude`・`~/.codex`）から決め、設定も引数も足さない
- **当たるモード**: リリース後テストを通るすべてのモード（まとめのプランの前後の検査は、まとめのプランがリリース後テストのステップを組むときだけ）

## あるべき姿の根拠

| 根拠 | 種類 | 何を示すか |
| --- | --- | --- |
| https://github.com/devbasex/ai-plugins/pull/2395#issuecomment-6570809748 の「補足」 | 実測 | 手で導入を試したことで利用者の設定ディレクトリの ndf の版が変わった。導入の確認のスクリプト（`env -i` で隔離して動く）だけなら変わらなかった |
| `$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json` と `known_marketplaces.json` の中身（2026-11-02 に読んだ） | 実測 | Claude Code はプラグインごとに範囲・版・コミットを、取得元ごとに取得元の場所を、この 2 つのファイルに持つ。時刻（`lastUpdated`）は自動の更新で変わる |
| 一時ディレクトリを `CODEX_HOME` にして `codex plugin marketplace add devbasex/ai-plugins --ref develop` と `codex plugin add ndf@ai-plugins` を打った結果（codex-cli 0.166.0、2026-11-02） | 実測 | Codex は取得元を `config.toml` の `[marketplaces.<名前>]` に、導入を `[plugins."<プラグイン>@<取得元>"]` に書き、版は `plugins/cache/<取得元>/<プラグイン>/<版>/` のディレクトリで持つ |

要求と受け入れ条件は #2401 の本文にある（コピーは [issues/issue-2401-requirements.md](issue-2401-requirements.md) ）。この文書は「どう作るか」だけを扱う。

## ドメインモデル

### コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| リリース（`ndf-release`） | 利用者の設定ディレクトリ・導入の操作・利用者の設定の控え・リリース後テスト |
| 開発の工程（`ndf-workflow`） | まとめのプラン・ステップ・止まる（人へ戻す） |

関係は公開された言語。まとめのプランは導入の確認のスクリプトを run のステップとして打つ。読むのは結果 JSON（`plugins/ndf/scripts/lib/README.md` の「手順のスクリプトの結果」）の終了コードと `summary` だけで、控えの中身は読まない。

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 利用者の設定ディレクトリ | 利用者だけ。NDF は読むだけで書かない | 設定ディレクトリ（Claude Code・Codex の 2 つ） | 導入済みのプラグイン（名前 `<プラグイン>@<取得元>` で追う）・取得元の登録（取得元の名前で追う） | 版・コミット・範囲・取得元の場所 |
| 利用者の設定の控え | `release-verification-steps.py`（`user-config-save` が書き、`user-config-check` が読む） | 控え（1 回のまとめのプランの実行に 1 つ） | — | 控えの項目（鍵と値の組。鍵の形は「入出力の契約」） |
| まとめのプラン | `supervise_lib`（`close_plan` が組む） | まとめのプラン | ステップ | 遷移（`next`・`on_fail`・`on_exit`） |

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 利用者の設定ディレクトリ | リリース後テストのステップの前後で、導入済みのプラグインの版・コミット・範囲と、取得元の登録の場所が変わらない | まとめのプランが振り返りへ進まずに止まり、変わった項目と前後の値を理由に載せる |
| I2 | 利用者の設定の控え | 控えと結果 JSON は、プラグインの名前・版・コミット・範囲と取得元の名前・場所だけを持つ。認証情報のファイル（Codex の `auth.json` など）を読まず、取得元の URL の利用者の情報（`user:pass@`）は伏せる | 実装のテストで落とす |
| I3 | 利用者の設定の控え | 控えが取れない・控えが読めない・設定のファイルが読めないとき、`user-config-save` と `user-config-check` は `ok` を返さない | 終了コード 2 で止まり、まとめのプランが止まる |
| I4 | まとめのプラン | リリース後テストのステップを持つまとめのプランは、その直前に控えを取るステップを、直後に比べるステップを持つ。リリース後テストのステップが失敗しても比べる | まとめのプランを組むテストで落とす |
| I5 | まとめのプラン | 比べるステップが `ok` でなければ、振り返りのステップへ進まない | まとめのプランを組むテストで落とす |
| I6 | 利用者の設定の控え | 導入の確認（`verify-install`）の間に利用者の設定ディレクトリのプラグインか取得元の登録が変わると、`metrics.user_env_unchanged` は false、`status` は `ok` でない | 終了コード 1。今の判定（利用者の rc ファイル）と同じ扱い |
| I7 | 利用者の設定の控え | 利用者の設定ディレクトリが変わらなければ、`verify-install` の結果の形・終了コード・`status` は今と同じ | 既存のテストで落とす |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | リリース後テストのステップが利用者の設定ディレクトリの状態を控えた | 控えを取るステップ（`user-config-save`） | 比べるステップ（控えのファイルを読む） |
| E2 | worker が `verify-install` で導入を確かめた | リリース後テストの worker | worker の記録の表（導入の確認の行） |
| E3 | `verify-install` が利用者の設定ディレクトリの前後を比べた | `verify-install` | worker（`status` と `items` の `kind: user_env`） |
| E4 | worker が受け入れ条件ごとに確かめた | リリース後テストの worker | worker の記録の表 |
| E5 | worker が利用者の設定ディレクトリへの導入が要る条件を保留にした | リリース後テストの worker | 記録の表の保留の行と、作業の報告の「判断が要る」（まとめのプランが失敗として受け、比べるステップへ回す） |
| E6 | worker がリリース後テストの記録を配布の記録の PR へ書いた | リリース後テストの worker | まとめのプランの close のステップ（今と同じ） |
| E7 | ステップが利用者の設定ディレクトリの今の状態を控えと比べた | 比べるステップ（`user-config-check`） | まとめのプランの遷移（`ok` なら振り返りへ、ほかは止まる） |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 利用者の設定ディレクトリ | 利用者の Claude Code の設定ディレクトリ（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）と Codex の設定ディレクトリ（`CODEX_HOME`、無ければ `~/.codex`） | 追加（`ndf-release`。要求の工程で足した行のまま） |
| 導入の操作 | プラグインの導入・更新と、取得元の登録・更新 | 追加（`ndf-release`。要求の工程で足した行のまま） |
| 利用者の設定の控え | 利用者の設定ディレクトリの、導入済みのプラグインと取得元の登録を鍵と値の組で書き出したもの。リリース後テストの前に取り、後で今の状態と比べる | 追加（`ndf-release`） |

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | 手順書と起動指示が、導入の確かめを `verify-install` に限り、利用者の設定ディレクトリでの導入の操作を禁じる | リリース後テストの worker |
| F2 | 利用者の設定ディレクトリへの導入が要る受け入れ条件を、導入せずに保留にして人へ戻す | リリース後テストの worker |
| F3 | リリース後テストのステップの前に利用者の設定の控えを取り、後で比べ、変わっていたら止まる | まとめのプラン（結果を読むのは利用者） |
| F4 | `verify-install` が、導入の前後で利用者の設定ディレクトリのプラグインと取得元の登録も比べる | リリース後テストの worker と、リリースのプランの導入の確かめのステップ |

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `release-verification` の Skill（`SKILL.md`） | 手順 1 に節「利用者の設定ディレクトリで導入しない」を置き、禁止・保留の書き方・「判断が要る」での返し方の正本になる。プランの外で使うときに控えを取って比べる手順も持つ |
| `release-verification-steps.py` の利用者の設定の読み取り（`user_config_snapshot`） | 利用者の設定ディレクトリを読むだけで、控えの項目（鍵と値の組）と読めなかったファイルの一覧を返す |
| `release-verification-steps.py` の `user_env_snapshot` | 今の rc ファイルとリンクの項目（`.bashrc`・`.zshrc`・`.local/bin/claude`）に、`user_config_snapshot` の項目を足して返す |
| `release-verification-steps.py` の `_env_items` | 前後の鍵の和集合で差分を作る（片方にしか無い鍵も `changed` にする） |
| `release-verification-steps.py` の副命令 `user-config-save` | 控えを `--out` のファイルへ書き、結果 JSON を出す |
| `release-verification-steps.py` の副命令 `user-config-check` | `--before` の控えと今の状態を比べ、結果 JSON を出す |
| `supervise_lib/release_templates.py` の `release_verify_step` | 起動指示に、禁止・保留の要点と Skill の節への参照（定数 `NO_USER_INSTALL`）を入れる |
| `supervise_lib/release_templates.py` の `user_config_steps` | 控えを取るステップ（`config-save`）・比べるステップ（`config-check`）・失敗のときに比べるステップ（`config-check-on-fail`）を組む |
| `supervise_lib/sprint.py` の `close_plan` | リリース後テストのステップを組むとき、マージの後を `config-save` へ、リリース後テストの後を `config-check` へつなぐ |

```mermaid
graph TB
  subgraph plan["まとめのプラン（supervise_lib）"]
    CP["close_plan"]
    UCS["user_config_steps"]
    RVS["release_verify_step"]
  end
  subgraph script["release-verification-steps.py"]
    SAVE["user-config-save"]
    CHECK["user-config-check"]
    VI["verify-install"]
    SNAP["user_config_snapshot"]
  end
  SKILL["release-verification の Skill"]
  DIR[("利用者の設定ディレクトリ")]
  CP --> UCS
  CP --> RVS
  UCS -->|打つ| SAVE
  UCS -->|打つ| CHECK
  RVS -->|起動指示が参照| SKILL
  SKILL -->|手順 1 で打つ| VI
  SAVE --> SNAP
  CHECK --> SNAP
  VI --> SNAP
  SNAP -->|読むだけ| DIR
```

## 構造

型は変えない（`VerifyContext` はそのまま）。変える関数と、それを持つモジュールだけを描く。

```mermaid
classDiagram
  class release_verification_steps {
    <<module>>
    +user_config_snapshot() Snapshot
    +user_env_snapshot() dict
    -_env_items(before, after) list
    +cmd_user_config_save(a)
    +cmd_user_config_check(a)
    +cmd_verify_install(a)
  }
  class Snapshot {
    <<tuple>>
    items: dict
    unreadable: list
  }
  class release_templates {
    <<module>>
    +NO_USER_INSTALL: str
    +release_verify_step(sprint, refs, record_pr) dict
    +user_config_steps() list
  }
  class sprint {
    <<module>>
    +close_plan(a, repo) dict
  }
  release_verification_steps ..> Snapshot
  sprint ..> release_templates
```

`Snapshot` は `user_config_snapshot` の戻り値の組（`NamedTuple`）である。`user_env_snapshot` は引数を持たず `dict` を返す今の形を保つ（既存のテストがこの形を差し替えて使う。I7）。

## 入出力の契約

### 控えの項目の鍵と値

| 鍵 | 値 | 読む元 |
| --- | --- | --- |
| `claude:plugin:<プラグイン>@<取得元>` | 導入の行ごとの `<範囲>:<版>@<コミットの先頭 8 文字>` を並べて空白でつないだもの。範囲が `user` 以外なら範囲の後に `(<projectPath>)` を付ける。コミットが無ければ `@…` を省く | Claude Code の設定ディレクトリの `plugins/installed_plugins.json` の `plugins` |
| `claude:marketplace:<取得元>` | `source` の値を鍵の順に並べた JSON（URL の利用者の情報を伏せる） | 同じく `plugins/known_marketplaces.json` |
| `codex:plugin:<プラグイン>@<取得元>` | `enabled=<true|false|->` と、版のディレクトリの名前を並べた `versions=<版>,<版>` | Codex の設定ディレクトリの `config.toml` の `[plugins."…"]` と、`plugins/cache/<取得元>/<プラグイン>/` のディレクトリ。片方にしか無いプラグインも鍵にする |
| `codex:marketplace:<取得元>` | `source_type`・`source`・`ref` を鍵の順に並べた JSON（URL の利用者の情報を伏せる） | 同じく `config.toml` の `[marketplaces.<取得元>]` |

- 時刻（`installedAt`・`lastUpdated`）・置き場所（`installPath`・`installLocation`）・自動の更新の設定（`autoUpdate`）は値に入れない
- 設定ディレクトリかファイルが無いときは、その鍵を作らない（導入していない状態であり、読めないこととは分ける）
- ファイルはあるが読めない・形が壊れているときは、`unreadable` に `<ファイルのパス>: <理由>` を 1 件足す。Python 3.10（`tomllib` が無い）で Codex の `config.toml` があるときも読めないとする
- `config.toml` は `marketplaces` と `plugins` の表だけを値に使い、ほかの表を控えへ写さない

### 副命令

```text
release-verification-steps.py user-config-save --out <控えのファイル>
release-verification-steps.py user-config-check --before <控えのファイル>
```

| 副命令 | 結果 | 終了コード | `items` | `metrics` |
| --- | --- | --- | --- | --- |
| `user-config-save` | 控えを書けた | 0（`ok`） | 無し | `keys`（項目の数）・`claude_config_dir`・`codex_home`・`out` |
| `user-config-save` | 設定のファイルが読めない・控えを書けない | 2（`stopped`） | `kind: unreadable` の項目（`name` はファイルのパス、`reason`） | 同上 |
| `user-config-check` | 変わっていない | 0（`ok`） | 無し | `unchanged: true`・`changed: 0`・`keys`・両方のディレクトリ |
| `user-config-check` | 変わった | 1（`stopped`） | 変わった鍵ごとに `kind: user_env`・`name`（鍵）・`result: changed`・`before`・`after`（無い側は `null`） | `unchanged: false`・`changed`（件数） |
| `user-config-check` | 控えが無い・読めない、または今の設定のファイルが読めない | 2（`stopped`） | `kind: unreadable` の項目 | `unchanged` を置かない |

`summary` は、変わったときに `利用者の設定ディレクトリが変わった（<件数> 件: <鍵>: <前> → <後>, …）` の形で、先頭の 3 件までを載せる。プランが止まった理由にこの `summary` が写る（I1）。`tool` は `release-verification` のまま。

### `verify-install` の結果

`items` に `kind: user_env` の項目として、上の鍵が加わる（今の `.bashrc`・`.zshrc`・`.local/bin/claude` と並ぶ）。`metrics.user_env_unchanged` はこれらも含めた前後の一致になる。利用者の設定ディレクトリが変わらなければ、結果の形は今と同じ（I7）。読めないファイルは鍵 `<claude|codex>:unreadable` の値として前後で比べ、`verify-install` 自身は止めない（決定 6）。

### まとめのプランのステップ

| id | type | コマンド・指示 | 遷移 |
| --- | --- | --- | --- |
| `config-save` | run | `{VERIFY_PY} user-config-save --out {state_dir}/user-config-before.json` | 0 → `release-verify`。ほか → 止まる（`on_fail` を持たない） |
| `release-verify` | work | 今の起動指示に `NO_USER_INSTALL` を足したもの | 完了 → `config-check`。失敗（「判断が要る」「できなかった」を含む）→ `on_fail: config-check-on-fail` |
| `config-check` | run | `{VERIFY_PY} user-config-check --before {state_dir}/user-config-before.json` | 0 → `retro`。ほか → 止まる |
| `config-check-on-fail` | run | `config-check` と同じ | `on_exit: {"0": "stop"}`（変わっていなくても止まる）。ほか → 止まる |

3 つの run のステップは `stage` を持たない（「リリース後テスト」と記録するのは今どおり `release-verify` だけ）。

### 起動指示に足す文（`NO_USER_INSTALL`）

> 導入の確かめは `verify-install` だけで行い、利用者の設定ディレクトリ（`CLAUDE_CONFIG_DIR` か `~/.claude`、`CODEX_HOME` か `~/.codex`）で導入の操作（`claude plugin install` / `update` / `marketplace add` / `marketplace update`、`codex plugin add` / `marketplace add`）を打たない。隔離のつもりで環境変数を付けて手で打つことも含む。確かめるのに利用者の設定ディレクトリへの導入が要る条件は、導入せずに記録の表のその行を「保留（理由: 利用者の設定ディレクトリへの導入が要る）」とし、作業の報告を「判断が要る」で返す（`/ndf:release-verification` の「利用者の設定ディレクトリで導入しない」）。

## 処理の流れ

```mermaid
sequenceDiagram
  participant P as まとめのプラン
  participant S as 控えの副命令
  participant W as worker
  participant V as verify-install
  P->>S: user-config-save で控えを取る（E1）
  S-->>P: 0 / 2（2 なら止まる）
  P->>W: 起動指示（禁止と Skill の節の参照）
  W->>V: 隔離した環境で導入（E2）
  V-->>W: 前後の比較を含む結果（E3）
  W->>W: 受け入れ条件ごとに確かめる（E4）
  alt 利用者の設定ディレクトリへの導入が要る
    W->>W: 保留の行を書き、判断が要るで返す（E5）
  end
  W-->>P: 記録を PR へ書いて報告（E6）
  alt worker が完了
    P->>S: config-check で比べる（E7）
    S-->>P: 0 なら振り返りへ、ほかは止まる
  else worker が失敗
    P->>S: config-check-on-fail で比べる（E7）
    S-->>P: どの終了コードでも止まる
  end
```

この図に含めない構成要素は次のとおりである。

| 構成要素 | 図に含めない理由 |
| --- | --- |
| `user_config_snapshot`・`user_env_snapshot`・`_env_items` | 控えの副命令と `verify-install` の中で呼ばれる |
| `release_verify_step`・`user_config_steps`・`close_plan` | まとめのプランを組む側で、実行の前に働く |
| `release-verification` の Skill | worker が読む手順として「worker」に含める |

## 非機能の実現方式

| 大項目 | 実現方式 |
| --- | --- |
| セキュリティ | `user_config_snapshot` が開くのは Claude Code の 2 つの JSON と Codex の `config.toml`、`plugins/cache/` のディレクトリの名前だけ。`config.toml` は 2 つの表だけを値に使う。取得元の URL は `scheme://user:pass@host` の利用者の情報を `***` に置き換えてから控えと結果へ入れる（I2）。控えのファイルはまとめのプランの状態ディレクトリに置き、リポジトリへコミットしない |
| 運用・保守性 | 変わったときの `items` に、鍵（プラグインか取得元の名前）と前後の値（範囲・版・コミット）が載り、`summary` がプランの止まった理由になる。人は前の版と取得元を読んで入れ直すかを決められる |
| システム環境 | 設定ディレクトリは `CLAUDE_CONFIG_DIR` → `~/.claude`、`CODEX_HOME` → `~/.codex` の順で決める。ai-plugins の開発環境の場所（`/home/ubuntu/.claude/ndf/accounts/team` など）を既定に書かない。Python 3.10 では `config.toml` を読めないと報告して止まる（黙って比べずに通さない） |

## 決定の記録

### 決定 1: 比べる処理をスクリプトに任せるため、まとめのプランのリリース後テストのステップを前後の run のステップで挟む

控えと比較は判断の要らない手順であり、worker に任せると手順書を読み落としたときに効かない。まとめのプランの run のステップなら、worker が何をしても前後で必ず打たれる。挟むステップは今のステップの種類（run・`on_fail`・`on_exit`）だけで組め、実行エンジンを変えない。

実行エンジンに「work のステップの前後で打つコマンド」の欄を足す形は採らない。仕組みを使うのはこのステップだけで、エンジンの変更がまとめのプランの組み方の変更より広くなる。worker に控えと比較を打たせる形も採らない。手順書の禁止と同じく、読み落とすと効かない。

根拠: Value 4 / Value 6（MVV 版 4）

### 決定 2: worker が止まったときも比べるため、リリース後テストのステップの失敗を、必ず止まる比べるステップへ回す

worker が手で導入したうえで「判断が要る」か「できなかった」で返すと、今は比べずにプランが止まり、変わったことが報告に出ない。失敗の行き先 `config-check-on-fail` は `on_exit: {"0": "stop"}` を持つ。変わっていなくても止まり、リリース後テストの失敗はそのまま人へ戻る。変わっていれば終了コード 1 で、変わった項目が理由に載る。

失敗の行き先を `config-check` にする形は採らない。変わっていなければ振り返りへ進み、リリース後テストの失敗が人へ戻らなくなる。

根拠: 上位の原則（人を守る）/ Value 2（MVV 版 4）

### 決定 3: 自動の更新による誤検出を人へ戻す側に倒すため、版・コミット・範囲・取得元の場所が変われば止める

利用者の設定ディレクトリは、同じマシンの別のセッションや Claude Code の自動の更新（`known_marketplaces.json` の `autoUpdate: true`）でも書き換わる。worker の操作か自動の更新かを区別する手段は見つかっていない（記録の行に書き手は残らない）。区別できない変化は、利用者の導入済みの版が変わったという事実として人へ戻す。時刻・置き場所・`autoUpdate` は版が変わらなくても動くため値に入れず、版の変わらない取得元の更新では止まらない。

誤検出を減らすために、変化を無視する時間帯や許す版の一覧を置く形は採らない。区別の根拠が無いまま許すと、手で導入した場合も通る。

根拠: 上位の原則（人を守る）/ Value 3（MVV 版 4）

### 決定 4: 1 か所で直せるようにするため、利用者の設定の読み取りを 1 つの関数にし、`verify-install` と 2 つの副命令で使う

`verify-install` の前後の比較（受け入れ条件 5）と、まとめのプランの前後の比較（受け入れ条件 4）は、同じファイルを同じ鍵で読む。`user_config_snapshot` を 1 つ置き、`user_env_snapshot` はそれを足すだけにする。Claude Code か Codex の記録の形が変わったときの直し先が 1 か所になる。

副命令を別のスクリプトに分ける形は採らない。読み取りの関数を 2 つのスクリプトで共有するために `lib/` へ移す必要が生まれ、今の 1 本のスクリプトで足りる範囲を超える。

根拠: Value 6（MVV 版 4）

### 決定 5: 新しく入ったプラグインを見落とさないため、前後の差分を鍵の和集合で取る

今の `_env_items` は前の鍵だけを回るため、導入の後にだけ現れた鍵（新しく入ったプラグイン・新しく登録した取得元）を差分に出さない。今の 3 つの鍵は常に前後の両方にあったため表に出なかった。和集合で回り、無い側は `null` にする。今の鍵だけなら結果は変わらない（I7）。

根拠: 上位の原則（人を守る）/ Value 6（MVV 版 4）

### 決定 6: 導入の確かめの判定を今と同じに保つため、`verify-install` は読めない設定のファイルで止まらず、控えの 2 つの副命令は止まる

`verify-install` の目的は導入した版と中身を確かめることで、利用者の設定のファイルが読めないことはその合否と別である。読めないことは値として前後で比べ、途中で読めるようになったか壊れたかだけを差分に出す。控えの 2 つの副命令は比べることだけが目的である。読めなければ「変わっていない」と言えないため、終了コード 2 で止まる（受け入れ条件 4 の「控えが取れなかったときも人へ戻す」）。

根拠: Value 3 / Value 6（MVV 版 4）

### 決定 7: 禁止の正本を 1 つにするため、Skill の節を正本とし、起動指示は要点と節の名前を 1 つの定数で持つ

worker は Skill の本文を読んで動くが、起動指示は Skill の本文より先に読まれる。起動指示に要点（禁止・保留の書き方・「判断が要る」）を置き、詳しい理由と手順は Skill の節を指す。起動指示の文は `release_templates.py` の定数 `NO_USER_INSTALL` 1 つに置き、ほかのプランの組み立てへ写さない。

根拠: Value 7 / Value 4（MVV 版 4）

### 決定 8: プランの外で Skill を使うときも比べられるため、手順書に控えと比較の 2 行を置く

`/ndf:release-verification` を利用者が直接呼ぶときは、まとめのプランの前後のステップが無い。手順書の手順 1 の前に `user-config-save`、記録の後に `user-config-check` を打つ手順を置く。まとめのプランの中では前後のステップが打つため、打たないと書く。

根拠: Value 5（MVV 版 4）

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 受け入れ条件 1・3 | 手順書の効き目は、次のスプリントの本番のリリース後テストで、利用者の設定ディレクトリが変わらず `config-check` が `ok` になることを人が見る（`.md` の文言のテストは書かない） | — （リリース後テストの手動確認） |
| 受け入れ条件 2 | まとめのプランを組むと、`release-verify` の起動指示が `NO_USER_INSTALL` を含む | 起動指示から定数を外すと落ちる |
| 受け入れ条件 4・I4・I5 | 配布の記録のあるまとめのプランの `next` のつながりが `merge → config-save → release-verify → config-check → retro → close → refine` で、`release-verify` の `on_fail` が `config-check-on-fail`、その `on_exit` が `{"0": "stop"}`。配布の記録の無いまとめのプランは 3 つのステップを持たない | 挟むステップを外す・`on_fail` を外す・`config-check` に `on_fail` を付けて先へ進めると落ちる |
| 受け入れ条件 4・I1 | 一時ディレクトリを `CLAUDE_CONFIG_DIR`・`CODEX_HOME` にして `user-config-save` → 設定のファイルの版を書き換え → `user-config-check` が終了コード 1、変わった鍵と前後の値を `items` に、件数を `summary` に載せる。書き換えなければ 0 | 比較を常に一致と返す・新しく足したプラグインの鍵を見落とすと落ちる |
| 受け入れ条件 4・I3 | 控えのファイルが無い・壊れている、設定のファイルが壊れているとき、`user-config-save` と `user-config-check` は終了コード 2 | 読めないことを 0 件と読んで 0 を返すと落ちる |
| 決定 3 | `lastUpdated`・`installedAt`・`installLocation`・`autoUpdate` だけを書き換えると `user-config-check` は 0 | 時刻を値に入れると落ちる |
| 受け入れ条件 5・I6 | `verify-install` の導入の途中で一時ディレクトリの設定ディレクトリの版を書き換えると、`user_env_unchanged` が false、`status` が `stopped`、`items` に `kind: user_env` の鍵が載る | `user_env_snapshot` が設定ディレクトリを読まない・差分を前の鍵だけで取ると落ちる |
| 受け入れ条件 6・I7 | `test_step_scripts.py` の `verify-install` の既存のテストが書き換えずに通る | `user_env_snapshot` の引数か戻り値の形を変えると落ちる |
| I2 | 取得元の URL に `user:pass@` を含む設定と、Codex の `auth.json` と `config.toml` のほかの表に置いた目印の文字列が、控えのファイルにも結果 JSON にも出ない | 伏せずに写す・ほかの表を写すと落ちる |
| 受け入れ条件 7 | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`・`python3 scripts/check-skill-frontmatter.py`・`claude plugin validate .` が通る | — |

既存の `test_supervise_pace_verify.py` の `test_close_comes_after_the_retro_and_before_the_refine` は、つながりの期待値に 2 つのステップが入る。つながりを変えることが、受け入れ条件 4 の振る舞いである。

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #2401 | 実装する | — | `plugins/ndf/scripts/release-verification-steps.py`、`plugins/ndf/scripts/supervise_lib/release_templates.py`、`plugins/ndf/scripts/supervise_lib/sprint.py`、`plugins/ndf/skills/release-verification/SKILL.md`、`plugins/ndf/scripts/tests/test_step_scripts.py`、`plugins/ndf/scripts/tests/test_supervise_pace_verify.py` |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 2026-11-02 に環境変数が効かなかった理由 | 未確認（要求の前提 2）。この設計は理由に依らず、禁止と前後の比較で守る |
| 自動の更新による誤検出の頻度 | 未確認。リリース後テストの worker は利用者の設定ディレクトリで `claude -p` を起動するため、起動時の自動の更新で版が変わると `config-check` が止まる。次のスプリントから、止まった回数と理由をまとめのプランの結果で数える |
| Codex が更新のときに古い版のディレクトリを消すか | 未確認。消しても残しても版のディレクトリの並びが変わるため、どちらでも変化として出る |
| Claude Code・Codex の記録の形の安定性 | Claude Code の `installed_plugins.json` は `version: 2` の形、Codex は codex-cli 0.166.0 の形で確かめた。形が変わると読めないとして止まる（I3）。黙って通らない |
| Claude Code の `settings.json` の有効化の設定（`enabledPlugins`） | 比べない。要求の守る対象（導入済みのプラグインの記録と取得元の登録）の外 |
| Python 3.10 の利用者の環境 | Codex の `config.toml` があると、控えの副命令が毎回止まる。NDF の動作の下限は 3.10 だが、3.10 で Codex を使う利用者がいるかは未確認 |
