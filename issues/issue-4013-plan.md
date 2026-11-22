# #4013: cross-review: `fixer_sessions: true` のリポジトリで修正担当が前の会話を続ける

## 関連リンク

- 要求仕様: [issue-4013-requirements.md](./issue-4013-requirements.md)（課題 https://github.com/devbasex/ai-plugins/issues/4013 ）。受け入れ条件は番号で指す
- 設計書: [issue-4013-design.md](./issue-4013-design.md)・詳細設計書 [issue-4013-design-detail.md](./issue-4013-design-detail.md)
- モード: `standard`（conductor の判定。修正担当の起動の引数と `.ndf/review.json` の契約を変えるため）

## 修正対象

詳細設計書の「地図」から引き写す。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/adapters/claude.py` | `Session`・`NO_CONVERSATION` と `session_args`・`result_line`・`context_tokens`・`context_window`・`conversation_missing`・`compacted` を新設し、`minimal_args`・`_full_args`・`claude_launch` が会話の指定を受ける |
| `plugins/ndf/src/ndf/domains/crossreview/resume_decide.py`（新設） | `Previous`・`Choice`・理由の定数と `choose`・`grown` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | `_Run` に `fixer_last`・`fixer_growth`、`ReviewSettings` に `fixer_sessions`。`read_settings`・`_fixer_launch`・`_fix` が会話の指定を決め、見つからなければ 1 度だけ新しい会話で起動し直す |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | `run` と `review-scope` でレビューする worktree を `read_settings` へ渡す |
| `plugins/ndf/src/ndf/domains/crossreview/criteria_decide.py` | `fixer_sessions_of` を新設 |
| `plugins/ndf/contracts/limits.json` | `cross-review.fixer.resume-margin`（`count` 50000） |
| `plugins/ndf/contracts/schemas/ndf-settings/review.schema.json` | `fixer_sessions`（version 1 だけ）と `examples` |
| `plugins/ndf/contracts/domains/ndf-files.json` | 行 `review` の `keys` に `fixer_sessions` |
| `plugins/ndf/docs/contracts/ndf-settings.md` | `.ndf/review.json` の節に `fixer_sessions` |
| `.ndf/review.json` | `"fixer_sessions": true` |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | 会話の続け方・続けない条件・起動し直し |
| `plugins/ndf/tests/adapters/test_claude_args.py`・`test_claude_reply.py` | 会話の指定の引数・結果の行の読み取り |
| `plugins/ndf/tests/checks/test_spec_docs.py` | スキーマの `fixer_sessions` の version の制限 |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_criteria.py`・`test_crossreview_resume.py`（新設）・`test_crossreview_run.py` | `fixer_sessions_of`・`choose`・駆動の 2 ラウンドの引数 |

## タスク分解

変更の種類は機能追加なので、最初のタスクは今の振る舞いを縛る退行テスト（`fixer_sessions` の無いリポジトリで修正担当がどのラウンドも `--no-session-persistence` で起動される・相手役の引数が変わらない）から始める。

### Task 1: 退行テスト（受け入れ条件 7・5・4）

- **対象ファイル:** `plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** `fixer_sessions` の無い・偽の宣言で 2 ラウンドの修正担当が今の引数で起動されること、相手役の引数が変わらないことを縛る
- **満たす受け入れ条件:** 4・5・7
- **進め方:** 今の実装で通るテストを先に置く

### Task 2: 会話を保存するかの宣言

- **対象ファイル:** `criteria_decide.py`、`review.schema.json`、`ndf-files.json`、`ndf-settings.md`、`test_crossreview_criteria.py`、`test_spec_docs.py`
- **変更内容:** `fixer_sessions_of` とスキーマ・表の行
- **満たす受け入れ条件:** 1・7
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 3: claude の起動の引数と結果の行の読み取り

- **対象ファイル:** `claude.py`、`test_claude_args.py`、`test_claude_reply.py`
- **変更内容:** 詳細設計の「claude の起動の引数」「結果の行の読み取り」のとおり
- **満たす受け入れ条件:** 1・3
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 4: 続けるかの判断

- **対象ファイル:** `resume_decide.py`（新設）、`limits.json`、`test_crossreview_resume.py`（新設）
- **変更内容:** `choose`・`grown` と余白の上限値
- **満たす受け入れ条件:** 2
- **進め方:** 条件ごとのテスト → 最小実装 → 整理

### Task 5: 修正担当の起動と起動し直し

- **対象ファイル:** `drive.py`、`cli.py`、`test_crossreview_run.py`
- **変更内容:** 詳細設計の「修正担当の起動」のとおり
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 駆動の 2 ラウンドの引数・続けない条件・起動し直しのテスト → 最小実装 → 整理

### Task 6: 手順書と ai-plugins の宣言

- **対象ファイル:** `02-fix-and-rotation.md`、`.ndf/review.json`
- **変更内容:** 会話の続け方の説明と `"fixer_sessions": true`
- **満たす受け入れ条件:** 1（宣言）
- **進め方:** テスト駆動を適用しない（`.md` の文言を照合するテストは書かない）

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u4013（#4013） | Task 1〜6 | 4013:1・4013:2・4013:3・4013:4・4013:5・4013:7 | 約 800 | （実装の後に書き足す） |

受け入れ条件 6（入れた後の実測）はリリースの後に確かめ、単位の受け入れ条件に含めない。**この単位は #4014 の単位をスプリントブランチへマージした後、かつレーン lracct が終わった後に始める**（`plugins/ndf/contracts/domains/ndf-files.json` を lracct も触るため。利用者が 2026-11-22 の設計承認で付けた条件）。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `drive.py` は 901 行だが触るのは修正担当の起動の 3 関数と 2 つのデータクラス。`claude.py` は 396 行で引数を組む関数の近くだけを触る。6 タスクのうち `drive.py` を触るのは 1 つ |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `drive.py`・`criteria_decide.py`・`test_crossreview_run.py` | #4014 の単位も同じファイルを触る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| lracct と `ndf-files.json` で衝突する | lracct の終わりを待ち、その後のスプリントブランチ（main を取り込んだもの）から切る |

## 完了の定義

- [ ] 受け入れ条件 1〜5・7 をすべて満たし、条件ごとに検証手段（テスト）と結果が対応している
- [ ] 単位の PR の CI が通り、cross-review が収束している
