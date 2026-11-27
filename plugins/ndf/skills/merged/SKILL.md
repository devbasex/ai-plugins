---
name: merged
description: "Delete merged branches and worktrees, stopping only where git refuses, then update the base branch. Use when a PR was merged（マージ後のクリーンアップ・ブランチを整理・worktreeを削除）."
argument-hint: "[PR番号]"
allowed-tools:
  - Bash
  - Read
---

# マージ後クリーンアップコマンド

PR マージ後の後始末をまとめて実行する。対象 PR のブランチ削除に加えて、残っているマージ済みブランチの整理とベースブランチのマージもこの Skill で扱う。

## 用途の切り分け（最初に判定する）

| リクエストの意図 | 実行するセクション |
|---|---|
| PR マージ後のクリーンアップ | 「クリーンアップの手順」→ 必要なら「マージ済みブランチの整理」 |
| マージ済みブランチの整理のみ | 「マージ済みブランチの整理」のみ（クリーンアップの手順は実行しない） |
| 作業中ブランチへ最新のベースブランチをマージする | 「ベースブランチのマージ」のみ（クリーンアップの手順は実行しない） |

「クリーンアップの手順」以外を目的とする場合、対象 PR は未マージであるのが通常のため、
マージの確認（`ndf worktree merged`）を前提条件にしてはならない。「マージ済みブランチの整理」と
「ベースブランチのマージ」はいずれも **単独で実行可能** で、PR のマージ状態に依存しない。

## 止まる条件

**クリーンアップは止めずに通す。** worktree・ローカルブランチ・Pull Request の head のリモート
ブランチの削除は、どれも事後に戻せる。**実行前確認は置かず、消した対象と戻し方を作業完了
報告へ載せる**（要否の基準は
[AUTHORING.md](../AUTHORING.md) の「実行前確認の要否を決める 3 つの質問」）。

**停止理由は git の拒否だけである。** `-D` の代わりになる判定をこの Skill の側に作らない。

| 対象 | 止めずに行う | 止まる（一覧で示し、同意の無い対象を消さない） | 触らない（報告に「対象外」） |
| --- | --- | --- | --- |
| worktree | `git worktree remove <path>` が 0 で終わる。拒まれたら未追跡・無視されたファイルを退避して外す（`ndf worktree merged` が行う） | 追跡ファイルに未コミットの変更が残っている（退避できず `--force` が消すため外さない）・locked の worktree・退避か削除が 0 以外で終わる（未完了。同意の対象にしない） | メインディレクトリ |
| ローカルブランチ | `git branch -d <name>` が 0 で終わる | 同じコマンドが 0 以外で終わる | 起点・本番チャネル・現在のブランチ・選んで集めるリリース方式（ブランチ戦略の設定の `release_strategy: release_branch`）で本番チャネルに含まれていないスプリントブランチ（`kept`。リリースの単位として本番へ届くまで要る） |
| リモートブランチ | マージ済み Pull Request の head で、同じリポジトリにあり、HEAD が `headRefOid` と一致する | — | 起点・本番チャネル・fork の head、HEAD が `headRefOid` と違う、対応する Pull Request が見つからない |
| 課題 | **閉じない**（スプリント課題の OPEN の一覧を報告に載せるだけ） | — | — |

- **起点・本番チャネル・現在のブランチは、同意を求めずに対象から外す。** 尋ねる場面を
  作ること自体が誤りで、1 回の誤答で開発の本流が消える。起点は「ベースブランチを決める」の
  `$dev_base`、本番チャネルはブランチ戦略の設定の `branches.production` である
- **リモートブランチを消すのは、HEAD が Pull Request の `headRefOid` と一致するときだけ。**
  マージの後に積まれたコミットは Restore branch で戻らず、消した後に戻す手段が無い。
  引けない・HEAD が違うときは、同意を得ても戻せないため消さずに対象外として報告する
- **squash / rebase でマージしたブランチは `git branch -d` が拒む**（リモートのブランチが
  消えた後は「起点へマージされていない」と判定されるため）。**`-D` へ落とさない。**
  止まる対象として一覧に載せ、判断の材料（下表）を添える

止まったときの一覧は、`ndf worktree merged` の結果の `items` のうち `kind` が `branch`・`result` が `kept` で、
`reason` が git の拒否（`git がマージ済みと認めない`）の行から作る。行の `name`（ブランチ）・`sha`（HEAD のハッシュ）・
`reason`（git の出力）を並べ、戻し方 `git branch <名前> <ハッシュ>` を添える。

**止まる対象は最後に 1 回だけ示す。** 見つけるたびに止まると、止まる回数が拒否の件数だけ
戻る。同意（`AskUserQuestion` の `metadata.source` は `ndf-ask:redline:C4`）を得た対象だけを `git branch -D` で消し、残りは報告に載せる。**このコマンドを、
この同意の外で実行しない。** worktree の `--force`（`git worktree remove --force`）は使わない。

**「未完了」（worktree を外せなかった・`kept`）は止まる対象と別の結果で、同意の対象に含めない。** `git worktree remove --force` を同意の一覧に載せない。原因を取り除いた後に `/ndf:merged <PR番号>` をもう一度実行する。

## ベースブランチを決める

**マージする先も、マージ済みかを見る先も、開発の起点である。** 既定ブランチとは限らない。
以降の手順はこの値を使う。

```bash
# 起点はブランチ戦略の設定（`.ndf/project.json` の `branch_strategy.branches.base`）。設定が無い・読めないときは、
# 作成のコマンドを出して止まる。既定ブランチへ落とさない
dev_base=$(jq -er '.branch_strategy.branches.base | strings' .ndf/project.json 2>/dev/null) ||
  { echo "ブランチ戦略の設定を読めない。ndf project branch-strategy init で作る" >&2; exit 1; }
# 名前の実在を確かめる。取得済みの参照に無ければ origin へ問い合わせる（取得していないだけの場合を「無い」と読まないため）。
# `git ls-remote` のパターンは参照名の末尾に一致するので、返った行の参照名そのものを検証する
dev_base_found=0
if git show-ref --verify --quiet "refs/remotes/origin/$dev_base" ||
   git show-ref --verify --quiet "refs/heads/$dev_base"; then
  dev_base_found=1
else
  dev_base_listing=$(GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin \
    "refs/heads/$dev_base" 2>/dev/null)
  while IFS= read -r line; do
    case "$line" in *$'\t'"refs/heads/$dev_base") dev_base_found=1; break ;; esac
  done <<<"$dev_base_listing"
fi
[ "$dev_base_found" -eq 1 ] || {
  printf 'NOTE: ブランチ戦略の設定の branches.base が指す %s は origin にもローカルにもありません\n' "$dev_base" >&2
  exit 1
}
```

## クリーンアップの手順

**マージの確認・メインディレクトリのマージ・worktree の削除・ローカルブランチの削除は `ndf worktree merged` が行う。**
引数が無ければ、自身が作成した最新の PR の番号を渡す。

```bash
ndf worktree --cwd <メインディレクトリ> merged <PR番号>...
```

出力は 1 行の JSON（`status` と終了コードの区分は [exit-codes.md](../../docs/contracts/exit-codes.md)、形は
[result.schema.json](../../contracts/schemas/result.schema.json)）。終了コードを見て次を決める。

| 終了コード | 次にすること |
| --- | --- |
| 0 | `items` に git が拒んだブランチ（`kind: branch`・`result: kept`・`reason` が `git がマージ済みと認めない`）があれば、「止まる条件」の一覧を示して同意（`ndf-ask:redline:C4`）を取り、同意を得た対象にだけ `git branch -D <名前>` を打つ。続けて「スプリント課題を報告する」→ 必要なら「マージ済みブランチの整理」→ 作業完了報告 |
| 1（`cause.kind` が `refused`） | メインディレクトリの `git merge --ff-only` が失敗した。`summary` を報告して止まる |
| 3 / 75 | 前提が無い（設定・認証）か、一時的な失敗。`summary` を報告して止まる（75 は時間をおいて打ち直す） |

- `items[]` の `kind` は `pr` / `main_dir` / `worktree` / `branch`、`result` は `removed`（worktree を外した）/ `deleted`（ブランチを消した。`restore` が戻し方）/ `absent`（ローカルに無い。削除済みとして扱う）/ `kept`（MERGED でない・メインディレクトリが checkout している・スプリントブランチを残す など。`reason` を報告へ載せる）/ `stopped` / `pulled` を取る
- **Pull Request の head のリモートブランチは消さない。** 残っていれば「マージ済みブランチの整理」の手順 4 の条件で消す
- worktree の未追跡・無視されたファイルは `<共通の git ディレクトリ>/ndf/worktree-trash/` へ退避してから外す。退避先は `reason` に載る（`退避先 <パス>`。退避するものが残らなければ `退避するものは無かった` で、退避先を作らない）
- 作り直せる生成物（パスのどこかに `.venv`・`node_modules`・`__pycache__`・`.pytest_cache`・`.mypy_cache`・`.ruff_cache` のディレクトリがあるもの、同じ階層に `Cargo.toml`・`pom.xml`・`dbt_project.yml` がある `target` のディレクトリ）は退避せずに捨て、`reason` の `（捨てた: <相対パス>, …）` に載る。設定が隣に無い `target` は作り直せると決められないため通常の退避へ回す
- **退避先は、そのブランチを含むバージョンが本番へ出たときに `release` のクリーンアップが回収の候補として挙げ、人の承認（`ndf-ask:redline:C4`）を得てから消す**（`ndf worktree trash --ref <本番に出たコミット>` が候補を挙げ、承認の後に `--yes --only <承認した退避先>...` を付けて、その名前の退避先だけを消す。候補を挙げた後に増えた退避先は消さない）。退避先は Git に無い利用者のファイルを含み消すと戻せないため、本番の承認だけでは消さない（共通原則の C3・C4）。それまでは残す。退避先とブランチの対応は退避先の隣のジャーナル `<退避先>.json`（`branch`・`head`・`merge_commit`・`discarded`）が持ち、`head` か `merge_commit` が本番のコミットに含まれれば候補になる。ジャーナルの無い退避先は消さずに件数だけを報告する
- 追跡ファイルの未コミットの変更（modified / staged / 削除）は退避の対象でなく `--force` が消すため、残っていれば外さず `kept` にする（`reason` に件数とファイル名）。コミットするか捨ててから、もう一度実行する
- worktree を外す直前に開いた公開を閉じ、テスト環境を破棄する（ボリュームも既定で消す。`.ndf/worktree.json` の `testenv.discard_volumes` が `false` なら残す）。公開を閉じられなければ `kept` で、`reason` に環境名・URL・打ち直しのコマンドが載る。破棄できなければ worktree は外れ、`reason` に環境名が載る
- 片付けの最後に、worktree の消えた割り当てのテスト環境を回収する（全体で 300 秒まで。ボリュームは上の破棄と同じ設定に従う）。結果は `swept` が `true` の行で `items` に載る（`result` は `reclaimed` / `released` / `failed` / `foreign` / `exposed` / `deferred` / `unseen`）。回収の失敗で `status` と終了コードは変わらない。結果の読み方は `worktree` の `references/test-execution.md` の「クリーンアップ」にある
- 外す worktree は、PR の head のブランチを checkout したものだけである。レビュー worktree（`cross-review` / `cross-refactoring` が使う一時の worktree）はこのコマンドの対象に入らない
- 読み手役に読ませた承認資料のコピー（NDF の状態の保存先の `reading-materials/`）は、同じリポジトリの閉じた PR のものを消す（引数で渡していない PR も対象）。渡していない PR の状態は、GitHub の呼び出しを増やさない範囲で確かめる。確かめられなかった PR のコピーは残し、次のクリーンアップで確かめる。消したコピーは `items` に載らない
- 片付けの後に、PR が閉じる課題の本文へ工程「クリーンアップ」の記録を付けるときは `ndf records cleanup-stage <PR番号>...` を打つ（記録済みの課題は打ち直さない）

### マージから行うとき

**マージの承認を得た後に限り、** CI の待ちからクリーンアップまでを続けて行える。承認ゲート
（`development-workflow` の Pull Request のマージの承認。`AskUserQuestion` の `metadata.source` は設計 Pull Request なら `ndf-gate:1`、
本番チャネルへのマージなら `ndf-gate:2`）はこのコマンドの前に置き、これらの
コマンドを承認の代わりにしない。

`ndf` のコマンドは 1 回の Bash の呼び出しで 1 つずつ打つ（2 つ以上をつなぐと hook が止める）。

```bash
# 1. CI の必須のチェックの結論が出るまで待つ（リポジトリの中で打つ）。必須のチェックは
#    .ndf/project.json の ci.required_checks の名前を 1 つずつ --required で渡す
ndf ci watch --pr <PR番号> --required <チェック名> [--required <チェック名>]... --budget 3600
# 2. マージする（PR の worktree の中で打つ。作業場所の HEAD が PR の head と違えば断る）
ndf ci merge --branch <PR の head のブランチ> [--method merge|squash|rebase]
# 3. クリーンアップ（上の「クリーンアップの手順」）
ndf worktree --cwd <メインディレクトリ> merged <PR番号>
```

- **マージ先が自動反映の本番チャネル（判定を決められないマージ先を含む）なら、`ndf-gate:2` の承認を得るまで手順 2 を打たない。**
  条件は `development-workflow` の「本番系へ届く操作」
- `ndf ci watch` が `test_failed`・`check_failed`（1）で止まったら、マージせずに `items[].name`（落ちたチェック）を報告する。
  上限の時間を過ぎた（`ci_pending`）ら、同じコマンドで打ち直す
- `ndf ci merge` は PR の本文に決めていない「マージの前に決める」残リスクの行（`- [ ]`）が残れば `refused`（1）で止まる。
  基底がマージキューを持てば PR をキューへ入れて `ci_pending`（1）で返す。キューがマージするまで
  `gh pr view <PR番号> --json state -q .state` が `MERGED` になるのを待ってから手順 3 へ進む
- **管理者の迂回（`gh pr merge --admin`）は使わない。** 必須のチェックやレビューが足りなければ GitHub が拒み、`not_ci`（1）で止まる
- 待ちが遅れたときは `gh pr checks <PR番号>` と `gh run view <run> --json jobs` で見る。実行が `completed` なのにチェックが
  pending のまま残るジョブは、`gh run rerun <run> --job <job>` で **1 度だけ**再実行する。再実行したジョブが再び残れば止まって報告する。
  ランナーを待っている（`queued`）・実行中のジョブは待つ

## スプリント課題を報告する

**この Skill は課題を閉じない。** スプリント課題が閉じるのは、スプリントの最終工程を
通った時点である（手順は `progress-tracking` の「スプリントを閉じる」が持つ）。**マージした
時点で閉じると、リリースで問題が出ても課題の一覧に出ない。**

クリーンアップで行うのは、**スプリント課題のうち OPEN のものをメモして報告へ載せること**だけである。

```bash
# 1. 本文から closing keywords と参照（Refs）の指し先を取り出す（当たりが無ければ grep が 1 で終わる）
gh pr view <PR番号> --json body -q .body | grep -oiE '(close[sd]?|fix(e[sd])?|resolve[sd]?|refs?):? *(https://github\.com/[^ ]+/issues/[0-9]+|([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[0-9]+)'

# 2. 指し先ごとに、まだ OPEN のものだけに絞り、報告へ載せる（他のリポジトリの指し先は --repo を付ける）
gh issue view <番号> [--repo <所有者>/<リポジトリ>] --json state -q .state    # OPEN / CLOSED
```

- GitHub の closing keywords（PR 本文に `Closes #123` のように書くと、マージ時にその課題を自動で閉じる機能）も参照（`Refs #N`。`progress-tracking` の「スプリントを閉じる」）も 1 つも無ければ何もしない
- 指し先が課題の URL のときは、URL の `<所有者>/<リポジトリ>` と番号を `gh issue view` に渡す
- 既定ブランチへマージしたリポジトリでは GitHub が先に閉じている。その場合はこの一覧が
  空になるだけで、扱いは変わらない

## マージ済みブランチの整理

残存するマージ済みブランチをまとめて削除する。**単独で実行可能**なセクションであり、
「クリーンアップの手順」（PR のマージ確認）を前提にしない。
OPEN な PR が残っている状態でも、ブランチ整理だけを目的に実行してよい。

```bash
# 起点はローカルに無いことがある。このセクションは単独でも実行するため、ここで取得して
# からリモート追跡ブランチを判定の先にする
git fetch origin "$dev_base"             # 1. 起点を取得
git branch --merged "origin/$dev_base"   # 2. マージ済みブランチを列挙
git branch -d <branch>                   # 3. ローカル削除（拒まれたら止まる対象へ積む）
# 4. リモートにも残っていれば、対応する Pull Request を引いて HEAD を突き合わせる
gh pr list --state merged --head <branch> --json number,headRefOid,headRepositoryOwner
git ls-remote origin "refs/heads/<branch>"
git push origin --delete <branch>        # HEAD が headRefOid と一致したときだけ
```

- **ベースブランチ・本番チャネル・現在のブランチは必ず除外する**（同意を求めずに外す）
- 手順 3 は「止まる条件」の表のとおり、0 で終われば消し、拒まれたら止まる対象へ積む
- **手順 4 で消すのは、同じリポジトリにあるマージ済み Pull Request の head で、HEAD が
  `headRefOid` と一致するものだけである。** Pull Request を引けないブランチと、HEAD が違う
  ブランチは消さずに対象外として報告する。マージの後に積まれたコミットは Restore branch で
  戻らず、消した後に戻す手段が無いためである。**同意を得ても消さない**（戻せないため、
  同意を求める一覧にも載せない）。対象外にした行には、HEAD のハッシュと `headRefOid`、
  マージ後に積まれたコミット（`git log --oneline <headRefOid>..origin/<名前>`）を添える
- fork の head（`headRepositoryOwner` がこのリポジトリの所有者と違う）も対象外である

## ベースブランチのマージ

作業中のブランチへ最新のベースブランチをマージする場合はこちらを使う。PR のマージ有無は前提条件にしない。

1. **ブランチ確認**: `git branch --show-current`。ベースブランチ自身なら `git pull` のみ実行して終了
2. **worktree の確認**: 未コミット変更があれば `git stash` で退避
3. **最新取得**: `git fetch origin "$dev_base"`
4. **マージ実行**: `git merge "origin/$dev_base" --no-edit`
   - コンフリクト時は `git diff --name-only --diff-filter=U` で一覧を表示し、AI が解消して作業を続け、解消の内容（ファイルと採った側）を報告に書く
5. **後処理**: stash していれば `git stash pop`。コンフリクトがなければ `git push` で反映し、マージ済みコミット数と変更ファイル数を報告

## 通過工程を報告に載せる

**記憶から書かない。** 工程を通ったかどうかは会話の中にしか残らず、セッションが変わると
引き継がれない。ステートマシンの出力をそのまま完了報告へ貼る。

```bash
ndf progress show <ステートマシンの ID>   # ID は ndf progress new の結果にある
```

- 記録が無ければ 1 行だけ返る。**すべての工程を欠落として並べない**
- 記録の無い必須の工程があれば、その名前と、記録するコマンドが出力に載る
- 実施済みであれば記録してから先へ進む。実施していなければ、その工程へ戻る
- 通過記録の読み方と、記録が無いときの扱いは
  [references/stage-completeness.md](../development-workflow/references/stage-completeness.md) にある

## 作業完了報告（必須）

**止めずに行った削除は、すべてここに戻し方つきで残す。** 報告に載らない自動の削除を作らない。

| 項目 | 形 |
| --- | --- |
| 消したローカルブランチ | `<名前>`（`<削除時のハッシュ>`）— 戻すなら `git branch <名前> <ハッシュ>` |
| 消したリモートブランチ | `origin/<名前>` — 戻すなら `<Pull Request の URL>` の Restore branch |
| 消した worktree | `<パス>`。無視されたファイルを退避したなら、退避先のパスと容量（`du -sh <退避先>`）と捨てた生成物 — 戻すなら `mv <退避先>/<退避した相対パス> <worktree を作り直した先>/<退避した相対パス>`、まとめて戻すなら `cp -a <退避先>/. <worktree を作り直した先>/` |
| 対象外 | 名前と理由（起点 / 本番チャネル / 現在のブランチ / fork / HEAD が `headRefOid` と違う / 対応する Pull Request が無い）。HEAD が違う・Pull Request が無いリモートブランチには、HEAD のハッシュと `headRefOid`、マージ後に積まれたコミット（`git log --oneline <headRefOid>..origin/<名前>`）を添える |
| 止まった対象 | 「止まる条件」の「止まったときの一覧」と、同意の結果 |
| 未完了 | 退避が失敗した worktreeのパス、退避先・退避済みのパス・失敗したパスと `mv` の出力（退避済みのものは退避先に残す。戻すなら同じ `mv` の逆）、「原因を取り除いた後に `/ndf:merged <PR番号>` をもう一度実行する」 |
| スプリント課題 | このマージの closing keywords（`Closes #N` などの記法）と参照（`Refs #N`）が指す課題のうち OPEN のもの。「閉じるのはスプリントの最終工程（`progress-tracking` の「スプリントを閉じる」）」と添える |
| スプリントの最後か | 最後 / 残りあり / **判断できない** |

あわせて次も載せる。

- 実行サマリー（PR タイトル、マージコミット、現在のブランチ）
- **通過工程の報告**（前節の `ndf progress show` の結果の工程の並びをそのまま貼る）
- ベースブランチの状態
- 復元していない stash が残っている場合はその旨と `git stash list` の該当エントリ
- PR URL

**スプリントの最後かを判断できなくても、運用者の回答を待たない。** 判断できないことを報告へ
書いて終わる。この Skill はバージョンを上げないため、ここで待っても得るものが無い。スプリントの範囲の
確認は `release` の開始条件（スプリントのすべての Pull Request がマージされている）が持つ。

## 次の工程

**クリーンアップが済んでも、変更は利用者へ届いていない。** マージはマージであり、利用者へ届けるのはリリースである。

スプリント（1 度にマージする Pull Request の集合）の最後のマージを行った場合は、続けて
`/ndf:release` でバージョンを上げる。まだ残りがある場合は、最後のマージを行う側へ渡す。単独の変更では
スプリントが 1 件になるため、このクリーンアップの直後がリリースの時期にあたる。

**スプリントの範囲が分からないときは、判断できないと報告して終わる。** どの Pull Request まで
含むかは、この Skill が読み取れる情報の外にあることがある。**ここで待たない。** バージョンを上げるのは
`release` であり、確認が要るのはリリースを始める時点である。待つと、リリースをしないクリーンアップでも止まる。

この Skill はバージョンを上げない。担い手と時期は `release` が持つ。

**このマージに人手の承認が要ったかどうかは、届く先が本番系かどうかで決まる。** 開発版や
検証環境のチャネルへ入れるマージは取り消せるため、承認を求めない。**本番系へ届く操作
だけが承認ゲートである**（設計 Pull Request のマージは、どのチャネルでも承認が要る別の承認ゲートである）。

**本番系へ届く操作は 3 つの形を取る。** リリース（`release`）、運用モードの実行
（`operation` の実装）、自動反映の本番チャネルへの Pull Request のマージである。リリースの規則は `/ndf:release` が持ち、
最後のものは承認ゲート（`ndf-gate:2`）を得るまで `ndf ci merge` を打たないことで止める（上の「マージから行うとき」）。
どのブランチが本番チャネルかはブランチ戦略の設定の `branches.production` が決める（設定が無ければ止まる）。

## 関連

- `/ndf:release` — この工程の後に行うリリース（バージョンを上げて公開する）
- `/ndf:cherry-pick-pr` — 環境ブランチへの cherry-pick PR 作成と、複数ブランチへ同じ修正を適用する原則
