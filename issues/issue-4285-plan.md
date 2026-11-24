# #4285: 想定した読み手に通じない文を、承認を求める前に見つけて直す

## 関連リンク

- 要求仕様: [issue-4285-requirements.md](issue-4285-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4285 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4285-design.md](issue-4285-design.md)、詳細設計書: [issue-4285-design-detail.md](issue-4285-design-detail.md)。決定は番号で指す
- モード: `standard`（配布するプラグインのコード・既定で働く hook・手順書を変える）。触るファイルが 4 を超えるので、実装計画は省けない

## 修正対象

詳細設計書の「地図」と「手順書の変更」から書き写した。次の 3 点は、設計の後にマージされた変更とマージ前の変更に合わせて、この計画で変えた。決定の中身は変えない。

- **指摘の基準の番号は 5 ではなく 6 にする。** プロジェクト MVV に反しないかを見る基準（ https://github.com/devbasex/ai-plugins/issues/4076 ）の実装が 5 を使っていて、`sprint/p9-lz2-ci-r3` に入っている。同じ番号を使うと、後からマージした側の指示で、番号の同じ 2 つの基準が並ぶ。番号は空いていてもよい（今も 3 は、重点の設定が無ければ現れない）
- **記録の形のスキーマは `contracts/schemas/reading-check.schema.json` ではなく `contracts/schemas/internal/reading-check.schema.json` に置く。** 設計の後にマージされた https://github.com/devbasex/ai-plugins/pull/4367 で、追記する記録は、種類ごとのスキーマを `schemas/internal/` に置き、`ndf.contracts.internal` の `KINDS` に登録する形になった。`records/jsonl.py` の読み書きは、この種類を渡さないと使えない。記録の各行に `version` が加わる
- **`drive.py` の行数の上限（1,000 行）への対処は、マージの順序で決める。** 上の #4076 の実装は、変更の読み取りを新しい `changes.py` へ移しており、移した後の `drive.py` は 915 行である。#4076 が先にマージされていれば、`changed_docs.py` は作らない。マージされていなければ、`drive.py` の 998 行に設計の 2 行を足した 1,000 行（上限ちょうど）で収める

`plugins/ndf/` を基点に書く。

| ファイル | 変える中身 | 単位 |
| --- | --- | --- |
| `contracts/data/writing-criteria.md`（新設） | 文書の基準の本文 | U1 |
| `contracts/data/ndf-common-principles.md` | 「人と AI の対話」の節に、「文書は、想定した読み手に通じる言葉で書く」の 1 文 | U1 |
| `contracts/data/review-criteria.json` | 基準 6 と指示の文（`document`）、`not_written` | U1 |
| `src/ndf/contracts/writing.py`（新設） | `criteria`・`types_section` | U1 |
| `src/ndf/domains/crossreview/criteria_decide.py` | `document_block` | U1 |
| `src/ndf/domains/crossreview/drive.py` | `_Run.documents`・`_prepare_round`・`_review_prompt`・`_fix_prompt` | U1 |
| `src/ndf/domains/crossreview/round_scope.py`（新設）・`cli.py`・`scope_decide.py` | `previous_head`（差分の範囲と、基準 6 の制限に使う前のラウンドの head を同じ条件で決める） | U1 |
| `src/ndf/core/worker.py` | `system_prompt` | U1 |
| `contracts/schemas/internal/reading-check.schema.json`（新設） | 記録の 1 行の形 | U2 |
| `src/ndf/contracts/internal.py`・`contracts/data/internal-names.json` | 種類 `reading-check` の登録 | U2 |
| `contracts/domains/ndf-files.json` | `reading-checks.jsonl` の行（保存先は `mvv-state`） | U2 |
| `src/ndf/domains/records/reading_decide.py`（新設） | `question_key`・`missing` | U2 |
| `src/ndf/domains/records/reading.py`（新設） | `prompt`・`add`・`rows`・`check` | U2 |
| `src/ndf/domains/records/cli.py` | `ndf records reading` の引数 | U2 |
| `src/ndf/domains/hooks/guards/reading_check.py`（新設） | `check` | U3 |
| `contracts/hooks/guards.json`・`contracts/hooks/runtimes.json` | guard `reading-check` の行と、Codex で動かさない理由 | U3 |
| `skills/document-style/SKILL.md`・`skills/document-drafting/SKILL.md`・`skills/document-rebuild/SKILL.md`・`skills/document-rebuild/references/{common,keyword-check}.md`・`skills/pr-review/SKILL.md`・`skills/requirements-design/SKILL.md`・`skills/requirements-design/references/{document-roles,spec-template}.md`・`skills/development-workflow/references/{glossary,approval-request,asking,agent-layers}.md`・`skills/decision-request/SKILL.md`・`skills/cross-review/docs/01-state-and-review.md`・`skills/fix/SKILL.md` | 詳細設計書の「手順書の変更」の表のとおり | U4 |
| `docs/glossary/glossary.json`・`docs/ndf-plugin-reference.md`（リポジトリの直下から） | 用語集の「読み手役」の項目と、hook の説明 | U4 |
| 生成物: `docs/contracts/*.md`・`contracts/schemas/io/*.json`・`docs/glossary.md`（リポジトリの直下から） | 生成するコマンド（`ndf check --write-spec` と `ndf records glossary render`）で作り直す | U2・U3・U4 |
| `issues/issue-4285-design.md`・`issues/issue-4285-design-detail.md` | 上の 3 点に合わせる | U1 |

テストのファイルは、各タスクの対象ファイルに挙げる。このリポジトリはテストを CI だけで打つ設定（`.ndf/project.json` の `test.ci.only`）なので、手元では静的な検査だけを打ち、テストの合否は PR の CI で確かめる。

## タスク分解

### Task 1: 設計書と詳細設計書を、この計画で変えた 3 点に合わせる

- **対象ファイル:** `issues/issue-4285-design.md`、`issues/issue-4285-design-detail.md`
- **変更内容:** 「基準 5」を「基準 6」へ直す（設計書の決定 4・決定 5・影響と危険・テスト設計・未確認のまま残ること、詳細設計書の地図・基準の本文を指示に入れる節・手順書の変更）。「5 つ目」は、cross-review に加わる順序ではなく番号で書く。詳細設計書の地図のスキーマの保存先を `schemas/internal/` へ直し、記録のキーに `version` を足す。要求仕様は変えない
- **満たす受け入れ条件:** 8（番号と保存先を設計とそろえる）
- **進め方:** 文書だけの変更なので、テスト駆動を適用しない。`.md` の文言を照合するテストは書かない決まりである。リンク切れは CI の検査で確かめる

### Task 2: 今の指示と、今の質問の振る舞いを縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/domains/crossreview/test_crossreview_documents.py`（新設）、`plugins/ndf/tests/worker/test_rules.py`、`plugins/ndf/tests/domains/hooks/test_reading_check.py`（新設）
- **変更内容:** 機能追加の最初のタスクとして、足す前の振る舞いを縛る。(1) Markdown を含まない PR では、レビューする AI と修正担当への指示が、`not_written` の文を除いて今と同じである。(2) 文書を書かない作業（`document-style` を読まない作業）の worker の規則文が、今と同じである。(3) 区分の無い質問と、記録の仕組みが無い今の `ndf-gate:` の質問は、止まらずに通る
- **満たす受け入れ条件:** 11、19
- **進め方:** 今のコードで通るテストを書く。(3) の `ndf-gate:` の質問のテストは、Task 7 で、新しい振る舞いを確かめるテストに置き換える

### Task 3: 基準の本文と、それを読む関数

- **対象ファイル:** `plugins/ndf/contracts/data/writing-criteria.md`、`plugins/ndf/contracts/data/ndf-common-principles.md`、`plugins/ndf/src/ndf/contracts/writing.py`、`plugins/ndf/tests/contracts/test_writing.py`（新設）
- **変更内容:** 基準の本文は、開発者の手元の `.ndf/handoff/writing-criteria.md`（メインディレクトリにある。Git では管理していない）の全文から、読み手役の手順の 7 節と、案であることの前書きを除いて移す。設計書の「文書の基準」のうち、全文に無いもの（「判断や作業を誤らせる文」と「読みにくいだけの文」の見分け方、利用者が差し戻した 3 つの文を直した例）を加える。食い違う所は全文に合わせる（設計書の「文書の基準」）。型 B は、日本語以外の文書では「その言語として不自然な文」と読み替えることを書く。手元のファイルが無いときは、設計書の「文書の基準」から書き起こし、PR の本文にそのことを書く。`writing.criteria` は全文、`writing.types_section` は 4 つの型と 2 つの区分の節を返し、見出しが無ければ `ContractError` を出す
- **満たす受け入れ条件:** 1、2、5、6、20
- **進め方:** 失敗するテスト（基準のファイルが無い・見出しが無いときに `ContractError` になる）→ 通す最小実装 → 整理。本文の中身は、読んで確かめる

### Task 4: 文書を含む PR のレビューと修正の指示に、基準を入れる

- **対象ファイル:** `plugins/ndf/contracts/data/review-criteria.json`、`plugins/ndf/src/ndf/domains/crossreview/criteria_decide.py`、`plugins/ndf/src/ndf/domains/crossreview/drive.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_criteria.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_documents.py`
- **変更内容:** 詳細設計書の「基準の本文を指示に入れる」の節の `document_block`・`_prepare_round`・`_review_prompt`・`_fix_prompt` と、`review-criteria.json` の変更。`_Run.documents` の判定には `_prepare_round` が読む変更ファイルの一覧を使い、git を追加で起動しない。2 ラウンド目以降の `previous` は、`round_scope.previous_head` が返す前のラウンドの head から得る。`drive.py` の行数の扱いは「修正対象」の頭のとおり
- **満たす受け入れ条件:** 7、8、9、10、11
- **進め方:** 失敗するテスト（Markdown を含む PR の 2 つの指示に基準の本文が無い）→ 通す最小実装 → 整理。テストの観点は、設計書の「テスト設計」の受け入れ条件 7・9・10・11 の行

### Task 5: 文書を書く worker の指示に、基準を入れる

- **対象ファイル:** `plugins/ndf/src/ndf/core/worker.py`、`plugins/ndf/tests/worker/test_rules.py`
- **変更内容:** `system_prompt` が、`document-style` を読む作業（`kinds.json`）の手順書の節の後に `writing.criteria()` を続ける。基準を読めないときは、`skills_section` と同じ停止理由で worker を起動しない。`worker.py` は今 991 行なので、足す行は 9 行以内に収める
- **満たす受け入れ条件:** 7、11
- **進め方:** 失敗するテスト（`document-style` を読む作業だけに本文が入る）→ 通す最小実装 → 整理

### Task 6: 読み手役が確認した記録と、`ndf records reading`

- **対象ファイル:** `plugins/ndf/contracts/schemas/internal/reading-check.schema.json`、`plugins/ndf/src/ndf/contracts/internal.py`、`plugins/ndf/contracts/data/internal-names.json`、`plugins/ndf/contracts/domains/ndf-files.json`、`plugins/ndf/src/ndf/domains/records/{reading_decide,reading,cli}.py`、`plugins/ndf/tests/domains/records/test_reading.py`（新設）、`plugins/ndf/tests/contracts/test_internal.py`、`plugins/ndf/tests/domains/records/test_cli.py`。生成物は `ndf check --write-spec` で作り直す
- **変更内容:** 詳細設計書の「読み手役の指示と確かめの記録」の節。記録の保存先は `gate-rejections.jsonl` と同じで、`NDF_MVV_STATE_DIR` があればその位置に書く。保存先を決める処理は、今ある `rejections_path` と同じ形になるので、詳細設計書の「地図」に無い共通の関数は作らず、`reading.py` に書く（まとめる候補は「リファクタリングの時機」に書いた）。記録は `jsonl.append_locked` で追記する。`reading.rows` は、壊れた行（書きかけ・形の違う行）を除き、新しいバージョンの行では `InternalError` を出す。`prompt` の指示は、`writing.types_section()` を使う
- **満たす受け入れ条件:** 12、13、17
- **進め方:** 失敗するテスト（`reading prompt` の指示に 4 つの型が無い・複数行と引用符を含む質問で `question_key` が区別されない）→ 通す最小実装 → 整理。`prompt` の `files`・`reader`・`questions_kind`、`add` の担当の表のキー、`check` の `questions` と `prs` は、詳細設計書の表の形を 1 度そのまま JSON ファイルで通す

### Task 7: 承認と判断の質問の前に、記録を調べる hook

- **対象ファイル:** `plugins/ndf/src/ndf/domains/hooks/guards/reading_check.py`、`plugins/ndf/contracts/hooks/guards.json`、`plugins/ndf/contracts/hooks/runtimes.json`、`plugins/ndf/tests/domains/hooks/test_reading_check.py`、`plugins/ndf/tests/domains/hooks/test_registration.py`・`test_runtimes.py`（guard の一覧を数えるテストだけ）。生成物は `ndf check --write-spec` で作り直す
- **変更内容:** 詳細設計書の「質問の前に記録を調べる（hook）」の節。`guards.json` の行は `order` 55・`fail_closed` 偽・`deny_key` `reason`・`limits` は `hook.git-rev-parse` と `hook.counter-lock`。止める理由の文は、区分と足りないものの語だけで決まる固定の文にする。新しい `InternalError` の行があるときは、開けない記録と同じく通す
- **満たす受け入れ条件:** 17、18、19
- **進め方:** 失敗するテスト（記録の無い `ndf-gate:` の質問が止まらない）→ 通す最小実装 → 整理。詳細設計書の「単体テストの観点」の順 1〜5 を 1 つずつ確かめる。`AskUserQuestion` の入力の形は、`ask-forward` の既存のテストの見本から作る

### Task 8: 手順書を基準への参照に直し、承認の前の確認の手順を足す

- **対象ファイル:** 「修正対象」の U4 の行
- **変更内容:** 詳細設計書の「手順書の変更」の表のとおり。読み手の説明と型の説明は書き写さず、`contracts/data/writing-criteria.md` へのリンクにする。`document-rebuild/SKILL.md` の「読み手役に読ませる」には、読み手役に渡すもの・返させるもの・合否・回数・記録（決定 7〜9）と、記録のコマンドを書く。`approval-request.md` には、`ndf records reading add` と `check` を打つ順序と、レビューで直した文を読み直す手順（決定 8・決定 12）を書く。用語集の「読み手役」の項目は、設計書の「用語」の意味に直し、`docs/glossary.md` を作り直す
- **満たす受け入れ条件:** 3、4、13、14、15、16
- **進め方:** テスト駆動を適用しない。リンク切れは CI の検査で確かめ、中身はレビューで読んで確かめる。受け入れ条件 14〜16 は、リリース後の最初の設計の承認とリリースの承認で確かめる（要求仕様の「検証手段」）

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| U1 基準と指示 | Task 1〜5 | 1、2、5、6、7、8、9、10、11、20 | 約 600 | |
| U2 記録 | Task 6 | 12、17 | 約 550 | |
| U3 hook | Task 7 | 17、18、19 | 約 400 | |
| U4 手順書 | Task 8 | 3、4、13、14、15、16 | 約 350 | |

行数は、生成物（`docs/contracts/*.md`・`docs/glossary.md`・`contracts/schemas/io/*.json`）を除いて数える。数えるコマンドは `git diff --shortstat <ベース>...HEAD -- . ':(exclude)plugins/ndf/docs/contracts' ':(exclude)plugins/ndf/contracts/schemas/io' ':(exclude)docs/glossary.md'`。

依存の向きは U1 → U2 → U3 → U4 である。U2 は U1 の `writing.types_section` を、U3 は U2 の `missing` と `rows` を呼ぶ。U4 の手順書は、U1〜U3 のコマンドと基準のファイルを指す。どの単位も、終えた後に動く。U1〜U3 だけをマージした時点では、質問を止める hook が働き、記録を作る手順書がまだ無い。質問が 2 回まで止まるだけで作業は進められるが、止まる回数を増やさないために、U3 と U4 は同じリリースに入れる。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `drive.py` は 998 行だが、触るのは 8 タスク中 1 タスクで、足すのは 2 行以内である（1,000 行の規則の上限に収まる。#4076 の実装が先に入れば 915 行）。`worker.py` は 991 行で、触るのは 8 タスク中 1 タスクである。cross-review のテストは 232 件（`tests/domains/crossreview/`）、worker のテストは 70 件（`tests/worker/`）、guard のテストは 59 件（`test_guards.py`）ある。#4076 が `drive.py` の分割を先に進めているので、同じ分割をここで重ねない |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 記録のファイルの保存先を決める処理（`records/reading.py` と `progress/gate/records.py` の `rejections_path`）、指示の本文の組み立て（`criteria_decide` の `reviewer_block`・`fixer_block`・`document_block`） | U2 が `rejections_path` と同じ形の処理を足し、U1 が同じ指示の組み立てに 3 つ目の関数を足す |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4076 の実装（ https://github.com/devbasex/ai-plugins/pull/4397 、 https://github.com/devbasex/ai-plugins/pull/4399 、 https://github.com/devbasex/ai-plugins/pull/4402 と、そのマージ先の `sprint/p9-lz2-ci-r3`）と、`criteria_decide.py`・`review-criteria.json`・`drive.py` の同じ関数を変える。後からマージする側に衝突が残る | 番号 6 と `document_block` の独立した関数で、衝突を `drive.py` の指示の 2 行と `review-criteria.json` の 2 か所に狭める。U1 の着手前に `origin/main` に #4076 が入ったかを見て、入っていれば U1 の作業場所に取り込んでから始める |
| `drive.py` に足す 2 行で 1,000 行を超える | U1 で行数を `wc -l` で数える。超えるときだけ、`_regular_blobs` から `changed_sections` までを `changed_docs.py` へ移す（詳細設計書の「基準の本文を指示に入れる」）。#4076 が `changes.py` へ移していれば、この移動はしない |
| 基準 6 の指摘が毎回出て、レビューが上限のラウンドまで回る | 決定 5 のとおり、2 ラウンド目以降の major を新しく読む文に限る。この課題の PR のレビューで、上限に達したときに残った基準 6 の major の件数を数え、PR の本文に書く |
| 記録を作る手順書がまだ無い期間に、hook が質問を止める | 同じ区分・同じ理由で止めるのは 2 回までで、その次は通る（決定 11）。U3 と U4 を同じリリースに入れる |
| 手元の `.ndf/handoff/writing-criteria.md` が無い・設計の後に変わった | Task 3 のとおり、無ければ設計書の「文書の基準」から書き起こす。設計の要点と食い違う所は全文に合わせ、食い違いを PR の本文に書く |

## 切り戻し手順

- U1: PR を revert する。記録やデータの移行は無い
- U2〜U3: hook だけを止めるなら、環境変数 `NDF_HOOK_OFF` に `reading-check` を足す。PR を revert しても、`reading-checks.jsonl` は読まれなくなるだけで、ほかの処理は影響を受けない
- U4: PR を revert する

## 完了の定義

- [ ] 受け入れ条件 1〜13 と 17〜20 を満たし、条件ごとに検証手段と結果が対応している。3・4・20 は読んで確かめる
- [ ] 受け入れ条件 14〜16 は、リリース後に確かめる（要求仕様の「検証手段」）
- [ ] CI のテストと、`ndf check` の静的な検査が通る
- [ ] 設計書の「未確認のまま残ること」の 3 項目のうち、Codex と Kiro CLI が基準 6 を同じ重さで読むかを、この課題の PR のレビューで 1 回確かめ、PR の本文に結果を書く
