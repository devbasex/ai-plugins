# #4566: CI テストの設定が無いリポジトリでもリファクタリングを通す

## 関連リンク

- 要求仕様: [issue-4566-requirements.md](./issue-4566-requirements.md)（正は <https://github.com/devbasex/ai-plugins/issues/4566> の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4566-design.md](./issue-4566-design.md)、[詳細設計](./issue-4566-design-detail.md)
- モード: `standard`（工程の実行条件とコマンドの引数を変える機能追加）

## 修正対象

パスは `plugins/ndf/` からの相対である。ファイルごとの関数は詳細設計の「地図」が持つ。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/testing/means.py`（新設） | テストを確かめる手段の判定 |
| `src/ndf/domains/crossrefactor/protected_decide.py`・`local_gate.py`（新設） | テストの定義のファイルの判定、手元の最終ゲート |
| `src/ndf/core/converge/worktree.py`・`src/ndf/testing/weight.py` | 取得済みの先頭を受け取る同期、リダイレクト先も含む語の並び |
| `src/ndf/domains/crossrefactor/{cli.py, drive.py, workspace.py, final_gate.py, final_gate_decide.py, prompt_decide.py, store.py}` | 引数、経路の分岐、排他ロック、記録、指示文、状態ファイルのキーの説明 |
| `src/ndf/progress/{command.py, refactor_values.py, invoke.py}`・`src/ndf/core/route/__init__.py` | 条件の語、起動の組み立て、プランの形 |
| `contracts/workflow/{stages/refactor.json, ndf.schema.json}`・`contracts/templates/{cross-refactoring.json, parts/steps.json}` | 工程の条件、`follow` のステップ |
| `contracts/{schemas/ndf-settings/project.schema.json, schemas/io/refactor.args.schema.json, version.json, limits/test-run.test.json, limits/cross-refactoring.gate-log.keep.json}`・`docs/contracts/commands.md`・`skills/development-workflow/schemas/project.schema.json` | 設定のキーとコントラクト |
| `skills/{development-workflow, cross-refactoring}/SKILL.md`・`docs/specifications/` の 3 本 | 手順書と確定仕様 |
| `tests/` の下 | 詳細設計の「地図」のテストの行 |

## タスク分解

着手の前に `main` を取り込む。`contracts/version.json` は、取り込んだ後の最後の版の次を足す。

### Task 1: 今の振る舞いを縛る退行テストを足す

- **対象ファイル:** `tests/progress/test_invoke_refactor.py`、`tests/route/test_templates.py`、`tests/domains/crossrefactor/test_drive.py`
- **変更内容:** CI の経路の起動の引数、組んだプランのステップと遷移、状態ファイルに記録する引数の並びのうち、今のテストが見ていないものを足す。今のコードで通る
- **満たす受け入れ条件:** 3
- **進め方:** テストを足すだけで、コードは変えない

### Task 2: 設定から、テストを確かめる手段とテストの定義のファイルを判定する

- **対象ファイル:** `means.py`、`protected_decide.py`、`weight.py`、`project.schema.json` とそのコピー、`tests/testing/test_means.py`、`tests/domains/crossrefactor/test_protected.py`
- **変更内容:** `means.decide`・`means.total_minutes`・`protected_of`・`narrow`・`inside_work`・`weight.all_words`。設定のキー `test.protected_files`・`test.local_total_minutes`
- **満たす受け入れ条件:** 4・5・6（判定の部分）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。解析の書き出し（`ndf` のプロジェクトの解析）が、手で足した 2 つのキーを消さないこともテストで確かめる

### Task 3: 手元の最終ゲートを作る

- **対象ファイル:** `local_gate.py`、`final_gate.py`、`final_gate_decide.py`、`prompt_decide.py`、`core/converge/worktree.py`、`workspace.py`、`test-run.test.json`、`cross-refactoring.gate-log.keep.json`、`tests/domains/crossrefactor/test_local_gate.py`、`test_prompt_decide.py`
- **変更内容:** 取り込み → テスト全体 → 指定の SHA の push か直し。権限を絞ったログの書き込みと期限での削除（`workspace.py`）、ログと指示文の伏せ字と上限（`local_gate.py`・`prompt_decide.py`）。検証用の作業場所、合格の記録、累計の上限。CI の記録に通ったチェックの名前を足す
- **満たす受け入れ条件:** 3・4
- **進め方:** 詳細設計の「処理の手順」の表の行ごとに、失敗するテスト → 通す実装。git は一時の bare リモートを使い、テストの起動と時計は差し替える

### Task 4: `ndf refactor run --local-test` を提案から最後まで通す

- **対象ファイル:** `cli.py`、`drive.py`、`workspace.py`、`refactor.args.schema.json`、`version.json`、`commands.md`、`test_drive.py`、`test_refactor_cli.py`
- **変更内容:** 引数と手段の確かめ、`--pushed-out`、PR ごとの排他ロック、対象範囲の絞り込み、完了の再利用、結果の要約
- **満たす受け入れ条件:** 4・6
- **進め方:** 担当の代わり（`_drive_world.py`）を使う通しのテストを先に落とす

### Task 5: 工程の条件と起動を手段に合わせる

- **対象ファイル:** `refactor.json`、`ndf.schema.json`、`command.py`、`refactor_values.py`、`invoke.py`、`core/route/__init__.py`、`cross-refactoring.json`、`parts/steps.json`、`tests/progress/` の 4 本、`test_templates.py`
- **変更内容:** 語 `tests_verifiable`、手段ごとの起動、`follow` のステップ、手元の経路のプランの組み替え
- **満たす受け入れ条件:** 1・2・3・4・5・6・7・8・9・10
- **進め方:** 手段の表の 4 行について、条件の値と起動が一致する失敗のテストから始める

### Task 6: 手順書と確定仕様を直す

- **対象ファイル:** `development-workflow` と `cross-refactoring` の `SKILL.md`、`docs/specifications/` の 3 本
- **変更内容:** 詳細設計の「手順書と確定仕様」の表のとおり。`development-workflow` の `SKILL.md` は行を増やさない
- **満たす受け入れ条件:** 11・12
- **進め方:** テスト駆動を適用しない（文書の変更で、文言を照合するテストは書かない）。参照の切れと Skill の形は検査で確かめる

## レビューの単位

1 つの課題で差分が 1,000 行を超える見込みなので、依存の向きで 3 つに分ける。単位 1 は呼び出し元の無い部品、単位 2 は直接打つコマンド、単位 3 はスプリントの自動の経路である。単位 3 をマージするまで、工程の振る舞いは変わらない。

行数は次のコマンドで数える。比較元のブランチかコミットを変数 `base_ref` に入れてから打つ。除くのは、生成して作るファイルとコピーである。

```bash
base_ref=origin/main  # 比較元のブランチかコミット
git diff --shortstat "${base_ref}...HEAD" -- . ':(exclude)issues' ':(exclude)plugins/ndf/docs/contracts/commands.md' ':(exclude)plugins/ndf/contracts/schemas/io' ':(exclude)plugins/ndf/skills/development-workflow/schemas'
```

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1 手段の判定と手元の最終ゲート | Task 1・2・3 | 3 | 約 900 | |
| 2 `--local-test` の通し | Task 4 | 4・6 | 約 400 | |
| 3 工程への組み込みと文書 | Task 5・6 | 1・2・5・7・8・9・10・11・12 | 約 500 | |

単位 1 が 1,000 行を超えそうなら、Task 3 を単位 1 から分ける。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | `drive.py` は 806 行で、触るのは 6 タスク中 1 タスク。手元の経路の処理は新しい `local_gate.py` に置き、`drive.py` に足すのは経路の分岐だけである。`command.py`（984 行）に足すのは条件の語の 1 行だけ。`test_drive.py` は 14 件、`test_templates.py` は 23 件のテストがある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `final_gate.py` と `local_gate.py` の周の回し方と直しの呼び出し、`verify.py` と `local_gate.py` のテストの起動 | 2 つの最終ゲートが同じ形の処理を持つ。起動の上限の扱いが 2 か所になる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `main` が先に進んでいて、`contracts/version.json` が衝突する | 着手の前に取り込み、版の行は最後に足す |
| `drive.py` が 1,000 行の上限に近づく | 手元の経路の判断（絞り込み・再利用の照合）は `local_gate.py` の関数にし、`drive.py` は呼ぶだけにする |
| このリポジトリは CI テストの設定があるので、手元の経路を実機で通せない | 単位のテストで確かめる。実機は、リリースの後に CI テストの設定が無い利用先で確かめる（要求仕様の「リリース後の確認の計画」） |

## 切り戻し手順

- 単位ごとの PR を `git revert` する。状態ファイルの移行は無い。`ci_declared` の値を求める処理は残るので、古いスプリントはそのまま流れる
- 更新の後に作ったスプリントは、語 `tests_verifiable` を持つ定義をコピーしている。戻すと条件を求められずに止まるので、そのスプリントは `ndf progress disable` で工程を外す
- 利用先が `test.protected_files` か `test.local_total_minutes` を書いた後に戻すと、設定の形の検査で未知のキーになる。キーを消してもらう

## 完了の定義

- [ ] 受け入れ条件 1〜3 と 6〜12 を満たし、条件ごとに検証手段と結果が対応している
- [ ] 受け入れ条件 4・5 は、工程の単位で満たし、検証手段と結果が対応している。条件 4 は `ndf refactor run --local-test` の直接の実行と同じ経路のテスト、条件 5 は工程の判定の単位のテストである（設計書の決定 8）
- スプリントが必須のチェックの無いリポジトリで条件 4・5 まで自動で進むことの確認は、#4472（ https://github.com/devbasex/ai-plugins/issues/4472 ）を直した後に行う。この実装計画の完了の条件には含めない
- [ ] PR の CI が通る（テストは手元で打たない。このリポジトリは CI テストの設定がある）
- [ ] `bash scripts/check-lint.sh`・`python3 scripts/check-ndf-contracts.py`・`python3 scripts/check-script-structure.py`・`python3 scripts/check-markdown-links.py --root .` が通る
- [ ] レビューの単位の実際の行数を書き足した
