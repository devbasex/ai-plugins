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

## 引数

`/ndf:pr [--draft] [base] ["メッセージ"]`。`--draft` はドラフト PR、既知のベース名（`main` / `master` /
`qa/*` / `release/*` / `staging/*` 等）はベース、それ以外はコミットメッセージ。既定は `main` ベース・非ドラフト。

## push / PR 作成前の同意取得（必須）

push と PR 作成は取り消しに追加の操作が要る書き込みである。自然文の依頼でも起動するため、この手順で守る。

**手順 3（push）と手順 4（PR 作成）の直前に、`plan` の結果から次を提示する。**

- push 先のブランチ名と、PR の宛て先ブランチ（`metrics.branch` / `metrics.base`）
- コミット対象のファイル一覧と変更量（`items` の `changes`、`metrics.commits` / `metrics.files`）
- 使用するコミットメッセージ
- 既存 PR の有無（`metrics.existing_pr`。新規作成なのか、既存 PR の更新なのか）

同意の扱い:

- 依頼が push と PR 作成まで明示的に含む（`/ndf:pr` の明示起動、「コミットしてPRを作って」等）なら、
  依頼を同意とみなす。提示は行い、結果報告に含める
- 作業の流れで暗黙に起動したときは、提示したうえで**明示的な同意を得てから push する**。
  同意が無ければ commit までで止める
- 宛て先ブランチが `main`/`master` 以外の場合は、手順 1 の誘導を優先する

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

- `metrics.redirect` が `worktree`: ベースブランチにいる。`/ndf:worktree` で worktree を用意し、
  そこへ移ってから打ち直す（入れ子の worktreeは作らない）
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
`## Summary`・`## 利用者向けの変化`・`## Test plan` を持ち、機密情報を含めない。更新のときはブランチの全コミット
（`git log origin/<base>..HEAD`）を反映し、既存の関連リンクを保つ。雛形は
`python3 "$SCRIPTS/pr-steps.py" template --out /tmp/pr-body.md` が書く。

**`## 利用者向けの変化` はリリースの CHANGELOG と更新案内へそのまま載る**（`release-steps.py notes` が組む）。
利用者に何ができるようになるか・使い方が変わる点を箇条書きにし、今の決まりだけを書く。見える変化が無ければ
「- 無し」と書く（リリースの説明文は題名で代わる）。節の無い本文では、`create` / `update` の `next` が節を足すよう求める。

```bash
python3 "$SCRIPTS/pr-steps.py" create --title "<タイトル>" --body-file /tmp/pr-body.md [--draft] [--base <base>] \
  --mode <モード> --stages "<通した工程をカンマ区切り>"
python3 "$SCRIPTS/pr-steps.py" update --body-file /tmp/pr-body.md [--title "<タイトル>"] \
  --mode <モード> --stages "<通した工程をカンマ区切り>"
```

- `--mode` と `--stages` を渡すと、本文の末尾に `モード: <mode> / 通した工程: <工程> → <工程>` の
  1 行を書く（既にあれば置き換える）。モードは `development-workflow` が判定した値、工程は工程表の
  行の名前である。**この 1 行は省かない。** リリース後の不具合の起票数と突き合わせる材料になる

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

## ミッションブランチ宛てと develop 宛て

**宛て先で、Pull Request の役割が変わる。** `plan` の `metrics.target` と `metrics.review` が区分を返す。

| 宛て先 | 何の Pull Request か | コードレビュー | `metrics` |
| --- | --- | --- | --- |
| `mission/<名前>` | 課題の Pull Request。課題の worktree からミッションブランチへ集める | 通さない。緑になったらミッションブランチへ取り込む | `target: mission` / `review: false` |
| develop（宣言の `base_branch`） | ミッションの Pull Request（ミッションで 1 本）か、ミッションブランチを経ない単独の Pull Request | 通す（リファクタリング・`cross-review`・完了判定を 1 回） | `target: develop` / `review: true` |

課題の Pull Request は `--base mission/<名前>` で出す。ミッションの Pull Request は、
ミッションブランチの worktree から `--base` を省いて出し、本文にミッションが閉じる課題を番号ごとに書く。

## 閉じる語は本文だけに書く

**閉じる語（`Closes` / `Fixes` / `Resolves`）は Pull Request の本文だけに、番号ごとに書く。** コミット
メッセージに書くと、マージでそのコミットが指す課題だけが先に閉じ、ミッションが割れる。**本文の閉じる語は
外さない**（`stage-completeness.md` と `progress-tracking` の「ミッションを閉じる」が本文を入力にする）。

## 設計 Pull Request の本文

**head のブランチ名が `design/` で始まる Pull Request の本文は、決定の中身を持たない**（決定は設計文書だけが持つ）。

| 節 | 何を書くか |
| --- | --- |
| `## Summary` | 何を設計したかと、設計文書へのリンク。**決定を言い換えて書かない** |
| `## 決めたこと` | **手で書かない。** `pr-body-decisions.sh sync` が設計文書の `## 決定の記録` の見出しから作る |
| `## Test plan` | 設計の段階で確かめたこと |

突き合わせは `bash "$SCRIPTS/pr-body-decisions.sh" check <number>`（0 一致・対象外 / 1 食い違い /
2 読めない）。**2 を一致と読まない。**

## 命名と検証ブランチ

ブランチは英語（github flow）、コミット・PR は日本語（prefix 例: `feat:` / `fix:` / `docs:` / `chore:`）。
検証ブランチ（`qa/*` 等）へは feature ブランチから直接 PR を出さず、`/ndf:cherry-pick-pr <base>` を使う
（マージで main の変更が混ざる）。

## マージに人手の承認が要るか

要否は**マージ先のチャネル**で決まる。Pull Request を出すこと自体に承認は要らない。

| マージ先 | 承認 |
| --- | --- |
| 開発版・検証環境のチャネル | 要らない |
| **本番のチャネル** | **要る** |
| head のブランチ名が `design/` で始まる Pull Request | **どのチャネルでも要る**（ドキュメントレビューの承認ゲート） |

**規則は `/ndf:release` が持つ。** 本番のチャネルは `.ndf/worktree.json` の `production_branch`、
宣言が無ければ既定ブランチ。全体像は `/ndf:development-workflow` の「人手の承認を求める関門」。

この工程に入ったら `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "Pull Request"` を 1 行打つ（3 層では起動指示の「記録のコマンド」を使う）。

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR
- `/ndf:deploy` — 環境ブランチへのデプロイPR（ブランチ全体）
- `/ndf:pr-tests` — Test Plan 自動実行
- `/ndf:pr-review` — PR単位レビュー
- `/ndf:merged` — マージ後のブランチ整理 / 現ブランチにベースブランチを取り込み
- `/ndf:release` — リリース。**本番の系へ届く操作の承認の規則を持つ**
