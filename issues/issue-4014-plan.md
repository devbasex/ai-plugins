# #4014: cross-review: 起きることを書けない重い指摘を軽い指摘として残す

## 関連リンク

- 要求仕様: [issue-4014-requirements.md](./issue-4014-requirements.md)（課題 https://github.com/devbasex/ai-plugins/issues/4014 ）。受け入れ条件は番号で指す
- 設計書: [issue-4014-design.md](./issue-4014-design.md)・詳細設計書 [issue-4014-design-detail.md](./issue-4014-design-detail.md)
- モード: `standard`（conductor の判定。cross-review の駆動の振る舞いと相手役への指示の契約を変えるため）

## 修正対象

詳細設計書の「地図」から引き写す。パスは `plugins/ndf/` からの相対。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/domains/crossreview/criteria_decide.py` | `effective_severity`・`downgraded` を新設し、`reviewer_block` の重要度の行を `review-criteria.json` の `impact.reviewer` から組む |
| `src/ndf/domains/crossreview/journal.py` | `_raised` を新設し、`FindingJournal.enter`・`_raised_again` が下げた後の重要度と `impact`・`raised_severity` を書く |
| `src/ndf/domains/crossreview/drive.py` | `_review_prompt` の JSON の形に `impact`、`_fix_prompt` の各指摘に起きることの 1 文 |
| `contracts/data/review-criteria.json` | `impact`（`reviewer`・`fixer_label`）と `summary` |
| `skills/cross-review/docs/01-state-and-review.md` | 1 ラウンドの JSON の形・指摘の基準・判定の表・状態ファイルの `findings[]` |
| `skills/cross-review/docs/02-fix-and-rotation.md` | 修正担当へ渡す指示の中身に起きることの 1 文 |
| `tests/domains/crossreview/test_crossreview_criteria.py` | 取り込む重要度の規則と指示の行 |
| `tests/domains/crossreview/test_crossreview_journal.py` | 取り込み・出し直し・引き継ぎの重要度 |
| `tests/domains/crossreview/test_crossreview_run.py` | 偽の相手役が文字列の指摘に 1 文を添える。下げた指摘で修正担当を起動しない通し・1 文が修正担当へ載る通し |
| `tests/domains/crossreview/test_crossreview_report.py` | 下げた行が記録のコメントの表に「軽い」で出る |

## タスク分解

変更の種類は機能追加なので、最初のタスクは今の振る舞いを縛る退行テストから始める（既存の `test_crossreview_run.py` の通しのテストが重要度の無い指摘で修正担当の起動を見ているため、偽の相手役に 1 文を添えて今の通しの振る舞いを保つ）。

### Task 1: 取り込む重要度の規則（criteria_decide）

- **対象ファイル:** `src/ndf/domains/crossreview/criteria_decide.py`、`tests/domains/crossreview/test_crossreview_criteria.py`
- **変更内容:** `effective_severity(severity, impact)`・`downgraded(row)` を新設する。下げる先と上げる先は `severities.json` の尺度から引く（詳細設計の「処理の手順」の表）
- **満たす受け入れ条件:** 1・3・4
- **進め方:** 10 通りの組み合わせと境界値・異常のテスト → 最小実装 → 整理

### Task 2: ジャーナルへの取り込み（journal）

- **対象ファイル:** `src/ndf/domains/crossreview/journal.py`、`tests/domains/crossreview/test_crossreview_journal.py`
- **変更内容:** `_raised` を新設し、`enter`・`_raised_again` が下げた後の値・`impact`・`raised_severity` を書く。`carry` は変えない
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 取り込み・同じラウンドの出し直し（両方の順）・直した指摘の再発・引き継ぎのテスト → 最小実装 → 整理

### Task 3: 相手役と修正担当への指示（drive・review-criteria.json）

- **対象ファイル:** `src/ndf/domains/crossreview/criteria_decide.py`（`reviewer_block`）、`src/ndf/domains/crossreview/drive.py`、`contracts/data/review-criteria.json`、`tests/domains/crossreview/test_crossreview_criteria.py`、`tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** `reviewer_block` の最後の行を `impact.reviewer` から組み、「基準に当たる指摘は」の文を外す。`_review_prompt` の形に `impact`、`_fix_prompt` に「— 起きること: <1 文>」
- **満たす受け入れ条件:** 2・5
- **進め方:** 指示を組む関数の出力のテスト（データの差し替えで出力が変わる）→ 最小実装 → 整理

### Task 4: 実行の通しと記録のコメントの表

- **対象ファイル:** `tests/domains/crossreview/test_crossreview_run.py`、`tests/domains/crossreview/test_crossreview_report.py`、`tests/domains/crossreview/test_crossreview_criteria.py`
- **変更内容:** 偽の相手役（`Fake`）の文字列の指摘に 1 文を添える。下げた指摘で修正担当を起動せず収束する通し・1 文が修正担当への指示に載る通し・下げた行が表に `minor`・「軽い（直さずに残した）」で出てスレッドを作らないテストを足す
- **満たす受け入れ条件:** 1・2・4
- **進め方:** 通しのテスト → Task 1〜3 の実装で通ることを確かめる（足りなければ最小の直し）

### Task 5: 手順書

- **対象ファイル:** `skills/cross-review/docs/01-state-and-review.md`、`skills/cross-review/docs/02-fix-and-rotation.md`
- **変更内容:** 詳細設計の「手順書と既存のテスト」の表のとおり
- **満たす受け入れ条件:** 1〜5（説明）
- **進め方:** テスト駆動を適用しない（`.md` の文言を照合するテストは書かない。ドキュメントは cross-review で見る）

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u4014（#4014） | Task 1・2・3・4・5 | 4014:1・4014:2・4014:3・4014:4・4014:5 | 約 400 | 364（追加 330・削除 34。`git diff --shortstat sprint/p12-lcrfast-r1...HEAD`、この 1 行の直しを含む） |

受け入れ条件 6（入れた後の実測）はリリースの後に記録を集計して確かめ、単位の受け入れ条件に含めない。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る実装のファイルは `criteria_decide.py` 104 行・`journal.py` 164 行・`drive.py` 901 行で、`drive.py` で触るのは指示を組む 2 関数だけ。5 タスクのうち `drive.py` を触るのは 1 つ。既存のテストは 4 ファイルで 81 件 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `drive.py`・`criteria_decide.py`・`test_crossreview_run.py`・`test_crossreview_criteria.py` | #4013 の単位も同じファイルを触る（`_fix_prompt` の近くの `_fixer_launch`・`criteria_decide.fixer_sessions_of`・偽の相手役） |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 偽の相手役に 1 文を添えないと、重要度の無い指摘が `minor` へ下がり、修正担当の起動を見ている既存の通しのテストが一斉に落ちる | Task 4 の最初に `Fake` へ 1 文を添え、既存の通しの振る舞いを保つ |
| #4013 の単位と `drive.py`・`criteria_decide.py` で衝突する | #4014 の単位を先にスプリントブランチへマージし、#4013 の単位はその後に切る |

## 完了の定義

- [ ] 受け入れ条件 1〜5 をすべて満たし、条件ごとに検証手段（テスト）と結果が対応している
- [ ] 単位の PR の CI（pytest ほか）が通り、cross-review が収束している
