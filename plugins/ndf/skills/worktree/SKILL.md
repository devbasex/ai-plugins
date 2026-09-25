---
name: worktree
description: "Prepare a git worktree under .worktrees/ and move stray changes out of the cloned directory. Use when development work starts（worktreeを用意・worktreeで作業・メインディレクトリの変更を移す）."
allowed-tools:
  - Bash
  - Read
  - Edit
---

# worktree で開発する

開発の変更は、リポジトリを clone したディレクトリではなく git の worktree の中で行う。
並行して複数の作業を進めても、互いの変更が同じ作業ディレクトリで混ざらない状態を保つ。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| メインディレクトリ | リポジトリを clone したディレクトリ。`git rev-parse --git-common-dir` の親にあたる |
| worktree | `git worktree add` で作った作業用ディレクトリ。ここでは `.worktrees/` 配下に置くものを指す |
| 開発用の worktree | 人が変更を加えて Pull Request にするもの。ブランチを持つ |
| レビュー用の worktree | `cross-review` と `cross-refactoring` が一時的に使うもの。システムの一時ディレクトリに置く |

この Skill が扱うのは開発用の worktree だけである。レビュー用の worktree は
それぞれの Skill が作って捨てるため、置き場所も後片付けの主体も異なる。

## メインディレクトリを編集してよい場合

原則の対象は開発の変更である。次のパスはメインディレクトリで編集してよい。

| パス | 扱う内容 |
| --- | --- |
| `issues/` | 計画と仕様の草案 |
| `docs/` | リポジトリ知識 |
| `.claude/` `.codex/` `.kiro/` `.agents/` | 各ランタイムの設定 |
| `.serena/` | コードインテリジェンスの設定と索引 |
| `.ndf/` | この仕組みの宣言ファイル |
| `.gitignore` | worktree の登録そのものに必要 |

リポジトリ側で `.ndf/worktree.json` の `guard.allow_paths` を書けば、この一覧を差し替えられる。

メインディレクトリの編集は拒否しない。編集の直前に案内が出て、セッション開始時に残った変更が
提示される。案内が出ても操作は成立するため、意図した編集であればそのまま続けてよい。

## この文書が受け取る値

| 変数 | 値 | 決め方 |
| --- | --- | --- |
| `$SCRIPTS` | プラグインの `scripts/` の絶対パス | [scripts-lookup.md](../development-workflow/references/scripts-lookup.md)。シェルが変わったら決め直す |

**続けて実行するときは 1 つの bash ブロックへまとめる。** 先頭で 1 度決めれば、後続のコマンドは決め直さずに済む（推奨であり、1 コマンドずつ実行してもよい）。

## 0. 宣言ファイルを用意する

**この Skill を起動したら、まずこれを実行する。**

```bash
# 「$SCRIPTS を決める」の手順でパスを決めてから実行する。
[ -n "${SCRIPTS:-}" ] || { echo "NDF の scripts/ を解決できない" >&2; exit 1; }
bash "$SCRIPTS/worktree-setup.sh" init; echo "exit=$?"
```

worktree 運用の仕組みは、リポジトリ側に `.ndf/worktree.json` があるときだけ動く。
無ければ hook もコマンドも何も出力せず終了コード 0 で終わる。**このコマンドがその
入口を作る。**

**終了コードが 0 でなければ先へ進まない。** `init` の出力をそのまま利用者に示し、手順 1 へ
進まずに止まる。宣言が読めない（JSON として壊れている・`version` が未対応）ときは、宣言の
状態を示す行と案内が標準エラーに出て 1 で終わる。**宣言を直す・消す・`init --force` で作り直す
のは利用者が決める。** エージェントは宣言を書き換えない。

- 読める宣言が既にあれば**上書きしない**。書き加えた内容は消えない。読めない宣言では
  1 で終わる（「既にあります」とは報告しない）
- 作った直後から、メインディレクトリの編集時の案内と逸脱検知が動く。メインディレクトリの
  ブランチは既定では動かさない（追従させるなら `follow_branch: true` を足す。「メイン
  ディレクトリのブランチ」）
- **作った宣言ファイルはコミットする。** リポジトリの設定であり、他の開発者にも同じ
  運用が要る

導入の状態は `worktree-setup.sh status` で見られる（宣言ファイルの有無、`.worktrees/`
の登録、稼働中の worktree 数）。**手順が分岐に使うなら `worktree-setup.sh check` を使う。**
宣言の状態を終了コードで返し（0 あり / 2 なし / 3 読めない / 1 判定できない）、ファイルを作らない。

ローカル環境での動作検証やテスト実行の分離を使うときは `localenv` / `testenv` を足す。
書き方は [references/declaration.md](references/declaration.md) にある。

**機械ごとに違う値は共有の宣言へ書かない。** ポートの帯・持ち込み物・追従の有無は、
コミットしない `.ndf/worktree.local.json` へ書く（`init` が `.ndf/.gitignore` で追跡から
外す）。上書きできる項目と重ね合わせの規則は
[references/declaration.md](references/declaration.md) の「機械ごとに違う値は個人の宣言へ
書く」にある。

## 1. 現在地を確かめる

worktree の中では `git rev-parse --show-toplevel` が worktree 自身を返す。メインディレクトリを
指すには共通の git ディレクトリの親を使う。

```bash
bash "$SCRIPTS/lib/worktree-common.sh" >/dev/null 2>&1  # 読み込みの確認のみ
. "$SCRIPTS/lib/worktree-common.sh"
wt_main_dir          # メインディレクトリの絶対パス
wt_in_worktree && echo "作業ツリーの中" || echo "主ディレクトリ"
```

`$SCRIPTS` は手順 0 で決めた値である。**シェルが変わったら決め直す。**

**既に worktree の中にいるなら、ここで終わる。** 入れ子の worktree は作らない。

## 2. worktree を用意する

### 2-1. `superpowers:using-git-worktrees` があれば委譲する

外部 Skill が導入されているときは、作成の手順をそちらへ渡す。置き場所の既定は
どちらも `.worktrees/` で一致するため、結果は変わらない。

導入の有無は、利用できる Skill の一覧に `superpowers:using-git-worktrees` が
含まれるかで判定する。含まれていれば、その Skill を起動して作成を任せ、
戻ってきたら手順 3 へ進む。含まれていなければ 2-2 へ進む。

### 2-2. `.worktrees/` の登録を確かめる

登録しないまま worktree を作ると、worktree の中身が追跡対象に入る。**作成より先に
登録する。**

```bash
main_dir=$(wt_main_dir)
if ! git -C "$main_dir" check-ignore -q .worktrees/ 2>/dev/null; then
  printf '\n# 開発用の作業ツリー\n.worktrees/\n' >> "$main_dir/.gitignore"
  git -C "$main_dir" add .gitignore
  git -C "$main_dir" commit -m "Chore: .worktrees/ を追跡対象から外す"
fi
```

**末尾の `/` を省かない。** `.gitignore` の `.worktrees/` はディレクトリだけに当たる
記法で、まだディレクトリが無い状態で `check-ignore .worktrees` を実行すると、登録済み
でも「登録されていない」と判定される（実測：ディレクトリ不在時、スラッシュなしは 1、
スラッシュありは 0）。

### 2-3. 作成して移る

置き場所はブランチ名をそのまま使う。`feature/foo` は `.worktrees/feature/foo` になり、
ブランチ名から場所が一意に決まる。

```bash
branch="feature/<name>"
git -C "$main_dir" fetch origin
# 起点は開発の本流であって、既定ブランチとは限らない。宣言の base_branch を
# 優先し、無ければ origin の HEAD が指す先へ落ちる
base=$(wt_base_branch "$main_dir") || exit 1
start="origin/$base"
git -C "$main_dir" show-ref --verify --quiet "refs/remotes/origin/$base" || start="$base"
git -C "$main_dir" worktree add -b "$branch" "$main_dir/.worktrees/$branch" "$start"
cd "$main_dir/.worktrees/$branch"
```

既存のブランチで作業を続けるなら `-b` を外す。

**ミッションの中では、課題の worktree をミッションブランチから切る。** ミッション
ブランチ（`mission/<名前>`）そのものは起点（`base_branch`）から切り、課題の worktree は
`--from` にミッションブランチを渡す。宣言の `base_branch` は develop のまま変えない。

```bash
# ミッションブランチ。起点は宣言の base_branch
bash "$SCRIPTS/worktree-setup.sh" create mission/<名前>
# 課題の worktree。起点はミッションブランチ
bash "$SCRIPTS/worktree-setup.sh" create feat/issue-<番号>-<名前> --from mission/<名前>
```

`create` は `.worktrees/` の登録を確かめ、`origin` を取得してから作り、worktree のパスと
起点を出力する。ベースブランチが origin にもローカルにも無いときは作らずに 1 で終わる。

**既定ブランチと開発の起点は別物である。** 既定ブランチに正式版を置き、開発の本流を
`develop` などの別のブランチに置くリポジトリでは、既定ブランチから分岐すると開発中の
変更が正式版から枝分かれする。起点は宣言（[references/declaration.md](references/declaration.md)
の `base_branch`）から取り、宣言が無ければ `origin` の HEAD が指すブランチへ落ちる。

宣言した名前が origin にもローカルにも無いときは、`wt_base_branch` が名前を挙げて失敗する。
既定ブランチへは落ちない。**まだ取得していないだけのブランチは「無い」とは読まない。**
取得済みの参照に見つからないときは origin へ問い合わせるため、起点を移した直後の作業
ディレクトリでも、`git fetch` を挟まずに解決できる。`origin/HEAD` が設定されていない場合は
`git -C "$main_dir" remote set-head origin -a` で設定できる。

Claude Code の worktree 作成ツールは新規作成先が固定されているため、`.worktrees/` を
使う場合は `git worktree add` で作成してからパスを指定して入る。既存の worktree へ
入る場合はパス指定が通る（`git worktree list` に載っていることが条件）。

## 3. メインディレクトリに残った変更を移す

セッション開始時に未コミットの変更が提示されたときの手順である。**メインディレクトリで
コミットしない。**

```bash
main_dir=$(wt_main_dir)
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
変更を差分に含めないため、取り込み済みの変更があるとパッチが空になる。手順 3 で
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

移送の後、両側の状態を確認する。メインディレクトリが元へ戻り、worktree に変更が
載っていることを `git status --short` で見る。

## 4. worktree を一覧する

```bash
git -C "$(wt_main_dir)" worktree list
```

`.worktrees/` 配下に無いものは、レビュー用の一時的な worktree である。この Skill の
対象ではない。

Pull Request がマージされた後の削除は `/ndf:merged` が行う。

## メインディレクトリのブランチ

**セッション開始時の hook は、既定ではメインディレクトリの HEAD を動かさない。** 並列に動く
エージェントのどれが開始・再開しても、他の担当が読んでいるメインディレクトリの内容が
入れ替わらないためである。

稼働中の worktree の内容をメインディレクトリでも見たいときは、宣言に `follow_branch: true` を
書く（[references/declaration.md](references/declaration.md)）。このときメインディレクトリの
ブランチは、稼働中の開発用 worktree へセッション開始時に追従する。同じブランチを 2 つの
作業ディレクトリへ checkout できないため、追従は detached HEAD で行う。detached HEAD では
コミットしてもブランチが動かないため、メインディレクトリで加えた変更が worktree のブランチへ
混ざることもない。

| 稼働中の開発用 worktree | メインディレクトリ（`follow_branch: true` のとき） |
| --- | --- |
| 1 つ | そのブランチが指すコミットを detached HEAD で開く |
| 0 個または複数 | 起点ブランチに合わせる（宣言が無ければ既定ブランチ） |

メインディレクトリに未コミットの変更があるときは追従せず、変更がある事実だけを伝える。

## ローカル環境での動作検証

画面を触って動作を確かめるサービス一式を持つリポジトリでは、worktree を作っただけでは
動かない。worktree は追跡されているファイルしか持たないため、依存物も環境ファイルも
無い。手順は [references/local-environment.md](references/local-environment.md) にある。

```bash
NDF="$SCRIPTS/worktree-localenv.sh"
WT="$main_dir/.worktrees/<ブランチ名>"
bash "$NDF" setup "$WT"       # 設定と依存物を持ち込む
bash "$NDF" mode "$WT"        # 相乗り(0) か 分離(1) かを提示
bash "$NDF" aim "$WT"         # ローカル環境が指すコードを向ける
bash "$NDF" verify "$WT"; echo $?  # 0 一致 / 1 不一致 / 2 未起動
```

**対象の worktree を引数で渡す。** 省略すると現在地が対象になるため、メインディレクトリから
実行するとメインディレクトリを照合してしまう。

**検証の直前に `verify` を通す。** ローカル環境に載っているコードが対象と違っていても、失敗と
しては現れない。別のコードを検証したことに気づけないまま進む。

リポジトリごとの差は `.ndf/worktree.json` が持つ。書き方は
[references/declaration.md](references/declaration.md) にある。**この宣言が無い
リポジトリでは、これらのコマンドは何も出力せず終了コード 0 で終わる。**

## テスト実行の分離

同じ保存先を使うテストは、同時に走らせると互いのデータを壊す。worktree ごとにテスト環境を
立てて分ける。手順は [references/test-execution.md](references/test-execution.md) にある。

```bash
TE="$SCRIPTS/worktree-testenv.sh"
bash "$TE" env "$WT"                        # 環境名・スロット・ポートを採番し台帳へ記録
bash "$TE" bake --tag "$(bash "$TE" tag "$WT")"  # 基準を作る（内容が同じなら焼き直さない）
bash "$TE" up "$WT" --profile core          # 起動する
bash "$TE" test "$WT" --kind stateful       # 実行したコマンドの終了コードがそのまま返る
bash "$TE" down "$WT" --volumes             # 破棄し、割り当てを解放する
```

**worktree を消す前に `down --volumes` を実行する。** 順序を逆にすると台帳から実体を
引けなくなる。

台帳は共通の git ディレクトリ配下（`.git/ndf/worktree-registry.json`）に置く。worktree の
中に置くと、削除した時点で割り当ての記録が消える。**解放しても行は消さず、解放の時刻を
書き込む。**

この工程に入ったら記録のコマンド `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "作業場所の用意"` を 1 行打つ（issue の本文とボードの両方に残る。`$SCRIPTS` の決め方は `development-workflow` の `references/scripts-lookup.md`、3 層では起動指示の「記録のコマンド」を使う）。


## 関連

- `/ndf:merged` — マージ後の worktree とブランチの削除
- `/ndf:pr` — worktree からの Pull Request 作成
- `/ndf:issue-plan-strategy` — 複数 Pull Request での並行開発
