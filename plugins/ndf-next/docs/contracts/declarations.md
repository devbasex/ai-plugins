# 宣言（.ndf/）

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

リポジトリの `.ndf/` に置く宣言（人が決める設定のファイル）である。正本は置き場の表（`contracts/domains/ndf-files.json` の種類 `declaration` の行）と宣言のスキーマで、読み手は表の `keys` に無いキーを読まない。範囲のチームは git が追跡するファイル、個人は追跡しないファイルである

## `.ndf/ask.json`（ask）

worktree・チーム・公開・[ask.schema.json](../../contracts/schemas/declarations/ask.schema.json)。読む工程: `ndf hooks`（人への問いの hook（PreToolUse）で区分を照らすとき）

人へ問う区分（AskUserQuestion の守り）と費用の閾値

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `allow` | array of string | — | — | 任意 | 許す区分（既定は cost・irreversible・human-action・legal） |
| `cost` | object | — | — | 任意 | 費用の区分 |
| `extra` | array of object | — | — | 任意 | 足す区分 |
| `mode` | string | `"enforce"` | `"enforce"`・`"advise"`・`"off"` | 任意 | 区分の無い問いの扱い。enforce は断る、advise は案内する、off は見ない |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "mode": "enforce", "cost": {"threshold_usd": 10}}`

## `.ndf/backlog.json`（backlog）

worktree・チーム・公開・[backlog.schema.json](../../contracts/schemas/declarations/backlog.schema.json)。読む工程: `ndf issues`（棚卸しの順位を付けるときと、深さの秒の上限を決めるとき）

棚卸しの順位の設定（容量・ラベルの下限・尺度ほか）と品質の枠、深さごとの頻度・閾値・上限

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `auto_threshold` | integer | `8` | — | 任意 | 自動で進める閾値（尺度の値） |
| `capacity` | integer | — | — | 任意 | 容量（引数の --capacity が上書きする）。無ければ容量で区切らない |
| `dependents_steps` | array of array of integer | `[[0, 1], [1, 5], [2, 8], [3, 13], [5, 20]]` | — | 任意 | [被依存の数の下限, 段階（尺度の値）] の列（下限の昇順） |
| `depths` | object | — | — | 任意 | 深さ（review・overhaul・research など）→ その深さの設定 |
| `label_floor` | object | `{}` | — | 任意 | ラベル → 段階（尺度の値）の下限 |
| `large_min_size` | integer | — | — | 任意 | 大きな課題とみなす最小の大きさ（尺度の値）。無ければ尺度の最後から 2 番目 |
| `large_slot_ratio` | number | `0.5` | — | 任意 | 大きな課題の枠の割合（0 以上 1 未満） |
| `margin_steps` | integer | `1` | — | 任意 | 前倒しの閾値の余白（尺度の段の数） |
| `quality` | object | — | — | 任意 | 品質の枠。無ければ枠を取らない。書いた枠の省略した項目は既定値 |
| `scale` | array of integer | `[1, 2, 3, 5, 8, 13, 20]` | — | 任意 | 尺度（2 個以上の正の整数の狭義の昇順） |
| `size_exponent` | number | `0.3` | — | 任意 | 大きさの指数 |
| `version` | integer | — | `1` | 任意 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"quality": {"label": "quality", "ratio": 0.2, "min": 1}}`
例: `{"capacity": 1, "label_floor": {"bug": 13}}`

## `.ndf/code-metrics.json`（code-metrics）

worktree・チーム・公開・[code-metrics.schema.json](../../contracts/schemas/declarations/code-metrics.schema.json)。読む工程: `ndf refactor`（リファクタリングでコードを測るとき）

リファクタリングが言語ごとに使う測りの Tool。null はその言語を測らない

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `tools` | object | — | — | 必須 | 言語 → 測りの Tool の名前。言語は python・javascript・typescript・php・go・ruby・java・kotlin・swift・rust・c・cpp・csharp・lua・shell、Tool は lizard・ruff-complexipy（python だけ）・null（測らない）。形が違えば読み手は宣言の全体を使わず既定で測る |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "tools": {"python": "ruff-complexipy", "go": null}}`

## `.ndf/context.json`（context）

worktree・チーム・公開・[context.schema.json](../../contracts/schemas/declarations/context.schema.json)。読む工程: `ndf hooks`（文脈の量の hook で再起動を判断するとき）

文脈の量による再起動の方針（現行方式か試行方式）と、試行方式の値

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `margin` | integer | `50000` | — | 任意 | 容量の余白（token）。試行方式だけに効く |
| `policy` | string | `"current"` | `"current"`・`"trial"` | 任意 | current（現行方式）か trial（試行方式） |
| `trial` | object | — | — | 任意 | 試行方式の値 |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "policy": "trial", "trial": {"decide_from": 200000, "limit": 400000}, "margin": 50000}`

## `.ndf/estimate.json`（estimate）

worktree・チーム・公開・[estimate.schema.json](../../contracts/schemas/declarations/estimate.schema.json)。読む工程: `ndf issues`（棚卸しの見積もりのとき）・`ndf refactor`（リファクタリングの予算を決めるとき）

見積もりに使う実績の置き場と、Size の基準。読み手（estimate_records.Decl）は下のほかのキー（version・$schema を含む）を知らないキーとして止める

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `allocation_minutes` | object | — | — | 任意 | cross-refactoring の配分の 1 件あたりの分を種類ごとに上書きする。省いた種類は初期値（skills/cross-refactoring/data/allocation-defaults.json）を使う。初期値に無い種類は止める。まだ読まない（M4 で読む。配分を決める処理は M4 #3298 で入る） |
| `min_samples`（非推奨。読み手は読まない） | integer | — | — | 任意 | 読み手は読まない。書いてある宣言を断らないために残す（#2505） |
| `records_dir` | string | — | — | 任意 | 実績の記録の置き場（リポジトリの根から。絶対パスも可） |
| `scale` | array of integer | — | — | 任意 | Size の取りうる値（小さい順に並べ替えて使う）。省くか空なら 1・2・3・5・8・13・20 |
| `size_unit_seconds` | number | — | — | 任意 | Size 1 の基準の秒。省くと、実績のある課題のうち所要時間の短い 4 分の 1 の中央値（4 課題以上のとき） |
| `window`（非推奨。読み手は読まない） | integer | — | — | 任意 | 読み手は読まない。書いてある宣言を断らないために残す（#2505） |

例: `{"size_unit_seconds": 60, "scale": [1, 2, 3]}`

## `.ndf/glossary.json`（glossary）

worktree・チーム・公開・[glossary.schema.json](../../contracts/schemas/declarations/glossary.schema.json)。読む工程: `ndf records glossary`（用語集の生成と検査のとき）・`ndf records spec`（確定仕様化で用語を照らすとき）

用語集の正本と生成する文書の置き場、検査の範囲

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `check` | object | — | — | 任意 | 検査の範囲 |
| `document` | string | — | — | 任意 | 生成する索引の文書のパス |
| `document_dir` | string | — | — | 任意 | 領域ごとに分けて生成する文書の置き場 |
| `format` | string | `"json"` | `"json"` | 任意 | 正本の形 |
| `max_lines` | integer | — | — | 任意 | 生成する文書 1 本の行数の上限 |
| `source` | string | — | — | 任意 | 用語集の正本のパス（リポジトリの根から） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "format": "json", "source": "docs/glossary/glossary.json", "document": "docs/glossary.md"}`

## `.ndf/instructions.json`（instructions）

worktree・チーム・公開・[instructions.schema.json](../../contracts/schemas/declarations/instructions.schema.json)。読む工程: `工程: 指示書の確かめ`（リリースの前に指示書の読み込みと予算を確かめるとき）

指示書（AGENTS.md・CLAUDE.md など）のファイル・読み込みの許可・リリース済み版の取り出し方・予算。無ければ既定の判定だけが動く

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `budget` | object | — | — | 任意 | budget（このリポジトリの宣言にある最上位のキー） |
| `decisions` | string | — | — | 任意 | 退避先の文書。指摘の文面に出す |
| `files` | array of string | — | — | 任意 | 走査する指示書の名前。/ を含まない値はファイル名の一致、含む値は根からの相対パスの一致。書くと既定の 3 つ（AGENTS.md / CLAUDE.md / KIRO.md）を置き換える |
| `imports` | object | — | — | 任意 | 許可する@インポート。キーは根からの相対パス、値は {参照先: 理由}。書けば許可の外を指摘する。{} は「許可が 1 つも無い」で、未指定とは別の値 |
| `pending_marker` | string | — | — | 任意 | 版が決まる前の段落の目印（例: の次の版で）。空文字列は宣言の不正 |
| `released` | object | — | — | 任意 | リリース済み版の取り出し方。pattern は名前付きの捕捉 (?P<version>…) を 1 つ持つ |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "files": ["AGENTS.md", "CLAUDE.md"], "budget": {"bytes": 24000}}`

## `.ndf/issue-items.json`（issue-items）

worktree・チーム・公開・[issue-items.schema.json](../../contracts/schemas/declarations/issue-items.schema.json)。読む工程: `工程: 起票`（Issue の本文の項目を決めるとき）

Issue の種類ごとの記載項目（必須・該当時・不要）と水準。あれば NDF の既定の代わりに使う

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `items` | array of object | — | — | 必須 | 記載項目の並び |
| `kind_labels` | object | — | — | 任意 | 種類 → GitHub のラベルの名前 |
| `kinds` | array of string | — | — | 必須 | Issue の種類の並び |
| `last` | string | `"request"` | — | 任意 | 書き手が書く節の最後に置く項目の id |
| `record_headings` | array of string | — | — | 任意 | 工程が足すセクションの見出しの語 |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "kinds": ["不具合", "機能"], "items": [{"id": "purpose", "name": "目的", "need": {"不具合": "必須", "機能": "必須"}, "level": "何を良くするかを 1〜2 文で書く"}]}`

## `.ndf/mvv.json`（mvv）

worktree・チーム・公開・[mvv.schema.json](../../contracts/schemas/declarations/mvv.schema.json)。読む工程: `ndf records mvv`（MVV の照合と改訂の提案のとき）・`ndf records preflight`（工程の前の確かめで本文の置き場を読むとき）・`ndf answer`（承認ゲートで MVV の版を照らすとき）

プロジェクト MVV の本文の置き場・改訂の設定・承認された版の並び。人の承認（M7）で書く

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `body` | string | `"mvv.md"` | — | 任意 | body（このリポジトリの宣言にある最上位のキー） |
| `settings` | MvvSettings | — | — | 任意 | settings（このリポジトリの宣言にある最上位のキー） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |
| `versions` | array of MvvVersion | — | — | 必須 | versions（このリポジトリの宣言にある最上位のキー） |

例: `{"version": 1, "body": "mvv.md", "settings": {"revise_after": {"overrides": 3}}, "versions": [{"version": 1, "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "approved_at": "2026-01-01T00:00:00+00:00", "approved_by": "example", "reason": "初版", "vet": {"verdict": "follow", "at": "2026-01-01T00:00:00+00:00"}, "changes": [], "body": "# 例のプロジェクト MVV\n"}]}`

## `.ndf/mvv.md`（mvv-body）

—・チーム・公開・[mvv-body.sections.json](../../contracts/schemas/declarations/mvv-body.sections.json)。読む工程: `ndf records mvv`（MVV の照合で本文を読むとき）・`ndf records preflight`（工程の前に本文があるかを確かめるとき）

Markdown の宣言。節の並び:

| 順 | 見出し | 必須か | 持つもの |
| --- | --- | --- | --- |
| 1 | # <プロジェクト名> のプロジェクト MVV | 必須 | 表題と、この MVV が何の基準かの 1 段落 |
| 2 | ## Mission | 必須 | 何のためにやるか |
| 3 | ## Vision | 必須 | 目指す姿 |
| 4 | ## Value | 必須 | 判断の基準（番号つきの項目） |
| 5 | ## NDF の共通原則との関係 | 任意 | 共通原則の範囲で読むこと |
| 6 | ## 必ず人の承認が要る操作 | 任意 | プロジェクトが足す操作（P の番号） |
| 7 | ## 改訂 | 任意 | 改訂の規則 |

## `.ndf/pace.json`（pace）

worktree・チーム・公開・[pace.schema.json](../../contracts/schemas/declarations/pace.schema.json)。読む工程: `ndf answer`（承認ゲートでレッドラインのパスを照らすとき）・`ndf loop`（開発ループの選定と予算を決めるとき）

進め方（fast・auto）の許可と、共通の層の範囲・レッドラインのパス・閾値・開発ループの設定

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `areas` | array of object | — | — | 任意 | 共通の層の範囲の並び |
| `auto` | object | — | — | 任意 | 進め方の許可 |
| `boundary_paths` | array of string | — | — | 任意 | レッドラインのパス（触れる変更は人の承認が要る）の glob |
| `business_hours` | object | — | — | 任意 | 業務時間（日をまたがない）。不正なら警告して宣言なしとして扱う |
| `fast` | object | — | — | 任意 | 進め方の許可 |
| `loop` | object | — | — | 任意 | 開発ループの設定 |
| `parallel` | object | — | — | 任意 | 並列の計画の設定 |
| `triggers` | object | — | — | 任意 | 進め方を切り替える閾値 |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "fast": {"enabled": true, "modes": ["light"]}, "boundary_paths": [".github/workflows/**"], "loop": {"enabled": true, "budget": {"laps": 10}}}`

## `.ndf/project.json`（project）

worktree・チーム・公開・[project.schema.json](../../contracts/schemas/declarations/project.schema.json)。読む工程: `ndf records preflight`（工程の前の確かめで、知らないキーを数える）・`ndf release-steps`（本番のマージで production_merge をメインディレクトリから読む）・`ndf refactor`（リファクタリングで test.suites の収集のコマンドを読む）・`ndf progress`（進行表を作るときに workflow.stages の工程の有効・無効を読む）

言語・テスト・CI・リリースの経路・課題の置き場の宣言。人と解析（M7）が書く。無ければ既定で動く

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `analysis` | Analysis か null | — | — | 任意 | 解析（M7）が書いた入力の sha256・ブランチ・書いた節の記録。人は書かない |
| `branch_strategy` | BranchStrategy | — | — | 任意 | ブランチ戦略の宣言（#3672）。ブランチの名前・取り込み方・リリース・承認・マージを 1 か所で持ち、ndf project branch-strategy だけが作る・更新する・移す・消す。工程の部品は ndf.core.branch_strategy で読む |
| `checks` | Checks か Unknown か null | — | — | 任意 | 静的解析・整形の Tool の並び |
| `ci` | Ci か Unknown か null | — | — | 任意 | CI の提供元・ワークフロー・必須のチェック |
| `delivery`（非推奨。読み手は読まない） | array of Delivery か Unknown か null | — | — | 任意 | 旧い宣言。ndf project branch-strategy migrate が branch_strategy.release.deliveries へ移す。解析は測らない・書かない |
| `docs` | Docs か Unknown か null | — | — | 任意 | 差分の文書のファイルの分け方（文書・振る舞いを決める文書・テストが読む文書とそのテスト） |
| `instructions` | Instructions か Unknown か null | — | — | 任意 | 指示書のファイル・読み込み・注記 |
| `issues` | Issues か Unknown か null | — | — | 任意 | 課題の置き場（primary と others） |
| `languages` | array of Language か Unknown か null | — | — | 任意 | 言語ごとの名前・フレームワーク・ファイルの数・版の並び。解析（M7）が書く |
| `materials` | Materials か Unknown か null | — | — | 任意 | 材料の宣言（見直しと大整理が読むニーズとシーズの調査の読み先・後回しのマイルストーン・原因の型・裏付けの計測） |
| `ndf_policies` | NdfPolicies か Unknown か null | — | — | 任意 | NDF の方針の採否（doc_lint・reject_md_wording_tests など） |
| `production_merge`（非推奨。読み手は読まない） | ProductionMerge か Unknown か null | — | — | 任意 | 旧い宣言。ndf project branch-strategy migrate が branch_strategy.merge へ移す。解析は測らない・書かない |
| `services` | Services か Unknown か null | — | — | 任意 | テストと動作の確かめに要るコンテナ・compose のファイル・データベース |
| `test` | Test か Unknown か null | — | — | 任意 | テストの戦略（suites）。テストのコマンドの正本 |
| `test_duration`（非推奨。読み手は読まない） | TestDuration か Unknown か null | — | — | 任意 | 読み手は読まない（今の版の読み手が読む値。11.0.0 の後に消す） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |
| `workflow` | object | — | — | 任意 | 工程の有効・無効の宣言（stages） |

例: `{"version": 1, "test": {"suites": [{"name": "pytest", "runner": "pytest", "command": "uv run pytest -q"}]}, "production_merge": {"admin": false}}`

## `.ndf/projects.json`（projects）

worktree・チーム・公開・[projects.schema.json](../../contracts/schemas/declarations/projects.schema.json)。読む工程: `工程: 起票と進行`（課題をボードへ載せ、状態を動かすとき）

課題を載せる GitHub Projects のボードと、項目の名前

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `fields` | object | — | — | 任意 | 鍵（status など）→ ボードの項目の名前 |
| `number` | integer | — | — | 必須 | ボードの番号 |
| `owner` | string | — | — | 必須 | ボードの持ち主（組織か利用者の名前） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "owner": "example-org", "number": 1, "fields": {"status": "Status"}}`

## `.ndf/release.json`（release）

worktree・チーム・公開・[release.schema.json](../../contracts/schemas/declarations/release.schema.json)。読む工程: `ndf release-steps`（リリースの手順で、段階に合うコマンドを順に打つとき）

リリースの段階ごとに打つコマンドの並び。無ければ何もしない

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `bump_undistributed` | boolean | `false` | — | 任意 | リリース対象に関わらない変化だけのプラグインも版を上げるか |
| `changelog` | string | `"CHANGELOG.md"` | — | 任意 | CHANGELOG のファイル（リポジトリの根から） |
| `steps` | array of object | — | — | 任意 | コマンドの並び。書いた順に実行し、最初に落ちたコマンドで止める。無ければ手順なし |
| `undistributed` | array of string | — | — | 任意 | リリース対象に関わらないファイルの形（プラグインの根からの gitignore の形）。無ければテスト |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |
| `version_files` | array of string | `[]` | — | 任意 | 版上げで、プラグインの名前を含む行の版を書き換えるファイル |

例: `{"version": 1, "steps": [{"name": "記録", "stage": "production", "command": ["python3", "scripts/record.py"], "writes": ["docs/metrics/"]}]}`

## `.ndf/review.json`（review）

worktree・チーム・公開・[review.schema.json](../../contracts/schemas/declarations/review.schema.json)。読む工程: `ndf loop`（レビューの引数を組むとき）・`工程: コードレビュー`（レビューの観点を足すとき）

レビューで必ず見る観点の並びと、指摘の基準・見送り・返信を差し替えるプロファイル。version 1 は focus だけを読みほかのキーを無視し、version 2 は focus と profile、version 3 は focus と最上位の criteria・not_written と profiles を読む

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `criteria` | criteria | — | — | 任意 | version 3 で全プロファイルに足す指摘の基準（番号 → 文）。番号はプロファイルか既定の基準と重ならない |
| `focus` | array of string | — | — | 任意 | レビューで必ず見る観点の文。version 1 では必須 |
| `not_written` | not_written | — | — | 任意 | version 3 で全プロファイルに足す、書かないものの並び |
| `profile` | profile | — | — | 任意 | version 2 のプロファイル（指摘の基準・書かないもの・見送りの種類・返信・観点の区分）。version 3 では書かず profiles に移す |
| `profiles` | array of profile_entry | — | — | 任意 | version 3 のプロファイルの候補。PR の変更ファイルのパスが paths に当たる先頭の 1 つを選ぶ。name は重ならず、paths を持たない候補は高々 1 つで末尾に置く |
| `version` | integer | — | `1`・`2`・`3` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "focus": ["処理の回数と所要時間"]}`
例: `{"version": 3, "focus": ["処理の回数と所要時間"], "profiles": [{"name": "docs", "paths": ["docs/**"], "criteria": {"1": "読み手が違う手順を踏む食い違い"}}, {"name": "code"}]}`

## `.ndf/runtimes.json`（runtime-policy）

メインディレクトリ（既定ブランチの worktree）（印のキーはメインディレクトリ）・チーム・公開・[runtime-policy.schema.json](../../contracts/schemas/declarations/runtime-policy.schema.json)。読む工程: `工程: ランタイムの起動`（ランタイムを起動する前に宣言の外の名前を止めるとき）

起動してよいランタイムと cross-review の固定の組。認可に関わるため、メインディレクトリから読む。読み手は allowed・review_seats のほかのキー（version・$schema を含む）を知らないキーとして止める

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `allowed`（メインディレクトリから読む） | array of string | — | `"claude"`・`"codex"`・`"agy"`・`"kiro"` | 必須 | 起動してよいランタイム（1 つ以上、重複なし） |
| `review_seats`（メインディレクトリから読む） | array of string | — | — | 任意 | cross-review の固定の組（2 つの異なる席の名前。どちらのランタイムも allowed にある） |

例: `{"allowed": ["claude"], "review_seats": ["claude", "claude-2"]}`

## `.ndf/supervise.json`（supervise）

worktree・チーム・公開・[supervise.schema.json](../../contracts/schemas/declarations/supervise.schema.json)。読む工程: `工程: プランの実行`（実装の後に同期とチェック・整形を打つとき）

プランの実行で打つ同期とチェック・整形のコマンドと、リリースの形。テストのコマンドは project.json の test だけが持つ

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `format` | array of object | — | — | 任意 | 整形のコマンドの並び。チェックの前に打つ |
| `impl_stall_seconds` | integer か null | — | — | 任意 | 実装の無進捗の許容時間（秒）。引数が先に効く。null は書かないのと同じ |
| `impl_timeout` | integer か null | — | — | 任意 | 実装の上限（秒）。引数が先に効く。null は書かないのと同じ |
| `queue` | object | — | — | 任意 | プランを流す待ち行列の資源の枠と共有の一覧。無ければ既定（graphql の枠 2 本・共有の一覧は無し） |
| `refactor_budget_minutes` | integer か null | — | — | 任意 | リファクタリングの想定最大時間（分）。引数が先に効く。null は書かないのと同じ |
| `release` | object | — | — | 任意 | リリースの形 |
| `sync_checks` | array of object | — | — | 任意 | 同期とチェックのコマンドの並び。書いた順に打ち、落ちたら止める |
| `test`（非推奨。読み手は読まない） | object | — | — | 任意 | 読み手は読まない（今の版の読み手が読む値。テストのコマンドは project.json の test が持つ） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "sync_checks": [{"name": "lint", "command": "bash scripts/check-lint.sh"}], "format": [{"name": "lint-fix", "command": "bash scripts/check-lint.sh --fix"}]}`

## `.ndf/worktree.json`（worktree）

メインディレクトリ（既定ブランチの worktree）（印のキーはメインディレクトリ）・チーム・公開・[worktree.schema.json](../../contracts/schemas/declarations/worktree.schema.json)。読む工程: `ndf worktree`（worktree を作る・片付ける・環境を用意するとき）・`ndf release-steps`（本番チャネルとリリースの範囲を決めるとき）

ベースブランチ・本番のブランチ・ブランチの運用・worktree の用意の宣言。メインディレクトリから読む。無ければ worktree の運用を使わない

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `base_branch` | string | — | — | 任意 | 開発の起点ブランチ。worktreeの分岐元になり、follow_branch が true のときはメインディレクトリの追従先にもなる。未指定なら remote のリモートの HEAD が指すブランチを使う。指定した名前がそのリモートにもローカルにも無いときは既定へ落とさず解決に失敗する |
| `branch_flow`（メインディレクトリから読む） | object | — | — | 任意 | ブランチ運用の宣言。無ければ既定値の行（ベースブランチから切り、ベースブランチを丸ごと出す） |
| `deps` | object | — | — | 任意 | 依存の用意。worktree を作った直後に copy_from_main・copy_as_real・run の順に行う。無いか空なら何もしない。個人設定からは反映しない |
| `follow_branch` | boolean | `false` | — | 任意 | true のときだけ、セッションセッション開始 hook がメインディレクトリのブランチを稼働中のworktreeへ detached HEAD で追従させる。既定ではメインディレクトリの HEAD を動かさない。真偽値以外は false と同じ |
| `guard` | object | — | — | 任意 | メインディレクトリでの編集の案内（guard）。allow_paths は案内を出さないパス |
| `localenv` | object | — | — | 任意 | ローカルの動作の確かめ（compose）の宣言。個人設定の値を重ねる |
| `production_branch`（メインディレクトリから読む） | string | — | — | 任意 | リリースした版が常用する利用者へ届く本番チャネルのブランチ。この先へ進める操作は人手の承認を求める。開発の起点である base_branch とは別の鍵で、流用しない。未指定なら remote のリモートの HEAD が指すブランチを使う。指定した名前がそのリモートにもローカルにも無いときは既定へ落とさず解決に失敗する |
| `remote` | string | `"origin"` | — | 任意 | 取得・照合・差分の基準・送信に使う git のリモートの名前。未指定なら既定の名前を使う。- で始まる名前は受けない |
| `testenv` | object | — | — | 任意 | テスト環境（ポートの採番・ゴールデンのタグ・リセット）の宣言。個人設定の値を重ねる |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "base_branch": "develop", "production_branch": "main", "branch_flow": {"start": "base", "promotion": "whole"}, "guard": {"allow_paths": ["issues/", "docs/"]}}`

## `.ndf/worktree.local.json`（worktree-local）

メインディレクトリ（既定ブランチの worktree）・個人・内部（予告なく変えてよい）・[worktree-local.schema.json](../../contracts/schemas/declarations/worktree-local.schema.json)。読む工程: `ndf worktree`（worktree の運用の宣言を読むとき（共有設定に重ねる））

各自の機械の値。git が追跡しない。共有設定（worktree.json）の follow_branch・localenv・testenv だけを重ね、ほかのキーは反映しない

| キー | 型 | 既定値 | 取りうる値 | 必須か | 説明 |
| --- | --- | --- | --- | --- | --- |
| `$schema` | string | — | — | 任意 | 編集のときの補完に使うスキーマの位置。読み手は参照しない |
| `follow_branch` | boolean | `false` | — | 任意 | true のときだけ、セッションの開始の hook がメインディレクトリのブランチを稼働中の worktree へ追従させる |
| `localenv` | object | — | — | 任意 | ローカルの動作の確かめの個人の値（共有設定の localenv に重ねる） |
| `testenv` | object | — | — | 任意 | テスト環境の個人の値（共有設定の testenv に重ねる） |
| `version` | integer | — | `1` | 必須 | 宣言の形の版。読み手は知らない版を読まない |

例: `{"version": 1, "follow_branch": true}`
