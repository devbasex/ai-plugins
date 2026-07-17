---
name: issue-plan-strategy
description: "Turn issues into plans and implementation workflows."
when_to_use: "issue → plan 作成 / 既存 plan の実装 (実行) を依頼されたとき。複数 PR に分割される設計や、release branch + 個別 PR + worktree 運用が必要なときに参照する。Triggers: 'issueのplanを作って', 'PLANxxの設計', '設計書を起こして', 'このplanを実装して', 'PLANxxを実装', 'planを実行', 'release branch 作って実装開始', 'multi-PR で進めて'"
argument-hint: "[issue-path-or-url] (例: issues/i16.md, https://github.com/org/repo/issues/123)"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# issue → plan → multi-PR ワークフロー

1 つの issue から plan を作る際、推奨される PR が複数に分かれることは日常的に発生する。本 skill はその際の **release ブランチ + 個別 PR ブランチ + Draft PR 先行作成 + git worktree 並行開発 + レビュー運用** の標準フローを規定する。

本 skill は **plan の作成フェーズと plan の実行(実装)フェーズの両方** をカバーする。同じワークフローが「設計を起こす段階」と「設計に従って実装する段階」を貫通することで、作成者と実装者(あるいは将来の自分)が同じ手順を共有できる。

## 発動条件

| トリガ | 例 | 入る Step |
|---|---|---|
| スラッシュコマンド (引数あり) | `/ndf:issue-plan-strategy issues/foo.md`、`/ndf:issue-plan-strategy https://github.com/org/repo/issues/123` | Step 0 から |
| スラッシュコマンド (引数なし) | `/ndf:issue-plan-strategy` (現在ブランチで作業中の issue/plan を解析) | Step 0 から |
| 自動発動 (作成系) | 「この issue の plan を作って」「設計書を起こして」「PLAN42 の設計を起こして」 | Step 1〜2 |
| 自動発動 (実行系) | 「この plan を実装して」「PLAN42 を実行して」「multi-PR で進めて」「release branch を切って実装開始」 | Step 0 → 既存 plan を読み → Step 3 以降 |

引数で渡された issue / plan は **ファイルパス / URL / 番号** いずれでも受け付ける:

- ファイルパス (`issues/PLANxx_*.md`): 直接 Read
- GitHub Issue URL / `#番号`: `gh issue view <num> --json title,body,labels` で取得
- それ以外の文字列: そのまま issue 本文として扱う

## Step 0: 作成フェーズか実行フェーズか判定

最初に **既に plan ファイルが存在するか** で判定する。skill 内で `Glob` を使うのが第一選択 (例: `Glob('issues/*PLAN42*')`)。shell で確認する場合は:

```bash
# issues/ 配下に該当 plan があるか (PLAN42 / feature-name 部分は実値に置換)
find issues/ -maxdepth 1 -iname '*PLAN42*' -o -iname '*feature-name*'
```

| 状況 | 進むフェーズ |
|---|---|
| plan ファイルがない / issue しかない | **作成フェーズ** (Step 1〜2 へ) |
| plan ファイルがあり、release branch がない | **実行フェーズ・初期化** (Step 3 へ) |
| release branch も Draft PR も既にある | **実行フェーズ・継続** (Step 5 以降。worktree / 並行開発 / レビュー / merge を進める) |

実行フェーズで入った場合、既存 plan の **「PR 分割計画」セクション**を必ず Read してから Step 3 以降の自動化判断に使う。

## 全体フロー

```
            ┌─ 作成フェーズ ──────────────────────────────────────┐
issue 取得 ─┤                                                    │
            │  plan 作成 (必要なら plan モード) ─ 単一PR? ─ YES ─▶ implementation-plan + /ndf:pr で完了
            │                                          │
            │                                          NO
            └──────────────────────────────────────────┼──────────┘
                                                       ▼
            ┌─ 実行フェーズ ──────────────────────────────────────┐
既存 plan ─▶│  Step 3: release branch 作成 + Draft release PR     │
            │  Step 4: 個別 PR ブランチ作成 + 各 Draft PR (release base)
            │  Step 5: git worktree で並行開発 (依存関係を考慮)    │
            │  Step 6: 個別 PR ごとに /ndf:cross-review (原則必須) │
            │           → /ndf:fix → merge into release           │
            │  Step 7: release ブランチで結合テスト相当のレビュー │
            │  Step 8: release PR body 最終化 → Ready & merge      │
            └─────────────────────────────────────────────────────┘
```

QA / staging 等の検証環境向けには、個別 PR or release PR 単位で `/ndf:cherry-pick-pr` を別途実行する (Step 9)。

実行フェーズに途中から入った場合は、対応する Step の途中再開で構わない。各 Step の冒頭で **既に存在するブランチ / PR / worktree を `git branch -a` / `gh pr list` / `git worktree list` で確認**してから作業に入る。

## Step 1: issue 取得と plan 作成 (作成フェーズ専用)

> 実行フェーズで入った場合はこの Step をスキップし、既存 plan を Read して Step 3 へ進む。

1. 引数を解釈して issue 本文を取得する
2. `issues/` 配下に plan ファイルが既に存在するか `Glob` で確認する
3. なければ `/ndf:implementation-plan` の **プランフォーマット**に従って plan ファイルを作成する
   - ファイル名は英数 (例: `issues/PLAN42_multi-pr-refactor.md`)
   - 内容に「複数 PR に分割する根拠」「PR 単位と依存関係」を必ず含める
4. 設計判断が重い場合は **Claude Code の plan モード** (ExitPlanMode を用いる読み取り専用フェーズ) に切り替えて十分検討してから実装へ進む

plan の構造は `/ndf:implementation-plan` を参照。本 skill では multi-PR を前提に **以下のセクションを追加**する:

```markdown
## PR 分割計画

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | feature/PLAN42-schema | スキーマ追加 | なし | ○ |
| 2 | feature/PLAN42-api    | API 実装    | PR1 | × (PR1 merge 後) |
| 3 | feature/PLAN42-ui     | UI 実装     | PR1 | ○ (mock で開始可) |

release branch: `release/PLAN42`
base branch: `main`
```

## Step 2: 単一 PR で足りるか判定

plan を書いた結果が以下のいずれかなら **release ブランチを作らず**、`/ndf:implementation-plan` + `/ndf:pr` の通常フローに切り替える:

- 変更ファイルが 1〜2 個で結合度が低い
- 1 PR で安全に review 可能 (差分 ~500 行以内が目安)
- 依存関係のある複数タスクが存在しない

複数 PR が妥当な場合 (スキーマ + API + UI、機能追加 + マイグレーション、複数モジュール横断 等) のみ Step 3 に進む。

## Step 3: release ブランチ + Draft PR 先行作成 (実行フェーズの開始点)

> 実行フェーズで自動発動した場合の最初の自動化対象。既に `release/<PLAN-ID>` ブランチや Draft PR が存在する場合は作成をスキップし、Step 4 へ進む。

### release ブランチ作成

```bash
git fetch origin <default-branch>
git checkout -b release/<PLAN-ID> origin/<default-branch>
git push -u origin release/<PLAN-ID>
```

### レビュアー視点の原則 (release PR body の大前提)

個別 PR はセルフレビュー (`/ndf:cross-review` 等) で merge される。**人間のレビュアーが見るのは release PR だけ**であり、個別 PR の存在をレビュアーに意識させてはならない。したがって:

- release PR の body は **self-contained 必須**: 「何のために」(背景・解決したい課題) と「何を」(release ブランチ全体としての変更内容) を、**個別 PR を一切参照せずに**理解できる粒度で書く
- 個別 PR リンクの列挙を body の本文にしない。開発中の進捗管理に使う場合は `<details>` 折りたたみ内の補足情報に格下げする
- `/ndf:cross-review` の light rotation と同じ原則を適用する: 現状の差分・実装を反映し、内部用語 (PLAN-ID 運用、round、rotated 等) をレビュアー向け本文に漏らさない

### release → default の Draft PR を先行作成

```bash
gh pr create \
  --base <default-branch> \
  --head release/<PLAN-ID> \
  --draft \
  --title "release: <PLAN-ID> <概要>" \
  --body "$(cat <<'EOF'
## Summary
- (背景) なぜこの変更が必要か / 解決したい課題
- (変更内容) release ブランチ全体として何をするか
- plan: issues/<PLAN-ID>_xxx.md

## Test plan (結合観点のみ)
- [ ] 個別 PR では検出できない結合テスト項目

<details>
<summary>開発用: 個別 PR 進捗 (レビュー対象外)</summary>

- [ ] #<TBD> PR1: ...
- [ ] #<TBD> PR2: ...
- [ ] #<TBD> PR3: ...

</details>

<!-- I want to review in Japanese. -->
EOF
)"
```

Draft 作成時点では実装が進んでいないため body は plan ベースの暫定でよいが、Ready for review 前に **実装の最終形を反映した body へ最終化**する (Step 8 参照)。

release PR を **先に作る理由**: PR 番号が確定し、個別 PR の説明から参照できるため。

## Step 4: 個別 PR ブランチ + Draft PR 先行作成

> 既存ブランチは `git branch -a | grep "feature/<PLAN-ID>-"` で確認し、未作成のものだけ作る。Draft PR の存在は `gh pr list --base release/<PLAN-ID> --state all` で確認。

各 PR について **同じパターンで先に Draft PR まで作る**:

```bash
# release ブランチを base に個別ブランチを切る
git fetch origin release/<PLAN-ID>
git checkout -b feature/<PLAN-ID>-<scope> origin/release/<PLAN-ID>

# 空コミットで push して Draft PR を作る (base=release と HEAD が同一だと
# gh pr create が "No commits between ..." で失敗するため、差分ゼロのまま PR
# 作成のトリガにする目的で `--allow-empty` を使う)
git commit --allow-empty -m "chore: <PLAN-ID>-<scope> Draft PR 作成"
git push -u origin feature/<PLAN-ID>-<scope>

gh pr create \
  --base release/<PLAN-ID> \
  --head feature/<PLAN-ID>-<scope> \
  --draft \
  --title "feat: <PLAN-ID>-<scope> <概要>" \
  --body "$(cat <<'EOF'
## Summary
- plan: issues/<PLAN-ID>_xxx.md
- release PR: #<release-pr-number>
- 担当範囲: <scope>

## Test plan
- [ ] ...

<!-- I want to review in Japanese. -->
EOF
)"
```

完了後 release PR の本文を `gh pr edit` で更新し、`<details>` 内の開発用チェックリストに個別 PR 番号を埋める (body 本文には書かない)。

## Step 5: git worktree で並行開発

並行可能 (依存なし or mock で先行可) な PR は **git worktree** で同時に開く:

```bash
# repo ルート (default branch のまま) で
git worktree add ../<repo>-<PLAN-ID>-schema feature/<PLAN-ID>-schema
git worktree add ../<repo>-<PLAN-ID>-ui     feature/<PLAN-ID>-ui

# それぞれの worktree で別ターミナル / 別エージェントを起動
```

ガイドライン:

- **依存のある PR は順次着手**する (PR1 merge → PR2 開始)
- 並行 PR 間で同じファイルを触る場合は事前にレビュー観点で分担を明確化する
- 終わった worktree は `git worktree remove <path>` で片付ける
- Claude Code から並行開発を指示する場合、Agent tool の `isolation: "worktree"` も検討する

## Step 6: 個別 PR のレビュー

**個別 PR は原則 `/ndf:cross-review <PR番号>` を必須**とする。codex + gemini の両者が
`APPROVE` に収束したことを確認してから Draft を解除し、release ブランチへ merge する。
個別 PR で重大バグを取りこぼすと、release PR 側の cross-review がまとめて検出する形に
なり、本 skill が禁止する「release PR で個別 PR 範囲の指摘を解決する」状態に陥る。

| 用途 | コマンド | 位置づけ |
|---|---|---|
| PR 作成前のセルフレビュー | `/ndf:review-branch` | push / PR 化の前段。cross-review の代替にはしない |
| 個別 PR の収束レビュー (原則必須) | `/ndf:cross-review <PR番号>` | codex + gemini 両方の APPROVE 収束を確認する本線 |
| GitHub 上の例外的な単発確認 | `/ndf:review <PR番号>` | ごく軽微な差分の単発確認に限定。cross-review の代替にはしない |
| 指摘の修正 | `/ndf:fix <PR番号>` | cross-review ループ内・後で自動起動される |

- Claude Code の `code-reviewer` などの単発レビュアーや `/ndf:review` の単発レビューを
  **cross-review の代替にしない**。単発レビューは片側 AI の一発判定にとどまり、収束ループを
  回さないため取りこぼしが残る。
- 個別 PR が cross-review で APPROVE → Draft 解除 → release ブランチへ merge (squash 推奨)。

## Step 7: release ブランチのレビュー (結合テスト相当のみ)

release ブランチへの merge が一通り進んだ段階で:

- **個別 PR で見た観点を再レビューしない**
- **結合テスト相当**の観点のみレビューする:
  - PR 間の API / 型 / スキーマ整合
  - 設定値の重複・矛盾
  - migration の順序依存
  - E2E シナリオ (`/ndf:playwright-scenario-test` の活用)
- ここで個別 PR 範囲のバグが見つかった場合は、**release PR にコメントせず**、該当の個別 PR (既に merge 済みなら新しい修正 PR を release 配下に作成) 側に指摘を書き込み、修正ループを回す
- release PR には integration 観点の指摘のみ残す

## Step 8: release PR body の最終化と release → default の merge

### body の最終化 (Ready for review の前に必須)

個別 PR が全て merge されたら、**Draft 解除の前に** release PR の body を実装の最終形を反映した self-contained な内容へ更新する:

```bash
# release ブランチ全体の差分を確認して body を書き直す
git fetch origin
git diff origin/<default-branch>...origin/release/<PLAN-ID> --stat
gh pr edit <release-pr-number> --title "..." --body "..."
```

最終化のチェック観点 (Step 3 のレビュアー視点の原則を満たすこと):

- [ ] **全個別 PR が `/ndf:cross-review` で APPROVE 収束済み** (Step 6 の前提。未実施の PR が残っていないこと)
- [ ] 「何のために」「何を」が個別 PR や plan ファイルを辿らずに理解できる
- [ ] 実装中の方針変更・スコープ増減が body に反映されている
- [ ] 個別 PR への参照が本文に残っていない (`<details>` 内の開発用情報は残してよい)
- [ ] 内部用語 (round、rotated 等) が漏れていない

> **cross-review を省略した個別 PR が残っている場合のフォールバック**: Ready for review の前に
> 未 cross-review の個別 PR を特定し、**その個別 PR に対して** `/ndf:cross-review <個別PR番号>` を
> 回して APPROVE 収束させる。該当個別 PR が既に閉じている場合は release ブランチ配下に修正 PR を
> 作成し、その修正 PR で cross-review を回す。**release PR に対して直接 cross-review を回すのは
> 避ける** — ループ内の `/ndf:fix` が release PR を対象に修正・Resolve してしまい、「個別 PR 範囲の
> 指摘は個別 PR 側で解決する」原則 (Step 7) が崩れるため。いずれにせよ後追い対応で手戻りが増えるので、
> 原則は Step 6 で各個別 PR を cross-review 済みにしておくこと。

### Draft 解除と merge

release PR が APPROVE されたら:

```bash
# Draft 解除
gh pr ready <release-pr-number>
# merge: 個別 PR が既に squash 済みで release ブランチに並んでいるため、
# main 側でも個別 PR 単位の commit を追跡できる `--merge` (merge commit 保持)
# が既定として推奨。プロジェクト規約で線形履歴必須なら `--rebase`、
# それ以外で commit 数を 1 本にしたい場合のみ `--squash`。
gh pr merge <release-pr-number> --merge --delete-branch
```

merge 後は plan ファイル末尾に「完了サマリ」(マージ済み PR 番号 / 検証結果) を追記してクローズ化する。

## Step 9: 検証環境 (qa/staging 等) への適用

QA / staging 検証は **個別 PR 単位** or **release ブランチ単位** のどちらでも OK。
`/ndf:cherry-pick-pr` は Claude Code 内の slash command なので、shell ではなく
Claude Code セッション上で実行する点に注意。

個別 PR 単位で qa に反映する場合:

```text
# (Claude Code 内で実行する slash command)
/ndf:cherry-pick-pr qa/staging
```

release ブランチごと qa に反映する場合 (まとまった検証が必要な場合):

```bash
# 1. shell で release ブランチに切り替え
git checkout release/<PLAN-ID>
```

```text
# 2. (Claude Code 内で実行する slash command)
/ndf:cherry-pick-pr qa/staging
```

詳細は `/ndf:cherry-pick-pr` と `/ndf:branch-fix-strategy` を参照。`feature → main` 系 PR を汚染しないため、検証ブランチ向けは必ず短命ブランチ経由で扱う。

## アンチパターン

| ❌ やってはいけないこと | 理由 |
|---|---|
| release ブランチを作らず巨大な 1 PR で出す | レビュー困難・revert 困難・並行開発不可 |
| 個別 PR の base を default にする | release で統合する意味が失われ、partial merge が default を汚染する |
| 個別 PR Draft 作成を実装後に回す | PR 番号が未確定でクロス参照や CI 待機の段取りが組めない |
| 個別 PR を cross-review せず、Claude Code の code-reviewer 等の単発レビューだけで release へ merge する | 片側 AI の一発判定で収束ループを回さないため重大バグを取りこぼし、release PR 側でまとめて検出され手戻りが増える (Step 6) |
| release PR で個別 PR 範囲の指摘を解決しようとする | 該当 PR が既に閉じている場合、コミット意図がずれる |
| release PR の body を個別 PR リンクの列挙だけにする | レビュアーは release PR 単体で変更を把握できず、個別 PR や plan を辿ることになる。body は self-contained 必須 (Step 3 / Step 8) |
| body 最終化せずに Ready for review にする | Draft 作成時の plan ベースの暫定 body のままだと実装の最終形と乖離する |
| 検証ブランチを feature/release に merge する | `feature → main` PR への汚染 (詳細: `/ndf:branch-fix-strategy`) |

## 関連 skill

- `/ndf:implementation-plan` — plan ファイルのフォーマット (本 skill が依存)
- `/ndf:branch-fix-strategy` — ブランチ汚染を避ける原則
- `/ndf:pr` — 通常の PR 作成 / 更新
- `/ndf:cherry-pick-pr` — 検証ブランチへの cherry-pick PR
- `/ndf:review` / `/ndf:review-branch` / `/ndf:cross-review` — レビュー
- `/ndf:fix` / `/ndf:resolve-pr-comments` — コメント対応
- `/ndf:playwright-scenario-test` — release ブランチでの E2E 結合テスト
