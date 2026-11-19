# #3852 #3853 #3855 #3843 の詳細設計: 記録の置き先・保留・時点つきの見出しをコマンドで扱う

設計文書 `issues/issue-3852-3853-3855-3843-design.md` の「構成要素」と「入出力のコントラクト」を、ファイルと関数の粒度へ下ろした文書である。#3853 は文書の規則だけで関数を持たないため、「ファイル配置」にだけ現れる。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/
│   ├── release/
│   │   ├── record.py            # 記録を持つ PR を探す（#3852）
│   │   └── cli.py               # cmd_verification_record（#3852）
│   └── records/
│       ├── close_decide.py      # 閉じるかの判定を 3 つの値へ（#3855）
│       ├── cli.py               # _close_check（#3855）
│       └── handoff_decide.py    # 見出しの頭・必須・順（#3843）
├── tests/domains/
│   ├── release/test_release_record.py
│   └── records/
│       ├── test_close.py
│       ├── test_cli.py
│       └── test_handoff.py
└── skills/
    ├── release-verification/SKILL.md              # #3852・#3853・#3855
    ├── release/SKILL.md                           # #3852
    ├── requirements-design/references/spec-template.md         # #3853
    ├── requirements-design/references/acceptance-criteria.md   # #3853
    ├── progress-tracking/SKILL.md                 # #3855
    ├── quality-gates/SKILL.md                     # #3855
    └── development-workflow/references/handoff.md # #3843
docs/glossary/glossary.json（と docs/glossary.md・docs/glossary/ndf-release.md）  # #3853 の語
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/release/record.py` | 変更 | 記録の無い PR がリリースの PR なら、含めた PR から記録を持つ PR を探して例外に載せる | `record_decide`・`names_decide`・`GitHub` |
| `plugins/ndf/src/ndf/domains/release/cli.py` | 変更 | ブランチ戦略の設定から本番ブランチと書式を渡し、記録を持つ PR を `items[]` に載せて止まる | `record`・`branch_strategy`・`names_decide` |
| `plugins/ndf/src/ndf/domains/records/close_decide.py` | 変更 | 課題を閉じてよいかを 3 つの値で返す | 無し |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | 3 つの値を `ok` / `stopped` / `gate` へ写す | `close_decide`・`result` |
| `plugins/ndf/src/ndf/domains/records/handoff_decide.py` | 変更 | 見出しの頭でセクションを見分け、必須の欠けと順の逆を数える | 無し |
| `plugins/ndf/tests/domains/release/test_release_record.py` | 変更 | #3852 の受け入れ条件 2〜4 | `_fakes.FakeHub` |
| `plugins/ndf/tests/domains/records/test_close.py` | 変更 | #3855 の受け入れ条件 1・3（判定） | 無し |
| `plugins/ndf/tests/domains/records/test_cli.py` | 変更 | #3855 の結果の写し方、#3843 の CLI の終了コード | 無し |
| `plugins/ndf/tests/domains/records/test_handoff.py` | 変更 | #3843 の受け入れ条件 1〜4。既存の本文の雛形（`BODY`）を必須の 5 セクションを持つ形へ直す | 無し |
| `plugins/ndf/skills/release-verification/SKILL.md` | 変更 | 「出力物」の置き先の表（決定 1）・手順 2 のリリース後の確認の計画を読む段落（決定 3）・閉じる判定の文（決定 4） | 無し |
| `plugins/ndf/skills/release/SKILL.md` | 変更 | 手順 3 と「出力物」の「リリースの Pull Request の本文へ置く」から workflow でリリースする形を除く（決定 1） | 無し |
| `plugins/ndf/skills/requirements-design/references/spec-template.md` | 変更 | 任意のセクション `## リリース後の確認の計画` を「検証手段」の後に足す（決定 3） | 無し |
| `plugins/ndf/skills/requirements-design/references/acceptance-criteria.md` | 変更 | 経路を動かすバージョンを書く規則と、旧いバージョンが動かす経路の例（決定 3） | 無し |
| `plugins/ndf/skills/progress-tracking/SKILL.md` | 変更 | 閉じる条件 2 と手順 3 の結果の読み方を 3 つの結果で書く（決定 4） | 無し |
| `plugins/ndf/skills/quality-gates/SKILL.md` | 変更 | 閉じる判定の 1 文（`close-check`）に保留の扱いを足す（決定 4） | 無し |
| `plugins/ndf/skills/development-workflow/references/handoff.md` | 変更 | 「作る・更新する」の `check` の終了コードの表の違反に、必須の欠けと順の逆を足す（決定 5） | 無し |
| `docs/glossary/glossary.json` | 変更 | 「経路を動かすバージョン」（`ndf-release`、`pending_source` は設計文書）。`ndf records glossary render` で文書を作り直す | 無し |

## クラスと関数の構造

型の関係は変えない（新しいクラスは例外 1 つと、判定の値の定数だけ）。

| 保存先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/src/ndf/domains/release/record.py` | `RecordElsewhere`（新設。`RecordError` の子） | `holders`（属性） |
| `plugins/ndf/src/ndf/domains/release/record.py` | モジュール | `write_verification`（変更）・`_record_holders`（新設） |
| `plugins/ndf/src/ndf/domains/release/cli.py` | モジュール | `cmd_verification_record`（変更）・`_release_names`（新設） |
| `plugins/ndf/src/ndf/domains/records/close_decide.py` | モジュール | `verdict`（新設。`closable` を置き換える）・`waiting_rows`（新設）・`blocking_rows`（変えない） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | モジュール | `_close_check`（変更） |
| `plugins/ndf/src/ndf/domains/records/handoff_decide.py` | モジュール | `section_of`（新設）・`check_body`（変更）・`trim`（変更） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `write_verification` | `record.py` | 変更 | `write_verification(hub: GitHub, number: int, payload: Mapping[str, Any], production: Optional[str] = None, formats: names_decide.NameFormats = names_decide.NameFormats(None)) -> str` | 記録を PR `number` へ書く。記録の無い PR なら、`production` があるときだけ記録を持つ PR を探して止まる | `payload` は辞書 | 書いたら URL を返す。書かなければ `RecordError` か `RecordElsewhere` | GitHub へコメント 1 件（書くときだけ）。記録が無いときは PR とコミットとコメントを読む |
| `_record_holders` | `record.py` | 新設 | `_record_holders(hub: GitHub, number: int, version: str, production: str, formats: names_decide.NameFormats) -> tuple[tuple[int, ...], str]` | PR `number` をリリースの PR として読み、同じバージョンのリリース記録を持つ含めた PR の番号と、持たないときの理由を返す | `number` は記録を持たない PR | `(番号の並び, "")` か `((), 理由)`。番号は小さい順で重ならない | GitHub を読むだけ |
| `cmd_verification_record` | `release/cli.py` | 変更 | 今と同じ（`argparse.Namespace -> dict`） | 本番ブランチと書式を渡して書き、`RecordElsewhere` を `items[]` つきの `precondition` へ写す | — | 結果 JSON | GitHub |
| `_release_names` | `release/cli.py` | 新設 | `_release_names(args: argparse.Namespace) -> tuple[Optional[str], names_decide.NameFormats]` | ブランチ戦略の設定の本番ブランチと書式を返す。設定が読めなければ `(None, NameFormats(None))` | — | 例外を出さない | メインディレクトリの設定を読む |
| `verdict` | `close_decide.py` | 新設 | `verdict(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], require: bool) -> tuple[str, str]` | 課題を閉じてよいか（`CLOSE` / `KEEP` / `ASK`）と理由を返す | — | 値は 3 つの定数のどれか | 無し |
| `waiting_rows` | `close_decide.py` | 新設 | `waiting_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]` | 人が決める行（その課題の `not_run`）を返す | — | 記録が無ければ空 | 無し |
| `_close_check` | `records/cli.py` | 変更 | 今と同じ | `verdict` を `ok` / `stopped`（`refused`）/ `gate` へ写す | — | 結果 JSON | 記録のファイルを読む |
| `section_of` | `handoff_decide.py` | 新設 | `section_of(heading: str) -> Optional[str]` | 見出しの文から、頭が当たるテンプレートのセクションの名前を返す | `heading` は `## ` を除いた見出しの文 | 当たらなければ None | 無し |
| `check_body` | `handoff_decide.py` | 変更 | 今と同じ | 見出しを `section_of` で写してから、無い・重なる・欠け・順・行数・バイト数・パスを数える | — | `CheckReport` | 無し |
| `trim` | `handoff_decide.py` | 変更 | 今と同じ | `section_of` が None のセクションだけを移す | — | 今と同じ | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_verification` | 記録の無い PR がリリースの PR で、含めた PR が記録を持つ | リリースの PR を渡した（#3852） | `_record_holders` | 止める（`RecordElsewhere`） | `stopped`・3（`precondition`）。`summary` に記録を持つ PR、`items[]` に `{"kind": "pull", "number": N}` | 何も書かない。示した PR で打ち直す |
| `write_verification` | 記録の無い PR で、探しても示せない | リリースの PR でない・バージョンが違う・含めた PR が持たない・設定が無い | `_record_holders` | 止める（`RecordError`） | `stopped`・3。今の文に、含めた PR の各々にあることと示せない理由を足す | 何も書かない |
| `_record_holders` | リリースの PR として読めない | `NotReleased`（未マージ・宛先が本番でない・head の書式が違う）・`RecordError`（マージコミットが読めない） | `record.released` | 呼び出し元へ返す（理由の文にする） | 上の行の理由 | — |
| `_record_holders` | 含めた PR のコメントが読めない | GitHub の誤り | `hub.comments` | 呼び出し元へ返す（`GitHubError` のまま。今の `_stopped_of` が写す） | `stopped`（`infra_wait` か `unknown`） | 書いていないので打ち直せる |
| `_release_names` | 設定が読めない | ブランチ戦略の設定が無い・壊れている | `branch_strategy.load` | 既定値で続ける（`production` を None にし、探さない） | 探さない場合の理由「ブランチ戦略の設定が読めない」 | — |
| `_close_check` | 記録が読めない・形が違う | ファイル・JSON・スキーマ | 今と同じ | 今と同じ | `stopped`・3 | 今と同じ |
| `check_body` | 必須のセクションが無い | conductor が書き落とした・`--trim` が移した | `check_body` | 違反に足す | `check_failed`・1、`violation: 必須のセクションが無い: <名前>` | 直して打ち直す |
| `check_body` | 順が逆 | セクションの並びがテンプレートと違う | `check_body` | 違反に足す | `check_failed`・1、`violation: セクションの順が逆: <後ろ> が <前> より前にある` | 直して打ち直す |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `write_verification` | 形が違う | 今と同じく `RecordError` |
| `write_verification` | PR が同じバージョンの記録を持つ | 信用する書き手を確かめて書く（今と同じ） |
| `write_verification` | 持たない・`production` が None | 今の文に「含めた PR の各々にある」と理由「ブランチ戦略の設定が読めない」を足して `RecordError` |
| `write_verification` | 持たない・`_record_holders` が番号を返した | `RecordElsewhere`（番号つき） |
| `write_verification` | 持たない・`_record_holders` が理由を返した | 今の文に「含めた PR の各々にある」と理由を足して `RecordError` |
| `_record_holders` | `released` が `NotReleased` か `RecordError` | `((), "#N はリリースの PR でない: <例外の文>")` |
| `_record_holders` | リリースのバージョン（`made.version`）と単位ごとのバージョンのどれも `version` と違う | `((), "#N はバージョン <v> のリリースの PR で、記録のバージョン <version> と違う")` |
| `_record_holders` | バージョンが合う単位（単位ごとの行が無ければ `made`）の含めた PR のうち、信用する書き手のコメントに同じバージョンのリリース記録があるもの | 1 本以上なら `(番号, "")`、0 本なら `((), "含めた PR（#…）のどれも v<version> のリリース記録を持たない")` |
| `verdict` | 記録が None・`require` | `(KEEP, "リリース後テストの記録が無い")`（今と同じ） |
| `verdict` | 記録が None・`require` でない | `(CLOSE, "リリース後テストの記録が無い（条件 2 を見ない）")`（今と同じ） |
| `verdict` | `failed` の行が 1 行以上 | `(KEEP, "リリース後テストに不合格の行が N 件ある")`（`not_run` の有無に依らない） |
| `verdict` | `failed` が無く `not_run` が 1 行以上 | `(ASK, "保留（not_run）の行が N 件ある。閉じるかを人が決める")` |
| `verdict` | どちらも無い | `(CLOSE, "リリース後テストに不合格と保留の行が無い")` |
| `_close_check` | `CLOSE` | `ok`、`closable: true`、`summary: "#N を閉じてよい: <理由>"` |
| `_close_check` | `KEEP` | `stopped`（`refused`）、`closable: false`、`summary: "#N を閉じない: <理由>"` |
| `_close_check` | `ASK` | `gate`、`closable: false`、`routes: {"approved": null, "rejected": null}`、`summary: "#N を閉じるかは人が決める: <理由>"` |
| `section_of` | `heading` がテンプレートの名前と同じ | その名前 |
| `section_of` | `heading` が名前で始まり、直後の 1 文字が空白（全角を含む）・`（`・`(` | その名前 |
| `section_of` | それ以外 | None |
| `check_body` | 見出しの `section_of` が None | `テンプレートに無いセクション: <見出し>`（今と同じ文） |
| `check_body` | 同じ名前に当たる見出しが 2 つ以上 | `セクションが重なる: <名前>`（名前で数える） |
| `check_body` | `REQUIRED_SECTIONS` の名前に当たる見出しが無い | `必須のセクションが無い: <名前>`（`REQUIRED_SECTIONS` の順に 1 件ずつ） |
| `check_body` | 当たった見出しを上から読み、テンプレートの位置が前の見出しより前に戻る | `セクションの順が逆: <その名前> が <前の名前> より前にある`（戻るたびに 1 件） |
| `trim` | 見出しの `section_of` が None | 移す（今と同じく「前の会話の進み」も当たる） |

- 繰り返しの終わる条件: 含めた PR・記録の行・見出しの数だけ回る
- 計算量の注意: `_record_holders` は含めた PR ごとにコメントを 1 回読む（`write` と同じ回数）。記録が無いと分かった後にだけ呼ぶ

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `REQUIRED_SECTIONS` | 定数（tuple） | `handoff_decide.py` | `("進め方（利用者の指示）", "課題の順", "現在地", "次にやること", "次に実行するコマンド")`。`TEMPLATE_SECTIONS` の部分集合で順も同じ |
| `_HEAD_FOLLOWERS` | 定数 | `handoff_decide.py` | `("（", "(")` と空白（`str.isspace`） |
| `CLOSE` / `KEEP` / `ASK` | 定数（str） | `close_decide.py` | `"close"` / `"keep"` / `"ask"` |
| `RecordElsewhere.holders` | 型の属性 | `record.py` | 記録を持つ PR の番号の tuple |
| `WORKFLOW_HINT` | 定数（str） | `record.py` | `"workflow でリリースする形ではリリース記録は含めた PR の各々にある"` |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `verification-record` の打ち直し | PR のコメント | 取らない（今と同じ） | 止まる経路は何も書かないので、何度打っても同じ |
| `close-check`・`handoff check` | 無し（読むだけ。`--trim` は今と同じ） | 今と同じ | 今と同じ |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_verification_record` | 止まった理由と記録を持つ PR の番号 | 結果 JSON（`summary`・`items[]`） | token・コメントの本文 |
| `_close_check` | 判定と理由 | 結果 JSON | 記録の `evidence` |
| `check_body` | 違反の文 | 結果 JSON の `items[].violation` | 本文の中身 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_record_holders` | `record.released`・`record._trusted_bodies`・`record_decide.records`・`record_decide.has_version` | `record.py` と `record_decide.py` を読んだ | 使う（リリースの PR の判定・含めた PR・信用する書き手・バージョンの照らし方を `write` と同じものにする） |
| `_release_names` | `cli._strategy`・`branch_strategy.Strategy.production`・`names_decide.formats_of` | `release/cli.py` の `cmd_record` を読んだ | 使う（`cmd_record` と同じ読み方。例外だけを既定値へ写す） |
| `verdict` | `close_decide.blocking_rows` | `close_decide.py` を読んだ | 使う（`failed` の行の数え方を変えない） |
| `section_of` | `handoff_decide.sections`・`_H2` | `handoff_decide.py` を読んだ | 使う（セクションの切り方は変えず、名前の写し方だけを足す） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_verification` | 記録を持つ含めた PR に書く（既存の `test_issue_3128_…`） | 含めた PR の片方だけが記録を持つ・単位が 2 つでバージョンの合う単位だけを探す | リリースの PR を渡すと `RecordElsewhere`（番号は記録を持つ PR だけ）・何も書かない。リリースの PR でない・バージョン違い・どれも持たない・`production` が None で `RecordError` |
| `cmd_verification_record` | — | — | `RecordElsewhere` で終了コード 3・`cause.kind` が `precondition`・`items[]` の番号 |
| `verdict` | `passed` だけで `CLOSE` | 記録に課題の行が無いと `CLOSE`（今と同じ。扱いは別の課題 #3860 で決める） | `not_run` だけで `ASK`、`failed` と `not_run` で `KEEP`、記録が無く `require` で `KEEP` |
| `_close_check` | `CLOSE` で 0 | — | `ASK` で `gate`・10・`routes` が 2 つの null、`KEEP` で 3 |
| `section_of` | 名前そのもの・`現在地（2026-11-19）`・`次にやること 2026-11-19`・`進め方（利用者の指示）（11-19）` | `現在地　（全角の空白）`・`現在地(x)` | `現在地メモ`・`前の会話の進み`・`セッション 95 の記録` で None |
| `check_body` | 必須の 5 つと任意の 2 つをテンプレートの順で持つ本体で通る | 任意の 2 つを持たない本体で通る | 必須を 1 つずつ欠く・順を入れ替える・頭で同じ名前に当たる 2 つの見出しで落ちる |
| `trim` | `## 現在地（2026-11-19）` を残す | コードブロックの中の `## ` を見出しにしない（今と同じ） | `## 前の会話の進み`・`## 現在地メモ` を移す |
