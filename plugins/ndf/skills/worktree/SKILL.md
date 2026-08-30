---
name: worktree
description: "Prepare a git worktree under .worktrees/ and move stray changes out of the cloned directory. Use when development work starts（作業ツリーを用意・worktreeで作業・主ディレクトリの変更を移す）."
allowed-tools:
  - Bash
  - Read
  - Edit
---

# 作業ツリーで開発する

開発の変更は、リポジトリを clone したディレクトリではなく git の作業ツリーの中で行う。
並行して複数の作業を進めても、互いの変更が同じ作業ディレクトリで混ざらない状態を保つ。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 主ディレクトリ | リポジトリを clone したディレクトリ。`git rev-parse --git-common-dir` の親にあたる |
| 作業ツリー | `git worktree add` で作った作業用ディレクトリ。ここでは `.worktrees/` 配下に置くものを指す |
| 開発用の作業ツリー | 人が変更を加えて Pull Request にするもの。ブランチを持つ |
| レビュー用の作業ツリー | `cross-review` と `cross-refactoring` が一時的に使うもの。システムの一時ディレクトリに置く |

この Skill が扱うのは開発用の作業ツリーだけである。レビュー用の作業ツリーは
それぞれの Skill が作って捨てるため、置き場所も後片付けの主体も異なる。

## 主ディレクトリを編集してよい場合

原則の対象は開発の変更である。次のパスは主ディレクトリで編集してよい。

| パス | 扱う内容 |
| --- | --- |
| `issues/` | 計画と仕様の草案 |
| `docs/` | リポジトリ知識 |
| `.claude/` `.codex/` `.kiro/` `.agents/` `.gemini/` | 各ランタイムの設定 |
| `.serena/` | コードインテリジェンスの設定と索引 |
| `.gitignore` | 作業ツリーの登録そのものに必要 |

リポジトリ側で `.ndf/localenv.json` の `guard.allow_paths` を書けば、この一覧を差し替えられる。

主ディレクトリの編集は拒否しない。編集の直前に案内が出て、セッション開始時に残った変更が
提示される。案内が出ても操作は成立するため、意図した編集であればそのまま続けてよい。

## 1. 現在地を確かめる

作業ツリーの中では `git rev-parse --show-toplevel` が作業ツリー自身を返す。主ディレクトリを
指すには共通の git ディレクトリの親を使う。

```bash
bash "$NDF_SCRIPTS/lib/worktree-common.sh" >/dev/null 2>&1  # 読み込みの確認のみ
. "$NDF_SCRIPTS/lib/worktree-common.sh"
wt_main_dir          # 主ディレクトリの絶対パス
wt_in_worktree && echo "作業ツリーの中" || echo "主ディレクトリ"
```

`$NDF_SCRIPTS` は `plugins/ndf/scripts`（配布後は各ランタイムのプラグイン配下）を指す。

**既に作業ツリーの中にいるなら、ここで終わる。** 入れ子の作業ツリーは作らない。

## 2. 作業ツリーを用意する

### 2-1. `superpowers:using-git-worktrees` があれば委譲する

外部 Skill が導入されている環境では、作成の手順をそちらへ渡す。置き場所の既定は
どちらも `.worktrees/` で一致するため、結果は変わらない。

導入の有無は、利用できる Skill の一覧に `superpowers:using-git-worktrees` が
含まれるかで判定する。含まれていれば、その Skill を起動して作成を任せ、
戻ってきたら手順 3 へ進む。含まれていなければ 2-2 へ進む。

### 2-2. `.worktrees/` の登録を確かめる

登録しないまま作業ツリーを作ると、作業ツリーの中身が追跡対象に入る。**作成より先に
登録する。**

```bash
main_dir=$(wt_main_dir)
if ! git -C "$main_dir" check-ignore -q .worktrees 2>/dev/null; then
  printf '\n# 開発用の作業ツリー\n.worktrees/\n' >> "$main_dir/.gitignore"
  git -C "$main_dir" add .gitignore
  git -C "$main_dir" commit -m "Chore: .worktrees/ を追跡対象から外す"
fi
```

### 2-3. 作成して移る

置き場所はブランチ名をそのまま使う。`feature/foo` は `.worktrees/feature/foo` になり、
ブランチ名から場所が一意に決まる。

```bash
branch="feature/<name>"
git -C "$main_dir" fetch origin
git -C "$main_dir" worktree add -b "$branch" "$main_dir/.worktrees/$branch" origin/main
cd "$main_dir/.worktrees/$branch"
```

既存のブランチで作業を続けるなら `-b` を外す。

Claude Code の作業ツリー作成ツールは新規作成先が固定されているため、`.worktrees/` を
使う場合は `git worktree add` で作成してからパスを指定して入る。既存の作業ツリーへ
入る場合はパス指定が通る（`git worktree list` に載っていることが条件）。

## 3. 主ディレクトリに残った変更を移す

セッション開始時に未コミットの変更が提示されたときの手順である。**主ディレクトリで
コミットしない。**

```bash
main_dir=$(wt_main_dir)
target="$main_dir/.worktrees/<ブランチ名>"

# 1. 移す対象を確かめる
git -C "$main_dir" status --short

# 2. 差分を取り出す（追跡対象。`git add` 済みの変更も含める）
git -C "$main_dir" diff HEAD > /tmp/ndf-stray.patch

# 3. 作業ツリーへ当てる
git -C "$target" apply /tmp/ndf-stray.patch

# 4. 当たったことを確かめてから、主ディレクトリ側を元へ戻す
git -C "$target" status --short
git -C "$main_dir" reset --hard HEAD
```

手順 2 で `git diff` ではなく `git diff HEAD` を使う。`git diff` は `git add` 済みの
変更を差分に含めないため、取り込み済みの変更があるとパッチが空になる。手順 4 が
`git checkout -- .` ではなく `git reset --hard HEAD` なのも同じ理由で、前者は取り込み
済みの変更を戻さない。

**手順 4 は主ディレクトリの変更を捨てる。** 手順 3 が成功し、作業ツリー側に変更が
載っていることを確かめてから実行する。

追跡されていないファイルは差分に含まれない。`git -C "$main_dir" status --short` の
`??` 行を見て、必要なものを作業ツリーへ複製してから主ディレクトリ側を削除する。

移送の後、両側の状態を確認する。主ディレクトリが元へ戻り、作業ツリーに変更が
載っていることを `git status --short` で見る。

## 4. 作業ツリーを一覧する

```bash
git -C "$(wt_main_dir)" worktree list
```

`.worktrees/` 配下に無いものは、レビュー用の一時的な作業ツリーである。この Skill の
対象ではない。

Pull Request がマージされた後の削除は `/ndf:merged` が行う。

## 主ディレクトリのブランチ

主ディレクトリのブランチは、稼働中の開発用作業ツリーへセッション開始時に追従する。
同じブランチを 2 つの作業ディレクトリへ checkout できないため、追従は detached HEAD で
行う。detached HEAD ではコミットしてもブランチが動かないため、主ディレクトリで加えた
変更が作業ツリーのブランチへ混ざることもない。

| 稼働中の開発用作業ツリー | 主ディレクトリ |
| --- | --- |
| 1 つ | そのブランチが指すコミットを detached HEAD で開く |
| 0 個または複数 | 既定ブランチに合わせる |

主ディレクトリに未コミットの変更があるときは追従せず、変更がある事実だけを伝える。

## 関連

- `/ndf:merged` — マージ後の作業ツリーとブランチの削除
- `/ndf:pr` — 作業ツリーからの Pull Request 作成
- `/ndf:issue-plan-strategy` — 複数 Pull Request での並行開発
