---
name: pr
description: "Commit, push, and open or update a pull request, draft included, after showing branch and files for approval. Use when asked to create or update a PR, or to push work for review（PRを作って・コミットしてプッシュ・PRを更新）."
argument-hint: "[--draft] [base-branch] or [commit-message]"
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# PR作成

このプロジェクトのコードを commit, push し、GitHub で Pull Request を作る。既に PR があれば本文を最新の変更内容に書き直す。

**制約**: デフォルトブランチ(main, masterなど)で直接コミット禁止

## 使用方法

```
/ndf:pr                           # main へ通常PR作成
/ndf:pr --draft                   # main へドラフトPR作成
/ndf:pr "新機能の追加"             # コミットメッセージ指定
/ndf:pr --draft "wip: 作業中"      # ドラフトPR + メッセージ指定
/ndf:pr qa/staging                # base非main → cherry-pick-prへ誘導
```

## 引数の解釈

- `--draft` が含まれていればドラフトPR
- 既知のベースブランチ名（`main`, `master`, `qa/*`, `release/*`, `staging/*` 等）が末尾にあればベース指定
- それ以外の文字列はコミットメッセージとして扱う
- デフォルトは `main` ベース、非ドラフト

## push / PR 作成前の同意取得（必須）

push と PR 作成は外部（GitHub）への書き込みで、取り消しには追加の操作が要る。
この Skill は自然文の依頼でも起動するため、安全性はこの手順で担保する
（frontmatter の発動制御には依存しない）。

**手順 3（push）と手順 4（PR 作成）の直前に、`plan` の結果から次を提示する。**

- push 先のブランチ名と、PR のベースブランチ（`metrics.branch` / `metrics.base`）
- コミット対象のファイル一覧と変更量（`items` の `changes`、`metrics.commits` / `metrics.files`）
- 使用するコミットメッセージ
- 既存 PR の有無（`metrics.existing_pr`。新規作成なのか、既存 PR の更新なのか）

同意の扱い:

- 利用者の依頼が push と PR 作成まで明示的に含む場合（`/ndf:pr` の明示起動、
  「コミットしてPRを作って」等）は、その依頼を同意とみなしてよい。提示は行い、
  結果報告に含める
- それ以外（作業の流れで暗黙に起動した場合）は、提示したうえで**明示的な同意を得てから
  push する**。同意が得られなければ commit までで止め、push も PR 作成も行わない
- ベースブランチが `main`/`master` 以外の場合は、手順 1 の誘導を優先する

## この文書が受け取る値

| 変数 | 値 | 決め方 |
| --- | --- | --- |
| `$SCRIPTS` | プラグインの `scripts/` の絶対パス | [scripts-lookup.md](../development-workflow/references/scripts-lookup.md)。シェルが変わったら決め直す |

## 手順

決まった処理は `pr-steps.py` が行う。各コマンドは 1 行の JSON を返す。`status` が `ok` なら次へ進み、
`stopped` なら `summary` と `next` を読んで直し、同じコマンドを打ち直す（形は `scripts/lib/README.md`）。

### 1. 計画

```bash
python3 "$SCRIPTS/pr-steps.py" plan [--draft] [--base <base>] [--message "<コミットメッセージ>"]
```

- `metrics.redirect` が `worktree`: 起点のブランチにいる。`/ndf:worktree` で作業ツリーを用意し、
  そこへ移ってから打ち直す（入れ子の作業ツリーは作らない）
- `metrics.redirect` が `cherry-pick-pr`: 起点が `main`/`master` でも宣言のブランチでもない。警告を出して
  `/ndf:cherry-pick-pr <base>` へ誘導し、利用者が明示的に継続を指示したときだけ `--force` で進める
- `metrics.closing_words_in_message` に語がある: メッセージから外す（「閉じる語は本文だけに書く」）
- `metrics.existing_pr` が null なら新規作成、あれば更新になる

### 2. コミット

メッセージは日本語。引数で指定されたものがあればそれを使い、なければ差分から書く。

```bash
python3 "$SCRIPTS/pr-steps.py" commit --message "<メッセージ>"
```

閉じる語があるとコミットせずに `stopped` で止まる。上位階層を含むすべての変更をコミットする。

### 3. 同意の確認と push

「push / PR 作成前の同意取得」に従って提示し、同意を確認してから打つ。

```bash
python3 "$SCRIPTS/pr-steps.py" push
```

credential helper が応答しない環境の退避（`gh auth git-credential` での再試行）はスクリプトが行う。

### 4. PR の作成・更新

本文を日本語で書いてファイルに置く。`.github/pull_request_template.md` があればその構造に従い、
`## Summary` と `## Test plan` を持ち、機密情報を含めない。更新のときはブランチの全コミット
（`git log origin/<base>..HEAD`）を反映し、既存の関連リンクを保つ。

```bash
python3 "$SCRIPTS/pr-steps.py" create --title "<タイトル>" --body-file /tmp/pr-body.md [--draft] [--base <base>]
python3 "$SCRIPTS/pr-steps.py" update --body-file /tmp/pr-body.md [--title "<タイトル>"]
```

- `create` は OPEN の PR があれば更新として振る舞う（`metrics.action`）
- 本文末尾の `<!-- I want to review in Japanese. -->`、GraphQL の上限での REST への退避、
  `pr-body-decisions.sh sync`（結果は `metrics.decisions_sync_exit`）はスクリプトが行う

**Test plan には実行したコマンドと終了コードを書く。証跡は `exit=0` にする**（「通った」という語を
証跡にしない。判定の規則は `quality-gates` にある）。

### 5. 完了報告

**PR を作成・更新しただけでは完了ではない。この手順まで実行して完了とする。**
既存 PR を更新しただけの場合も同じ報告を行う。

```bash
python3 "$SCRIPTS/pr-steps.py" report <番号>
```

結果の `next` が報告の文である。`主な変更` の 1〜3 行だけを書き足し、そのまま利用者へ示す。
URL は最終行に生のまま置く（Markdown リンクにすると利用者の画面では番号しか表示されない）。
コミット履歴は報告に含めない。

## 閉じる語は本文だけに書く

**閉じる語（`Closes` / `Fixes` / `Resolves`）を書くのは Pull Request の本文だけである。** コミット
メッセージに書くと、既定ブランチへのマージで**そのコミットが指す課題だけ**が先に閉じ、同じまとまりの
課題が閉じたものと開いたものに割れる。**本文の閉じる語は外さない**（`development-workflow` の
`references/stage-completeness.md` と `progress-tracking` の「まとまりを閉じる」が本文を入力にする）。
閉じる語は番号ごとに要る。

## 設計 Pull Request の本文

**head のブランチ名が `design/` で始まる Pull Request の本文は、決定の中身を持たない。** 本文と設計文書が
同じ決定を別の文で持つと、設計を変えたときに片方だけが直る。

| 節 | 何を書くか |
| --- | --- |
| `## Summary` | 何を設計したかと、設計文書へのリンク。**決定を言い換えて書かない** |
| `## 決めたこと` | **手で書かない。** `pr-body-decisions.sh sync` が設計文書の `## 決定の記録` の見出しから作る |
| `## Test plan` | 設計の段階で確かめたこと |

突き合わせは `bash "$SCRIPTS/pr-body-decisions.sh" check <number>`（0 一致・対象外 / 1 食い違い /
2 読めない）。**2 を一致と読まない。**

## 命名規則

ブランチは英語（github flow）、コミット・PR は日本語。prefix 例: `feat:` / `fix:` / `refactor:` /
`docs:` / `test:` / `chore:`。

## 検証ブランチ(qa/*等)へのPR作成

**featureブランチから直接検証ブランチへPRを作成してはいけません**（merge すると main が汚染される）。
`/ndf:cherry-pick-pr <base-branch>` を使う。

## マージに人手の承認が要るか

**Pull Request を出すこと自体は承認の関門ではない。** 要否を決めるのは**マージ先のチャネル**
である。検証環境や開発版のチャネルへ入れるマージは取り消せるため、承認を求めない。

| マージ先 | 承認 |
| --- | --- |
| 開発版・検証環境のチャネル | 要らない |
| **本番のチャネル** | **要る** |
| head のブランチ名が `design/` で始まる Pull Request | **チャネルによらず要る**（ドキュメントレビューの関門） |

**規則は `/ndf:release` が持つ。** どのブランチが本番のチャネルかはリポジトリが宣言し
（`.ndf/worktree.json` の `production_branch`）、宣言が無ければ既定ブランチを指す。判定の
全体像は `/ndf:development-workflow` の「人手の承認を求める関門」にある。

この工程に入ったら記録のコマンド `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "Pull Request"` を 1 行打つ（issue の本文と盤面の両方に残る。`$SCRIPTS` の決め方は `development-workflow` の `references/scripts-lookup.md`、3 層では起動指示の「記録のコマンド」を使う）。

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR
- `/ndf:deploy` — 環境ブランチへのデプロイPR（ブランチ全体）
- `/ndf:pr-tests` — Test Plan 自動実行
- `/ndf:pr-review` — PR単位レビュー
- `/ndf:merged` — マージ後のブランチ整理 / 現ブランチに起点ブランチを取り込み
- `/ndf:release` — 配布。**本番の系へ届く操作の承認の規則を持つ**（もう 1 つの形は
  運用モードの実行で、そちらは `development-workflow` の `references/operation-run.md`）
