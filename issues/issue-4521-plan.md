# #4521: 解析が書く項目を、読み手のあるものだけにする

## 関連リンク

- 要求仕様: [issue-4521-requirements.md](./issue-4521-requirements.md)（受け入れ条件は番号で指す）
- 設計書: [issue-4521-design.md](./issue-4521-design.md)、詳細設計書: [issue-4521-design-detail.md](./issue-4521-design-detail.md)
- モード: `standard`（公開インタフェース（設定の形と `ndf project` の結果）を変える機能追加）

実装の worktree は、#4580 のマージ（`7504f88a3`）を含む最新の `main` から作る。

## 修正対象

関数ごとの中身は詳細設計の「地図」と各小見出しにある。パスの根は、`docs/`（根の確定仕様）と `.ndf/` を除き `plugins/ndf/` である。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/domains/project/measure_repo.py`・`measure_ci.py` | 読み手の無い測定を消す |
| `src/ndf/domains/project/cli.py` | 測定の組み立て |
| `src/ndf/domains/project/write.py` | 回答できる項目の絞り込み、記録の `analyzer`、差分の伏せ字、項目の番号 |
| `src/ndf/domains/project/analysis_record.py`・`check.py` | 旧い形のキー、解析器のバージョン |
| `contracts/schemas/ndf-settings/project.schema.json`・`skills/development-workflow/schemas/project.schema.json` | 6 項目を消す |
| `contracts/domains/ndf-files.json`・`languages.json` | ファイルの表と要約 |
| `docs/contracts/ndf-settings.md` | 生成物 |
| `skills/development-workflow/references/project-analysis.md`・`docs/specifications/ndf-project-analysis-record.md` | 手順と仕様 |
| `.ndf/project.json` | 6 項目と指紋を消す |
| `tests/domains/project/` の `test_project_measure.py`・`test_project_cli.py`・`test_analysis_record.py`・`test_measure_project_branch_state.py`・`test_write_plan.py` | テスト |

## タスク分解

テストは `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/project -q -n 4` で走らせる（CI テストの設定があるので、合否は PR の CI で決める）。どのタスクも終わりに `plugins/ndf/bin/ndf check` を通し、生成物がずれれば `--write-spec` で作り直す。

### Task 1: 今の `test`・`materials` の測定の結果を固定する
- **対象ファイル:** `tests/domains/project/test_project_measure.py`
- **変更内容:** `test`（質問になるリポジトリと、測った値になるリポジトリ）と `materials` の値・候補・根拠、`ci` の必須のチェック、指示書の案内の 2 種類を固定する退行テストを足す
- **満たす受け入れ条件:** 6・7
- **進め方:** 今のコードで通ることを確かめてから、先に置く

### Task 2: 読み手の無い測定と GitHub の呼び出しを消す
- **対象ファイル:** `measure_repo.py`・`measure_ci.py`・`cli.py`・`test_project_measure.py`・`test_project_cli.py`・`test_measure_project_branch_state.py`
- **変更内容:** 詳細設計の「リポジトリと CI の測定」
- **満たす受け入れ条件:** 5・6・7
- **進め方:** 呼ぶ API の並びのテストを先に落とし、消す。消した関数のテストは消すか書き換える

### Task 3: 設定の形から 6 項目を消し、解析器のバージョンを上げる
- **対象ファイル:** 2 つの `project.schema.json`・`ndf-files.json`（行 `project`）・`languages.json`・`ndf-settings.md`・`analysis_record.py`・`check.py`・`write.py`・`.ndf/project.json`・`test_analysis_record.py`・`test_project_cli.py`・`test_write_plan.py`
- **変更内容:** 詳細設計の「旧い形のキーと書き出し」。今のテストが例に使う `checks`・`languages` は残る項目へ置き換える。`.ndf/project.json` は同じ変更で直す（直さないと `ndf check` が落ちる）
- **満たす受け入れ条件:** 2・3・4・8・9・10・11・12・14
- **進め方:** 6 キーが残る設定の `check` が終了コード 3 になるテストと、取り除く値が出力に無いテストを先に書く

### Task 4: 手順書と確定仕様
- **対象ファイル:** `project-analysis.md`・`ndf-project-analysis-record.md`
- **変更内容:** 詳細設計の「手順書・確定仕様・このリポジトリの設定」。手順書に書くコマンドは、書く前に打って確かめる
- **満たす受け入れ条件:** 13
- **進め方:** 文書なのでテスト駆動を適用しない。`.md` の文言を照合するテストは書かない。`ndf check`・`python3 scripts/check-skill-frontmatter.py`・`bash scripts/check-lint.sh` を通す

## レビューの単位

行数は追加と削除の和で、生成物（`skills/development-workflow/schemas/project.schema.json`・`docs/contracts/` の下）を数えない: `git diff --shortstat <ベース>...HEAD -- . ':(exclude)plugins/ndf/skills/development-workflow/schemas' ':(exclude)plugins/ndf/docs/contracts'`

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 測定を絞る | Task 1・2 | 5〜7 | 約 500 | |
| 設定の形の移行 | Task 3 | 2〜4・8〜12・14 | 約 600 | |
| 手順書と確定仕様 | Task 4 | 13 | 約 100 | |

- 400 行を超える単位がある。「設定の形の移行」は、スキーマの削除と `.ndf/project.json`・テストを分けると `ndf check` が通らないためである

## リリースへの引き継ぎ

NDF を更新した利用者が初めて設定を書き出すと、確認なしに 6 項目が取り除かれる。手順書の注意はそれより前に読まれるとは限らないので、変更履歴で知らせる。

変更履歴のバージョンのセクションは、リリースの工程が各 PR の本文の `## 利用者向けの変化` と `## 移行の手順` から作る（`release` の Skill）。次の担当で引き継ぐ。

| 担当する工程 | すること |
| --- | --- |
| 実装の PR を作る工程（Task 3 を含む PR） | 下の内容を、PR の本文の 2 つの見出しに書く |
| リリースの工程（変更履歴の本文を組み直すとき） | 下の内容が変更履歴のバージョンのセクションに残っていることを確かめ、載せたコマンドを打って確かめる |

知らせる内容:

- NDF を更新した後、初めて設定を書き出すと、`.ndf/project.json` の `languages`・`services`・`issues`・`checks`・`ndf_policies`・`instructions` の 6 項目が、人が直した値も含めて削除される。開発の作業の開始時の自動の書き出しと、手で打つ `ndf project write` のどちらでも起きる（`--dry-run` を付けた場合は何も削除しない）。その場で確認は求められない
- 残したい値がある人は、**更新後の最初の書き出しより前に**（NDF の更新の前でもよい）、メインディレクトリで `git diff --cached -- .ndf/project.json` と `git diff -- .ndf/project.json` の両方を確かめて控える。コミットしていない値は、削除された後には戻せない
- 設定を git で追跡していないリポジトリでは、削除される値はどれも戻せないので、最初の書き出しより前にファイルをコピーする
- 控えやコピーは、リポジトリの外の、本人だけが読める場所に置く。削除される項目には認証情報が入っていることがあり、リポジトリの中に置くとコミットされて他の人に読まれるおそれがある
- 古い NDF が書き出すと 6 項目が書き戻るので、チームで NDF のバージョンをそろえる

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 主な変更は削除で、足す処理は `write.py` の数か所である |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `measure_repo.py` の削除の後に残る構造 | 削除で関数の分け方が合わなくなる可能性がある |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 単位の途中で、旧版の NDF の解析がメインディレクトリへ 6 項目を書き戻す | 設計の「このリポジトリだけの事情」の扱いに従う。実装の worktree の `.ndf/project.json` はメインディレクトリからコピーしない |
| `write.py` を触るほかのスプリントが `main` へ先に入る | 各単位の始めに `main` を取り込み、#4580 の `_measured_branches`・`_record_branches` を残す |
| 利用者が 6 項目に手で加えてコミットしていない編集を失う | 設計の決定 1 の扱いである。手順書とリリースの変更履歴に、打つ前の確かめ方を書く |

## 切り戻し手順

- マージの前は、単位ごとのコミットを戻す
- リリースの後は、直しを PATCH で出す。利用者の設定から消した 6 項目は、コミット済みの値なら git の履歴から戻せる。コミットしていなかった値は戻せない。読み手が無いので、戻さなくても動きは変わらない

## 完了の定義

- [ ] 受け入れ条件 1〜14 をすべて満たし、条件ごとに検証手段と結果が対応している（条件 1 は要求仕様の表で満たしている）
- [ ] 実装の PR の本文に、「リリースへの引き継ぎ」の知らせる内容が書かれている
- [ ] PR の CI が通る（`pytest`・`ndf check`・`check-skill-frontmatter.py`・`check-lint.sh`）
