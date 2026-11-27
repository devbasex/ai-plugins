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
| `experimental/ndf/mvv_replay.py`（新設） | サブコマンド `material`・`judge`・`bodies`・`tally`・`gate`・`guard-check`（`main`・`prompt_at`・`material_of`・`read_pairs`・`review_paths`・`latest_review`・`judge_once`・`gate_once`・`GuardedApi`・`guarded_hub`・`guard_check`） |
| `experimental/ndf/mvv_replay_cases/`（新設） | 推定の扱いの有無だけが違う作り物の材料の対（要求仕様と設計書を 1 組にした材料を 2 つ） |
| `plugins/ndf/src/ndf/adapters/markdown.py` | `tables`（`Table`・`TableRow`） |
| `plugins/ndf/src/ndf/domains/records/design_check_decide.py`（新設） | `decision_rows`・`redline_rows`・`findings`・`anchors_lost`・`added_lines`・`heading_touched`・`delegates`・`named`・`numbers_in`・`blank` と定数 |
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
| `plugins/ndf/contracts/data/mvv-stop-reasons.json` | 定型文 `table_unreadable` |
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
- **変更内容:** サブコマンド `material` と `judge` を作る（`prompt_at`・`material_of`・`judge_once`）。材料は PR ごとに 1 回組んで保存し、比べる判定には同じファイルを渡す。`material` は、材料を組んだ時点の PR 本文とその SHA-256（`Material.pr_body`・`pr_body_sha256`）と、変えたファイルのパス（`Material.changed`）も保存する。`judge` は PR ごとの判定結果と、起動ごとの実行の識別子（UUID）をファイルへ保存し、後の `bodies` と `tally` が判定を呼び直さずに使えるようにする。この Task の `material` は、表の集計（`Material.tabled` を埋める処理）を持たず、`tabled` を None（未確認）のまま保存する。空のタプルは「番号なし」の意味なので使わない。表の集計は、後の単位で新設する `design_check_decide.redline_rows` を使うので Task 14 で足す。順 1・2 は表の集計を使わないので、この Task だけで打てる。推定の対を 1 つ（扱いありと扱いなしの 2 つの材料）置く。10 系の判定の記録の boundary から、対照 20 件の番号ごとに当たる操作を決めて設計書の表へ書く。2524 は元の差分から操作と番号を決め、当たらなければ根拠を #3017 に記録して対照から外す
- **満たす受け入れ条件:** 5・6・7 の確かめの手段
- **進め方:** 実験版は単体テストを持たない（設計書の決定 5）。手元で 1 件を判定して、結果のファイルにモデル ID・Claude Code のバージョン・指示文のコミット・材料のベースと head の SHA・材料のハッシュが載ることを確かめる

### Task 4: 指示文を変え、対照で守りを確かめてから入れる（決定 6 の順 1・2）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py`（`SYSTEM`）
- **変更内容:** 作業ツリーで `SYSTEM` を設計書の「MVV 判定の指示文」の変更後の文に変え、Task 3 の実験版で順 1（対照 20 件と設計 PR #4583）を判定する。合格したら順 2（推定の対）を判定し、合格したときだけ `SYSTEM` の変更をコミットする。打ったコマンド・件数・費用を記録に残す
- **満たす受け入れ条件:** 5a・5b・6 の実装の前の確かめ（指示文の確かめとして記録する。合否はリリース後テストの決定 6 の順 3 で確定する）
- **進め方:** 順 1 か順 2 に合格しなければ、`SYSTEM` の変更を作業ツリーから戻してコミットせず、件ごとの結果を #3017 と conductor へ報告する。ほかのタスクは続ける。受け入れ条件 5a は満たしていないとして課題を閉じない。指示文を直したときは順 1 から打ち直す

### Task 5: 表の行と欄を読む

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/markdown.py`・`plugins/ndf/tests/adapters/test_markdown.py`
- **変更内容:** `tables` と型 `Table`・`TableRow`
- **満たす受け入れ条件:** 4・5 の部品
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 6: 番号の無い委ね方と表の行を判定する

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/design_check_decide.py`・`plugins/ndf/tests/domains/records/test_design_check.py`
- **変更内容:** 詳細設計書の「設計の検査の判定ロジック」の 10 関数と定数。委ねる語の式は設計書の「入出力のコントラクト」の式をそのまま置く
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
- **進め方:** Git のアダプターを差し替えた偽物で、未コミット・未追跡・削除・名前の変更・表の行を消したファイル・見出しを足したファイル・読めない hunk の見出しの場合を先に失敗するテストにする → 最小実装 → 整理。単体テストでは git を起動しない（詳細設計書の「`records` のサブコマンド」の単体テストの観点）。書き終えたら、実際の git との接続を手元で確かめる。一時リポジトリで、未コミット・未追跡・マージ先だけの変更・1 行の置き換え・外部 diff と textconv を設定した場合を作って打ち、続けてこのリポジトリの設計書とこの計画で `ndf records design check --base main` を打って、結果を確かめる

### Task 9: 設計文書を push するプランに検査を置く

- **対象ファイル:** `plugins/ndf/contracts/templates/design.json`・`design-detail.json`・`document-review.json`・`plugins/ndf/contracts/workflow/stages/design.json`・`document-review.json`・`plugins/ndf/contracts/worker/kinds.json`・`plugins/ndf/tests/route/test_flows.py`・`plugins/ndf/tests/route/test_templates.py`
- **変更内容:** 詳細設計書の「設計・詳細設計のプランのテンプレート」と「文書レビューのプランのテンプレートと工程の定義」のステップとつなぎ直し。作業 `design-check-fix`
- **満たす受け入れ条件:** 4（決定 2）
- **進め方:** テスト設計の決定 2 の 2 行を、プランを歩くテスト（`walk`）で先に失敗させる → テンプレートを直す → 整理。Task 1 で縛った分岐は行き先だけを書き直す

### Task 10: 省ける判定のときに表を読んで省かない

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_gate.py`・`plugins/ndf/contracts/data/mvv-stop-reasons.json`・`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** `_tabled`・`_table_numbers` と定数 2 つ。`_judged` の最後を `j.posted_if_current(_tabled(j, rows, _bound(j, …)))` にする
- **満たす受け入れ条件:** 5c・6
- **進め方:** テスト設計の「5c・決定 7」の行の壊し方を、判定の代役と偽の GitHub で先に失敗するテストにする → 最小実装 → 整理

### Task 11: 手順書と決定の書き方を直す

- **対象ファイル:** `plugins/ndf/skills/design/SKILL.md`・`plugins/ndf/skills/design/references/decisions.md`
- **変更内容:** 詳細設計書の「設計の手順書と決定の書き方」の 3 か所。書くコマンドは Task 8 の後に打って結果を確かめてから書く
- **満たす受け入れ条件:** 1〜4
- **進め方:** 文書の変更なのでテスト駆動を適用しない（`.md` の文言を照合するテストは書かない）。`python3 scripts/check-skill-frontmatter.py` と `ndf check` で参照切れを確かめる

### Task 12: 導入した版のゲートを打つモードを作る

- **対象ファイル:** `experimental/ndf/mvv_replay.py`
- **変更内容:** サブコマンド `gate`・`guard-check`（`gate_once`・`GuardedApi`・`guarded_hub`・`guard_check`）と、引数 `--plugin-root`。GitHub へ送る口を差し替え、読み取りだけを送り、確認用 PR へのコメントの投稿だけを捕まえ、ほかの要求は送る前に拒む（詳細設計書の「判定の比較（実験版）」）
- **満たす受け入れ条件:** 5・6（リリース後テストの手段）
- **進め方:** 捕まえる経路だけを試す。表の確かめ（Task 10）に依らない
  1. `guard-check` を打ち、GitHub へ何も送らずに、書き込みの経路がすべて捕まるか拒まれることを確かめる
  2. このリポジトリの作業ツリーの `plugins/ndf` ディレクトリを `--plugin-root` に渡し、確認用の開いた PR 1 件で `--answer stand-in` を 1 回打つ。設計書には「人へ残す判断」の表を置かない。省く結果になるので、捕まえたコメントは 1 件で、その本文に証跡の行があり、拒んだ要求は 0 件で、確認用 PR に新しいコメントが付いていないことを確かめる。証跡の行がある本文が GitHub へ届かないことが、この手順で確かめたい点である
  3. 確認用 PR とブランチは Task 13 で使うので残す

### Task 13: 表による停止を、導入する形のゲートで確かめる

- **対象ファイル:** 無し（確認だけ）
- **変更内容:** 無し。Task 10 の表の確かめと Task 12 のゲートのモードを合わせて打つ
- **満たす受け入れ条件:** 5c・6（リリース後テストの手段が働くことの確認）
- **依存:** Task 10 と Task 12。`--plugin-root` には、Task 10 を含む作業ツリーの `plugins/ndf` ディレクトリを渡す。実験版はその直下の `src` から `ndf` を読み込むので、作業ツリーのルートを渡すと読み込めない。Task 10 の前に打つと、表の確かめを持たない版で打つことになり、表による停止を確かめられない
- **進め方:** Task 12 の確認用 PR の設計書に「人へ残す判断」の表（番号 `C7` の行）を足して push し、`--answer stand-in` で打つ。`skip` が偽で、`why` に表の理由と設計書のパスと `C7` があり、捕まえたコメントが 1 件で証跡の行が無く、拒んだ要求が 0 件であることを確かめる。確認が終わったら PR を閉じてブランチを消す

### Task 14: 実験版の材料に表の集計を足す

- **対象ファイル:** `experimental/ndf/mvv_replay.py`
- **変更内容:** サブコマンド `material` に、詳細設計書の「判定の比較（実験版）」の `material_of` の「表の確かめ」を足す。差分の設計成果物の（head の SHA, パス）の組を `read_pairs` でまとめて読み、組ごとの「取得済み」「本文が無い」「未取得」と読みに行った回数を保存し、PR ごとに `design_check_decide.redline_rows` を当てて `Material.tabled` を埋める
- **満たす受け入れ条件:** 7（順 4 の確かめの手段）
- **依存:** Task 3 と Task 6（`redline_rows`）
- **進め方:** 実験版は単体テストを持たない（設計書の決定 5）。手元で、表に番号を持つ行がある PR・表の無い PR・本文が切り詰められるか読めない組を含む PR の 3 件で `material` を打ち、`tabled` が「番号あり」「番号なし」「未確認」に分かれることを確かめる。同じ `--store` で 2 回打ち直し、「未取得」の組を読みに行くのが計 2 回で止まることも確かめる

### Task 15: 本文の確認の材料を集め、結果を集計へ戻す

- **対象ファイル:** `experimental/ndf/mvv_replay.py`
- **変更内容:** 詳細設計書の「判定の比較（実験版）」の `review_paths`・`bodies`・`tally` を作る
  - `review_paths`: `issues/` の一覧の名前に `issues/` を付けて照合し、PR ごとに `ReviewPaths`（読む組と、一覧を読めたか）を返す
  - `bodies`: 保存した判定結果（`--judged`）を今回の材料と照らしてから、本文の確認に当てる PR を選ぶ。前の回までの本文の確認の結果（`--reviewed`）で確認済みの PR は、一覧を読む前に外す。PR 本文とファイルの本文を確認担当が読むファイルへ書き、読めていない本文がある PR に印を付ける
  - `latest_review`: 追記した本文の確認の記録から、材料と一致する最新の記録を PR ごとに選ぶ。`bodies` と `tally` の両方が使う
  - `tally`: 保存した判定結果・材料・本文の確認の結果（`--reviewed`）だけから、判定上の値・確認後の値・経路全体の値を出す。判定結果と本文の確認の記録は、今回の材料と一致したときだけ採る。`judge` が起動ごとに書く実行の識別子を使い、`--judged` を回の数だけ受け、同じ実行の識別子が重なれば止め、全回の結果から最終の合否を 1 つ出す。保留には「再測定が要る」「未確認が残る」「再測定でも差が残らない」のどれかの理由を付け、再測定は「再測定が要る」のときだけ始める。変更後の側の「含む」で不合格とし、変更後の側に未確認の PR が残れば合格を保留し、「#3017 へ記録する件」の一覧を書く
- **満たす受け入れ条件:** 7（順 4 の確かめの手段）
- **依存:** Task 14（`read_pairs` と `Material.tabled`）
- **進め方:** 実験版は単体テストを持たない（設計書の決定 5）。手元で、Task 14 で保存した材料と、その材料を Task 3 の `judge` で判定した結果を使って `bodies` を打ち、同じ課題の要求仕様・設計書・詳細設計書が PR で変えていなくても出力に載ることを確かめる。続けて、作り物の本文の確認の結果で `tally` を 3 回打つ。1 回目は変更後の側で「含む」の PR を含めて不合格になること、2 回目は head の SHA を変えた記録が採られずに未確認へ数えられること、3 回目は `c93e690ed` の側だけの「含む」が合否を変えずに「#3017 へ記録する件」に載ることを確かめる。さらに、変更後の側に未確認の PR を 1 件残した結果で合格が保留になること、3 回分の `--judged` で差が 1・0・3 件のとき保留になること、同じ判定結果を 3 回渡すと止まること、同じ PR に「判断できない」の後に「含む」を追記すると「含む」が採られること、`--reviewed` を渡した `bodies` が確認済みの PR の一覧を読まないことを確かめる。`tally` は LLM も GitHub も呼ばない

決定 6 の順 3・4 は、リリースの後にリリース後テストの担当が Task 3・12・14・15 の実験版で打つ。実装のタスクには含めない。順 4 では、`material`・`judge`・`bodies`・`tally` の順に打ち、`bodies` の出力を確認担当が読んで本文の確認の結果を書く。

## レビューの単位

見込みの差分の行数は追加と削除の和で、テストを含む。数えないパスは無い。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 判定の比較と指示文 | Task 1・2・3・4・12 | 2912:5a・2912:5b・2912:6・2912:7 の確かめの手段、2912:8 | 約 620（実験版 約 420、材料の組み立てと指示文とテスト 約 150、設計書の表 約 50） | （実装の後に書き足す） |
| 設計の検査の判定 | Task 5・6 | 2912:4 | 約 550（判定 約 200、アダプター 約 40、テスト 約 310） | （実装の後に書き足す） |
| 検査のコマンドと起点の解決 | Task 7・8 | 2912:4・2912:8 | 約 400（集め方と CLI 約 150、起点の移動 約 80、テスト 約 170） | （実装の後に書き足す） |
| プランと手順書 | Task 9・11 | 2912:1〜4 | 約 350（テンプレートと工程と作業の定義 約 120、手順書 約 30、テスト 約 200） | （実装の後に書き足す） |
| MVV 判定の表の確かめ | Task 10・13・14・15 | 2912:5c・2912:6・2912:7 の確かめの手段 | 約 400（ゲート 約 50、テスト 約 200、実験版の表の集計 約 30、本文の確認の材料と集計 約 120。Task 13 は確認だけで差分を持たない） | （実装の後に書き足す） |

依存の向き: 「設計の検査の判定」の `redline_rows` を「MVV 判定の表の確かめ」（Task 10 と Task 14）が、「検査のコマンドと起点の解決」のコマンドを「プランと手順書」が使う。「MVV 判定の表の確かめ」の Task 13 は、「判定の比較と指示文」の Task 12 のゲートのモードと、同じ単位の Task 10 を使う。Task 14 は、「判定の比較と指示文」の Task 3 の `material` に表の集計を足す。Task 15 は、Task 14 の `read_pairs` と Task 3 の判定結果を使う。流す順は表の上から下で、「判定の比較と指示文」はほかの単位に依存しない。Task 4 が合格しないときも、Task 4 の `SYSTEM` の変更だけを外して単位を出す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 触るファイルの行数は `docrebuild/drive.py` 566・`records/cli.py` 549・`records/mvv_gate.py` 528・`records/mvv_material_decide.py` 283・`adapters/markdown.py` 124 で、どれも 600 行以下。別のタスクと同じファイルを触るタスクは 15 件中 11 件（Task 13 はファイルを触らない）で、1 つのファイルに集まるタスクは `mvv_replay.py` の Task 3・12・14・15 の 4 件が最大で、ほかは 2 件である（Task 1 と後のタスクのテストのファイル、`mvv_material_decide.py` の Task 2・4、`test_design_check.py` の Task 6・8）。`mvv_replay.py` は新設の実験版で、単体テストを持たず、4 件はサブコマンドごとに分かれるので、先に整える対象にしない。判定と集め方の本体は新しいファイルに入る。覆うテストは `test_mvv_gate.py` 48・`test_docrebuild_run.py` 33・`test_flows.py` 31・`test_mvv_material.py` 8・`test_markdown.py` 5。起点の解決の移動は、移動先が新しいファイルなので Task 7 の中で行う |
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
- [ ] 実装の前の確かめ（決定 6 の順 1・2）の結果を、指示文の確かめとして記録している。順 1・2 は切らない材料を判定者へ直接渡すだけで、実際のゲートの経路を通らないので、この結果では受け入れ条件 5a・5b・6 を済みとしない
- [ ] 受け入れ条件 5c の表の読み分けを `test_mvv_gate.py` の表の確かめのテストで確かめている
- [ ] 「レビューの単位」の実際の行数を書き足している

ここから先の完了の条件とリリース後テストの合否は、指示文の変更（`SYSTEM`）を含めて出すかで分かれる。指示文を外して出す場合は、指示文に依る確かめを打てないので、表の確かめで確定できる受け入れ条件 5c だけを確定し、残りは未確認として残す。

| 項目 | 指示文を含めて出す場合 | 順 1 か順 2 に合格せず、指示文を外して出す場合 |
| --- | --- | --- |
| 実装の前の確かめ | 順 1・2 の両方に合格している | 件ごとの結果を #3017 へ記録し、作業の依頼元へ報告している。この行を除き、上の項目を満たせば実装は完了とする |
| リリース後テストで打つもの | 決定 6 の順 3 のすべて（配った版の部品での対照の判定し直し、順 2 の推定の対の「扱いあり」の材料の 3 回の判定、`guard-check` と確認用 PR でのゲートの 2 回）と、順 3 に合格した後の順 4 | 同じ導入した版で `guard-check` に合格してから、確認用 PR の表の無い head と表を足した head で、判定の代役を使ってゲートを 1 回ずつ打つ。指示文に依る確かめ（対照の判定し直し、推定の対の判定、実物の LLM によるゲート、順 4）は打たない |
| 確定する受け入れ条件 | 5a（推定の対で 3 回のうち 2 回以上「省ける」）、5b・6（対照の判定し直しと実物の LLM によるゲート）、5c・6（代役によるゲート）、7（順 4 の件数・割合・費用・打ったコマンドを記録に残す） | 5c だけ（代役によるゲート） |
| 未確認として残す受け入れ条件 | 打てずに未確認になった確かめに当たるもの | 5a・5b・6・7。#2912 は閉じない |

どちらの場合も、リリース後テストで打つまで、その受け入れ条件を済みと記録しない。
- [ ] 「レビューの単位」の実際の行数を書き足している
</content>
