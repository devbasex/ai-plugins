# 設計承認ゲートの承認資料: 一覧で過不足を決め、CLI と Factory でリンク集として開ける（#2721）

## 目的

- **今どうなっているか**: 設計で書く成果物の多く（データ構造の図・画面・技術の選定・非機能・構成図）が設計承認ゲートの提示に現れない。動作する画面モックと周辺システムとの関係は、書き方の定義も無い。承認する人は設計文書を探して読むまで判断できない
- **誰に効くか**: 設計承認ゲートで承認する人と、`standard` の設計を書く担い手
- **変えると何が成り立つか**: 承認資料が 1 つの一覧で過不足なく決まり、欠けた資料は検査が名前で挙げる。承認する人は CLI でも Factory でも、資料 1 件 1 行のリンク集から各資料を開いて判断できる

## 適用範囲

- **働く範囲**: NDF を使うすべてのリポジトリの、`standard` の設計と設計承認ゲート。Factory の画面は規定までで、実装は #2815 が持つ
- **プロジェクトごとに違うもの**: 画面モックの表現方法。`.ndf/project.json` の新しい欄 `ui_mock` で受ける。ほかに新しい設定は足さない
- **当たるモード**: `standard`。`legacy-refactor` の設計 Pull Request はリンク集の組み立てだけを使う。`light` / `operation` / `documentation` の工程は変えない
- **触る領域**: 構成要素・型と振る舞い・呼び出される約束・非機能・ドメインモデル（Factory の画面は規定だけで作らないため、画面に当たらない）

## あるべき姿の根拠

| 根拠 | 種類 | 何を示すか |
| --- | --- | --- |
| 依頼の原文「メインのコンソールや Web 画面はリンク集となり、各リンクをたどって見れるようにすることが望ましい」（要求の「依頼（原文）」） | 利用者の指示の原文 | 提示の本体は資料 1 件 1 行のリンク集にする |
| 依頼の原文「画面モック（動作するもの）…プロジェクトでの表現方法の定義を必須とする」 | 利用者の指示の原文 | 画面モックの置き場はプロジェクトの設定で宣言し、無いときは止める |
| arc42 の 12 節・C4 モデルの 4 階層と補助の図（STD1・STD2） | 外部の一次情報 | 構成要素・文脈・配置・実行時の振る舞い・決定・品質・リスク・用語を、別々の問いとして持つ |
| Google の設計文書の「cross-cutting concerns such as security, privacy, and observability」（PRD1） | 外部の一次情報 | セキュリティ・ログとアラートは、周辺との接し方として必ず問う |
| `grep -rn "モック" plugins/ndf/skills/design plugins/ndf/skills/development-workflow` が 0 件（要求の「今の定義」） | 実測 | 画面モックの書き方と置き場の定義が今は無い |

要求と受け入れ条件は #2721 の本文にある（コピーは [issue-2721-requirements.md](issue-2721-requirements.md) ）。この文書は「どう作るか」だけを扱う。
決定の記録は [issue-2721-design-decisions.md](issue-2721-design-decisions.md) にある。承認資料の一覧は [issue-2721-design-materials.md](issue-2721-design-materials.md) にある。

## ドメインモデル

### コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| NDF の開発ワークフロー（`ndf-workflow`） | 承認資料・承認資料の一覧・リンク集・画面モック・デザインイメージ・技術スタックの一覧・周辺システムとの関係・触る領域 |
| Factory（`ndf-factory`） | 承認の依頼の事象・承認の画面 |

`ndf-workflow` と `ndf-factory` の関係は公開された言語（Published Language）とする。リンク集の 1 行の形（資料名・要旨・リンク・作る段階）を先に固め、NDF はその形で出し、Factory はその形で受ける。互いの実装は独立して変えられる。

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 承認資料の一覧 | `design/references/approval-materials.md` を変える Pull Request だけ | 一覧 | 資料の行 | 水準・作る段階・該当の判定（触る領域）・置き場の節 |
| 設計文書 | 設計の担い手（`design`） | 設計文書 | 節 | 触る領域の行・`無し（<理由>）` の行 |
| プロジェクトの宣言 | 利用者と `project-decl.py write` | 宣言 | — | 画面モックの表現方法（`ui_mock`） |
| リンク集 | 設計時の行は `design-materials.py links`、承認時の行は conductor | リンク集 | 行 | 資料名・要旨・リンク |

検査（`design-materials.py check`）は 3 つの集約を読むだけで、どれも書き換えない。

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 承認資料の一覧 | 資料名は重複しない。水準は `必須` か `該当時`、作る段階は `設計時` か `承認時` で、`該当時` の行は既知の触る領域を 1 つ持つ | 検査とリンク集の組み立てが、一覧の壊れた行を挙げて終了コード 2 で止まる |
| I2 | 設計文書 | 該当する資料の置き場の節が、本文を持って存在する | 検査が資料名を 1 件ずつ挙げて終了コード 1 で終わる |
| I3 | 設計文書 | 触る領域の行が無いときは、`該当時` の資料をすべて該当として扱う | （判定の規則。破れる経路は無い。I2 の検査が欠けを挙げる） |
| I4 | プロジェクトの宣言 | 触る領域に画面がある（か判定できない）設計では、画面モックの表現方法が宣言されている | 検査が「画面モックの表現方法が未宣言」を挙げて終了コード 1 で終わる。画面が無い設計では挙げない |
| I5 | リンク集 | 該当しない資料の行を出さない。必須で対象が無い資料は `無し（<理由>）` の 1 行で出す | 組み立てがこの規則でだけ行を作る |
| I6 | リンク集 | 1 行は資料名・改行を含まない 120 字以内の要旨・リンクで、資料の中身を写さない | 要旨を 120 字で切り、末尾に `…` を付ける |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 設計で触る領域を決めた | 設計の担い手（手順 1） | 設計文書の触る領域の行 |
| E2 | 該当する資料の一覧が決まった | `design-materials.py`（一覧と触る領域から） | 検査とリンク集の組み立て |
| E3 | 資料を書いた（画面を変えるなら、画面モックを宣言どおりの置き場に置いた） | 設計の担い手 | 検査 |
| E4 | 資料の欠けの検査を通した | `design-materials.py check`（単発は手順 5、スプリントは Draft を外す前。`ui_mock` の秘密の判定だけは、スプリントでも `pr` の前に `check --secret-only` で通す） | 設計 Pull Request の作成か Draft の解除 |
| E5 | 設計 Pull Request を出した | `pr` | リンク集の組み立て |
| E6 | CLI で承認の提示を組み立てた | conductor（`design-materials.py links` の行に承認時の 3 行を足す） | 承認する人 |
| E7 | Factory に承認の依頼と資料のリンク集が届いた | 実行の担い手（`factory/runner/`。#2815） | Factory の承認の画面 |
| E8 | 承認する人がリンク集から資料を開いて承認か差し戻しを答えた | 承認する人 | 差し戻しの記録（`gate-rejections.jsonl`） |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 承認資料の一覧 | 設計承認ゲートで示しうる資料を、資料名・判断する問い・作る段階・水準・該当の判定・形式・書き方の詳細・根拠・置き場の節とともに並べた正本の表 | 追加（要求で足した行の正本を `approval-materials.md` へ移す） |
| 画面モック | 画面を変える変更で、承認する人が実際に操作して挙動を確かめられる試作の画面。見た目だけを示すデザインイメージとは別の資料 | 追加（同上） |
| デザインイメージ | 画面の見た目を示す画像か、見た目の値（色・寸法・字面）の記述 | 追加（同上） |
| 技術スタックの一覧 | 変更が使うインフラ・外部サービス・ライブラリと、それぞれを採った理由（新規のものは比べた候補、自作するものは既存のライブラリを採らなかった理由）を並べた表 | 追加（同上） |
| 周辺システムとの関係 | 変更が接する既存の機能と外部の系（セキュリティ・データ分析・ログ出力とアラートを含む）と、その接し方 | 追加（同上） |
| リンク集 | 承認の提示の本体の形。資料 1 件につき 1 行で、資料名・要旨・開くリンクを並べる | 追加（同上） |
| 触る領域 | 設計の手順 1 で決める、変更が触る設計の領域。語は `design` の手順 1 の表の 7 つ（構成要素・型と振る舞い・永続データ・呼び出される約束・画面・非機能・ドメインモデル）に限る | 追加 |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 設計文書と決定の記録の書式（arc42・C4 モデル・ADR）と、見出しへのリンクの規則に事実上の標準がある |
| 主要な製品 | 調べる | 設計レビューの資料を主要な組織がどう組んでいるか（Google の設計文書・Rust の RFC）を比べる |
| ライブラリとサービス | 調べる | 動作する画面モックの置き場（Storybook・Chromatic・プレビュー環境・Claude の artifact）と、検査に使える手元の部品を比べる |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | arc42 | 設計文書の 12 節（Introduction & Goals・Constraints・Context & Scope・Solution Strategy・Building Block View・Runtime View・Deployment View・Crosscutting Concepts・Architectural Decisions・Quality Requirements・Risks & Technical Debt・Glossary） | 一覧の行の分け方（文脈・構成要素・実行時の振る舞い・配置・決定・品質・リスク・用語を別の問いにする）と根拠の列 | https://arc42.org/overview （2026-11-08） |
| STD2 | C4 モデル | 4 階層（System context・Container・Component・Code）と補助の図（system landscape・dynamic・deployment） | 構成要素の行と周辺システムとの関係の行を分け、文脈図と配置図を周辺の行に置く | https://c4model.com/ （2026-11-08） |
| STD3 | ADR（Architectural Decision Record） | 「captures a single AD and its rationale」。MADR・Nygard・Y-Statement のテンプレート | 決定の記録を 1 行に保ち、採らなかった案と理由を同じ行の資料にする | https://adr.github.io/ と https://adr.github.io/adr-templates/ （2026-11-08） |
| STD4 | Nygard の ADR の書式 | title・context・decision・status・consequences | 決定の記録の行の根拠 | https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions （2026-11-08） |
| STD5 | Design Tokens Format Module 2025.10 | 見た目の値の交換形式（`$value` が必須、`$type`・`$description` は任意）。W3C の標準の過程に無い Community Group の文書 | デザインイメージの「見た目の値の記述」の形として指す（`interface-ui.md` の既存の記述を引き継ぐ） | https://www.designtokens.org/tr/drafts/format/ （2026-11-08） |
| STD6 | GitHub の見出しへのリンク | 小文字にし、空白をハイフンへ、ほかの空白と記号を除く。重なれば `-1` から番号を足す | リンク集の URL のアンカーを、文書全体を読んだ既存の `md.headings` が返す見出しの `anchor` で作る | https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax （2026-11-08） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | Google の設計文書 | Context and scope・Goals and non-goals・The actual design（system-context diagram・APIs・data storage）・Alternatives considered・Cross-cutting concerns。cross-cutting は「security, privacy, and observability」を必ず考える節 | 周辺システムとの関係の行に、セキュリティ・データ分析・ログとアラートの観点を固定で置く | https://www.industrialempathy.com/posts/design-docs-at-google/ （2026-11-08） |
| PRD2 | Rust の RFC のテンプレート | Summary・Motivation・Guide-level explanation・Reference-level explanation・Drawbacks・Rationale and alternatives・Prior art・Unresolved questions・Future possibilities | 外部の調査（Prior art）と未確認のまま残ること（Unresolved questions）を独立した行にする | https://github.com/rust-lang/rfcs/blob/master/0000-template.md （2026-11-08） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | `plugins/ndf/scripts/lib/md.py`（Markdown の節・表・見出しのアンカーを読む既存の部品） | リポジトリの HEAD | リポジトリの LICENSE | — | 無し | 満たす。節の範囲・表のセル・アンカーを読める | `plugins/ndf/scripts/lib/md.py`（2026-11-08） |
| LIB2 | Storybook | 10.6 | 未確認 | 未確認 | 無し | 満たす（部品と画面を単独で動かせる。静的に組んで公開し、レビューで操作できる） | https://storybook.js.org/docs と https://storybook.js.org/docs/sharing/publish-storybook （2026-11-08） |
| LIB3 | Chromatic | 未確認 | 未確認 | 未確認 | 無し | 満たす（Pull Request ごとに公開した Storybook のリンクをチェックに出す） | https://storybook.js.org/docs/sharing/publish-storybook （2026-11-08） |
| LIB4 | Vercel のプレビュー環境 | — | 未確認 | — | 無し | 満たす（ブランチと Pull Request ごとに URL が付く） | https://vercel.com/docs/deployments/environments （2026-11-08） |
| LIB5 | Claude の artifact | — | 未確認 | — | 無し | 満たす（小さな対話できる画面を作り、共有のリンクで開ける。Claude Code から非公開の URL で公開できる） | https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them （2026-11-08） |
| LIB6 | `plugins/ndf/scripts/design-survey.py`（設計文書の決定を読む既存の検査） | リポジトリの HEAD | リポジトリの LICENSE | — | 無し | 一部。設計文書のまとまりを読む部分は使えるが、責務は決定の 2 行の検査 | `plugins/ndf/scripts/design-survey.py`（2026-11-08） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| LIB2〜LIB5 のライセンス・最後のリリースの日付・版 | 試していない | 依存として足さず、画面モックの表現方法の値の種類として名前を使うだけのため、取得の対象から外した |
| ADR の管理ツール（adr-tools・log4brains）が資料の欠けを検査できるか | 試していない | 検査の対象は設計文書の節であり、ADR の 1 ファイル 1 決定の形と合わないため調べなかった |
| Design Tokens Format Module の 39,945 字より後の節 | https://www.designtokens.org/tr/drafts/format/ | 取得の結果が要約だけで返り、原文を読めなかった。設計は先頭の節（`$value`・`$type`・`$description`）だけに拠る |

調べた量: 候補 14 件・取得の試み 13 回。

## 技術スタックの一覧

| # | 種類 | 名前 | 新規か既存か | 使う理由 | 比べた候補 |
| --- | --- | --- | --- | --- | --- |
| T1 | ライブラリ | `lib/md.py`・`lib/design_docs.py`・`lib/step_result.py`・`lib/project_decl.py` | 既存 | 節・表・アンカーの読み取り、文書のまとまり、結果の形、宣言の読み取りを既に持つ | LIB1・LIB6 |
| T2 | 自作 | `design-materials.py` | 新規 | 一覧の表と設計文書の節を突き合わせる検査は、手元の取り決め（一覧の列・触る領域の語）に依り、既存のツールに当たるものが無い | LIB1・LIB6・STD3 |
| T3 | インフラ・サービス | GitHub（`gh`） | 既存 | Pull Request の head ブランチを読み、URL を作る | — |

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | 承認資料の一覧で、設計で書く資料と承認で見る資料を確かめる | 設計の担い手・承認する人 |
| F2 | 設計文書の欠けた資料を名前で知る | 設計の担い手・supervisor |
| F3 | 画面モックの表現方法をプロジェクトの設定に宣言する | 利用者 |
| F4 | CLI の提示のリンク集から資料を開いて承認する | 承認する人 |
| F5 | Factory の承認の画面のリンク集から資料を開いて承認する（規定まで。実装は #2815） | 承認する人 |

## 構成要素

| 要素 | 新設 / 変更 | 責務 |
| --- | --- | --- |
| `plugins/ndf/skills/design/references/approval-materials.md` | 新設 | 承認資料の一覧の正本（[issue-2721-design-materials.md](issue-2721-design-materials.md) の表）。行の意味・水準の読み方・リンク集の 1 行の形を持つ |
| `plugins/ndf/skills/design/references/deliverables.md` | 変更 | 要否表から `standard` の列を外し、`standard` の水準は一覧が持つと書く。`standard` の省いた理由の書き先を「設計文書の該当の節の `無し（<理由>）`」に変える。`light` / `operation` / `legacy-refactor` の列と行は変えない |
| `plugins/ndf/skills/design/references/design-template.md` | 変更 | 適用範囲に触る領域の行を足す。節「技術スタックの一覧」「周辺システムとの関係」「画面」（「画面モック」「デザインイメージ」）を足し、「入出力の契約」を API だけの節にする。節と実装計画の対応の表へ 3 行を足す |
| `plugins/ndf/skills/design/references/external-survey.md` | 変更 | 技術スタックの一覧の書き方（種類・新規か既存か・使う理由・比べた候補）。調査の表の番号で候補を指す |
| `plugins/ndf/skills/design/references/system-architecture.md` | 変更 | 周辺システムとの関係の書き方（文脈図・配置図と、観点 5 行の表） |
| `plugins/ndf/skills/design/references/interface-ui.md` | 変更 | 画面モックとデザインイメージの書き方。画面モックは宣言された置き場へのリンクを節に置く |
| `plugins/ndf/skills/design/references/data-structure.md` | 変更 | データの流れの図（どのデータがどの構成要素と外部の系を通るか）の書き方 |
| `plugins/ndf/skills/design/SKILL.md` | 変更 | 手順 1 で触る領域を適用範囲に書く。手順 5 で `design-materials.py check` を打つ（`standard` だけ） |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | 変更 | 設計 Pull Request の「判断に使うもの」を一覧の参照に置き換え、提示の本体をリンク集にする。`documentation` の企画承認とリリース承認ゲートの節は変えない |
| `plugins/ndf/skills/development-workflow/references/project-analysis.md` | 変更 | `ui_mock` は解析で書かず利用者が書く、と答え方の表に 1 行足す |
| `plugins/ndf/scripts/design-materials.py` | 新設 | `check`（欠けの検査）と `links`（リンク集の設計時の行）。一覧の表を実行時に読む |
| `plugins/ndf/scripts/project_lib/model.py` | 変更 | `UiMock`（`kind`・`location`）を足し、`ProjectDecl` に省略できる `ui_mock` を足す |
| `plugins/ndf/scripts/project_lib/merge.py` | 変更 | `merge_project()` が解析の項目を巡回した後に、既存の宣言の `ui_mock` へ秘密の検査を掛ける。秘密の形なら `{"unknown": "秘密の形"}` に置き換え、`secret` の行（`{"kind": "secret", "key": "ui_mock"}`）を出す。`project-decl.py write` はこの行を `ui_mock: 秘密の形の値を捨てて不明にした` と表示し、置き換えた欄の名前だけを利用者へ知らせて値は出さない。値は補完しない。置き換えは利用者が承認した（C7・C1、2026-11-08） |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更（生成物） | `project-decl.py schema` で作り直す |
| `plugins/ndf/scripts/supervise_lib/procedures.py`・`plugins/ndf/scripts/supervise_lib/sprint_waves.py`・`plugins/ndf/scripts/supervise_lib/paths.py` | 変更 | スプリントの設計のプランに、`ui_mock` の秘密の判定（`materials-secret`）・欠けの検査（`materials-check`・`fix-materials`・`materials-recheck`）・リンク集の組み立て（`materials-links`）を足す。`materials-secret` は `rebuild` と `pr` の間に置き、`design-materials.py check --secret-only` を打つ。落ちれば `pr` へ進まずに止め、「判断が要る」として人へ戻す（設計 Pull Request の push と cross-review より前に止めるため）。`materials-secret` は `standard` と `legacy-refactor` のときに足す。検査の 3 ステップは `a.mode == "standard"` のときだけ足し、`materials-links` は `standard` と `legacy-refactor` のときに足して `gate` の入力に加える。ほかのモードのプランは変えない |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__design-materials.py.json` | 新設（生成物） | エントリポイントの契約。既存の生成の手順で作る |
| `plugins/ndf/scripts/tests/test_design_materials.py` | 新設 | 検査とリンク集の入出力のテスト |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更 | 用語の 7 語の正本を `approval-materials.md` に向け（`pending_source` を `source` へ）、`glossary.py render` で作り直す |

```mermaid
graph TB
  subgraph design[design の参照]
    AM[approval-materials.md<br>承認資料の一覧]
    DL[deliverables.md]
    DT[design-template.md]
    REF[external-survey / system-architecture /<br>interface-ui / data-structure]
  end
  subgraph wf[development-workflow]
    AR[approval-request.md]
    PJ[project.json の ui_mock]
  end
  subgraph scripts[スクリプト]
    DM[design-materials.py<br>check / links]
    SW[スプリントの設計のプラン]
  end
  DL -->|standard の水準| AM
  AR -->|判断に使うもの| AM
  AM -->|書き方の詳細| REF
  AM -->|節の並び| DT
  DM -->|読む| AM
  DM -->|読む| PJ
  SW -->|打つ| DM
```

## 承認資料の一覧

[issue-2721-design-materials.md](issue-2721-design-materials.md) にある。

## 周辺システムとの関係

```mermaid
graph LR
  AI[設計の担い手<br>と conductor] -->|打つ| DM[design-materials.py]
  DM -->|読む| GH[GitHub の Pull Request<br>gh pr view]
  DM -->|読む| PJ[.ndf/project.json]
  AI -->|直前の応答に書く| CLI[承認する人の端末]
  RN[Factory の実行の担い手<br>#2815] -->|links を載せた gate.reached| API[Factory の API]
  API --> WEB[Factory の承認の画面<br>#2815]
  CLI -->|リンクを開く| GH
  WEB -->|リンクを開く| GH
```

| 観点 | 接する相手 | 接し方 | この変更で変わること |
| --- | --- | --- | --- |
| 既存の機能 | 外部の調査の検査（`design-survey.py`）・用語チェック・本文の決定の同期（`pr-body-decisions.sh`） | 同じ設計文書を読む。互いの出力は使わない | 無し。スプリントの設計のプランで、外部の調査の検査の後に並ぶ |
| 外部の系 | GitHub（`gh pr view` で head ブランチと URL を読む） | 読むだけ。書き込まない | `links` が読む。`gh` が使えないときは URL の代わりにリポジトリの中のパスを出す |
| セキュリティ（認証・認可・秘密） | `ui_mock.location` | URL かパスを書く欄で、秘密を書かない | 既存の秘密の除去は解析の項目（`ITEM_KEYS`）を巡回するときだけ働き、`ui_mock` は対象の外である。そこで `merge_project()` が巡回の後に `ui_mock` へ同じ判定（`has_secret`）を掛け、秘密の形なら `{"unknown": "秘密の形"}` に置き換えて値を出さない。置き換えたことは `project-decl.py write` の出力の 1 行（`ui_mock: 秘密の形の値を捨てて不明にした`）で利用者へ知らせ、欄の名前だけを示して値は出さない。この置き換えは利用者の宣言（C7）を書き換えるが、秘密を漏らさない守り（C1）として利用者が設計承認ゲートで認めた（2026-11-08）。解析で値を補完しない方針は変えない。利用者が手で書いて保存を通らない宣言は、`design-materials.py check` が同じ判定を掛け、秘密の形なら値を出さずに `ui_mock_secret` で止める（宣言は書き換えない）。スプリントでは、この判定だけを `check --secret-only` として設計 Pull Request を出す `pr` のステップより前に打ち、公開されるリポジトリと外部の AI のレビューへ値が出る前に止める。新しい認証・認可は無い |
| データ分析（計測・集計） | 承認の答えの記録（`gate-rejections.jsonl`） | 変えない | 無し（提示の形だけが変わり、答えの記録の形は変わらない） |
| ログ出力とアラート | スクリプトの結果の 1 行の JSON（`step_result`） | 既存の形で出す | 無し（新しいログとアラートは足さない） |

## 構造

```mermaid
classDiagram
  class ProjectDecl {
    ui_mock: UiMock | Unknown | None
  }
  class UiMock {
    kind: artifact | storybook | preview | other
    location: str
  }
  class CatalogRow {
    name: str
    question: str
    stage: 設計時 | 承認時
    level: 必須 | 該当時
    area: str | None
    place: str | None
  }
  class LinkRow {
    name: str
    summary: str
    url: str | None
    stage: design | approval
  }
  ProjectDecl --> UiMock
  CatalogRow ..> LinkRow : 該当する行から作る
```

`CatalogRow` と `LinkRow` は `design-materials.py` の中の型で、ほかのスクリプトへ出さない。`LinkRow` の形だけを、結果の JSON と Factory の `links` で共有する。

## 入出力の契約

### `design-materials.py`

```text
python3 design-materials.py check (--issue N... | --file PATH... | --secret-only) [--root DIR]
python3 design-materials.py links (--issue N... | --file PATH...) [--pr N] [--out PATH] [--root DIR]
```

- `--issue` / `--file` の読み方は `design-survey.py` と同じである。`--issue` は HEAD の `issues/` から番号でつながる設計文書と仕様のコピーを読み、`--file` は worktree から読む。文書を読む部分は `lib/design_docs.py` を共有する
- 一覧の表は `design/references/approval-materials.md` から読む（スクリプトからの相対パス）
- 触る領域の行は、設計文書の「適用範囲」の節の `触る領域` で始まる箇条（`- **触る領域**: 画面・永続データ`）から読む。区切りは `・`・`、`・`,`。値 `無し` は該当時の資料が無いことを表す
- 設計文書を分けたときは、まとまりの全ファイルを 1 つの設計文書として読む。まとまりは、本体の `issue-<番号>-design.md` と分割先の `issue-<番号>-design-<名前>.md` である。資料の置き場の節はまとまりの全ファイルから探し、同じ見出しの節が複数あれば中身を持つ方（分割先へのリンクだけの節でない方）を採る。I2 の「本文を持つ」は、採った節で判定する。要旨の件数はまとまり全体で数え、`url` は採った節のファイルとアンカーで作る
- 画面モックの表現方法は `project_decl.read_project_decl(root)` の `ui_mock` から読む。宣言は利用者が手で書くため、保存時の置き換え（`project-decl.py write`）を通っていないことがある。そこで `check` は読んだ `ui_mock` へ `has_secret` を掛ける。秘密の形なら値を出さずに `ui_mock_secret` を挙げ、終了コード 1 で終わる
- `check --secret-only` は設計文書も一覧の表も読まず、宣言の `ui_mock` の秘密の判定だけを行う。秘密の形なら `ui_mock_secret` で終了コード 1、宣言を読めなければ終了コード 2、それ以外（`ui_mock` が無い・不明を含む）は 0 で終わる。スプリントの設計のプランが、設計 Pull Request を出す `pr` のステップより前に打つ

### `design-materials.py` の結果

`check` の結果（`lib/step_result.py` の形の 1 行の JSON）:

| 終了コード | status | items の `rule` |
| --- | --- | --- |
| 0 | ok | 無し |
| 1 | stopped | `missing`（該当する資料の節が無いか本文が空）・`mock_link_missing`（画面モックの節にリンクもパスも無い）・`ui_mock_undeclared`（画面を変える設計で表現方法が未宣言）・`unknown_area`（触る領域の行に 7 語の外の語がある）・`ui_mock_secret`（宣言の `ui_mock` が秘密の形。`text` は `ui_mock` の欄の名前だけで、値は出さない） |
| 2 | stopped | `catalog_broken`（一覧の表が I1 を満たさない）・文書か git を読めない |
| 3 | stopped | `--issue` で文書が 1 つも見つからない |

items の 1 件は `{"name": "<資料名>", "rule": "<上の値>", "text": "<置き場の節か、読んだ行>"}` である。`missing` は 1 資料 1 件で、資料名だけで欠けが分かる。

`links` の結果: items に設計時の行を一覧の順で並べる。1 件は次の形で、Factory の承認の依頼の事象の `links` の 1 件と同じ形である（公開された言語）。

| キー | 型 | 中身 |
| --- | --- | --- |
| `name` | 文字列（1〜100 字） | 資料名（一覧の「資料名」の括弧の前） |
| `summary` | 文字列（0〜120 字、改行なし） | 要旨（下の規則） |
| `url` | 文字列か `null` | 節を開く URL。`--pr` か今のブランチの Pull Request があれば `https://github.com/<所有者>/<名前>/blob/<head ブランチ>/<パス>#<アンカー>`、無ければ `<パス>#<アンカー>`。節が無ければ `null` |
| `stage` | `design` / `approval` | 作る段階。`links` が出すのは `design` だけ |

要旨の規則:

| 資料 | 要旨 |
| --- | --- |
| 決定の記録 | `決定 <N> 件` |
| 要求と受け入れ条件 | `受け入れ条件 <N> 件` |
| 起票者の推定 | `<N> 件（承認で確かめる）`。行が無ければ `無し` |
| 用語 | `追加 <a> 語・意味の変更 <b> 語` |
| 外部の調査 | `候補 <N> 件`。省いたときは `省く: <理由>` |
| 未確認のまま残ること | `<N> 件` |
| 画面モック | 節の最初のリンクの文字（URL は `url` に入る） |
| ほか | 節の最初の文の行（Markdown の記号を除く） |
| 節が無い | `未確認（節が無い）` |

`--out` を渡すと、同じ行を CLI の提示の形（`- <資料名>: <要旨> <URL>`）で書く。URL の前後に半角の空白を置く（`document-style` のルール 12）。

### `.ndf/project.json` の `ui_mock`

```json
{"ui_mock": {"kind": "storybook", "location": "https://<所有者>.github.io/<名前>/pr-{pr}/"}}
```

| キー | 型 | 中身 |
| --- | --- | --- |
| `kind` | `artifact` / `storybook` / `preview` / `other` | 表現方法の種類（LIB5 / LIB2・LIB3 / LIB4 / それ以外） |
| `location` | 文字列（1 字以上） | 画面モックを置く場所。URL のテンプレートかリポジトリの中のパス。`{pr}` と `{branch}` を差し込める |

欄は省略できる（今の宣言はそのまま読める）。`{"unknown": "<理由>"}` と空の欄は未宣言として扱う。`location` が秘密の形（資格情報を含む URL など）なら、`project-decl.py write` の保存で `{"unknown": "秘密の形"}` に置き換わる。出力には次の 1 行が出て、値は出さない。

```text
  ui_mock: 秘密の形の値を捨てて不明にした
```

### Factory の承認の画面

規定まで（受け入れ条件 12）。実装は #2815 が持つ。

承認の依頼の事象（`gate.reached`）の本文に、省略できる `links` を足す。

| キー | 型 | 中身 |
| --- | --- | --- |
| `links` | 配列（0〜40 件）。省略できる | 1 件は上の `links` の結果の 1 件と同じ形（`name`・`summary`・`url`・`stage`）。並びは提示の順 |

`material`（承認資料の本文）は残す。`links` を持たない事象は、今と同じ表示になる。

| 画面 | 何をする場所か |
| --- | --- |
| スプリントの詳細の承認の欄 | 答えを待っている承認の依頼のリンク集を、資料名・要旨・開くの 3 列で並べる。行は CLI のリンク集と同じ並び |
| 承認待ちの一覧 | 変えない。行からスプリントの詳細へ移る |

| 状態 | 表示 |
| --- | --- |
| 読み込み中 | 今の読み込み中の表示 |
| `links` が無い | リンク集の欄を出さない |
| `links` が空 | 「資料のリンクが届いていない」 |
| `url` が `null` の行 | 開くの列に「未確認」 |
| 読み込みの誤り | 今の誤りの表示と読み直し |

開くの列は新しいタブで開く。見えるのは、今のスプリントの詳細を見られる人と同じである。

## 処理の流れ

```mermaid
sequenceDiagram
  participant D as 設計の担い手
  participant M as design-materials.py
  participant C as conductor
  participant H as 承認する人
  participant F as Factory（#2815）
  D->>D: 手順 1 で触る領域を適用範囲に書く
  D->>M: check（手順 5。スプリントは Draft を外す前）
  alt 欠けがある
    M-->>D: 資料名を挙げて終了コード 1
    D->>D: 節を書くか ui_mock の宣言を求める
  else 欠けが無い
    M-->>D: 終了コード 0
  end
  D->>M: check --secret-only（スプリントは pr の前）
  alt ui_mock が秘密の形
    M-->>D: ui_mock_secret で終了コード 1。pr へ進まず人へ戻す
  end
  D->>C: 設計 Pull Request を出す
  C->>M: links（Pull Request の番号）
  M-->>C: 設計時の行（JSON と提示の行）
  C->>C: 承認時の 3 行（収束・本文との一致・MVV 判定）を足す
  C->>H: 直前の応答にリンク集を書き、承認を問う
  C-->>F: 実行の担い手が links を gate.reached に載せる
  H->>H: リンクから資料を開いて答える
```

図は実行する要素だけを描く。構成要素の表のうち参照の文書（`.md`）・生成物（スキーマと契約）・テスト・用語集は実行の順序を持たないため図に現れない。`project_lib/model.py` は `design-materials.py` が宣言を読むときに通る。

スプリントの設計のプランに足すステップは、モードで決まる。`light` / `operation` / `documentation` のプランは変えない（受け入れ条件 14）。

| ステップ | 置く位置 | 足すモード | 落ちたとき |
| --- | --- | --- | --- |
| `materials-secret`（`design-materials.py check --secret-only`） | `rebuild` と `pr` の間 | `standard`・`legacy-refactor` | `pr` へ進まずにプランを止め、「判断が要る」として人へ戻す（宣言を直すのは利用者で、共通原則の C1・C7 に当たる） |
| `materials-check` | `survey-recheck` の後 | `standard` | `fix-materials`（直しは 1 回）→ `materials-recheck` を通す |
| `materials-links` | 検査の後。出力を `gate` の入力に足す | `standard`・`legacy-refactor` | — |

`materials-secret` は `pr` の前に置く。設計 Pull Request の push と cross-review の前に、手で書いた `ui_mock.location` の秘密を止めるためである。

`fix-materials` は欠けた節を書く。`ui_mock_undeclared` と `ui_mock_secret` は直さずに「判断が要る」と返す。宣言はプロジェクトの設定で、共通原則の C7 に当たる。`ui_mock_secret` は秘密にも触れるため、C1 にも当たる。

## 非機能の実現方式

| 大項目 | 条件 | 実現方式 |
| --- | --- | --- |
| 運用・保守性 | 一覧の正本は 1 か所。検査はコマンド 1 つで結果が出る | 一覧は `approval-materials.md` の 1 つの表で、要否表と `approval-request.md` は行を写さずに指す。`design-materials.py check` は表を実行時に読むため、行を足すとスクリプトを変えずに検査に効く（該当時の行に新しい領域を使うときだけ、7 語の語彙を変える） |
| 性能・拡張性 | 提示の本体は資料 1 件 1 行で、中身を写さない | リンク集の行は最大 26 行、要旨は 120 字で切る（I6）。Factory の `links` は 40 件までで、今の承認資料の本文の上限（256 KiB）とは別に数える |

## 決定の記録

[issue-2721-design-decisions.md](issue-2721-design-decisions.md) にある。

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1・2・3・5・I1 | 出荷する `approval-materials.md` を `check` が読むと終了コード 2 にならず、表の資料名が重複せず、水準と作る段階が 2 値で、該当時の行が既知の領域を持つ | 表の行を重ねる・水準に 3 つ目の値を書く・該当時の行の領域を消すと、`catalog_broken` で 2 になる |
| 4・6・14・15 | 設計承認ゲートで承認する人が、[issue-2721-design-materials.md](issue-2721-design-materials.md) の「今の定義との対応」の表と、この文書の外部の調査の表、`deliverables.md` の差分（`light` / `operation` / `legacy-refactor` の列が変わらない）を見る | （文書の対応で、テストで縛らない。`.md` の文言を照合するテストを書かない） |
| 7・I2 | 該当する資料の節が無い設計文書を `check` に渡すと、資料名ごとに `missing` が出て終了コード 1。すべてあれば 0 | 節の有無を見ずに 0 を返す・欠けを 1 件にまとめると落ちる |
| 7・10・I2 | 決定の記録を分割先に置き、本体には分割先へのリンクだけの同じ見出しの節を置いた設計文書を `check` と `links` に渡すと、`missing` が出ず、`決定の記録` の要旨が分割先の決定の件数になり、`url` が分割先のファイルの節のアンカーを指す。分割先の節も空なら `missing` で 1 | 本体の節だけを読む・リンクだけの節で本文ありと判定する・件数を本体だけで数えると落ちる |
| 7・I3 | 触る領域の行が無い設計文書では、該当時の 5 行もすべて `missing` の対象になる | 行が無いときに該当時を外すと落ちる |
| 8・I4 | `ui_mock` の無い宣言で、触る領域に画面がある設計を渡すと `ui_mock_undeclared` で 1 | 宣言を見ずに通すと落ちる |
| 9・I4 | `ui_mock` の無い宣言で、画面の無い設計を渡すと `ui_mock_undeclared` が出ない | 画面の有無によらず挙げると落ちる |
| 10・I6 | `links` が該当する資料ごとに 1 件を出し、URL が設計文書の節のアンカーか画面モックのリンクを指す。要旨は改行を含まず 120 字以内。同じ見出しが 2 つある設計文書では、2 つ目の節を指す URL のアンカーに `-1` が付く | アンカーを付けない・見出しの文字列だけからアンカーを作る・要旨に節の本文を写すと落ちる |
| 11・I5 | 該当しない資料の行を出さず、必須の節が `無し（<理由>）` なら要旨がその 1 行になる | 該当しない行を出す・`無し` の行を落とすと落ちる |
| 12 | 承認の依頼の事象の `links` の 1 件と、`links` の結果の 1 件が同じキーを持つ | （#2815 の契約テストで縛る） |
| 13 | #2815 が起票され、この文書から番号で指されている | （起票の事実。テストで縛らない） |
| 16 | 全体テストと Skill の frontmatter の検査が通る | （CI と手元の実行で確かめる） |
| `ui_mock` の形 | `project-decl.py check` が `kind` の 4 値の外と空の `location` を拒み、欄の無い宣言を通す | 欄を必須にすると、今の宣言が落ちる |
| スプリントの設計のプラン | `--mode standard` で組んだプランで `survey-recheck` の次が `materials-check` で、`gate` の入力に `materials-links` がある。`--mode light` で組んだプランに `materials-check` と `materials-links` が無い。`--mode legacy-refactor` で組んだプランに `materials-links` があり `materials-check` が無い。`--mode standard` と `--mode legacy-refactor` で組んだプランで `rebuild` の次が `materials-secret` で、`materials-secret` が `pr` より前にあり、落ちたときの行き先が `pr` でない。`--mode light` で組んだプランに `materials-secret` が無い | 検査のステップを足さない・モードを見ずに足す・秘密の判定を `pr` の後に置くと落ちる |
| `ui_mock` の秘密の除去 | 既存の宣言の `ui_mock.location` が資格情報入りの URL のとき、`project-decl.py write` の後の宣言で `ui_mock` が `{"unknown": "秘密の形"}` になり、結果の行に `{"kind": "secret", "key": "ui_mock"}` があり、表示に `ui_mock: 秘密の形の値を捨てて不明にした` が出て、出力のどこにも元の値が出ない | `ui_mock` を巡回の外に置いたままにする・知らせる行を出さない・行に値を入れると落ちる |
| `ui_mock` の秘密の検査 | 利用者が手で書いた宣言の `ui_mock.location` が資格情報入りの URL のとき、`design-materials.py check` が `ui_mock_secret` を挙げて終了コード 1 で終わり、出力のどこにも元の値が出ない。`check --secret-only` も設計文書を渡さずに同じ結果になり、秘密の形でない `ui_mock` では終了コード 0 で終わる | `check` が `ui_mock` を検査せずに通す・`text` に値を入れる・`--secret-only` が設計文書を求めると落ちる |

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #2721 | 実装する | — | `plugins/ndf/skills/design/references/approval-materials.md`、`plugins/ndf/skills/design/references/deliverables.md`、`plugins/ndf/skills/design/references/design-template.md`、`plugins/ndf/skills/design/references/external-survey.md`、`plugins/ndf/skills/design/references/system-architecture.md`、`plugins/ndf/skills/design/references/interface-ui.md`、`plugins/ndf/skills/design/references/data-structure.md`、`plugins/ndf/skills/design/SKILL.md`、`plugins/ndf/skills/development-workflow/references/approval-request.md`、`plugins/ndf/skills/development-workflow/references/project-analysis.md`、`plugins/ndf/skills/development-workflow/schemas/project.schema.json`、`plugins/ndf/scripts/design-materials.py`、`plugins/ndf/scripts/project_lib/model.py`、`plugins/ndf/scripts/project_lib/merge.py`、`plugins/ndf/scripts/supervise_lib/procedures.py`、`plugins/ndf/scripts/supervise_lib/sprint_waves.py`、`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/tests/`、`plugins/ndf/contracts/`、`docs/glossary/glossary.json`、`docs/glossary.md` |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 要否表の `standard` の列を指していた記述 | 列を外したとき、ほかの Skill と文書が「要否表の `standard`」を指していないか。実装の最初に `grep -rn "standard" plugins/ndf/skills/design` と参照のチェックで洗い出す |
| 既存の設計文書 | `issues/` と `docs/specifications/` の設計文書は新しい節を持たない。書き直さない（要求の「含まない」）。検査は新しく書く設計文書だけに掛かる |
| ai-plugins の `ui_mock` の値 | ai-plugins は Factory の画面を持つ。値は #2815 の設計で決めて宣言する（C7。設計承認ゲートで人が見る） |
| 要旨の「最初の文の行」の読みやすさ | 節の書き出しが表や図のとき、要旨が意味を持たない。実装で実際の設計文書 3 件に `links` を打って確かめ、合わなければ規則を直す |
| Factory の実行の担い手が `links` の結果を受け取る経路 | ステージの出力から読むか、ファイルで渡すか。#2815 の設計で決める |

## 出し直しの指摘への対応

| 項目 | 指摘 | 対応 | 直した箇所・直せない理由 |
| --- | --- | --- | --- |
| lproc:1-1 | 要求文書の検査（requirements-check）が終了コード 2 で止まった原因を確かめて直す。検査の側の不具合なら PR 本文に書き、別の課題へ回す。決定 6 は設計のとおりでよい | 直した | 原因: 設計の工程は要求を書く前に検査を打ち、要求のコピーがまだ無いと終了コード 2 を返して要求を書く工程へ進む。終了コード 2 はこの想定どおりの分岐である。設計のブランチで `spec-copy.py check 2721 issues/issue-2721-requirements.md` を打ち直すと終了コード 0（本文と一致）を返す。要求文書に直す箇所は無い。分岐の終了コード 2 を PR 本文と MVV 判定が失敗として扱うのは検査の側の不具合で、既存の課題 #1502 に回した。PR #2818 の本文の「テスト」の節に、原因・打ち直した結果・回し先を記載した（lproc:2-1）。決定 6 は変えず、[決定の記録](issue-2721-design-decisions.md) の決定 6 の S6 の記述を「利用者が採った（2026-11-09）」にした |
| lproc:2-1 | PR 本文の「テスト」の節に、要求文書の検査が終了コード 2 で終わった原因・打ち直した結果（終了コード 0）・#1502 への回し先を書き、この表の記載先と実物を一致させる | 直した | PR #2818 の本文の「テスト」の節に、要求文書の検査（`spec-copy.py check 2721 issues/issue-2721-requirements.md`）の行を足した（2026-11-09。足した後に `gh pr view 2818` で本文に行があることを確かめた）。原因（要求のコピーを書く前に打つ分岐の終了コード 2）・打ち直した結果（2026-11-09 に終了コード 0、課題の本文と一致）・回し先（#1502。分岐の終了コード 2 を失敗として読む検査の側の不具合）を書いた。この表の記載先は、その本文の行と一致する |
| lproc:2-2 | `ui_mock` の値が秘密の形のとき `{"unknown": "秘密の形"}` へ置き換える変更は利用者が認めた（C7・C1）。設計に承認を記録し、置き換えたことを値を出さずに知らせる出力を持たせる | 直した | 承認の記録: [決定の記録](issue-2721-design-decisions.md) の決定 6 と、周辺システムとの関係のセキュリティの行。知らせる出力: 構成要素の表の `merge.py` の行と、入出力の契約の `ui_mock` の節に、`project-decl.py write` が出す 1 行（`ui_mock: 秘密の形の値を捨てて不明にした`。欄の名前だけで値は出さない）を書いた。テスト設計の `ui_mock` の秘密の除去の行で、この行が出ることと値が出ないことを縛る |
| lproc:3-1 | develop と衝突していて CI が 1 件も走っていない。develop を取り込んで衝突を解き、必須の検査をすべて通す | 直せない | 衝突は解いた: develop を取り込み、衝突した用語集（`docs/glossary/glossary.json` と生成する `docs/glossary.md`）は develop の内容にこの設計の 7 語を足して作り直した。取り込みで develop が廃止した、ループの 1 回を指す古い語が要求文書の 2 行に当たったため、課題 #2721 の本文を「ループの 1 回」へ直し、要求文書を本文から作り直した。取り込んだ後に develop が v10.27.49-dev.1 へ進んだため、もう一度取り込んだ（衝突なし。develop との差分はこの設計の 6 ファイルだけ）。手元では CI と同じ検査が通った（2026-11-09。用語集の検査 `glossary.py check --diff origin/develop --rules all` 当たりなし、`scripts/check-lint.sh` 違反なし、Skill の frontmatter の検査と指示書の検査が終了コード 0、pytest 12933 件成功・4 件スキップ、決定の突き合わせ `pr-body-decisions.sh check 2818` 一致）。pytest は開発ループの実行が持つ環境変数（`ANTHROPIC_API_KEY` など）を外して打った。付けたままでは、アカウントの切り替えを確かめるテストが手元の値を読んで落ちる。ただしこの工程は push しないため、CI の結果は未確認である。push の後に PR の工程が CI を待って確かめる |
| lproc:3-2 | lproc:2-1 が満たされていない。PR 本文の「テスト」の節に、要求文書の検査の行（原因・打ち直した結果・#1502 への回し先）を足す | 直した | PR #2818 の本文の「テスト」の表に、要求文書の検査の行を足した。中身は lproc:2-1 の行のとおり。2026-11-09 に `gh pr view 2818` で行があることを確かめた。PR の工程が本文を作り直すときは、この行を残す |
| lproc:3-3 | 設計文書と PR 本文の「指摘への対応」の表を実物の本文と一致させる。確かめていないものを「直した」と書かない | 直した | この表の各行の記載先を実物で確かめた（2026-11-09）。lproc:1-1 の決定 6 の記述は [決定の記録](issue-2721-design-decisions.md) の決定 6 に、lproc:2-2 の承認と知らせる 1 行は決定の記録と、この文書の入出力の契約・テスト設計にある。CI の結果は確かめていないため lproc:3-1 は「直せない」とした。PR 本文の同じ節は設計文書から写す節である。2026-11-09 の時点の PR 本文の表は lproc:2-2 までの 3 行で、lproc:3-1〜3-3 の行と lproc:2-1 の日付はまだ写っていない。push の後に PR の工程が `revise-note.py check` で写す |
