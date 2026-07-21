# Issue 33: cross-review 再開時の未解決 thread 考慮

## 関連リンク

- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/33
- 関連 Skill: `plugins/ndf-shared/skills/cross-review/SKILL.md`

## 概要

`ndf:cross-review` の再開時に、前回中断ラウンドで残った未解決 review thread が `judge` の収束判定に考慮されない問題を修正する。

最低限の対応として、再開時の既存 open thread と `comments_count` の意味をドキュメントに明記する。可能であれば `state.py` に open thread 検査を追加し、未解決 thread が残っている状態で即 approved に進まないガードを入れる。

## 問題・背景

再開ラウンドで codex / gemini が approve 相当を返すと、`state.py judge` は当該ラウンドの `result.json.intent` だけで `final=approved` にできる。前回中断前に投稿された未解決 thread はこの判定に含まれないため、Step 7.5 の最終スイープだけが取りこぼし防止になっている。

また `result.json.comments_count` は「そのラウンドで新規投稿されたコメント数」であり、PR 上の実 open thread 総数ではない。fix / sweep の実行者がこの件数を実 open thread 数と誤解すると、再開・複数ラウンド累積時に取りこぼしが起きる。

## 修正対象

- `plugins/ndf-shared/skills/cross-review/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`
- `plugins/ndf-shared/skills/cross-review/docs/02-fix-and-rotation.md`
- `plugins/ndf-shared/skills/cross-review/scripts/state.py`
- `plugins/ndf-shared/skills/cross-review/tests/`
- `plugins/ndf-claude/skills/cross-review/`
- `plugins/ndf-codex/skills/cross-review/`
- `plugins/ndf-kiro/skills/cross-review/`

runtime 別配布物は `plugins/ndf-shared` を正とし、`scripts/build-runtime-plugins.sh` で同期する。

## タスク分解

### Task 1: 再開時 open thread の仕様を文書化

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`
- **変更内容:** `state.py judge` は当該ラウンドの intent を見ること、再開前から存在する open thread は Step 7.5 sweep が回収責任を持つことを明記する。

### Task 2: `comments_count` の意味を明記

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`, `plugins/ndf-shared/skills/cross-review/docs/02-fix-and-rotation.md`
- **変更内容:** `comments_count` は投稿数であり、実 open thread 数ではないことを fix / sweep のプロンプト周辺に明記する。open thread は GraphQL の `reviewThreads` で洗い直す方針に統一する。

### Task 3: 再開時 open thread ガードを検討・実装

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/scripts/state.py`
- **変更内容:** `init` 再開時または `judge` 前後で、PR 上の unresolved review thread 数を取得する helper を追加する。未解決 thread がある状態で即 approved になる場合は、次のどちらかを実装方針として選ぶ。
  - `judge` は `open_thread_count > 0` の場合に continue を返し、fix / sweep 経由へ進める。
  - `state.json` に `resumed_open_threads` を記録し、report / sweep に必須入力として渡す。

実装範囲が過大になる場合は、Task 1 / Task 2 の docs 強化を先行し、script ガードは別 PR に分ける。

### Task 4: テスト追加

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/tests/`
- **変更内容:** 再開 state と open thread count の扱いを unit test で固定する。GitHub API 呼び出し部分は subprocess / helper を mock し、ネットワーク不要で検証する。

### Task 5: runtime 配布物同期

- **対象ファイル:** `plugins/ndf-claude/`, `plugins/ndf-codex/`, `plugins/ndf-kiro/`
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行し、shared の変更を runtime 別配布物へ反映する。

## PR 分割計画

単一 PR で進める。主対象は cross-review skill 内の docs / state helper / tests であり、依存関係のある複数機能に分割するほどの変更ではない。

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `fix/issue-33-cross-review-resume-open-threads` | 再開時 open thread の仕様明記と必要な script guard / tests 追加 | なし | - |

release branch: なし
base branch: `main`

## 影響範囲

- `ndf:cross-review` の再開フロー
- `state.py judge` の収束判定
- Step 7.5 最終スイープの必須性に関する利用者理解
- runtime 別 NDF plugin 配布物

## テスト計画

- [ ] `python3 plugins/ndf-shared/skills/cross-review/tests/...` または該当 pytest を実行する
- [ ] `bash scripts/build-runtime-plugins.sh --check`
- [ ] `bash scripts/validate-runtime-plugins.sh`
- [ ] 再開 state の unit test で、open thread がある場合の期待挙動を確認する
- [ ] Markdown link check が通ることを確認する
