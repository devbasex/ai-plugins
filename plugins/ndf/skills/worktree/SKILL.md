---
name: worktree
description: "Prepare a git worktree under .worktrees/ and move stray changes out of the cloned directory. Use when development work starts（worktreeを用意・worktreeで作業・メインディレクトリの変更を移す）."
allowed-tools:
  - Bash
  - Read
  - Edit
---

# worktree で開発する

開発の変更は、git の worktree の中で行い、リポジトリを clone したディレクトリには加えない。
並行して複数の作業を進めても、互いの変更が同じ作業ディレクトリで混ざらない状態を保つ。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| メインディレクトリ | リポジトリを clone したディレクトリ。`git rev-parse --git-common-dir` の親にあたる |
| worktree | `git worktree add` で作った作業用ディレクトリ。ここでは `.worktrees/` 配下に置くものを指す |
| 開発 worktree | 人が変更を加えて Pull Request にするもの。ブランチを持つ |
| レビュー worktree | `cross-review` と `cross-refactoring` が一時的に使うもの。システムの一時ディレクトリに置く |

この Skill が扱うのは開発 worktree だけである。レビュー worktree は
それぞれの Skill が作って捨てるため、置く場所もクリーンアップの主体も異なる。

## メインディレクトリを編集してよい場合

原則の対象は開発の変更である。次のパスはメインディレクトリで編集してよい。

| パス | 扱う内容 |
| --- | --- |
| `issues/` | 計画と仕様の草案 |
| `docs/` | リポジトリ知識 |
| `.claude/` `.codex/` `.kiro/` `.agents/` | 各ランタイムの設定 |
| `.serena/` | コードインテリジェンスの設定と索引 |
| `.ndf/` | この仕組みの設定ファイル |
| `.gitignore` | worktree の登録そのものに必要 |

リポジトリ側で `.ndf/worktree.json` の `guard.allow_paths` を書けば、この一覧を差し替えられる。

メインディレクトリの編集は拒否しない。編集の直前に案内が出て、セッション開始時に残った変更が
提示される。案内が出ても操作は成立するため、意図した編集であればそのまま続けてよい。

## 0. 設定ファイルを用意する

**この Skill を起動したら、まずこれを実行する。**

```bash
command -v ndf >/dev/null 2>&1 || { echo "ndf が PATH に無い: プラグインを有効にするか、bin/ndf へのシンボリックリンクを PATH に置く" >&2; exit 3; }
ndf worktree init   # 終了コードを見る（0 作った・既にある / 1 読めない設定）
```

`ndf` の有無の確かめ方は [scripts-lookup.md](../development-workflow/references/scripts-lookup.md) にある。

worktree 運用の仕組みは、リポジトリ側に `.ndf/worktree.json` があるときだけ動く。
無ければ hook もコマンドも何も出力せず終了コード 0 で終わる。**このコマンドがその
設定を作る。**

**終了コードが 0 でなければ先へ進まない。** `init` の結果の `summary` をそのまま利用者に示し、手順 1 へ
進まずに止まる。設定が読めない（JSON として壊れている・`version` が未対応）ときは、`refused`（1）で止まり、
`summary` に読めないファイルと理由が出る。**設定を直す・消す・`init --force` で作り直す
のは利用者が決める。** エージェントは設定を書き換えない。

- 読める設定が既にあれば**上書きしない**。書き加えた内容は消えない。読めない設定では
  1 で終わる（「既にあります」とは報告しない）
- 作った直後から、メインディレクトリの編集時の案内と逸脱検知が動く。メインディレクトリの
  ブランチは既定では動かさない（追従させるなら `follow_branch: true` を足す。「メイン
  ディレクトリのブランチ」）
- **作った設定ファイルはコミットする。** リポジトリの設定であり、他の開発者にも同じ
  運用が要る

**導入の状態は `ndf worktree check` で見る。** ファイルを作らない。結果の `items` の `ndf_settings` が設定の状態
（`present` あり / `absent` なし / `not_readable` 読めない）を、`base`・`production`・`sprint_start` がブランチ戦略の設定から読んだ
ブランチを出す。設定が読めないか、ブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）が無いときは `precondition`（3）で止まる。
稼働中の worktree は `ndf worktree list` が並べる。

ローカル環境での動作検証やテスト実行の分離を使うときは `localenv` / `testenv` を足す。
書き方は [references/ndf-settings.md](references/ndf-settings.md) にある。

**機械ごとに違う値は共有設定へ書かない。** ポートの帯・持ち込み物・追従の有無は、
コミットしない `.ndf/worktree.local.json` へ書く（`init` が `.ndf/.gitignore` で追跡から
外す）。上書きできる項目と重ね合わせの規則は
[references/ndf-settings.md](references/ndf-settings.md) の「機械ごとに違う値は個人設定へ
書く」にある。

## 1. 現在地を確かめる

worktree の中では `git rev-parse --show-toplevel` が worktree 自身を返す。メインディレクトリを
指すには共通の git ディレクトリの親を使う。

```bash
main_dir=$(dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd -P)")   # メインディレクトリの絶対パス
[ "$(git rev-parse --path-format=absolute --git-dir)" != "$(git rev-parse --path-format=absolute --git-common-dir)" ] \
  && echo "worktreeの中" || echo "メインディレクトリ"
```

`$main_dir` はシェルの変数で、Bash の呼び出しをまたぐと消える。**以降の手順の `$main_dir` は、ここで出た絶対パスをそのまま書く。**

**`ndf` のコマンドは 1 回の Bash の呼び出しで 1 つだけ打つ。** 2 つ以上をつなぐと hook が止める。以降のブロックに並ぶ `ndf` の行は 1 行ずつ打つ。

**既に worktree の中にいるなら、ここで終わる。** 入れ子の worktree は作らない。

## 2. worktree を用意する

### 2-1. `superpowers:using-git-worktrees` があれば委譲する

外部 Skill が導入されているときは、作成の手順をそちらへ渡す。置く場所の既定は
どちらも `.worktrees/` で一致するため、結果は変わらない。

導入の有無は、利用できる Skill の一覧に `superpowers:using-git-worktrees` が
含まれるかで判定する。含まれていれば、その Skill を起動して作成を任せ、
戻ってきたら作られた worktree で依存の用意（[依存の用意](#依存の用意)）を打ってから
手順 3 へ進む。含まれていなければ 2-2 へ進む。

```bash
ndf worktree deps "<作られた worktree のパス>"
```

### 2-2. `.worktrees/` の登録を確かめる

登録しないまま worktree を作ると、worktree の中身が追跡対象に入る。**作成より先に
登録する。**

```bash
if ! git -C "$main_dir" check-ignore -q .worktrees/ 2>/dev/null; then
  printf '\n# 開発用のworktree\n.worktrees/\n' >> "$main_dir/.gitignore"
  git -C "$main_dir" add .gitignore
  git -C "$main_dir" commit -m "Chore: .worktrees/ を追跡対象から外す"
fi
```

**末尾の `/` を省かない。** `.gitignore` の `.worktrees/` はディレクトリだけに当たる
記法で、まだディレクトリが無い状態で `check-ignore .worktrees` を実行すると、登録済み
でも「登録されていない」と判定される（実測：ディレクトリ不在時、スラッシュなしは 1、
スラッシュありは 0）。

### 2-3. 作成して移る

置く場所はブランチ名をそのまま使う。`feature/foo` は `.worktrees/feature/foo` になり、
ブランチ名から場所が一意に決まる。

```bash
branch="feature/<name>"
ndf worktree --cwd "$main_dir" create "$branch"           # 結果の items[0].path が worktree のパス
ndf worktree --cwd "$main_dir" deps "$main_dir/.worktrees/$branch"
cd "$main_dir/.worktrees/$branch"
```

ブランチが既にあれば、`create` はそのブランチを checkout した worktree を作る（新しいブランチを切らない）。

**スプリントの中では、課題の worktree をスプリントブランチから切る。** スプリント
ブランチ（`sprint/<名前>`）そのものはスプリントの起点（ブランチ戦略の設定の `sprint.start` が指すブランチ）から切り、
課題の worktree は `--from` にスプリントブランチを渡す。設定は変えない。

```bash
# スプリントブランチ。起点はスプリントの起点
ndf worktree create sprint/<名前>
# 課題の worktree。起点はスプリントブランチ
ndf worktree create feat/issue-<番号>-<名前> --from sprint/<名前>
```

`create` は `origin` を 1 回取得してから作り、結果の `items` に worktree のパス（`path`）と
起点（`base`・`start_ref`）を出す。`.worktrees/` が `.gitignore` に無い・保存先が既にある・起点が origin にも
ローカルにも無いときは作らずに止まる。依存の用意は行わないため、続けて `ndf worktree deps <worktree>` を打つ
（[依存の用意](#依存の用意)）。

**既定ブランチと開発の起点は別物である。** 既定ブランチに正式版を置き、開発の本流を
別のブランチ（ベースブランチ）に置くリポジトリでは、既定ブランチから分岐すると開発中の
変更が正式版から枝分かれする。起点はブランチ戦略の設定（[references/ndf-settings.md](references/ndf-settings.md)
の「ブランチ名とブランチ戦略」）から取り、設定が無ければ理由を出して止まる。既定ブランチへは落ちない。

設定した名前が origin にもローカルにも無いときは、`create` が名前を挙げて止まる。
既定ブランチへは落ちない。`create` は作る前に `git fetch origin` を打つため、起点を移した直後の作業
ディレクトリでも、取得していないだけのブランチを「無い」とは読まない。

Claude Code の worktree 作成ツールは新規作成先が固定されているため、`.worktrees/` を
使う場合は `ndf worktree create` で作成してからパスを指定して入る。既存の worktree へ
入る場合はパス指定が通る（`git worktree list` に載っていることが条件）。

## 3. メインディレクトリに残った変更を移す

セッション開始時に未コミットの変更が提示されたときの手順である。**メインディレクトリで
コミットしない。**

```bash
target="$main_dir/.worktrees/<ブランチ名>"

# 1. 移す対象を確かめる
git -C "$main_dir" status --short

# 2. 差分を取り出す（追跡対象。`git add` 済みの変更も含める）
git -C "$main_dir" diff HEAD > /tmp/ndf-stray.patch

# 3. worktree へ当てる（追跡対象の変更が無ければパッチは空になる）
if [ -s /tmp/ndf-stray.patch ]; then
  git -C "$target" apply /tmp/ndf-stray.patch
fi

# 4. worktree 側に変更が載ったことを確かめる
git -C "$target" status --short
```

手順 2 で `git diff` ではなく `git diff HEAD` を使う。`git diff` は `git add` 済みの
変更を差分に含めないため、マージ済みの変更があるとパッチが空になる。手順 3 で
中身の有無を見るのは、変更が未追跡ファイルだけのときにパッチが空になり、`git apply`
が入力なしとして失敗するためである。

**次の手順 5 はメインディレクトリの変更を捨てる。** 手順 4 の出力を見て、移したかった変更が
worktree 側に載っていることを確かめてから実行する。**手順 1〜4 とまとめて実行しない。**
手順 3 が失敗していた場合、確かめずに進むと変更が失われる。

```bash
# 5. メインディレクトリ側を元へ戻す（手順 4 を確認した後に実行する）
git -C "$main_dir" reset --hard HEAD
```

`git checkout -- .` ではなく `git reset --hard HEAD` を使うのは、前者が `git add` 済みの
変更を戻さないためである。

追跡されていないファイルは差分に含まれない。`git -C "$main_dir" status --short` の
`??` 行を見て、必要なものを worktree へ複製してからメインディレクトリ側を削除する。

移送の後、両側の状態を確認する。メインディレクトリが元へ戻り、worktree に差分が
載っていることを `git status --short` で見る。

## 4. worktree を一覧する

```bash
ndf worktree list   # 開発 worktree（`.worktrees/` 配下）だけを並べる
```

`git -C "$main_dir" worktree list` に出て `.worktrees/` 配下に無いものは、レビュー用の一時的な worktree である。この Skill の
対象ではない。

Pull Request がマージされた後の削除は `/ndf:merged` が行う。

## メインディレクトリのブランチ

**セッション開始 hook は、既定ではメインディレクトリの HEAD を動かさない。** 並列に動く
エージェントのどれが開始・再開しても、他の担当が読んでいるメインディレクトリの内容が
入れ替わらないためである。

稼働中の worktree の内容をメインディレクトリでも見たいときは、設定に `follow_branch: true` を
書く（[references/ndf-settings.md](references/ndf-settings.md)）。このときメインディレクトリの
ブランチは、稼働中の開発用 worktree へセッション開始時に追従する。同じブランチを 2 つの
作業ディレクトリへ checkout できないため、追従は detached HEAD で行う。detached HEAD では
コミットしてもブランチが動かないため、メインディレクトリで加えた変更が worktree のブランチへ
混ざることもない。

| 稼働中の開発用 worktree | メインディレクトリ（`follow_branch: true` のとき） |
| --- | --- |
| 1 つ | そのブランチが指すコミットを detached HEAD で開く |
| 0 個または複数 | ベースブランチに合わせる（設定が無ければ既定ブランチ） |

メインディレクトリに未コミットの変更があるときは追従せず、変更がある事実だけを伝える。

## `EnterWorktree` との付き合い方

**NDF の手順は `EnterWorktree` と Agent の `isolation: "worktree"` を使わない。** 隔離したセッションでは、
Claude Code 本体が Bash を走らせる直前に「git がメインディレクトリを触らない」と示せないコマンドを拒む
（`Refusing to run it`）。判定は権限モードに関係なく働き、バージョンで変わる。

| 状況 | どこで何を打つか |
| --- | --- |
| NDF の手順で worktree に入る | `ndf worktree create` の後に `cd <worktree>` する（手順 2-3） |
| 隔離の中でコマンドが拒まれた（`git -C <メインディレクトリ>` など） | `ExitWorktree` で隔離を外し、`cd <worktree>` してから同じコマンドを打つ |

2.1.283 で確かめた。`git -C <メインディレクトリ>` は拒まれ、`ExitWorktree` の後は拒まれた行がそのまま通った。

## 依存の用意

worktree は追跡されているファイルしか持たないため、`vendor/`・`node_modules/`・`.env` が無く、pre-commit や
テストが落ちる。設定（`.ndf/worktree.json`）の `deps` に用意の仕方を書くと、NDF が worktree を作るとき
（3 層の run と queue・cross-review・cross-refactoring・落ちたテストの見分け）と、`ndf worktree deps` を打ったときに
メインディレクトリから依存物を用意する。書き方は [references/ndf-settings.md](references/ndf-settings.md) の `deps` にある。

```json
{ "version": 1, "deps": { "copy_from_main": ["vendor"], "copy_as_real": [".env"] } }
```

結果の `summary` に `依存の用意: 済み（2 件・1 秒）` が出れば用意できている。失敗すると、どの手順が失敗したかと
出力の末尾を `summary` に出して 0 以外で終わる。**worktree は消さない。** 直してから手で打ち直す。

```bash
ndf worktree deps "$WT"                  # マーカーを見ずにやり直す
ndf worktree deps "$WT" --if-unprepared  # 用意が済んでいなければ用意する
```

用意が済むと worktree ごとの git の管理ディレクトリにマーカー（`ndf-deps`）を書く。マーカーは用意した時の HEAD と設定の
中身を持つ。使い回す worktree は、HEAD も設定も変わっていなければやり直さない（別の commit へ同期した後はやり直す）。
このやり直しでは、既にある `copy_from_main` / `copy_as_real` の複製先はメインディレクトリと食い違っていてもそのまま使い、
無い複製先の複製と `run` だけを走らせる（`run` が入れ替えた依存物や、メインディレクトリ側の更新で止めない）。
コミットしていない lock ファイルの変更などで用意し直すときは、`--if-unprepared` を付けずに打つ。

**`copy_from_main` に置くのは、worktree の中でその場で書き換えないパスだけにする。** ハードリンクで複製するため、
その場で書き換えるとメインディレクトリの同じファイルも変わる。書き換えるもの（`.env`・自動読み込みの生成物）は
`copy_as_real` に置く。symlink は張らない（PHP の `__DIR__` などがメインディレクトリを指し、メインディレクトリの
コードでテストが走る）。

## ローカル環境での動作検証

画面を触って動作を確かめるサービス一式を持つリポジトリでは、worktree を作っただけでは
動かない。worktree は追跡されているファイルしか持たないため、依存物も環境ファイルも
無い。手順は [references/local-environment.md](references/local-environment.md) にある。

```bash
WT="$main_dir/.worktrees/<ブランチ名>"
ndf worktree localenv setup "$WT"         # 設定と依存物を持ち込む
ndf worktree localenv mode "$WT"          # metrics.result が share（相乗り）か isolate（分離）
ndf worktree localenv aim "$WT"           # ローカル環境が指すコードを向ける
ndf worktree localenv verify "$WT"; echo $?  # 0 一致 / 1 不一致 / 3 未起動
```

**対象の worktree を引数で渡す。** 省略すると現在地が対象になるため、メインディレクトリから
実行するとメインディレクトリを検証してしまう。

**検証の直前に `verify` を通す。** ローカル環境に載っているコードが対象と違っていても、失敗と
しては現れない。別のコードを検証したことに気づけないまま進む。

リポジトリごとの差は `.ndf/worktree.json` が持つ。書き方は
[references/ndf-settings.md](references/ndf-settings.md) にある。**この設定が無い
リポジトリでは、これらのコマンドは `summary` に適用外と出して終了コード 0 で終わる。**

## テスト実行の分離

同じ保存先を使うテストは、同時に走らせると互いのデータを壊す。worktree ごとにテスト環境を
立てて分ける。手順は [references/test-execution.md](references/test-execution.md) にある。

```bash
ndf worktree testenv env "$WT"                   # 環境名・スロット・ポートを採番し worktree レジストリへ記録
ndf worktree testenv tag "$WT"                   # 基準のタグ（metrics.tag）
ndf worktree testenv bake --tag "<タグ>"         # 基準を作る（内容が同じなら焼き直さない）
ndf worktree testenv up "$WT" --profile core     # 起動する
ndf worktree testenv test "$WT" --kind stateful  # テストが落ちれば test_failed（1）。終了コードは metrics.exit
ndf worktree testenv down "$WT"                  # 破棄し、割り当てを解放する。ボリュームも消す（消すのは既定で、戻せない）
```

**`testenv test` は止まっているテスト環境を起動しない。** 種類の設定に `service` があれば、そのコンテナが worktree を
見ているかを先に確かめ、届かなければテストを走らせずに `precondition`（3）で止まる。先に `up "$WT"` で起動してから打ち直す。
`/ndf:merged` のクリーンアップは worktree を外す前に破棄する。
手で worktree を消すときは先に `down "$WT"` を打つ。worktree が消えた割り当ては、クリーンアップとスロットの枯渇のときに
NDF が回収する（`reclaim`）。
ボリュームを残すのは `.ndf/worktree.json` の `testenv.discard_volumes` に `false` を定義したときだけで、設定より優先して
消すときは `--volumes` を付ける。データを残して止めるのは `stop "$WT"` である。起動方・破棄の担い手・回収の結果の読み方・
残ったボリュームの消し方も `references/test-execution.md` にある。

worktree レジストリは共通の git ディレクトリ配下（`.git/ndf/worktree-registry.json`）に置く。worktree の
中に置くと、削除した時点で割り当ての記録が消える。**解放しても行は消さず、解放の時刻を
書き込む。**

## 関連

- `/ndf:merged` — マージ後の worktree とブランチの削除
- `/ndf:pr` — worktree からの Pull Request 作成
- `/ndf:issue-plan-strategy` — 複数 Pull Request での並行開発
