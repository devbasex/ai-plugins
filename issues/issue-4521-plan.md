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
| `src/ndf/domains/project/cli.py` | 測定の組み立て、`--out` の権限、控えと保管の書き込み |
| `src/ndf/domains/project/write.py` | 移行と控え、`test` の再解析、回答の扱い |
| `src/ndf/domains/project/answer_decide.py`・`held.py`（新設） | 回答の判定、控えと保管 |
| `src/ndf/domains/project/analysis_record.py`・`check.py`・`items.py`・`secret.py` | 旧い形のキー、解析器のバージョン、保管の理由、共通の判定 |
| `contracts/schemas/ndf-settings/project.schema.json`・`skills/development-workflow/schemas/project.schema.json` | 6 項目を消す |
| `contracts/schemas/internal/project-held-answers.schema.json`（新設）・`contracts/data/internal-names.json` | 保管の形 |
| `contracts/domains/ndf-files.json`・`languages.json` | ファイルの表と要約 |
| `contracts/schemas/io/project.args.schema.json`・`project.result.schema.json`・`docs/contracts/ndf-settings.md`・`docs/contracts/commands.md` | 説明と生成物 |
| `skills/development-workflow/references/project-analysis.md`・`docs/specifications/ndf-project-analysis-record.md` | 手順と仕様 |
| `.ndf/project.json` | 6 項目と指紋を消す |
| `tests/domains/project/` の `test_project_measure.py`・`test_project_cli.py`・`test_analysis_record.py`・`test_measure_project_branch_state.py`・`test_write_plan.py`・`test_answer_decide.py`（新設）・`test_project_held.py`（新設） | テスト |

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
- **対象ファイル:** 2 つの `project.schema.json`・`ndf-files.json`（行 `project`）・`languages.json`・`ndf-settings.md`・`analysis_record.py`・`check.py`（`ANALYZER`）・`write.py`（`p_number`・`_build_record`・`analyzer` の確かめ。確かめは Task 6 で `measure_problem` に移す）・`.ndf/project.json`・`test_analysis_record.py`・`test_project_cli.py`・`test_write_plan.py`
- **変更内容:** スキーマと旧い形のキー、記録の `analyzer` を測定の値にする。今のテストが例に使う `checks`・`languages` は残る項目へ置き換える。`.ndf/project.json` は同じ変更で直す（直さないと `ndf check` が落ちる）
- **満たす受け入れ条件:** 2・3・4・8・9・11・12・14
- **進め方:** 6 キーが残る設定の `check` が終了コード 3 になるテストを先に書く

### Task 4: 移行の控え、差分の伏せ字、`--out` の権限
- **対象ファイル:** `write.py`（`removed_by_hand`・`_project_file`）・`held.py`（`replace_owner_only`）・`cli.py`（`_guard_store`・`_commit`・`_measure`）・`ndf-files.json`（行 `project-removed`）・`test_project_held.py`・`test_project_cli.py`
- **変更内容:** 詳細設計の「旧い形のキーと控え」
- **満たす受け入れ条件:** 10・16
- **進め方:** 失敗するテスト → 最小の実装 → 整理

### Task 5: `test` の再解析で回答を失わない
- **対象ファイル:** `write.py`（`merge_project`・`probe_inside`・`suites_note`・`_declare_ci_tests`）・`test_project_cli.py`・`test_write_plan.py`
- **変更内容:** 詳細設計の「書き出し（test の再解析）」
- **満たす受け入れ条件:** 無し（決定 6。確かめ方は設計の「テスト設計」の決定 6 の行）
- **進め方:** 範囲外のパスの表を先にテストにする

### Task 6: 回答の照合と秘密
- **対象ファイル:** `answer_decide.py`・`secret.py`・`items.py`・`held.py`（`context_of`・`place_of`）・`check.py`（`repo_dirs`）・`write.py`・`cli.py`（`measure_project`・`_ask_context`・`_inputs`・`_write`）・`test_answer_decide.py`・`test_project_cli.py`・`test_write_plan.py`
- **変更内容:** 詳細設計の「回答の照合と秘密」。この時点では保管はせず、`answer_held` の案内も出さない（Task 7 で保管と同時に足す）
- **満たす受け入れ条件:** 14・15（照合・秘密・場所の部分）
- **進め方:** 順序表の 12 行を先にテストにする。測定の結果を作るテストの補助に `context`・`branch_state`・`current` を足す

### Task 7: 保管・再開・破棄
- **対象ファイル:** `held.py`・`project-held-answers.schema.json`・`internal-names.json`・`ndf-files.json`（行 `project-held`）・`write.py`（`plan`・`cleanup_plan`）・`cli.py`（`_ask_context`・`_commit`）・`check.py`（`_stale_reasons`・`_held_reasons`・`judge`）・`project.result.schema.json`・`test_project_held.py`
- **変更内容:** 詳細設計の「保管と再開」
- **満たす受け入れ条件:** 15
- **進め方:** 場所ごとの見え方と消し方のテストを先に書く。時刻は引数で渡し、実時間を待たない

### Task 8: 手順書・引数の説明・確定仕様
- **対象ファイル:** `project-analysis.md`・`ndf-project-analysis-record.md`・`cli.py`（`--answers` の説明）・`project.args.schema.json`・`commands.md`
- **変更内容:** 詳細設計の「手順書・確定仕様・このリポジトリの設定」。手順書に書くコマンドは、書く前に打って確かめる
- **満たす受け入れ条件:** 13
- **進め方:** 文書なのでテスト駆動を適用しない。`.md` の文言を照合するテストは書かない。`ndf check`・`python3 scripts/check-skill-frontmatter.py`・`bash scripts/check-lint.sh` を通す

## レビューの単位

行数は追加と削除の和で、生成物（`skills/development-workflow/schemas/project.schema.json`・`docs/contracts/` の下・`project.args.schema.json`）を数えない: `git diff --shortstat <ベース>...HEAD -- . ':(exclude)plugins/ndf/skills/development-workflow/schemas' ':(exclude)plugins/ndf/docs/contracts' ':(exclude)plugins/ndf/contracts/schemas/io/project.args.schema.json'`

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 測定を絞る | Task 1・2 | 5〜7 | 約 500 | |
| 設定の形の移行と控え | Task 3・4 | 2〜4・8〜12・14・16 | 約 900 | |
| test の再解析 | Task 5 | 無し | 約 300 | |
| 回答の照合と秘密 | Task 6 | 14・15 | 約 750 | |
| 保管と再開 | Task 7 | 15 | 約 900 | |
| 手順書と確定仕様 | Task 8 | 13 | 約 200 | |

- 400 行を超える単位がある。「設定の形の移行と控え」は、スキーマの削除（約 290 行）と `.ndf/project.json`・テストを分けると `ndf check` が通らないためである。控えは、人が書いた値を消す変更と同じ単位に置く（分けると、控えの無いまま値を消すコミットができる）
- 「回答の照合と秘密」と「保管と再開」は、順序表と保管の 2 つの依存の向きで分けた。前の単位だけでも、適用しなかった回答は今の値を残して `mismatch` を出すので動く
- 見込みが 1,000 行を超えそうな場合は、「保管と再開」を、保管と再開（`check`・`measure`）と、消し方（`resolves`・`discard`・`unresume`・場所の片付け）に分ける

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `write.py` は 405 行、`cli.py` は 334 行で、`write.py` に触るのは 8 タスク中 5 タスク。`tests/domains/project/` の関係する 6 ファイルがこの 2 つを覆う。足す判定と保管は新しいモジュール（`answer_decide.py`・`held.py`）に置くので、今の構造の上に積み増さない |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `write.py` の行の組み立てと表示（`veil_rows`・`row_line`・`merge_item`）、`cli.py` の git の確かめ（`_guard_record`・`_guard_store`） | 複数の単位が同じファイルに同じ形の処理を足すため |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 単位の途中で、旧版の NDF の解析がメインディレクトリへ 6 項目を書き戻す | 設計の「このリポジトリだけの事情」の扱いに従う。実装の worktree の `.ndf/project.json` はメインディレクトリからコピーしない |
| `write.py` を触るほかのスプリントが `main` へ先に入る | 各単位の始めに `main` を取り込み、#4580 の `_measured_branches`・`_record_branches` を残す |
| 新しい関数名が `ndf check` の `copy-dup`、固定の文字列の集まりが `const-fact` に当たる | 詳細設計の名前と置き方に従い、各タスクの終わりに `ndf check` を打つ |

## 切り戻し手順

- マージの前は、単位ごとのコミットを戻す
- リリースの後は、直しを PATCH で出す。利用者の設定から消した 6 項目は、git の履歴か `.ndf/analysis/removed/` の控えから戻せる。読み手が無いので、戻さなくても動きは変わらない

## 完了の定義

- [ ] 受け入れ条件 1〜16 をすべて満たし、条件ごとに検証手段と結果が対応している（条件 1 は要求仕様の表で満たしている）
- [ ] PR の CI が通る（`pytest`・`ndf check`・`check-skill-frontmatter.py`・`check-lint.sh`）
