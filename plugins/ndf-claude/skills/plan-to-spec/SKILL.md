---
name: plan-to-spec
description: "Finalize an implemented plan into a permanent specification document. Use after implementation is complete and an issues/ plan, PLAN file, design note, or implementation plan should become the final as-is specification under docs/ or another authoritative specification location. Triggers: 'planを仕様書にして', '確定仕様書に移動', '実装完了後にplanを整理', 'planをdocsへ移動', '仕様書としてリライト', 'plan-to-spec', 'finalize plan spec'."
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# Plan to Spec

実装完了後の plan を、開発履歴ではなく **現在のコードと一致する確定仕様書**として保存する。plan は作業中の意思決定記録であり、完了後は読者が実装経緯を追わなくても仕様を理解できる形に変換する。

## 基本方針

- plan の内容をそのまま移動せず、最終実装の as-is 仕様として書き直す
- 開発中の履歴、TODO、PR 分割、作業チェックリスト、途中変更、未採用案は削除する
- 仕様書の置き場は既存 docs 構造に合わせ、なければ `docs/specifications/` を作成する
- 仕様書はコードと照合し、実装と矛盾する記述を残さない
- 完了報告は本 skill のテンプレートに従う

## 入力

`$ARGUMENTS`

引数は plan ファイルパス、issue 番号、PR 番号、または関連キーワードを受け付ける。引数がない場合は、現在ブランチの差分、`issues/`、`docs/`、`git log` から直近の plan を特定する。

## 手順

### 1. 対象 plan と実装範囲を特定する

1. 引数がファイルパスならその plan を読む
2. 引数が PR / issue 番号なら `gh pr view` / `gh issue view` とローカルファイル検索で関連 plan を探す
3. 引数がない場合は以下を確認する:
   - `git status --short`
   - `git branch --show-current`
   - `git log --oneline --decorate -20`
   - `find issues docs -maxdepth 3 -type f \( -iname '*plan*' -o -iname '*PLAN*' \)`
4. plan が複数候補ある場合は、現在ブランチ・PR・変更ファイルと最も関連が強いものを選ぶ。不明ならユーザーに確認する
5. 実装範囲を特定する:
   - PR がある場合: `gh pr diff` / `gh pr view --json files,title,body`
   - ローカル変更の場合: `git diff --stat` / `git diff`
   - merge 済みの場合: 関連コミット範囲の `git show` / `git diff`

### 2. 仕様書の配置先を決める

既存 docs の分類に合わせて配置する。優先順位:

1. 同種の仕様書がある既存ディレクトリ (`docs/specifications/`, `docs/specs/`, `docs/features/`, `docs/architecture/`, `docs/modules/` など)
2. 対象機能に対応する既存 docs 配下
3. 適切な場所がなければ `docs/specifications/` を作成

ファイル名は英数字・ハイフン中心にし、内容が分かる名前にする。例:

```text
docs/specifications/auth-session-management.md
docs/features/review-workflow.md
docs/architecture/plugin-skill-loading.md
```

移動は履歴が追えるように、可能なら `git mv <plan> <spec>` を使う。plan を残す必要がある運用の場合は、ユーザーに確認してからコピーに切り替える。

### 3. 仕様書としてリライトする

他の仕様書の章立て・表記・粒度を先に確認し、同じ体裁へ合わせる。標準章立ては以下を使う。既存 docs に明確な型がある場合はそちらを優先する。

```markdown
# {仕様名}

## 概要

## 背景

## 対象範囲

## 仕様

## データ・設定

## 外部連携

## エラー処理

## セキュリティ

## 運用

## テスト観点

## 関連リンク
```

該当しない章は削除してよい。小さな仕様では `概要`、`仕様`、`運用`、`テスト観点`、`関連リンク` 程度に圧縮する。

### 4. 削除・変換ルール

削除するもの:

- 実装タスクのチェックリスト
- PR 分割計画、worktree 運用、作業担当、レビュー進捗
- 「これから実装する」「予定」「案」「未定」など完了前提の表現
- 開発中に破棄された方針、調査メモ、試行錯誤
- AI エージェント向けの作業指示

変換するもの:

- 「実装する」→「提供する」「使用する」「保持する」
- 「修正対象」→「構成」「関連ファイル」
- 「テスト計画」→「テスト観点」または「検証方法」
- 「背景・問題」→ 現在の仕様を理解するために必要な背景だけ残す

### 5. リンクを再調査して修正する

仕様書内のリンクは移動後の位置から有効になるように直す。

- 相対リンクは新しい配置先基準で更新する
- 存在しないファイルリンクは `find` / `rg --files` で移動先を探す
- GitHub issue / PR / 外部ドキュメントのリンクは必要最小限にする
- 開発中の一時リンク、ローカル絶対パス、エージェント固有の transcript リンクは削除する

リンク先が確認できない場合は、推測で残さず削除するか、確実な上位ドキュメントへ差し替える。

### 6. コードと仕様を照合する

仕様書を書いた後、実装と一致しているかレビューする。

確認観点:

- 仕様書に書いた機能・制約・設定名・ファイルパスが実コードに存在する
- 実コードに存在する重要な挙動が仕様書から抜けていない
- エラー処理、権限、環境変数、永続化、外部連携の記述が実装と一致する
- テスト観点が実装されたテストや手動確認に対応している
- 廃止された名前、過剰な略語、未承認の用語を使っていない

用語確認では、`AGENTS.md`、`CLAUDE.md`、`docs/`、README、既存仕様書の表記を優先し、プロジェクトで authorize された名称に合わせる。略語は初出で正式名称を併記し、ローカルな作業略称は使わない。

### 7. 仕様書レビューを行い修正する

セルフレビューを 1 回行い、必要に応じて修正する。レビュー観点:

- 他の仕様書と章立て・見出し粒度・表記が揃っているか
- plan 由来の作業履歴が残っていないか
- as-is 仕様として読めるか
- コードと矛盾していないか
- リンクが移動後のパスで正しいか
- 用語がプロジェクト標準に合っているか
- 仕様書として過不足がないか

レビュー結果で修正した内容は、完了報告に要約する。

### 8. 完了報告

完了時は以下のテンプレートで報告する。

```markdown
## Plan to Spec 完了報告

### 対象
- 元 plan: `{元planパス}`
- 確定仕様書: `{仕様書パス}`

### 実施内容
- plan を `{配置先ディレクトリ}` に移動し、確定仕様書としてリライト
- 開発履歴・TODO・PR分割などの作業情報を削除
- リンクを移動後のパス基準で修正

### レビュー結果
- 体裁: `{他仕様書との整合結果}`
- コード一致: `{照合した主な実装ファイルと結果}`
- 用語: `{標準用語への修正有無}`

### 検証
- `{実行したコマンドや確認内容}`

### 補足
- `{残課題がなければ「なし」}`
```

残課題がある場合は、仕様書に曖昧な記述を残さず、完了報告の補足に明確に分離する。
