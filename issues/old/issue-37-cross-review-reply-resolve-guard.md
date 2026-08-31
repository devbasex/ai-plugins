# Issue 37: cross-review reply / resolve 漏れガード

## 関連リンク

- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/37
- 関連 Skill: `plugins/ndf-shared/skills/cross-review/SKILL.md`
- 関連 Skill: `plugins/ndf-shared/skills/resolve-pr-comments/SKILL.md`
- 関連 Skill: `plugins/ndf-shared/skills/fix/SKILL.md`

## 概要

`ndf:cross-review` の修正フェーズで、修正済み thread への reply 投稿、`resolveReviewThread`、PR レベル Summary コメント投稿が抜けたまま次 round や approve に進むことを防ぐ。

あわせて `resolve-pr-comments` の返信 API 例を実行可能な形に修正し、reply 失敗が resolve 成功で隠れないようにする。

## 問題・背景

cross-review の Step 5 は `/ndf:fix` サブエージェントが reply + resolve + Summary コメントまで実行する契約になっている。しかしメインセッションが手動で修正・push して次 round に進めると、reply / resolve がスキップされても `state.py start-round` / `judge` / `merge-fix` 側で検知できず、未解決 inline thread が残る可能性がある。

また `resolve-pr-comments/SKILL.md` の REST 返信例が `-f in_reply_to=<comment_id>` になっており、GitHub API では typed field の `-F in_reply_to=<comment_id>` を使う必要がある。reply 失敗後に GraphQL resolve だけ成功すると、thread は resolved でも「どの修正で対応したか」の inline reply が残らない。

## 修正対象

- `plugins/ndf-shared/skills/cross-review/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`
- `plugins/ndf-shared/skills/cross-review/docs/02-fix-and-rotation.md`
- `plugins/ndf-shared/skills/cross-review/scripts/state.py`
- `plugins/ndf-shared/skills/resolve-pr-comments/SKILL.md`
- `plugins/ndf-shared/skills/fix/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/tests/`
- `plugins/ndf-claude/`, `plugins/ndf-codex/`, `plugins/ndf-kiro/`

runtime 別配布物は `plugins/ndf-shared` を正とし、`scripts/build-runtime-plugins.sh` で同期する。

## タスク分解

### Task 1: resolve-pr-comments の reply API 例を修正

- **対象ファイル:** `plugins/ndf-shared/skills/resolve-pr-comments/SKILL.md`
- **変更内容:** `gh api repos/:owner/:repo/pulls/$PR_NUMBER/comments -f body=... -f in_reply_to=...` を、`in_reply_to` が数値として送られる `-F in_reply_to=<comment_id>` へ修正する。必要なら GraphQL reply mutation の代替も併記する。

### Task 2: reply + resolve + verify の小 script 方針を追加

- **対象ファイル:** `plugins/ndf-shared/skills/resolve-pr-comments/SKILL.md`, 必要に応じて `plugins/ndf-shared/skills/resolve-pr-comments/scripts/`
- **変更内容:** 対応済み thread に reply を投稿し、GraphQL `resolveReviewThread` を実行し、最後に unresolved count を確認する流れを標準化する。script を追加する場合は、reply 失敗時に resolve へ進まない `set -e` 相当の挙動にする。

### Task 3: cross-review の次 round ガードを設計

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/scripts/state.py`
- **変更内容:** 前 round に `REQUEST_CHANGES` があり、対応する fix 結果または sweep 結果が無い状態で `start-round` / `judge` / `merge-fix` が進まないようにする。最低限、`merge-fix` で `resolved_threads`、`summary_comment_url`、fix result file の存在を検証する。

### Task 4: final report 前の unresolved sweep 検証を必須化

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/SKILL.md`, `plugins/ndf-shared/skills/cross-review/docs/02-fix-and-rotation.md`
- **変更内容:** Step 7.5 の `sweep-pr<PR>-result.json` に `remaining_open=0` が必要であることを、report 前の必須検証として明記する。`remaining_open > 0` の場合は approved として完了報告しない。

### Task 5: fix の戻り値契約を強化

- **対象ファイル:** `plugins/ndf-shared/skills/fix/SKILL.md`, `plugins/ndf-shared/skills/cross-review/docs/02-fix-and-rotation.md`
- **変更内容:** 修正済み thread は reply URL または comment id と resolve 結果を戻り値に含める契約にする。reply なし resolve を禁止し、deferred / rejected の扱いと区別する。

### Task 6: テスト追加

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/tests/`
- **変更内容:** fix result が無い、`resolved_threads` が空、`summary_comment_url` が無い、unresolved count が残っている、などのケースで state guard が fail する unit test を追加する。

### Task 7: runtime 配布物同期

- **対象ファイル:** `plugins/ndf-claude/`, `plugins/ndf-codex/`, `plugins/ndf-kiro/`
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行し、shared の変更を runtime 別配布物へ反映する。

## PR 分割計画

原則は単一 PR で進める。ただし reply / resolve helper script を新設して state guard まで実装すると差分が大きくなるため、実装時に 2 PR へ分割してもよい。

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `fix/issue-37-resolve-pr-comments-reply-api` | reply API 例修正、resolve-pr-comments / fix 契約整理 | なし | ○ |
| 2 | `fix/issue-37-cross-review-resolve-guard` | cross-review state guard、sweep 検証、tests 追加 | PR1 | × |

release branch: 分割する場合のみ `release/issue-37-cross-review-reply-resolve-guard`
base branch: `main`

## 影響範囲

- `ndf:cross-review` の round 進行条件
- `/ndf:fix` の戻り値契約
- `/ndf:resolve-pr-comments` の API 手順
- PR 上の review thread 解決履歴
- runtime 別 NDF plugin 配布物

## テスト計画

- [ ] `plugins/ndf-shared/skills/cross-review/tests/` の pytest を実行する
- [ ] reply API 例が `-F in_reply_to=<comment_id>` になっていることを確認する
- [ ] state guard の unit test で、reply / resolve / summary / unresolved count の不足を検出できることを確認する
- [ ] `bash scripts/build-runtime-plugins.sh --check`
- [ ] `bash scripts/validate-runtime-plugins.sh`
- [ ] Markdown link check が通ることを確認する
