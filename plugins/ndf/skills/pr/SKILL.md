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

このプロジェクトのコードをcommit, pushし、GitHubでPull Requestを作成する。既にPRがあればPR説明を最新の変更内容に更新する。

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

**手順 4（プッシュ）と手順 5（PR 作成）の直前に、次を提示する。**

- push 先のブランチ名と、PR のベースブランチ
- コミット対象のファイル一覧（`git status --short`）と変更量（`git diff --stat`）
- 使用するコミットメッセージ
- 既存 PR の有無（新規作成なのか、既存 PR の更新なのか）

同意の扱い:

- 利用者の依頼が push と PR 作成まで明示的に含む場合（`/ndf:pr` の明示起動、
  「コミットしてPRを作って」等）は、その依頼を同意とみなしてよい。提示は行い、
  結果報告に含める
- それ以外（作業の流れで暗黙に起動した場合）は、提示したうえで**明示的な同意を得てから
  push する**。同意が得られなければ commit までで止め、push も PR 作成も行わない
- ベースブランチが `main`/`master` 以外の場合は、手順 2 の誘導を優先する

## 手順

### 0. PR確認

- `git branch --show-current` で現在ブランチを確認
- `gh pr list --head <branch>` で既存PR確認
- 既にPRが存在しOPEN状態なら:
  - `git add` → `git commit`（日本語メッセージ）→ **「push / PR 作成前の同意取得」に従って提示** → `git push`
  - **既存PR説明を更新** する（「PR説明の更新」節を参照）
  - **手順 6 の完了報告を行う**（PR を更新しただけの場合も省略しない）
- PRがない、またはmerge/close済みなら次へ

### 1. ブランチ確認・切り替え

- デフォルトブランチの場合: **`/ndf:worktree` の手順で作業ツリーを用意し、そこへ移ってから作業する。**
  作業ツリーは `<主ディレクトリ>/.worktrees/<ブランチ名>` に作られ、ブランチもそこで作られる
- デフォルトブランチ以外: `git stash` → `git pull origin <default-branch>`（コンフリクト時は停止しユーザに報告）→ `git stash pop`

既に作業ツリーの中にいる場合は、そのまま続ける。入れ子の作業ツリーは作らない。
作業ツリーを使わない事情があるときは、主ディレクトリで `git checkout -b` してもよい。
その場合もマージ後の後片付けは `/ndf:merged` が扱う。

### 2. ベースブランチ判定

- 引数の末尾が `main`/`master` 以外のベースブランチ名（`qa/staging`, `release/v2` 等）の場合:
  - **警告を出して `/ndf:cherry-pick-pr <base>` に誘導する**
  - 理由: base非mainのPRに直接pushすると `feature → main` のPRに環境固有コードが混入する（詳細は `/ndf:cherry-pick-pr`）
  - ユーザーが明示的に継続を指示した場合のみ進める

### 3. 変更コミット

- `git status` → `git add` → `git commit`（日本語メッセージ）
- 引数で指定されたコミットメッセージがあればそれを使用、なければ差分から生成
- 上位階層を含むすべての変更をcommit

### 4. プッシュ

**「push / PR 作成前の同意取得」に従って提示し、同意を確認してから実行する。**

```bash
git push -u origin <branch-name>
```

### 5. PR作成

- **作成する PR のタイトル・ベースブランチ・ドラフト有無を提示してから実行する**
  （手順 4 で一括して同意を得ている場合は再確認不要）
- `.github/pull_request_template.md` が存在すれば適用
- `--draft` 指定ならドラフトPR作成
- タイトル・説明は日本語、body は `## Summary` + `## Test plan`
- 機密情報（トークン、パスワード、APIキー等）を含めない
- body 末尾に `<!-- I want to review in Japanese. -->` を入れる
- **body は必ずHEREDOC形式で渡す**（`\n` リテラル混入防止）:

```bash
gh pr create --title "タイトル" $DRAFT_FLAG --body "$(cat <<'EOF'
## Summary
- 変更内容

## Test plan
- [ ] テスト項目

<!-- I want to review in Japanese. -->
EOF
)"
```

`DRAFT_FLAG` は `--draft` 指定時のみ `--draft`、それ以外は空。

**Test plan には実行したコマンドと終了コードを書く。** 出力の末尾だけを読むと、落ちていても
通ったと読める。検査は集計を標準出力へ書きながら判定を終了コードだけで返すことがあるため、
**「通った」という語ではなく `exit=0` を証跡にする**（判定の規則は `quality-gates` にある）。

**`gh pr create` は GraphQL を呼ぶ。上限に達していると作成そのものが失敗する。**

```console
$ gh pr create --base develop --title "..." --body "..."
error checking for existing pull request: GraphQL: API rate limit already exceeded for user ID ...
```

既存の Pull Request の確認に GraphQL を使うためで、**GraphQL の上限は REST とは別に枯れる**。
この文言が出たら REST へ退避する。同じ内容の Pull Request が既にないかは REST でも引ける。

```bash
# 既存の確認（GraphQL を使わない）
gh api "repos/<所有者>/<リポジトリ>/pulls?head=<所有者>:<枝>&state=open" --jq '.[].number'

# 作成
jq -n --arg t "<タイトル>" --arg h "<枝>" --arg b "<起点>" --rawfile body /tmp/pr-body.md \
  '{title:$t, head:$h, base:$b, body:$body}' > /tmp/pr.json
gh api "repos/<所有者>/<リポジトリ>/pulls" --input /tmp/pr.json --jq '.html_url'
```

**退避するのは上限で失敗したときだけである。** `gh pr create` はテンプレートの適用と枝の
push 済みかの確認を持つため、通る間はそちらを使う。`gh pr view` / `gh pr checks` /
`gh pr merge` も同じ経路を持つため、上限に達している間は同じ形で REST へ退避する。

### 6. 完了報告

**PR を作成・更新しただけでは完了ではない。この手順まで実行して完了とする。**
既存 PR を更新しただけの場合（手順 0 の経路）も同じ報告を行う。

値を取得する:

```bash
gh pr view <number> --json number,title,url,isDraft,baseRefName,headRefName,body
git rev-list --count origin/<base>..HEAD
git diff origin/<base>..HEAD --stat | tail -1
```

次の形で報告する:

```
PR #<番号> <タイトル>

- ベース / ソース: <base> ← <head>（ドラフト: あり / なし）
- 変更量: <コミット数> コミット / <ファイル数> ファイル / +<追加> -<削除>
- 主な変更: <1〜3 行>
- PR 本文: Summary の要点 1 行 / Test plan <項目数> 件（実行済み <n> 件）

URL: <gh pr view で取得した url をそのまま>
```

**URL は報告の最終行に、生の URL のまま置く。** 途中の行に混ぜると探すことになり、
`[#124](https://…)` のような Markdown リンクにすると利用者の画面では番号しか表示されず、
URL を取り出せない。

コミット履歴は報告に含めない。PR ページで読めるため、報告で重ねて示す必要はない。

## PR説明の更新（既存PRがある場合）

既存PRがある場合、以下の手順でPR説明を更新する:

1. **変更内容の分析**:
   - `git log origin/<default-branch>..HEAD` でブランチ全体のコミット履歴
   - `git diff origin/<default-branch>..HEAD --stat` で変更ファイル一覧
   - 必要に応じて変更ファイルの詳細を取得
2. **既存PR説明の確認**:
   - `gh pr view <number> --json body` で現在のPR説明を取得
   - 既存の関連リンク（Issue参照、設計ドキュメント等）は保持する
3. **PR説明の生成**:
   - `.github/pull_request_template.md` のテンプレート構造に従う
   - ブランチの**全コミット**の変更内容を反映する（最新コミットだけでなく全体）
   - 「Summary」「Test plan」「やらないこと」等を適切に記述
4. **更新の実行**:
   ```bash
   gh pr edit <number> --body "<new-description>"
   ```

## 命名規則

- ブランチ: 英語（github flow）
- コミット・PR: 日本語
- コミットメッセージ prefix 例:
  - `feat:` 新機能
  - `fix:` バグ修正
  - `refactor:` リファクタリング
  - `docs:` ドキュメント
  - `test:` テスト
  - `chore:` その他

## 検証ブランチ(qa/*等)へのPR作成

**重要: featureブランチから直接検証ブランチへPRを作成してはいけません。**

### アンチパターン（禁止）

```
feature/xxx ──PR──→ qa/staging   ← ❌ qa/staging をmergeするとmainが汚染される
```

### 正しい手順

`/ndf:cherry-pick-pr <base-branch>` を使う（自動化済み）。原則と手順は `/ndf:cherry-pick-pr` に記載のとおり。

## マージに人手の承認が要るか

**Pull Request を出すこと自体は承認の関門ではない。** 要否を決めるのは**マージ先のチャネル**
である。検証環境や開発版のチャネルへ入れるマージは取り消せるため、承認を求めない。

| マージ先 | 承認 |
| --- | --- |
| 開発版・検証環境のチャネル | 要らない |
| **本番のチャネル** | **要る** |
| head のブランチ名が `design/` で始まる Pull Request | **チャネルによらず要る**（設計レビューの関門） |

**規則は `/ndf:release` が持つ。** どのブランチが本番のチャネルかはリポジトリが宣言し
（`.ndf/worktree.json` の `production_branch`）、宣言が無ければ既定ブランチを指す。判定の
全体像は `/ndf:development-workflow` の「人手の承認を求める関門」にある。

この工程に入ったら `/ndf:progress-tracking <issue番号> "Pull Request"` を呼ぶ（記録の手順はその Skill が持つ）。

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR
- `/ndf:deploy` — 環境ブランチへのデプロイPR（ブランチ全体）
- `/ndf:pr-tests` — Test Plan 自動実行
- `/ndf:pr-review` — PR単位レビュー
- `/ndf:merged` — マージ後のブランチ整理 / 現ブランチに起点ブランチを取り込み
- `/ndf:release` — 配布。**本番の系へ届く操作の承認の規則を持つ**（もう 1 つの形は
  運用モードの実行で、そちらは `development-workflow` の `references/operation-run.md`）
