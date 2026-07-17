# Issue 35: issue-plan-strategy 個別 PR cross-review 必須化

## 関連リンク

- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/35
- 関連 Skill: `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- 関連 Skill: `plugins/ndf-shared/skills/cross-review/SKILL.md`

## 概要

`ndf:issue-plan-strategy` の multi-PR ワークフローで、個別 PR のレビューが軽量レビューだけで済まされ、重大バグが release 統合後まで残る運用を防ぐ。

Step 6 を「個別 PR は原則 `/ndf:cross-review` 必須」と読める内容に変更し、release PR Ready 前の前提条件とアンチパターンを明文化する。

## 問題・背景

現行の Step 6 は `/ndf:review-branch`、`/ndf:review`、`/ndf:cross-review` を選択肢として並べているため、個別 PR を `code-reviewer` や単発レビューだけで release ブランチへ merge できるように読める。

その運用では、release PR 側の cross-review が個別 PR 範囲の重大バグをまとめて検出する形になり、ワークフロー自身が禁止している「release PR で個別 PR 範囲の指摘を解決する」状態に近づく。

## 修正対象

- `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/SKILL.md`
- `plugins/ndf-claude/skills/issue-plan-strategy/SKILL.md`
- `plugins/ndf-codex/skills/issue-plan-strategy/SKILL.md`
- `plugins/ndf-kiro/skills/issue-plan-strategy/SKILL.md`
- 必要に応じて `docs/ndf-plugin-reference.md`

runtime 別配布物は `plugins/ndf-shared` を正とし、`scripts/build-runtime-plugins.sh` で同期する。

## タスク分解

### Task 1: Step 6 のレビュー方針を強化

- **対象ファイル:** `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- **変更内容:** 個別 PR は原則 `/ndf:cross-review <PR番号>` を実行し、codex + gemini の収束を確認してから release ブランチへ merge する、と明記する。

### Task 2: 軽量レビューの位置づけを限定

- **対象ファイル:** `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- **変更内容:** `/ndf:review-branch` は PR 作成前のセルフレビュー、`/ndf:review` は例外的な単発確認に限定する。`ndf:code-reviewer` 単発レビューを cross-review の代替にしない方針を明記する。

### Task 3: release PR Ready 前チェックを追加

- **対象ファイル:** `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- **変更内容:** Step 8 の body 最終化 / Ready for review 前チェックに「全個別 PR が cross-review approved 済み」を追加する。省略した場合のフォールバックは個別 PR の状態別に明記する: **open なら** 当該個別 PR で `/ndf:cross-review` を回す（release PR には回さない。ループ内 `/ndf:fix` が release PR を修正し原則が崩れるため）、**既に release へ merge 済みなら** 元の差分は release ブランチにあり新規 PR には乗らないため release PR で `/ndf:cross-review` を回して追認する。いずれも後追い対応で手戻りが増える点も記載する。

### Task 4: アンチパターン追記

- **対象ファイル:** `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md`
- **変更内容:** 「個別 PR を cross-review せず、code-reviewer / 単発レビューだけで release へ merge する」をアンチパターン表に追加する。

### Task 5: cross-review 側との整合確認

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/SKILL.md`
- **変更内容:** issue-plan-strategy から見た cross-review の役割と矛盾がないか確認する。必要なら関連リンクまたは利用場面の説明を補足する。

### Task 6: runtime 配布物同期

- **対象ファイル:** `plugins/ndf-claude/`, `plugins/ndf-codex/`, `plugins/ndf-kiro/`
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行し、shared の変更を runtime 別配布物へ反映する。

### Task 7: cross-review skill を model 起動可能化（実装中に追加）

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/SKILL.md`
- **背景:** 本 issue の実装中、cross-review を毎回スラッシュコマンドで手入力する必要があった（`disable-model-invocation: true` によりモデルから起動不可のため）ことから、追加要望として対応した。
- **変更内容:** `disable-model-invocation: true` を削除し、メインセッションから Skill tool 経由で起動可能にする。あわせて `when_to_use` を追加し、通常の単発レビュー依頼は `/ndf:review`、本 skill は収束ループを明示したときのみという責務分担を明文化する（重い codex + gemini 収束ループが単発レビュー依頼で自動選択されるのを防ぐ）。

## PR 分割計画

単一 PR で進める。変更は主に skill 文書の運用ルール強化で、コード変更や複数機能の段階的 merge は不要。

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `docs/issue-35-require-cross-review-per-pr` | issue-plan-strategy の個別 PR cross-review 必須化と runtime 同期 | なし | - |

release branch: なし
base branch: `main`

## 影響範囲

- `ndf:issue-plan-strategy` の multi-PR 実行手順
- 個別 PR と release PR のレビュー責務分担
- release PR Ready 前のチェックリスト
- runtime 別 NDF plugin 配布物
- `ndf:cross-review` skill の起動方式（model 起動可能化 + `when_to_use` 追加。Task 7）

## テスト計画

- [ ] `bash scripts/build-runtime-plugins.sh --check`
- [ ] `bash scripts/validate-runtime-plugins.sh`
- [ ] Markdown link check が通ることを確認する
- [ ] `plugins/ndf-shared/skills/issue-plan-strategy/SKILL.md` と runtime 別コピーに drift がないことを確認する
