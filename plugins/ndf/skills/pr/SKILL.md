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

`/ndf:pr [--draft] [base] ["メッセージ"]`。`--draft` はドラフト PR、既知のベース名（既定ブランチ・ブランチ戦略の設定の `branches.base` /
`qa/*` / `release/*` / `staging/*` 等）はベース、それ以外はコミットメッセージ。既定のマージ先はブランチ戦略の設定のスプリントの PR のマージ先（`branches.base`）で、非ドラフト。

## push / PR 作成前の同意取得（必須）

push と PR 作成は取り消しに追加の操作が要る書き込みである。自然文のリクエストでも起動するため、この手順で守る。

**手順 3（push）と手順 4（PR 作成）の直前に、手順 1 で確かめた次の内容を提示する。**

- push 先のブランチ名と、PR の宛て先ブランチ
- コミット対象のファイル一覧と変更量（`git status --short` と `git diff --stat`）
- 使用するコミットメッセージ
- 既存 PR の有無（新規作成なのか、既存 PR の更新なのか）

同意の扱い:

- リクエストが push と PR 作成まで明示的に含む（`/ndf:pr` の明示起動、「コミットしてPRを作って」等）なら、
  リクエストを同意とみなす。提示は行い、結果報告に含める
- 作業の流れで暗黙に起動したとき、push 先が fork・他のリポジトリなら、提示したうえで**明示的な同意を得てから push する**
  （`AskUserQuestion` の `metadata.source` は `ndf-ask:redline:C5`）。同意が無ければ commit までで止める
- 暗黙に起動して push 先が開発対象と同じリポジトリなら、問わずに AI が決め、決めたことと理由を結果報告に書く
- 宛て先ブランチが既定ブランチ以外の場合は、手順 1 の誘導を優先する

## 手順

**PR を作る処理は git と gh で行う。** `ndf` の結果 JSON を読むのは手順 2 の closing keywords の確かめだけで、
`status` が `stopped` なら `summary` を読んで直し、同じコマンドを打ち直す（終了コードの区分は
[exit-codes.md](../../docs/contracts/exit-codes.md)）。

### 1. 計画

```bash
git branch --show-current                                      # push 先のブランチ
jq -r '.branch_strategy.branches.base' .ndf/project.json        # 既定の宛て先（ブランチ戦略の設定）
git status --short                                             # コミット対象のファイル
git diff --stat HEAD                                           # 変更量
gh pr view --json number,url,baseRefName,state                 # 既存 PR（無ければ 1 で終わる）
```

- 今のブランチがベースブランチ（ブランチ戦略の設定の `branches.base`）か既定ブランチ: `/ndf:worktree` で worktree を用意し、
  そこへ移ってから打ち直す（入れ子の worktree は作らない）
- 宛て先が既定ブランチでも設定のブランチでもない検証ブランチ（`qa/*` 等）: 警告を出して
  `/ndf:cherry-pick-pr <base>` へ誘導し、利用者が明示的に継続を指示したときだけ進める
- ツールのパス（既定は `.serena/project.yml`・`.serena/serena_config.yml`。`.ndf/worktree.json` の `tool_paths` で足し引きする。
  `worktree` の [ndf-settings.md](../worktree/references/ndf-settings.md) の「ツールが書き換えるファイルを追跡しているリポジトリ」）は
  コミット対象から外す
- 既存 PR が `OPEN` なら更新、無ければ新規作成になる

### 2. コミット

メッセージは日本語。引数で指定されたものがあればそれを使い、なければ差分から書く。
**メッセージに GitHub の closing keywords（PR 本文に `Closes #123` のように書くと、マージ時にその課題を自動で閉じる機能）を
書かない**（「closing keywords は本文だけに書く」）。課題に触れるなら `（#123）` の参照の形で書く。

```bash
git add -A                                       # 上位階層を含むすべての変更
git restore --staged -- <ツールのパス>            # ツールのパスを index から外す（ファイルの中身は残す）
git commit -m "<メッセージ>"
ndf worker closing-check --base origin/<宛て先>   # 送るコミットに closing keywords があれば refused（1）で止まる
```

`closing-check` が止まったら、`items` の `sha` と `words` のコミットのメッセージを書き直してから（`git commit --amend` か
`git rebase`）打ち直す。

### 3. 同意の確認と push

「push / PR 作成前の同意取得」に従って提示し、同意を確認してから打つ。

```bash
git push -u origin "$(git branch --show-current)"
```

credential helper が応答しない環境では、`gh auth setup-git` で gh を credential helper にしてから打ち直す。

### 4. PR の作成・更新

本文は `document-style` の規則で日本語で書き、ファイル（例: `/tmp/pr-body.md`）に置く。`.github/pull_request_template.md` が
あればその構造に従い、`## Summary`・`## 利用者向けの変化`・`## 移行の手順`・`## Test plan` を持ち、機密情報を含めない。
更新のときはブランチの全コミット（`git log origin/<base>..HEAD`）を反映し、既存の関連リンクを保つ。
本文の末尾には `<!-- I want to review in Japanese. -->` の 1 行を置く。

**`standard` の実装 PR は `## 設計と違う点` のセクションを持つ。** 完了判定の設計との突き合わせ（`quality-gates` の
`references/design-match.md`）で見つけた、設計の決定・設計に名前の出るテスト・既存の確定仕様・受け入れ条件と実装の
違いを「| 設計の箇所 | 違い | 理由 |」の表で並べる。違いが無ければ「- 無し」、設計文書が無ければ
「- 該当なし（設計文書が無い）」と書く（設計書を省いた変更は該当なしにせず、実装計画の「修正対象」と照らした結果を並べる。設計 PR は対象にしない）。

**`## 利用者向けの変化` はリリースの CHANGELOG のバージョンのセクションへそのまま載る**（リリースが組む）。
利用者に何ができるようになるか・使い方が変わる点を箇条書きにし、今の決まりだけを書く。見える変化が無ければ
「- 無し」と書く（リリースの説明文は題名で代わる）。**セクションを省かない。**

**`## 移行の手順` は CHANGELOG のバージョンのセクションの `### 移行の手順` へ載る。** 利用者が新しいバージョンへ移るときに自分で行う操作
（引数の置き換え・認証のし直し・導入し直し）を、プラグインの名前を添えて箇条書きにする。操作が要らなければ
「- 無し」と書く。プラグインの名前に触れる箇条は、デプロイでそのプラグインのバージョン種別の候補を MAJOR にする。要求の
「公開インタフェース」の行に互換なしのフラグがある課題では、要求の移行性の行を手順にする。

**`## 置き換える箇条`（任意）は、同じバージョンの前の PR の「利用者向けの変化」の箇条を CHANGELOG と承認資料から外す。**
この PR が前の PR の振る舞いを変えたときに書き、前の PR の本文は直さない。書き方は `release` の
`references/release-steps.md` の「置き換える箇条」にある。

```bash
# 新規作成
gh pr create --base <宛て先> --head "$(git branch --show-current)" --title "<タイトル>" --body-file /tmp/pr-body.md [--draft]
# 既存 PR の更新
gh pr edit <番号> --body-file /tmp/pr-body.md [--title "<タイトル>"]
```

- 本文に `モード: <モード>` の 1 行を書く。モードは `development-workflow` が判定した値である。スプリントの Pull Request の
  「## 通した工程」はステートマシン（`ndf progress`）が書き直すため、手で書かない
- `gh pr edit` が GraphQL の上限で断られたら、REST で打ち直す: `gh api -X PATCH repos/<所有者>/<リポジトリ>/pulls/<番号> -F body=@/tmp/pr-body.md`

**Test plan には実行したコマンドと終了コードを書く。証跡は `exit=0` にする**（「通った」という語を
証跡にしない。判定の規則は `quality-gates` にある）。

### 5. 完了報告

**PR を作成・更新しただけでは完了ではない。この手順まで実行して完了とする。**
既存 PR を更新しただけの場合も同じ報告を行う。

```bash
gh pr view <番号> --json number,title,url,baseRefName,headRefName,isDraft
```

結果から、作成か更新か・タイトル・宛て先・ドラフトかを書き、`主な変更` の 1〜3 行を書き足して利用者へ示す。
URL は最終行に生のまま置く（Markdown リンクにすると利用者の画面では番号しか表示されない）。
コミット履歴は報告に含めない。

## スプリントブランチ宛てとベースブランチ宛て

**宛て先で、Pull Request の役割が変わる。**

| 宛て先 | 何の Pull Request か | コードレビュー |
| --- | --- | --- |
| `sprint/<名前>` | 課題の Pull Request。課題の worktree からスプリントブランチへ集める | 通さない。緑になったらスプリントブランチへマージする |
| `sprint/<名前>`（並列の計画のレーンで、課題のまとまりの記録があるスプリント） | まとまりの Pull Request。まとまり 1 つにつき 1 本で、代表の課題の番号のブランチから出す | 通す（そのまとまりの差分だけを範囲にした `cross-review`）。収束してからスプリントブランチへマージする |
| ベースブランチ（ブランチ戦略の設定の `branches.base`） | スプリントの Pull Request（スプリントで 1 本）か、スプリントブランチを経ない単独の Pull Request | 通す（リファクタリング・`cross-review`・完了判定を 1 回）。まとまりの記録があるスプリントの Pull Request は `cross-review` を回さず、リファクタリングと完了判定だけを通す |

課題の Pull Request は `--base sprint/<名前>` で出す。スプリントの Pull Request は、
スプリントブランチの worktree からベースブランチ宛てに出し、本文にスプリントが閉じる課題を番号ごとに書く。
ベースブランチ（ブランチ戦略の設定の `branches.base`）が既定ブランチと同じリポジトリでは、上のベースブランチの行の Pull Request が既定ブランチ宛になる。
そのときの本文の扱いは次のセクションにある。

## closing keywords は本文だけに書く

**closing keywords（`Closes` / `Fixes` / `Resolves` の後に番号を書く記法）は Pull Request の本文だけに、番号ごとに書く。** コミット
メッセージに書くと、マージでそのコミットが指す課題だけが先に閉じ、スプリントが割れる。**本文の closing keywords は
外さない**（`stage-completeness.md` と `progress-tracking` の「スプリントを閉じる」が本文を入力にする）。

**マージ先が既定ブランチの Pull Request では、本文に closing keywords を書かず、`Refs #<番号>` で参照する。** GitHub は
既定ブランチへのマージの時点に closing keywords の課題を閉じ、最終工程より先に閉じてしまう。課題を閉じる時点と参照の形は
`progress-tracking` の「スプリントを閉じる」が SSOT である。

## 設計 Pull Request の本文

**head のブランチ名が `design/` で始まる Pull Request の本文は、決定の中身を持たない**（決定は設計文書だけが持つ）。

**設計 Pull Request の題は、設計文書の H1 と同じ文にして `--title` に渡す。** 題の形は `design` の
[design-template.md の「承認する人が読む欄」](../design/references/design-template.md#承認する人が読む欄) にある。

| セクション | 何を書くか |
| --- | --- |
| `## Summary` | 何を設計したかと、設計文書へのリンク。**解き方と決定を言い換えて書かない**（解き方は設計文書の `## 解き方の全体像`、決定は `## 決定の記録` を読む。`legacy-refactor` の設計文書は `## 目指す姿` を読む） |
| `## Test plan` | 設計の段階で確かめたこと |

**設計文書が 2 本以上あるときは、Summary に設計文書を、ファイル名の最初の課題番号の昇順に並べる。** 根本原因が共通で
複数の課題を 1 本にまとめた設計文書（`issue-<番号1>-<番号2>-design.md`）は、名前の最初の番号で並べる。同じ Pull Request を
何度組んでも同じ順にするためで、承認資料（`development-workflow` の `approval-request.md` の「設計文書が 2 本以上の
設計 Pull Request」）も同じ順で設計文書ごとのまとまりを並べる。

**設計文書の中身（決定の記録・出し直しの指摘への対応）を本文へ写さない。** 写すと設計文書と本文が食い違ったときに
どちらが正か分からなくなる。出し直しの設計では、Summary に設計文書の `## 出し直しの指摘への対応` へのリンクを添える。

## 命名と検証ブランチ

ブランチは英語（github flow）、コミット・PR は日本語（prefix 例: `feat:` / `fix:` / `docs:` / `chore:`）。
検証ブランチ（`qa/*` 等）へは feature ブランチから直接 PR を出さず、`/ndf:cherry-pick-pr <base>` を使う
（マージで main の変更が混ざる）。

## マージに人手の承認が要るか

要否は**マージ先のチャネル**で決まる。Pull Request を出すこと自体に承認は要らない。

| マージ先 | 承認 |
| --- | --- |
| 開発版・検証環境のチャネル | 要らない |
| **本番チャネル** | **要る** |
| head のブランチ名が `design/` で始まる Pull Request | **どのチャネルでも要る**（ドキュメントレビューの承認ゲート） |

**規則は `/ndf:release` が持つ。** 本番チャネルは ブランチ戦略の設定の `branches.production`、
設定が無ければ既定ブランチ。全体像は `/ndf:development-workflow` の「人手の承認を求める承認ゲート」。

## 関連

- `/ndf:cherry-pick-pr` — 環境ブランチへのcherry-pick PR
- `/ndf:deploy` — 環境ブランチへのデプロイPR（ブランチ全体）
- `/ndf:pr-tests` — Test Plan 自動実行
- `/ndf:pr-review` — PR単位レビュー
- `/ndf:merged` — マージ後のブランチ整理 / 現ブランチにベースブランチをマージ
- `/ndf:release` — リリース。**本番系へ届く操作の承認の規則を持つ**
