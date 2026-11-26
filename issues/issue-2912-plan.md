# #2912: 番号の無い「人へ残す判断」を検査で止め、MVV 判定が表を読んで承認を省かない

## 関連リンク

- 要求仕様: [#2912](https://github.com/devbasex/ai-plugins/issues/2912)（コピーは [issue-2912-requirements.md](./issue-2912-requirements.md)）。受け入れ条件は番号で指す
- 設計書: [issue-2912-design.md](./issue-2912-design.md)。詳細設計書: [issue-2912-design-detail.md](./issue-2912-design-detail.md)
- モード: `standard`（課題の本文の判定。設計の判断と MVV 判定の約束を足し、既定で動くプランと承認ゲートの省き方を変える）

## 修正対象

詳細設計書の「地図」から引き写す。関数ごとの仕様は詳細設計書の構成要素ごとの小見出しにある。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | `patch_material` に行数の引数 `limit`。指示文 `SYSTEM` に起票者の推定の扱いの箇条 |
| `experimental/ndf/mvv_replay.py`（新設） | サブコマンド `judge`・`gate`（`main`・`prompt_at`・`material_of`・`judge_once`・`gate_once`・`CapturingHub`） |
| `experimental/ndf/mvv_replay_cases/`（新設） | 推定の扱いの有無だけが違う作り物の要求と設計の 2 組 |
| `plugins/ndf/src/ndf/adapters/markdown.py` | `tables`（`Table`・`TableRow`） |
| `plugins/ndf/src/ndf/domains/records/design_check_decide.py`（新設） | `decision_rows`・`redline_rows`・`findings`・`anchors_lost`・`added_lines`・`delegates`・`named`・`numbers_in`・`blank` と定数 |
| `plugins/ndf/src/ndf/core/start_point.py`（新設） | `resolve`・`StartPointError`（`docrebuild.drive.start_point` から移す） |
| `plugins/ndf/src/ndf/domains/docrebuild/drive.py` | `start_and_changes` が `start_point.resolve` を呼ぶ。`start_point`・`_resolve` を消す |
| `plugins/ndf/src/ndf/domains/records/design_check.py`（新設） | `from_files`・`from_base`・`check`・`Target`・`DesignCheckError` |
| `plugins/ndf/src/ndf/domains/records/cli.py` | `_design_check`・`_add_design`、`_parser` に登録、モジュールの説明に 1 行 |
| `plugins/ndf/src/ndf/domains/records/__init__.py` | 責務の表に設計の検査の行 |
| `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | `_tabled`・`_table_numbers`・定数 2 つ、`_judged` の最後の 1 行 |
| `plugins/ndf/contracts/templates/design.json` | 検査・繰り返しの数え・直し・直した後のレビューのステップと、push へ向かう分岐のつなぎ直し |
| `plugins/ndf/contracts/templates/design-detail.json` | 検査と直しのステップ、値 `base` |
| `plugins/ndf/contracts/templates/document-review.json`（新設） | レビュー → 検査 → push のプラン |
| `plugins/ndf/contracts/workflow/stages/design.json`・`document-review.json` | 設計レビューとドキュメントレビューのプランを `document-review` にする |
| `plugins/ndf/contracts/worker/kinds.json` | 作業 `design-check-fix` |
| `plugins/ndf/skills/design/SKILL.md`・`references/decisions.md` | 手順 5・6 の確かめをコマンドへ、委ねる行の書き方 |
| `plugins/ndf/tests/…`（詳細設計書の地図の 7 ファイル） | 各タスクのテスト |

## タスク分解

設計書の決定 6 の順序に従い、指示文の変更は、実物の LLM で対照の番号を確かめてから入れる。そのため、判定の比較の実験版と指示文を最初の単位にする。設計の検査と表の確かめは、指示文の合否に依らずに守りを強める変更なので、順 1・2 に合格しなくても進める。

### Task 1: 触る振る舞いを退行テストで縛る

- **対象ファイル:** `plugins/ndf/tests/domains/records/test_mvv_material.py`・`plugins/ndf/tests/route/test_flows.py`・`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** 変える前の振る舞いのうち、変えずに残す次の 3 つをテストで縛る。今あるテストで縛れていれば足さない
  - `patch_material` が 200 行で切ること
  - `design` のプランの `ci-fix`・`base-sync`・`fix-base`・`fix-push-reviewed` の後が push へ進むこと（後のタスクで行き先だけを変える）
  - `gate` の省く判定が `target` を持ち、省かない判定が持たないこと
- **満たす受け入れ条件:** 8
- **進め方:** 今のコードで通ることを確かめてからコミットする（機能追加の最初のタスク）

### Task 2: 材料を切らずに組めるようにする

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py`・`plugins/ndf/tests/domains/records/test_mvv_material.py`
- **変更内容:** `patch_material(files, limit=PATCH_LINES)`。None なら切らない。本番の呼び出しは変えない
- **満たす受け入れ条件:** 7（実験版が材料の全文を判定者へ渡す前提）
- **進め方:** 失敗するテスト（201 行を `limit=None` で切らない）→ 最小実装 → 整理

### Task 3: 判定の比較の実験版と、対照の当たる操作を決める

- **対象ファイル:** `experimental/ndf/mvv_replay.py`・`experimental/ndf/mvv_replay_cases/`・`issues/issue-2912-design.md`（決定 6 の対照の表の「当たる操作」の欄）
- **変更内容:** サブコマンド `judge` を作る（`prompt_at`・`material_of`・`judge_once`）。推定の対の 2 組を置く。10 系の判定の記録の boundary から、対照 20 件の番号ごとに当たる操作を決めて設計書の表へ書く。2524 は元の差分から操作と番号を決め、当たらなければ根拠を #3017 に記録して対照から外す
- **満たす受け入れ条件:** 5・6・7 の確かめの手段
- **進め方:** 実験版は単体テストを持たない（設計書の決定 5）。手元で 1 件を判定して、結果のファイルにモデル ID・Claude Code のバージョン・指示文のコミット・材料のハッシュが載ることを確かめる

### Task 4: 指示文を変え、対照で守りを確かめてから入れる（決定 6 の順 1・2）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py`（`SYSTEM`）
- **変更内容:** 作業ツリーで `SYSTEM` を設計書の「MVV 判定の指示文」の変更後の文に変え、Task 3 の実験版で順 1（対照 20 件と設計 PR #4583）を判定する。合格したら順 2（推定の対）を判定し、合格したときだけ `SYSTEM` の変更をコミットする。打ったコマンド・件数・費用を記録に残す
- **満たす受け入れ条件:** 5・6（実装の前の確かめ）
- **進め方:** 順 1 か順 2 に合格しなければ、`SYSTEM` の変更を作業ツリーから戻してコミットせず、件ごとの結果を #3017 と conductor へ報告する。ほかのタスクは続ける。受け入れ条件 5 は満たしていないとして課題を閉じない。指示文を直したときは順 1 から打ち直す

### Task 5: 表の行と欄を読む

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/markdown.py`・`plugins/ndf/tests/adapters/test_markdown.py`
- **変更内容:** `tables` と型 `Table`・`TableRow`
- **満たす受け入れ条件:** 4・5 の部品
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 6: 番号の無い委ね方と表の行を判定する

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/design_check_decide.py`・`plugins/ndf/tests/domains/records/test_design_check.py`
- **変更内容:** 詳細設計書の「設計の検査の判定ロジック」の 9 関数と定数。委ねる語の式は設計書の「入出力のコントラクト」の式をそのまま置く
- **満たす受け入れ条件:** 4
- **進め方:** 設計書の「行の例」の 2 つの表と、テスト設計の受け入れ条件 4 の行を、先に失敗するテストにする → 最小実装 → 整理

### Task 7: 起点の解決を共通の場所へ移す

- **対象ファイル:** `plugins/ndf/src/ndf/core/start_point.py`・`plugins/ndf/src/ndf/domains/docrebuild/drive.py`・`plugins/ndf/tests/domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** `drive.start_point` の中身を `start_point.resolve` へ移し、`start_and_changes` は `StartPointError` を `Precondition` に包む
- **満たす受け入れ条件:** 8（振る舞いを変えない）
- **進め方:** 振る舞いを変えない移動なので、今の `test_start_point` の呼び先と例外の名前だけを変え、差分の前後で同じ件数が通ることを確かめる

### Task 8: `ndf records design check` を足す

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/design_check.py`・`cli.py`・`__init__.py`・`plugins/ndf/tests/domains/records/test_design_check.py`
- **変更内容:** `from_files`・`from_base`・`check` と CLI の登録。`--result` に同じ結果を書く
- **満たす受け入れ条件:** 4
- **進め方:** 一時リポジトリで、未コミット・未追跡・削除・名前の変更・マージ先だけの変更・表の行を消したファイルの場合を先に失敗するテストにする → 最小実装 → 整理。書き終えたら、このリポジトリの設計書とこの計画で `ndf records design check --base main` を打ち、結果を確かめる

### Task 9: 設計文書を push するプランに検査を置く

- **対象ファイル:** `plugins/ndf/contracts/templates/design.json`・`design-detail.json`・`document-review.json`・`plugins/ndf/contracts/workflow/stages/design.json`・`document-review.json`・`plugins/ndf/contracts/worker/kinds.json`・`plugins/ndf/tests/route/test_flows.py`・`plugins/ndf/tests/route/test_templates.py`
- **変更内容:** 詳細設計書の「設計・詳細設計のプランのテンプレート」と「文書レビューのプランのテンプレートと工程の定義」のステップとつなぎ直し。作業 `design-check-fix`
- **満たす受け入れ条件:** 4（決定 2）
- **進め方:** テスト設計の決定 2 の 2 行を、プランを歩くテスト（`walk`）で先に失敗させる → テンプレートを直す → 整理。Task 1 で縛った分岐は行き先だけを書き直す

### Task 10: 省ける判定のときに表を読んで省かない

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_gate.py`・`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** `_tabled`・`_table_numbers` と定数 2 つ。`_judged` の最後を `j.posted_if_current(_tabled(j, rows, _bound(j, …)))` にする
- **満たす受け入れ条件:** 5・6
- **進め方:** テスト設計の「5・決定 7」の行の壊し方を、判定の代役と偽の GitHub で先に失敗するテストにする → 最小実装 → 整理

### Task 11: 手順書と決定の書き方を直す

- **対象ファイル:** `plugins/ndf/skills/design/SKILL.md`・`plugins/ndf/skills/design/references/decisions.md`
- **変更内容:** 詳細設計書の「設計の手順書と決定の書き方」の 3 か所。書くコマンドは Task 8 の後に打って結果を確かめてから書く
- **満たす受け入れ条件:** 1〜4
- **進め方:** 文書の変更なのでテスト駆動を適用しない（`.md` の文言を照合するテストは書かない）。`python3 scripts/check-skill-frontmatter.py` と `ndf check` で参照切れを確かめる

### Task 12: 導入した版のゲートを打つモードを作る

- **対象ファイル:** `experimental/ndf/mvv_replay.py`
- **変更内容:** サブコマンド `gate`（`gate_once`・`CapturingHub`）と、引数 `--plugin-root`。捕まえる書き込みのメソッドは、`mvv_gate.gate` から `publish.Publisher` の先を読んで決める（詳細設計書の「未確認のまま残ること」）
- **満たす受け入れ条件:** 5・6（リリース後テストの手段）
- **進め方:** このリポジトリの作業ツリーを `--plugin-root` にして、確認用の開いた PR 1 件で 2 回打ち、捕まえたコメントが 1 件ずつで証跡の行が無いことを確かめる。確認が終わったら PR を閉じてブランチを消す

決定 6 の順 3・4 は、リリースの後にリリース後テストの担当が Task 3・12 の実験版で打つ。実装のタスクには含めない。

## レビューの単位

見込みの差分の行数は追加と削除の和で、テストを含む。数えないパスは無い。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 判定の比較と指示文 | Task 1・2・3・4・12 | 2912:5・2912:6・2912:7 の確かめの手段、2912:8 | 約 650（実験版 約 450、材料の組み立てと指示文とテスト 約 150、設計書の表 約 50） | （実装の後に書き足す） |
| 設計の検査の判定 | Task 5・6 | 2912:4 | 約 550（判定 約 200、アダプター 約 40、テスト 約 310） | （実装の後に書き足す） |
| 検査のコマンドと起点の解決 | Task 7・8 | 2912:4・2912:8 | 約 400（集め方と CLI 約 150、起点の移動 約 80、テスト 約 170） | （実装の後に書き足す） |
| プランと手順書 | Task 9・11 | 2912:1〜4 | 約 350（テンプレートと工程と作業の定義 約 120、手順書 約 30、テスト 約 200） | （実装の後に書き足す） |
| MVV 判定の表の確かめ | Task 10 | 2912:5・2912:6 | 約 250（ゲート 約 50、テスト 約 200） | （実装の後に書き足す） |

依存の向き: 「設計の検査の判定」の `redline_rows` を「MVV 判定の表の確かめ」が、「検査のコマンドと起点の解決」のコマンドを「プランと手順書」が使う。流す順は表の上から下で、「判定の比較と指示文」は残りの単位と依存しない。Task 4 が合格しないときも、Task 4 の `SYSTEM` の変更だけを外して単位を出す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 触るファイルの行数は `docrebuild/drive.py` 566・`records/cli.py` 549・`records/mvv_gate.py` 528・`records/mvv_material_decide.py` 283・`adapters/markdown.py` 124 で、どれも 600 行以下。別のタスクと同じファイルを触るタスクは 12 件中 9 件だが、1 つのファイルに集まるタスクは最大 2 件である（Task 1 と後のタスクのテストのファイル、`mvv_material_decide.py` の Task 2・4、`mvv_replay.py` の Task 3・12、`test_design_check.py` の Task 6・8）。判定と集め方の本体は新しいファイルに入る。覆うテストは `test_mvv_gate.py` 48・`test_docrebuild_run.py` 33・`test_flows.py` 31・`test_mvv_material.py` 8・`test_markdown.py` 5。起点の解決の移動は、移動先が新しいファイルなので Task 7 の中で行う |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `design_check_decide` と `mvv_material_decide` の番号の正規表現（文中の番号と、MVV の表の行頭の番号）、3 つのテンプレートで同じ形の検査・繰り返しの数え・直しのステップ | 単位をまたいで同じ形の処理が 2 か所以上にできる。共通のステップへ寄せるか（`templates/parts/steps.json`）は、3 つのテンプレートがそろった後に比べる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 順 1 で前から落ちていた番号が見つかり、指示文を入れられない | Task 4 の進め方のとおり、指示文の変更だけを外して報告し、#3017 に記録する。設計の検査と表の確かめは出す |
| 委ねる語の式が、このリポジトリの既存の設計文書の地の文で指摘を出す | Task 8 の最後に、開いた設計 PR の文書で `--base main` を打ち、出た指摘が設計書の「行の例」の判定どおりかを確かめる |
| 表のある設計書を直す後の設計 PR が、誤字の直しでも人の承認へ戻る | 設計書の決定 7 の代償として受け入れる。リリースの説明に 1 行書く |

## 完了の定義

- [ ] 受け入れ条件 1〜4 と 8 を満たし、4 は `test_design_check.py` と `test_flows.py`、8 は PR の CI の全体のテストで確かめている
- [ ] 受け入れ条件 5・6 は、実装の前の確かめ（決定 6 の順 1・2）の結果と、`test_mvv_gate.py` の表の確かめのテストで確かめている。順 3 はリリース後テストで確かめる
- [ ] 受け入れ条件 7 は、リリース後テストで決定 6 の順 4 を打ち、件数・割合・費用・打ったコマンドを記録に残す
- [ ] 「レビューの単位」の実際の行数を書き足している
</content>
