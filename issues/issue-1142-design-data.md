# 定数からデータへ: 同じ事実を 1 か所の表に置き、読む側はそこから引き、新しい重複を継続的統合が止める（#1142）

## 目的

- **今どうなっているか**: 同じ事実（ランタイムの名前・モード・設計文書の種類・閾値など）を、複数のファイルの最上位の定数がそれぞれ字で持ち、一致はテストが縛っている。#3144 の詳細設計では、設計文書の種類を 1 つ足すのに 4 つの定数（`glossary.DEFAULT_PATHS`・`classifications.DESIGN_DOC_FILE_SUFFIXES`・`categories.DESIGN_DOC_SUFFIXES`・新設の `design_detail.DETAIL_SUFFIX`）を直す形になり、詳細設計を通るモードは `workflow-common.sh` の `WF_STAGE_MATRIX` とテストで二重になった
- **誰に効くか**: NDF のスクリプトを直す worker と、そのレビュー。値を 1 つ足すたびに直す場所を探す手間と、直し漏れの不具合が減る
- **変えると何が成り立つか**: 事実ごとに字で書く場所が 1 つになり、値を足すときに直すファイルが 1 つになる。一致を縛るテストが要らなくなり、新しく同じ事実を定数で書くと継続的統合が落とす

## 適用範囲

- **働く範囲**: このリポジトリの `plugins/ndf` のスクリプト（テストと `experimental/` を除く `.py` と `.sh`）と、そのテスト。利用先のリポジトリでは働かない（検査は ai-plugins の継続的統合だけで走る）
- **プロジェクトごとに違うもの**: 無い。表は NDF の事実を持ち、プロジェクトごとに違う値は今どおり `.ndf/` の宣言か引数で受ける（表は宣言の既定値の置き場にならない）
- **当たるモード**: #1142 の `standard`。移行ステップはそれぞれ #1142 の実装の PR として流す

## あるべき姿の根拠

| 根拠 | 種類 | 何を示すか |
| --- | --- | --- |
| 「定数が多すぎる。全体再設計で直すしかない」（2026-11-13、#3144 の詳細設計を見て） | 利用者の指示の原文 | 定数の重複をこの課題の範囲で直す |
| 要求の決めたこと「同じ事実を複数の定数に持たせ、一致をテストで縛る作りをやめ、事実を 1 か所のデータ（宣言・契約の JSON・表）に置いて、読む側はそこから引く」 | 利用者の指示の原文 | 置き場はデータで、読む側は引く |
| `python3 scripts/measure/const-facts.py`（develop 01a53e912、2026-11-13）: 字の定数 1,563 個のうち 479 個が 417 組に入る | 実測 | 重複は点でなく面で起きている |
| STD1（JSON Schema の `enum`） | 外部の一次情報 | 値の集まりをデータとして宣言し、形を機械で確かめる標準の形がある |
| PRD1（GitHub Linguist の `languages.yml`） | 外部の一次情報 | 言語の一覧という事実を 1 つのデータに置き、ID は生成で足す運用が広く使われている |

要求と受け入れ条件は #1142 の本文にある（コピーは [issue-1142-requirements.md](issue-1142-requirements.md) の「定数からデータへ（2026-11-13 追記）」と受け入れ条件「定数からデータへ」）。この文書は「どう作るか」だけを扱う。全体の設計は [issue-1142-design.md](issue-1142-design.md) 、移行の順序の全体は [issue-1142-design-migration.md](issue-1142-design-migration.md) 、決定 1〜25 は [issue-1142-design-decisions.md](issue-1142-design-decisions.md) にある。並行の設計のうち、工程とモードの表は #3092（[issue-3092-design.md](issue-3092-design.md) ）、設計文書の種類の契約は #3144（[issue-3144-design-detail.md](issue-3144-design-detail.md) の「内部のデータと定数」）が持つ。

## ドメインモデル

### コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| 契約（`plugins/ndf/contracts/`） | 事実の表・行・列・持ち主のモジュール・基準の行 |

コンテキストマップ: 契約は共有カーネル（[issue-1142-design.md](issue-1142-design.md) のライブラリと同じ扱い）で、各コンテキストは読み手として従う。表の形を変える PR は、読み手とそのテストを同じ PR で直す。

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 事実の表 | 表の `owner` に書いた 1 つのコンテキスト（表ごとに 1 つ。ほかのコンテキストは読むだけ） | `contracts/facts/<名前>.json` | 行（`id` で決まる） | 列の値 |
| 外部の表: 既存の契約 | 契約（このコンテキスト。値を移す移行ステップの PR が、読み手とそのテストを同じ PR で直す） | `contracts/limits.json`・`contracts/settings/`・`contracts/forms/` | 行・方針 | — |
| 外部の表: 工程とモードの表 | #3092 の NDF の開発ワークフロー（工程の定義） | `contracts/workflow/` | 行・方針 | — |
| 外部の表: 設計文書の種類の契約 | #3144 の開発の工程（設計文書の種類） | `contracts/design-docs.json` | 種類の行 | — |
| 外部の表: 止まった理由の表 | #1142 のライブラリ（[issue-1142-design-scripted-flow.md](issue-1142-design-scripted-flow.md) の集約） | `contracts/causes.json` | 種類 | — |
| 重複の基準 | 移行ステップの PR（自分が直した行を消す）。定数を別のファイルへ移す PR は行を置き換える（I-DT4c） | `scripts/const-fact-baseline.json` | 基準の行（組の鍵で決まる） | 種類・事実・扱い |

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I-DT1 | 事実の表 | 1 つの事実は、1 つの表か 1 つの持ち主のモジュールの定数にだけ字で書かれる。ほかは引くか import する | 継続的統合の規則 `const-fact` が落とす（基準の行を除く） |
| I-DT2 | 事実の表 | 表は `contracts/facts/fact.schema.json` を満たし、`id` が表の中で重ならない | 継続的統合が落とす |
| I-DT3 | 事実の表 | 表の行が別の表を指す値（外部キー）は相手の表にある（例: 収束ループのフェーズと `limits.json` の `monitor.phase.<id>`） | 継続的統合が落とす |
| I-DT4a | 重複の基準 | 今の木で拾った組は、すべて基準にある | 増えた組として継続的統合が落とす。組を増やした PR の中で直す |
| I-DT4b | 重複の基準 | 基準の行は、すべて今の木で見つかる | 使われない行として継続的統合が落とす。組を直した PR が、同じ PR で基準の行を消す |
| I-DT4c | 重複の基準 | 定数を別のファイルへ移す PR は、基準の行を置き換えてよい。merge-base と比べて、種類・事実・定数の名前が同じでファイルだけが違う行を、消した数と同じ数だけ足した分は増えた行に数えない | 置き換えに当たらない行の追加は、決定 50 の比べ（merge-base より行が増えた変更）が落とす |
| I-DT5 | 重複の基準 | 基準の行はすべて扱い（`移す: <移行ステップ>` か `移さない: <理由>`）を持つ | 継続的統合が落とす |
| I-DT6 | 事実の表 | 実行時の読み手は標準ライブラリの `json` だけで表を読む（hook の経路でも読む） | 構造チェックの `hook-deps` と同じ形で落とす |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 移行の前の基準を測った（重複の組 417） | `scripts/measure/const-facts.py` | 基準のファイル |
| E5 | 事実を表へ移した（移行ステップ DT2〜DT8） | 移行ステップの PR | 基準のファイル（行を消す） |
| E9 | 移行の後を同じ物差しで測り直した | `scripts/measure/const-facts.py` | 振り返り |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 事実の表 | 1 つの事実（例: ランタイムの一覧）を行と列で持つ契約の JSON。`contracts/facts/` の 1 ファイル | 追加 |
| 持ち主のモジュール | 1 つのコンテキストの Python だけが読む事実を、定数として 1 か所で持つモジュール。ほかのモジュールは import する | 追加 |
| 重複の組 | 同じ事実を持つと見える 2 つ以上の最上位の定数の組。種類は `same-set`・`subset`・`affix`・`same-str`・`member`・`same-num` | 追加 |
| 重複の基準 | 移行の前に拾った重複の組と、組ごとの扱いの一覧。ラチェットの基準になる | 追加 |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 値の集まりをデータとして宣言し機械で確かめる形（JSON Schema）を使うか決める |
| 主要な製品 | 調べる | 一覧の事実を 1 つのデータに置く運用と、重複した字を落とす静的解析の製品の形を比べる |
| ライブラリとサービス | 調べる | 表の形の検査・読み手の型・重複の検査を、既存のライブラリで得られるか決める |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | JSON Schema Validation（draft 2020-12、draft-bhutton-json-schema-validation-01） | `enum` は値が配列の要素のどれかに等しいこと、`const` は 1 つの値に等しいことを定める（6.1.2・6.1.3）。IETF の Internet-Draft の状態 | 表の形を `fact.schema.json` で宣言し、ほかの契約が表の `id` を `enum` で指せる形にする。#3092 も同じ draft で検査する | https://json-schema.org/draft/2020-12/json-schema-validation （2026-11-13） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | GitHub Linguist | 言語の定義（拡張子・TextMate のスコープ）を `languages.yml` に置き、言語の追加はこのファイルの変更で行う。ID は `script/update-ids` が生成する | 事実は 1 つのデータに置き、そこから導けるもの（部分集合・ID）は写さず生成か引く | https://github.com/github-linguist/linguist/blob/main/CONTRIBUTING.md （2026-11-13） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | `jsonschema` | 4.26.0 | MIT | 2026-01-07 | 無し | 満たす。draft 2020-12 で表の形を検査できる。最初に使う DT1 が、手元の検査と配布先の両方で使えるようにする（決定 46）。#3092 は決定 3 で足すとしたが、マージした実装（v10.27.61）は依存に入れていない | https://pypi.org/pypi/jsonschema/json （2026-11-13） |
| LIB2 | `check-jsonschema` | 0.39.0 | PyPI の license 欄は空（確かめていない） | 2026-10-26 | 無し | 一部。スキーマに照らす CLI で、表の外部キーと定数との重複は見られない | https://pypi.org/pypi/check-jsonschema/json （2026-11-13） |
| LIB3 | `pydantic` | 2.14.3 | MIT | 2026-11-05 | 無し | 満たさない。読み手の型は作れるが、hook の経路で外部パッケージを読まない（I-DT6・決定 20） | https://pypi.org/pypi/pydantic/json （2026-11-13） |
| LIB4 | `pylint` の `duplicate-code`（R0801） | 4.1.3 | GPL-2.0-or-later | 2026-10-11 | 無し | 確かめられなかった（下）。名前からは似た行の検出で、定数の値の集合の一致を見るものではない | https://pypi.org/pypi/pylint/json （2026-11-13） |
| LIB5 | `ruff` の `PLR2004`（magic-value-comparison） | 0.16.9（このリポジトリの lock） | MIT | — | 無し | 満たさない。比較の中の名前の無い値を指摘する規則で、2 つの定数が同じ事実を持つことは見ない | https://docs.astral.sh/ruff/rules/magic-value-comparison/ （2026-11-13）、`uv run --frozen --project . --all-extras ruff --version` |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| SonarQube の重複した文字列の規則（python:S1192） | https://rules.sonarsource.com/python/RSPEC-1192/ | HTTP 301 で製品の紹介のページへ移され、規則の本文を取れなかった |
| `pylint` の `duplicate-code` の対象と設定 | https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/duplicate-code.html | メッセージの一覧のページで、規則の説明が無かった |
| `ruff` に重複した字の定数を落とす規則があるか | 検索「ruff rule duplicated string literal constant "PLR" OR "magic value" duplicate literal」 | 見つかったのは `PLR2004` だけ。「無い」とは確かめていない |

調べた量: 候補 7 件・取得の試み 10 回。

## 測った結果

**組は 417 で、組に入る定数は 479 個（字の定数 1,563 個の 31%）である。** develop 01a53e912、2026-11-13、`python3 scripts/measure/const-facts.py`（`--json` で 1 行 JSON）。数え方はスクリプトの docstring にある。要求の計測表の「54 / 2,270」は、2 つ以上のファイルで同じ値を持つ値の数と、字に限らない最上位の定数の総数で、数え方が違う。

| 種類 | 拾うもの | 組 |
| --- | --- | ---: |
| `same-set` | 2 つ以上のファイルの定数が同じ字の集合を持つ | 35 |
| `subset` | 字の集まりが別のファイルの集まりに含まれる | 202 |
| `affix` | 6 字以上の 2 つ以上の要素が、別の集まりの要素の接頭辞か接尾辞になる（例: `-design.md` と `issues/*-design.md`） | 15 |
| `same-str` | 4 字以上の同じ字を 2 つ以上のファイルの定数が丸ごと持つ | 41 |
| `member` | 字の定数が別のファイルの集まりの要素になる | 109 |
| `same-num` | 同じ名前で同じ数の定数が 2 つ以上のファイルにある | 15 |
| `test-bind` | テストの assert が別々のモジュールの 2 つの定数の一致を縛る | 5 |
| `test-parse` | テストが定数の定義の字（`NAME=…`）を正規表現で読む | 26 |
| `test-literal` | テストの 3 つ以上の字の集まりが、定数の集まりと同じ集合になる | 138 |

#3144 の例のうち、`design_detail.DETAIL_SUFFIX` はまだ無い（`grep -rn "^DETAIL_SUFFIX" plugins/ndf --include=*.py` が 0 件）。残りの 3 つは `affix` 2 組と `subset` 1 組で拾えた。`WF_STAGE_MATRIX` はシェルの表の 1 列目を工程名の並びとして読み、テストの 1 本（`test_document_rebuild_stage.py`）が `test-parse` で拾えた。

**拾えないもの**: 定数と `.md` の表の一致を縛るテスト（`test_transcript_agents.py` の `STEP_PHASES` と `agent-layers.md`）は構文木に現れず、`grep -rn "\.md\"" plugins/ndf --include=test_*.py` で手で拾った。関数の中の字（定数に名前が無いもの）も拾わない。これらは要求の計測表の 6（4 つ以上のファイルに出る文字列）の側で扱う。

## 事実ごとの移し先

組を、値の中身から事実に分けた（分け方は字の規則。控えは作業の手元の `classify.py`）。下の表は事実ごとの組・定義の数と、定義を持つファイルの直近 60 日の変更の回数の合計（`git log --since=60.days --format= --name-only -- plugins/ndf`。`glossary.py` は用語の同期のコミットで 1,062 回あり、これを含む事実は大きく出る）。

### 上位 10 と、要求が挙げた 2 つ

| 事実 | 組 | 定義 | 変更 | 移し先 | 読む側 | 移さない分と理由 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| 結果の状態の語（`failed`・`pending`・`完了` など） | 46 | 81 | 2,354 | GitHub の check の結論の 2 組（`gh_checks.FAILED_CONCLUSIONS` と `review_lib/ci.CI_FAILED_CONCLUSIONS`、`gh_checks.CI_ONLY_PASSING` と `gh_pr_status.OK_RESULTS`）は持ち主のモジュール `lib/gh_checks.py`。DBOS の状態（`durable.RUNNING` ほか 2 つ）は `lib/durable.py` | import | 残りは移さない。別の状態機械の語が同じ字を持つだけで、事実が違う（R3） |
| 閾値と版 | 15 | 38 | 714 | 名前の同じ閾値（`TAIL_LINES`・`RECORD_DAYS`・`ERROR_EXCERPT`・`RECLAIM_LIMIT`・`BODY_MATCH_CHARS` と `OSCILLATION_BODY_CHARS`・`FOREIGN_WINDOW` と `WINDOW`）は既存の `contracts/limits.json` の方針 | `limits.get(<方針>)` | 形式の版（`VERSION = 1`・`DECLARATION_VERSION = 1`）は移さない。ファイルの形式ごとに別の事実（決定 28、R4） |
| 結果 JSON の `tool` の名前 | 17 | 31 | 1,635 | エントリポイントのモジュールの `TOOL` を持ち主にする | 下のモジュールは import か引数で受ける | 偶然の一致（`TOOL = "release"` と `DEV_SERIES = "release"`）は移さない（R3） |
| ランタイム（`claude`・`codex`・`kiro`・`agy`） | 105 | 18 | 175 | 新しい表 `contracts/facts/runtimes.json`。行はランタイム、列は `host`（導入先）・`partner`（相手役）・`auth_probe`・`usage_observable`・`model_args` | `facts.ids("runtimes", host=True)` などの条件で引く。部分集合を定数に写さない | 無し。テストの字の集まり 80 か所も表から引く形に直す |
| 宣言と状態のファイル名（`.ndf/worktree.json`・`.worktrees`・`glossary.json` ほか） | 8 | 18 | 1,226 | 既存の `contracts/settings/<名前>.json` の `file` と `contracts/files/` の型 | `facts.setting_path("worktree.json")`。シェルは `lib/facts.sh` | 無し |
| 工程名とフェーズ（`設計`・`実装`・`検査`・`仕上げ`・`取り込み`） | 8 | 14 | 304 | #3092 の工程の文書 `contracts/workflow/stages/<工程>.json` に列 `phase` を足す（#3092 の後） | `STEP_PHASES`・`STAGE_PREFIXES`・`POSTS`・`WF_FAST_*_STAGES` は工程の文書から導く | 無し。`agent-layers.md` の表との一致のテストを消す |
| 収束ループのフェーズ（`fix`・`implement`・`add-tests` ほか） | 20 | 13 | 418 | 新しい表 `contracts/facts/loop-phases.json`。列は `coding`・`fix`・`resume_as`。`limits.json` の `monitor.phase.<id>` は外部キー（I-DT3） | cross-review と cross-refactoring の両方が同じ表を引く | 無し |
| 指摘の重さ（`critical`・`major`・`minor`・`nit`） | 14 | 12 | 70 | 新しい表 `contracts/facts/severities.json`。列は `rank`・`blocking` | cross-review・fix・pr-review・`lib/body_findings.py` | 無し |
| 環境変数名（`NDF_CLAUDE_ACCOUNT` ほか） | 7 | 11 | 124 | 持ち主のモジュール（`lib/claude_accounts.py`・`lib/claude_api_keys.py`）の定数。`lib/assignee_env.py` は import | import | `contracts/env/` は登録簿で、コードとの一致を `check-ndf-contracts.py` が既に見るため写しに数えない |
| PR 本文の見出しと印（`## 利用者向けの変化`・`## 移行の手順`・`## 同意を求めること`・レビューの印） | 7 | 11 | 101 | 既存の置き場 `contracts/forms/` に `pr-body.json` を足す | `pr-steps.py`・`release_lib/`・`lib/step_result.py` が引く | 無し |
| モード（要求の例） | 11 | 7 | 1,187 | #3092 のモードの文書 `contracts/workflow/modes/` から導く。「設計を通るモード」は工程「設計」を持つモード、「詳細設計を通るモード」は工程「詳細設計」を持つモード | `glossary.DESIGN_MODES`・`pace.DEFAULT_MODES` は導出に変える | 無し。`WF_MODES`・`WF_MODE_HEIGHT` を正規表現で読むテスト 5 か所を消す |
| 設計文書の種類（要求の例） | 3 | 3 | 1,075 | #3144 の `contracts/design-docs.json` | #3144 の `lib/doc_kinds.py` | 無し。#3144 の実装が移し、この設計は基準の行を消すだけ |

### 残りの事実（規則で扱う）

上の 12 を除く組は、事実に分けた 35 組と、どの事実にも分けられなかった 121 組（定義 222）である。どれも次の規則で扱い、基準の行に扱いを書く（I-DT5）。

| 規則 | 当てる組 | 扱い |
| --- | --- | --- |
| R1 | 2 つ以上の言語（Python とシェル・`.md` の表）か、2 つ以上のコンテキストが読む事実。機能を足すと値が増える集まり | 表へ移す。既存の契約に置き場があればそこ、無ければ `contracts/facts/<名前>.json` |
| R2 | 1 つのコンテキストの Python だけが読む事実と、外部の仕様の語（GitHub・DBOS） | 持ち主のモジュールの定数にし、ほかは import |
| R3 | 別の事実が同じ字を持つだけの組（状態の語・汎用の語・偶然の部分集合） | 移さない。基準に `移さない: 別の事実（<それぞれの事実>）` で残す |
| R4 | ファイルの形式の版 | 移さない（決定 28） |
| R5 | 部分集合で、部分が全体の属性で決まるもの（例: 導入先のランタイム） | 全体の表に列を足し、部分は条件で引く |

R1 に当たるもので上の表に無い事実: ブランチの接頭辞（`sprint/`・`design/`・`release/`。8 定義。`WF_DESIGN_PREFIX` をシェルが持つ）は `contracts/facts/branches.json`、承認ゲートと承認のラベル（`design`・`release`・`design-approved`。6 定義。`WF_APPROVAL_LABEL` をシェルが持つ）は `contracts/facts/gates.json`、進め方（`auto`・`fast`・`normal`。5 定義。`WF_PACES`）は #3092 の後に `contracts/workflow/paces.json`、指示書のファイル（`AGENTS.md`・`CLAUDE.md`・`KIRO.md`。6 定義）は `contracts/facts/instruction-files.json`、版の上げ幅・実行主体の層・プランの種類・テストの置き場と名前の印は R2 で持ち主のモジュールへ寄せる。

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `contracts/facts/<名前>.json`（新設） | 事実の表。行と列だけを持ち、コードを持たない |
| `contracts/facts/fact.schema.json`（新設） | 表の共通の形（STD1） |
| `plugins/ndf/scripts/lib/facts.py`（新設） | 実行時の読み手。表を標準ライブラリの `json` で 1 プロセスに 1 回読み、行・`id`・条件での絞り込みを返す。副命令 `ids` でシェルにも返す |
| `plugins/ndf/scripts/lib/facts.sh`（新設） | シェルの読み手。`ndf_fact_ids <表> [<列>=<値>]` が `lib/facts.py ids` を 1 回呼び、改行区切りで受ける |
| `scripts/contracts_lib/const_facts.py`（新設） | 重複の組を拾う核。`scripts/measure/const-facts.py` と検査が同じ核を呼ぶ |
| `scripts/check-ndf-contracts.py`（変更） | 規則 `const-fact`（重複の組と基準の照合）と `fact-table`（表の形・外部キー）を足す |
| `scripts/const-fact-baseline.json`（新設） | 重複の基準。組の鍵・種類・事実・扱い |
| `scripts/measure/const-facts.py`（この設計の PR で新設） | 移行の前後を測る。DT1 で核を `const_facts.py` へ移し、出力は変えない |

```mermaid
flowchart LR
  T[contracts/facts/*.json] --> F[lib/facts.py]
  W[#3092 contracts/workflow/] --> F
  D[#3144 contracts/design-docs.json] --> F
  F --> P[Python の読み手]
  F --> S[lib/facts.sh]
  S --> H[シェルの読み手]
  C[contracts_lib/const_facts.py] --> K[check-ndf-contracts.py]
  C --> M[measure/const-facts.py]
  B[const-fact-baseline.json] --> K
  T --> K
```

## データ構造

**表は 1 事実 1 ファイルで、行の `id` が事実の値である。** 部分集合は列で表し、別の定数に写さない。

```json
{
  "version": 1,
  "summary": "NDF が起動するランタイム",
  "owner": "外部 CLI の起動",
  "columns": {
    "host": {"type": "boolean", "summary": "NDF を導入する先（Claude Code・Codex）"},
    "partner": {"type": "boolean", "summary": "cross-review・cross-refactoring の相手役として起動する"}
  },
  "rows": [
    {"id": "claude", "host": true, "partner": true},
    {"id": "codex", "host": true, "partner": true},
    {"id": "kiro", "host": false, "partner": true},
    {"id": "agy", "host": false, "partner": true}
  ]
}
```

| 鍵 | 型 | 意味 |
| --- | --- | --- |
| `version` | 整数 | 表の形式の版（決定 28 と同じく NDF の版から切り離す） |
| `summary` | 字 | 事実の 1 行の説明。`contracts/index.md` の逆引きに出る |
| `owner` | 字 | 表を書き換えてよいコンテキスト（[issue-1142-design.md](issue-1142-design.md) のコンテキストの名前） |
| `columns` | 名前 → `{type, summary, ref?}` | 列。`ref` は外部キーの先（例: `limits.json#monitor.phase`） |
| `rows` | 配列 | 並びは意味を持つ（工程の順のように順序が事実のときは並びで表す） |

**基準の行**（`scripts/const-fact-baseline.json` の `rows`）:

| 鍵 | 意味 |
| --- | --- |
| `key` | 組の鍵。種類と、`<ファイル>:<定数の名前>` を並べた字（行番号を含めない。行がずれても鍵は変わらない。ファイルを移すと変わるため、移す PR は I-DT4c の置き換えで行を書き換える） |
| `kind` | 重複の組の種類 |
| `fact` | 事実の名前（上の表の 1 列目） |
| `plan` | `移す: DT<番号>` か `移さない: <理由>` |

読み手の逆引き（どのエントリポイントがどの表を読むか）は、コードの `facts.ids("<表>")` と `ndf_fact_ids <表>` の呼び出しから拾い、`check-ndf-contracts.py --render` が `contracts/index.md` に節「事実の表」を生成する。表に `readers` を手で書かない（写しになる）。

## 入出力の契約

`check-ndf-contracts.py` の結果 JSON の形（`{"tool", "status", "summary", "items", "metrics"}`）と終了コードは変えない。`items` に規則を 2 つ足す。

| 規則 | 落とすもの | 項目の `name` |
| --- | --- | --- |
| `const-fact` | 基準に無い重複の組（新しい重複）。基準にあって今の木に無い行（使われない行）。扱いの無い基準の行。表の値（`id`）と同じ字を持つ新しい定数と、表の `id` を 2 つ以上含む新しい定数の集まり（I-DT1） | 組の鍵 |
| `fact-table` | `fact.schema.json` を満たさない表・重なる `id`・相手に無い外部キー | `<表>:<行>` |

基準の増減は、既存のラチェット（`contracts_lib/ratchet.py`）と同じく、ベースブランチとの merge-base の基準より行が増えた変更を落とす。

## 処理の流れ

```mermaid
flowchart TD
  A[PR の変更] --> B[構文木を 1 回読む（#2996）]
  B --> C[const_facts: 定数とテストから組を拾う]
  C --> D{組が基準にあるか}
  D -- 無い --> X[落とす: 新しい重複]
  D -- ある --> E{基準の行が今の木にあるか}
  E -- 無い --> Y[落とす: 使われない行。直した PR が行を消す]
  E -- ある --> F{定数が表の id を持つか}
  F -- 持つ --> Z[落とす: 表から引く]
  F -- 持たない --> G[fact-table: 表の形と外部キー]
  G --> H[通す]
```

## 移行の順序

**子の課題が触るファイルを避けて先に流せる分を流し、重なる分は子の課題の後に置く。** 子の課題: #2988（step_result と cross-refactoring の import の輪をほどく。閉じた）、#2990（耐久ワークフローの判断を純粋な関数にする）、#2991（構造の検査の基準を責務へ置き直す）、#2963（supervise の遷移の判断を耐久の起動から切り離す）、#2993（supervise の engine・flow・queue の輪をほどく）、#2992（並列の計画の parallel_* を組み直す）、#2994（開発ループの sprint_loop_* を分ける）、#2995（release-steps.py をリリースの工程ごとに分ける）、#2996（構造の検査と契約の検査の構文木の解析を 1 回にする）。並行の設計: #3092（工程の進行表）・#3144（詳細設計の工程）。

| 移行ステップ | 中身 | 触るファイル | 後に置く課題 |
| --- | --- | --- | --- |
| DT1 | 検査の土台: 核・規則・基準・読み手・表の形・`jsonschema` の依存 | 依存の宣言と固定（根とプラグインの両方。どのファイルとどのグループ名かは実装で決める。v10.27.61 の時点で `jsonschema` はどちらにも無い）、`scripts/contracts_lib/const_facts.py`、`scripts/check-ndf-contracts.py`、`scripts/const-fact-baseline.json`、`scripts/measure/const-facts.py`、`plugins/ndf/scripts/lib/facts.py`、`plugins/ndf/scripts/lib/facts.sh`、`plugins/ndf/contracts/facts/fact.schema.json`、`plugins/ndf/contracts/index.md`、`.github/workflows/script-structure.yml`、`docs/specifications/ndf-entrypoint-contracts.md`、`scripts/tests/test_const_facts.py`、`plugins/ndf/scripts/tests/test_facts.py` | #2996（構文木の 1 回の解析に乗る）。#2991 とは触るファイルが重ならない |
| DT2 | ランタイム | `plugins/ndf/contracts/facts/runtimes.json`、`plugins/ndf/scripts/lib/assignment.py`、`plugins/ndf/scripts/lib/auth.py`、`plugins/ndf/scripts/lib/usage_capture.py`、`plugins/ndf/scripts/lib/models.py`、`plugins/ndf/scripts/lib/wait_notice.py`、`plugins/ndf/scripts/hook.py`、`plugins/ndf/scripts/hook_lib/wait_notify.py`、`plugins/ndf/scripts/training-optout.py`、`plugins/ndf/skills/external-ai/scripts/external-ai.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/participants.py`、`plugins/ndf/skills/pr-review/scripts/pr-review-steps.py` と、字の集まりを直すテスト | DT1 |
| DT3 | 収束ループのフェーズ・指摘の重さ・テストの印 | `plugins/ndf/contracts/facts/loop-phases.json`、`plugins/ndf/contracts/facts/severities.json`、`plugins/ndf/scripts/lib/body_findings.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/findings.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/categories.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/ci.py`、`plugins/ndf/skills/cross-refactoring/scripts/assignee_launch.py`、`plugins/ndf/skills/cross-refactoring/scripts/safety_net.py`、`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/pathkinds.py`、`plugins/ndf/skills/fix/scripts/fix-steps.py`、`plugins/ndf/skills/pr-review/scripts/pr_review_findings.py` | DT1 |
| DT4 | 閾値（`limits.json` へ） | `plugins/ndf/contracts/limits.json`、`plugins/ndf/scripts/hook_lib/token_guard.py`、`plugins/ndf/scripts/issue-file.py`、`plugins/ndf/scripts/issue-items.py`、`plugins/ndf/scripts/hook_lib/wait_notify.py`、`plugins/ndf/scripts/relay_lib/version_dir.py`、`plugins/ndf/scripts/lib/run_owner.py`、`plugins/ndf/scripts/lib/post_queue.py`、`plugins/ndf/scripts/lib/finding_match.py`、`plugins/ndf/scripts/lib/container_reach.py`、`plugins/ndf/scripts/supervise_lib/verify_steps.py` | DT2（`hook_lib/wait_notify.py` が重なる） |
| DT5 | 宣言のファイル名・環境変数名・PR 本文の見出し・指示書のファイル | `plugins/ndf/contracts/forms/pr-body.json`、`plugins/ndf/contracts/facts/instruction-files.json`、`plugins/ndf/scripts/hook_lib/worktree.py`、`plugins/ndf/scripts/lib/worktree-common.sh`、`plugins/ndf/scripts/lib/worktree_deps.py`、`plugins/ndf/scripts/lib/worktree_base.py`、`plugins/ndf/scripts/glossary.py`、`plugins/ndf/scripts/merged_lib/base_sync.py`、`plugins/ndf/scripts/lib/diff_kinds.py`、`plugins/ndf/scripts/lib/assignee_env.py`、`plugins/ndf/scripts/pr-steps.py`、`plugins/ndf/scripts/lib/pr_mode.py`、`plugins/ndf/scripts/instructions_lib/declaration.py`、`plugins/ndf/scripts/project_lib/fingerprint.py`、`plugins/ndf/scripts/project_lib/measure_repo.py`、`plugins/ndf/scripts/project_lib/mvv_collect.py` | DT1。`supervise_lib/decl.py` は #2993 の後（DT8） |
| DT6 | モード・工程名とフェーズ・進め方・ブランチの接頭辞・承認ゲートとラベル | `plugins/ndf/contracts/workflow/stages/`（列 `phase`）、`plugins/ndf/contracts/workflow/paces.json`、`plugins/ndf/contracts/facts/branches.json`、`plugins/ndf/contracts/facts/gates.json`、`plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh`、`plugins/ndf/scripts/lib/transcript_agents.py`、`plugins/ndf/scripts/hook_lib/token_guard.py`、`plugins/ndf/scripts/lib/pace.py`、`plugins/ndf/scripts/glossary.py`、`plugins/ndf/scripts/lib/branch_flow.py`、`plugins/ndf/scripts/lib/legacy_names.py`、`plugins/ndf/scripts/lib/gate_rejections.py`、`plugins/ndf/scripts/mvv-gate.py`、`plugins/ndf/skills/cross-review/scripts/classifications.py`、`plugins/ndf/scripts/tests/test_transcript_agents.py`、`plugins/ndf/skills/development-workflow/tests/test_document_rebuild_stage.py`、`plugins/ndf/skills/development-workflow/tests/test_documentation_mode.py`、`plugins/ndf/skills/development-workflow/tests/test_operation_mode.py` | #3092（工程とモードの文書）・#3144（設計文書の種類）。`hook_lib/token_guard.py` が重なる DT4 と、`glossary.py` が重なる DT5 の後 |
| DT7 | 設計文書の種類 | 基準の行を消すだけ（`scripts/const-fact-baseline.json`） | #3144 の実装 |
| DT8 | 子の課題が触るファイルの残り: `tool` の名前・状態の語の持ち主・閾値の残り | `plugins/ndf/scripts/lib/sprint_loop_record.py`（#2994）、`plugins/ndf/scripts/release_lib/materials.py`・`plugins/ndf/scripts/release_lib/others.py`・`plugins/ndf/scripts/release_lib/carry.py`（#2995）、`plugins/ndf/scripts/lib/parallel_switch.py`・`plugins/ndf/scripts/lib/parallel_lane.py`・`plugins/ndf/scripts/lib/parallel_ledger.py`（#2992）、`plugins/ndf/scripts/lib/durable.py`（#2990）、`plugins/ndf/scripts/supervise_lib/decl.py`（#2993） | それぞれの子の課題の後。子の課題の PR が同じ事実に触れたら、その PR で表から引く形にしてよい |
| E9 | 測り直し | `scripts/measure/const-facts.py` を打ち、前後の数を課題のコメントへ | DT1〜DT8 |

DT2・DT3・DT5 は触るファイルが重ならず、並列に流せる。DT4 は DT2 の後に流す。どの移行ステップも、直した組の基準の行を同じ PR で消す（I-DT4b が使われない行を落とすため、消し忘れは残らない）。

## 消す一致のテスト

**移した事実の一致を縛るテストは、その移行ステップの PR で消す。** 振る舞いを確かめるテストは、表から引いた値で書き直して残す。

| テスト | 縛っているもの | 消す移行ステップ |
| --- | --- | --- |
| `plugins/ndf/scripts/tests/test_relay.py:2885` | `relay_version_dir.FOREIGN_WINDOW == run_owner.WINDOW` | DT4 |
| `plugins/ndf/skills/cross-review/tests/test_measure.py:687` | `measure_mod.COUNTED_CLASSIFICATIONS == review_lib.matching.COUNTED_CLASSIFICATIONS` | DT3（値は既に `cross-review/scripts/classifications.py` の 1 か所にあり、テストは import の経路の一致だけを縛る） |
| `plugins/ndf/skills/cross-review/tests/test_queue_idempotency.py:52` | `queue_mod.BODY_MATCH_CHARS == review_lib.matching.OSCILLATION_BODY_CHARS` | DT4 |
| `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py:862` | `verify_steps.RECLAIM_LIMIT == container_reach.RECLAIM_LIMIT` | DT4 |
| `plugins/ndf/scripts/tests/test_transcript_agents.py:243` | `STEP_PHASES` と `agent-layers.md` の表 | DT6 |
| `plugins/ndf/skills/development-workflow/tests/test_document_rebuild_stage.py:37`、`test_documentation_mode.py:28・62`、`test_operation_mode.py:50・56・94` | `WF_STAGE_MATRIX`・`WF_MODES`・`WF_MODE_HEIGHT` の定義の字を正規表現で読む | DT6（工程とモードの文書を読む #3092 のテストへ寄せる） |
| `test-literal` の 138 か所（うちランタイムの集まり 80 か所。多いのは `cross-refactoring/tests/test_init.py` 15・`cross-review/tests/test_state_review_pool.py` 15・`cross-review/tests/test_state_resume_args.py` 12） | 定数の値をテストに写す | 事実の移行ステップ（ランタイムは DT2） |

`test-bind` の残りの 1 件（`test_claude_api_keys.py:232`）と `test-parse` の残り 20 件は、出力の `KEY=value` の行を読むテストで、定数の一致を縛っていない。基準に `移さない: 一致の縛りでない` で残す。

## 決定の記録

### 決定 45: 事実を字で書く場所を 1 つにするため、2 つ以上の言語かコンテキストが読む事実は契約の表に、1 つのコンテキストの Python だけが読む事実は持ち主のモジュールの定数に置く

表へ移すと、読み手が増えても値は 1 か所で、シェルと `.md` の検査も同じ値を引ける。1 つのコンテキストの中だけで読む事実まで表へ移すと、import 1 つで済むものにファイルと読み手の呼び出しが増え、読む量が増える。外部の仕様の語（GitHub の check の結論・DBOS の状態）は外部が決めるため、表にせず包みのモジュールが持つ。

すべてを表へ移す案は、1 コンテキストの事実にも表と検査が要り、移す量が 479 定義に広がるため採らない。すべてを持ち主のモジュールの定数にする案は、シェルの定数（`WF_*`）と `.md` の表が写しのまま残るため採らない。

根拠: Value 8・Value 11（MVV 版 7）

### 決定 46: 表の形をそろえて機械で確かめるため、新しい事実は `contracts/facts/<名前>.json` の 1 事実 1 ファイルにし、JSON Schema の draft 2020-12 で形を確かめる

#3092 と同じ draft と同じ `jsonschema` を使い、別の依存を増やさない。`jsonschema` は最初に使う移行ステップの DT1 が、手元の検査と配布先（プラグインの依存の導入の経路。#3092 の決定 3 と同じ置き場）の両方で使えるようにする。片方だけだと、DT1 の後に流す scripted-flow の子 6 が配布先で答えを確かめ直すときに、依存が無くて止まる。どのファイルとどのグループ名に足すかは実装で決める。#3092 は決定 3 で `jsonschema` を足すとしたが、マージした実装（PR #3171、v10.27.61）は依存に入れておらず、公式スキーマの検査も置いていない。そのため DT1 が足し、#3092 の決定 3 の検査を後から置くときもこの依存を使う。1 事実 1 ファイルにすると、移行ステップごとに触るファイルが分かれ、並列の PR が衝突しない。部分集合は列で表し（R5）、Linguist の `languages.yml` と同じく一覧は 1 つのデータにだけ書く。

1 つの大きな JSON に全事実をまとめる案は、すべての移行ステップが同じファイルを触って並列に流せないため採らない。YAML は標準ライブラリで読めず hook の経路で使えないため採らない。

- 作り方: 標準に倣う・ライブラリやサービスを使う
- 比べた候補: STD1 / LIB1 / LIB2 / PRD1

根拠: Value 8・Value 12（MVV 版 7）

### 決定 47: hook の経路でも同じ表を読めるよう、実行時の読み手は標準ライブラリの `json` だけで 1 プロセスに 1 回読む `lib/facts.py` 1 つにし、部分集合は列の条件で引く

hook は用意済みの環境の python を直に起動し、外部パッケージを前提にできない（決定 20）。`lib/limits.py` が `limits.json` を同じ形で読んでおり、読み方を 1 つにそろえる。形の検査は継続的統合（`fact-table`）が `jsonschema`（DT1 が足す依存。決定 46）で行い、実行時は検査しない。

`pydantic` で型付きの読み手を作る案は、hook の経路に外部パッケージを入れるため採らない。表ごとに読み手のモジュールを作る案は、読み方が表の数だけ増えるため採らない。

- 作り方: 自前で作る
- 比べた候補: LIB3 / LIB1

根拠: Value 9・Value 11（MVV 版 7）

### 決定 48: シェルを Python へ書き換えずに同じ表を読むため、シェルは `lib/facts.sh` の関数が `lib/facts.py ids` を 1 回呼んで値を受ける

シェルを書き換えない方針（決定 14）を保ったまま、`WF_DESIGN_PREFIX`・`WF_APPROVAL_LABEL`・`WT_DECLARATION_FILE` の写しを無くせる。呼ぶのは source の時点の 1 回で、bash 3.2 で動く形（改行区切りの読み）にする。

シェルの定数を残し、表との一致を検査で縛る案は、値を足すときに直すファイルが 2 つのまま残るため採らない。ビルドの時点でシェルの定数を生成する案は、生成物を持つファイルと生成の手順が増えるため採らない。

根拠: Value 11・Value 3（MVV 版 7）

### 決定 49: 工程・モード・設計文書の種類を 2 か所の表に持たないため、並行の設計の置き場（#3092 の `contracts/workflow/`、#3144 の `contracts/design-docs.json`）をそのまま正とし、この設計は導出と検査だけを足す

工程とモードの文書は #3092 が、設計文書の種類の契約は #3144 が作る。この設計が別の表を作ると、同じ事実が 2 つの表に分かれる。工程とフェーズの対応は工程の文書の列 `phase` に置き（#3092 の後の DT6）、「詳細設計を通るモード」はモードの文書から導く。

工程とモードの表をこの設計で先に作る案は、#3092 と同じファイルを触り、置き場が食い違うため採らない。

根拠: Value 11・Value 8（MVV 版 7）

### 決定 50: 新しい重複を止めるため、`check-ndf-contracts.py` に規則 `const-fact` と `fact-table` を足し、基準を `scripts/const-fact-baseline.json` の 1 ファイルに置いて merge-base より行が増えた変更を落とす

コードと契約を突き合わせる検査は `check-ndf-contracts.py`（ジョブ `ndf-contracts-check`）が持ち、merge-base との比べ（`fetch-depth: 0`）と `allow-added` のラチェットが既にある。基準は 417 組と多く、既存の例外リストの 1 項目 1 ファイルの形にすると 400 余りのファイルになるため、1 ファイルの行で持つ。「AI が陥りやすい作り」の兆候の基準ファイル（[issue-1142-design-smells.md](issue-1142-design-smells.md) の決定 37）は元のファイル 1 本に 1 つで持つが、置き方が違うのは書き換える者の数が違うためである。兆候の基準は兆候を減らしたどの PR も書き換える（決定 38）ので、1 ファイルにすると並列の PR が同じファイルで衝突する（#1249）。重複の基準の行を書き換えるのは、事実を移す移行ステップの PR と、定数を別のファイルへ移す PR（子の課題 #2990〜#2995。行を同じ数だけ置き換えるだけで、行は増えない。I-DT4c）である。重複を拾う核は `contracts_lib/const_facts.py` に置き、計測のスクリプトも同じ核を呼ぶ（数え方を 2 つにしない）。構文木は #2996 の 1 回の解析を使う。

静的解析の既存の規則で済ませる案は、`ruff` の `PLR2004` が比較の中の名前の無い値だけを見て、2 つの定数が同じ事実を持つことを見ないため採らない。`pylint` の `duplicate-code` は対象を確かめられず、似た行の検出で値の集合の一致を見るものではないと判断した。構造チェック（`check-script-structure.py`）に置く案は、merge-base の比べを持たず、#2991 と同じファイルを触るため採らない。

- 作り方: 自前で作る
- 比べた候補: LIB4 / LIB5 / LIB2

根拠: Value 11・Value 3（MVV 版 7）

### 決定 51: 一覧の箇所ごとに扱いを残すため、基準の行は `移す: <移行ステップ>` か `移さない: <理由>` を持ち、別の事実が同じ字を持つだけの組と形式の版は移さない

要求の受け入れ条件は、箇所ごとに移したか移さない理由が残ることを求める。字が同じでも事実が違う組（CI の結論の `failed` と投稿キューの `failed`）を 1 つにすると、片方の事実の変更がもう片方を壊す。形式の版はファイルの形式ごとに別に上がる（決定 28）。扱いの無い行は検査が落とす（I-DT5）。

組をすべて移す対象として扱う案は、偶然の一致を結び付けて結合を増やすため採らない。

根拠: Value 14・Value 8（MVV 版 7）

### 決定 52: 一致を縛るテストを残さないため、移した事実の一致のテストはその移行ステップの PR で消し、振る舞いのテストは表から引いた値で書き直す

事実が 1 か所になれば、2 つの定数の一致は成り立つ以外にない。テストに値を写した字の集まり（`test-literal` 138 か所）は、表の行を足すたびにテストも直す写しになる。表の形と外部キーは `fact-table` が見るため、テストで重ねて確かめない。

一致のテストを移行の後も残す案は、同じことを検査とテストが重ねて確かめるため採らない。

根拠: Value 11（MVV 版 7）

### 決定 53: 子の課題と触るファイルを重ねないため、検査の土台は #2996 の後、工程とモードは #3092 の後に置き、残りは子の課題が触るファイルを除いて先に流し、除いた分を子の課題の後に置く

#2996 は構造の検査と契約の検査の構文木の解析を 1 回にし、`check-ndf-contracts.py` を触る。先に規則を足すと、#2996 が解析を組み直すときに規則も書き直しになる。`parallel_*`（#2992）・`sprint_loop_*`（#2994）・`release_lib/`（#2995）・`durable.py`（#2990）・`supervise_lib/`（#2993）に当たる分は DT8 にまとめ、子の課題の後に流す。子の課題の PR が同じ事実に触れたときは、その PR で表から引く形にしてよく、DT8 の行が減る。

子の課題より先にすべてを移す案は、子の課題の PR と同じファイルで衝突するため採らない。

根拠: Value 5（MVV 版 7）

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 要求「同じ事実を 2 か所以上の定数に持つ箇所の一覧がある」 | `const-facts.py --json` が組の一覧を返し、基準のファイルが全組を持つ | 核から種類を 1 つ外す（例: `affix` を拾わない）と、#3144 の例の 3 組が一覧から消える |
| 要求「箇所ごとに、移したか移さない理由が残る」・I-DT5 | 扱いの無い基準の行で検査が落ちる | 扱いを空にした行を通す |
| 要求「値を足すときに直すファイルが 1 つになる」・I-DT1 | ランタイムの表に行を 1 つ足すだけで、`host` の条件で引く読み手の結果に現れる | 読み手が字の集まりを持つ |
| 要求「新しく足す定数が既にあるデータと同じ事実を持たないことを CI が確かめる」 | 表の `id` を 2 つ含む集まりを持つ定数を足した木で、`const-fact` が落ちる | 表の値との照合を外す |
| I-DT2 | `id` の重なる表・列の型の違う行で `fact-table` が落ちる | スキーマの検査を外す |
| I-DT3 | `loop-phases.json` に `limits.json` の `monitor.phase.<id>` に無い行を足すと落ちる | 外部キーの照合を外す |
| I-DT4a | 基準に無い組を足すと「増えた組」で落ちる | 拾った組と基準の照合を外す |
| I-DT4b | 直した組の行を基準に残すと「使われない行」で落ちる | 基準の行と今の木の照合を外す |
| I-DT4c | 定数を別のファイルへ移し、基準の行のファイルだけを同じ数だけ書き換えた PR が通る。種類か事実か定数の名前の違う行を足した PR と、消した数より多く足した PR は落ちる | 置き換えを数えずに増えた行として落とす・ファイル以外の違う行も置き換えとして通す |
| I-DT6 | `lib/facts.py` を hook の経路から import した木で、構造チェックの `hook-deps` が外部パッケージの import を見つけない | `lib/facts.py` が `jsonschema` を import する |
| 決定 48 | `ndf_fact_ids runtimes host=true` が bash 3.2 で導入先のランタイムを改行区切りで返す | 列の条件を読まずに全行を返す |

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #1142 | 実装する | — | `scripts/measure/const-facts.py`、`scripts/contracts_lib/const_facts.py`、`scripts/check-ndf-contracts.py`、`scripts/const-fact-baseline.json`、`scripts/tests/test_const_facts.py`、`.github/workflows/script-structure.yml`、`docs/specifications/ndf-entrypoint-contracts.md`、`plugins/ndf/scripts/lib/facts.py`、`plugins/ndf/scripts/lib/facts.sh`、`plugins/ndf/scripts/tests/test_facts.py`、`plugins/ndf/contracts/facts/fact.schema.json`、`plugins/ndf/contracts/facts/runtimes.json`、`plugins/ndf/contracts/facts/loop-phases.json`、`plugins/ndf/contracts/facts/severities.json`、`plugins/ndf/contracts/facts/branches.json`、`plugins/ndf/contracts/facts/gates.json`、`plugins/ndf/contracts/facts/instruction-files.json`、`plugins/ndf/contracts/forms/pr-body.json`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/contracts/index.md` |

移行ステップごとの触るファイルは「移行の順序」の表にあり、DT2〜DT8 の各 PR がその行のファイルを触る。#1142 の先頭の設計文書の表へは、移行ステップを並べる時点で写す（DT6・DT8 は #3092 と子の課題の後のため、この設計の承認の時点では並べない）。

## 人へ残す判断

無し

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 基準の行の数 | 417 組のうち R3（別の事実）で移さない組の数は、基準のファイルを作る DT1 で 1 組ずつ扱いを書いた時点で決まる。上の表の事実への分け方は字の規則で、`その他` の 121 組はまだ分けていない |
| 検査の所要時間 | `const-facts.py` は手元で約 3.6 秒（`time` で測った）。#2996 の 1 回の解析に乗せた後の増分は DT1 の CI で測る |
| `lib/facts.sh` の起動の費用 | source のたびに python を 1 回起動する。worktree の hook の経路のシェル（`worktree-common.sh`）で体感の遅れが出るかは DT5 で測る |
| #3092 の工程の文書に列 `phase` を足せるか | #3092 の工程の文書は Serverless Workflow の書式で、独自の列を置ける場所（`metadata` など）は #3092 の実装で決まる。置けなければ `contracts/facts/stage-phases.json` に工程の名前を外部キーとして持つ |
