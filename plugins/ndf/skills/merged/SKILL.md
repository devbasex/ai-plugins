---
name: merged
description: "Delete merged branches and worktrees after listing them for approval, then update the base branch. Use when a PR was merged（マージ後の後片付け・ブランチを整理・worktreeを削除）."
argument-hint: "[PR番号]"
allowed-tools:
  - Bash
  - Read
---

# マージ後クリーンアップコマンド

PR マージ後の後始末をまとめて実行する。対象 PR のブランチ削除に加えて、残っているマージ済みブランチの整理と起点ブランチの取り込みもこの Skill で扱う。

## 用途の切り分け（最初に判定する）

| 依頼の意図 | 実行する節 |
|---|---|
| PR マージ後のクリーンアップ | 「クリーンアップの手順」→ 必要なら「マージ済みブランチの整理」 |
| マージ済みブランチの整理のみ | 「マージ済みブランチの整理」のみ（クリーンアップの手順は実行しない） |
| 作業中ブランチへ最新の起点ブランチを取り込む | 「起点ブランチの取り込み」のみ（クリーンアップの手順は実行しない） |

「クリーンアップの手順」以外を目的とする場合、対象 PR は未マージであるのが通常のため、
手順 1（PR のマージ確認）を前提条件にしてはならない。「マージ済みブランチの整理」と
「起点ブランチの取り込み」はいずれも **単独で実行可能** で、PR のマージ状態に依存しない。

## 削除前の同意取得（必須）

worktree 削除・ローカルブランチ削除・リモートブランチ削除はいずれも取り消しが難しい。
**この 3 種類の操作は、実行の直前に削除対象を一覧で提示して利用者の同意を得る。
同意が得られていない対象は削除しない。** この Skill は自然文の依頼でも起動するため、
安全性はこの手順で担保する（frontmatter の発動制御には依存しない）。

| 操作 | 提示するもの |
|---|---|
| worktree 削除 | worktree のパスと、未コミット変更の有無（`git -C <path> status --short`） |
| ローカルブランチ削除 | ブランチ名と、起点ブランチへ未マージのコミットがあるか |
| リモートブランチ削除 | リモート名とブランチ名。共有ブランチに影響するため、他の削除と分けて同意を取る |

- 「削除してよいか」だけを尋ねるのは確認にならない。**対象そのものを一覧で示す**
- 同意が得られなかった対象はスキップし、作業完了報告にスキップした対象と理由を記載する
- 利用者が対象を明示して削除を依頼した場合（`/ndf:merged 123` で PR 番号を指定した等）は、
  その依頼が対象への同意にあたる。それでも一覧の提示は行い、依頼に含まれない対象
  （マージ済みブランチの一括整理、リモート削除）については改めて同意を取る

## 起点ブランチを決める

**取り込む先も、マージ済みかを見る先も、開発の起点である。** 既定ブランチとは限らない。
以降の手順はこの値を使う。

```bash
# 起点は開発の本流であって、既定ブランチとは限らない。宣言（`.ndf/worktree.json` の
# `base_branch`）を先に読み、その名前が実在することを確かめる。取得済みの参照に無ければ
# origin へ問い合わせる（取得していないだけの場合を「無い」と読まないため）。実在しなければ
# 既定ブランチへ落とさずに止まる。宣言が無ければ origin の HEAD が指す先を使い、それも
# 取れなければ慣例の名前のうちローカルにあるものへ落とす
# （共通ライブラリ `wt_base_branch` と同じ順序）
dev_base=$(jq -r 'select(.version == 1) | .base_branch | select(type == "string")' \
  .ndf/worktree.json 2>/dev/null)
if [ -n "$dev_base" ]; then
  dev_base_found=0
  if git show-ref --verify --quiet "refs/remotes/origin/$dev_base" ||
     git show-ref --verify --quiet "refs/heads/$dev_base"; then
    dev_base_found=1
  else
    # `git ls-remote` のパターンは参照名の末尾に一致する。問い合わせの成功だけを見ると
    # `refs/heads/x/refs/heads/develop` のような別のブランチでも「ある」と読むため、
    # 返った行の参照名そのものを照合する（共通ライブラリ `wt_branch_exists` と同じ形）
    dev_base_listing=$(GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin \
      "refs/heads/$dev_base" 2>/dev/null)
    while IFS= read -r line; do
      case "$line" in *$'\t'"refs/heads/$dev_base") dev_base_found=1; break ;; esac
    done <<<"$dev_base_listing"
  fi
  [ "$dev_base_found" -eq 1 ] || {
    printf 'NOTE: .ndf/worktree.json の base_branch が指す %s は origin にもローカルにもありません\n' \
      "$dev_base" >&2
    exit 1
  }
else
  dev_base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
  for candidate in main master; do
    [ -n "$dev_base" ] && break
    git show-ref --verify --quiet "refs/heads/$candidate" && dev_base=$candidate
  done
  dev_base=${dev_base:-main}
fi
```

## クリーンアップの手順

1. **マージ確認**: 引数の（引数がなければ自身が作成した最新の）PR が起点ブランチへ merge されていることを github mcp で確認。merge されていなければクリーンアップは実施せず終了
2. **作業ツリー退避**: `git branch --show-current` で**退避元のブランチ名を記録**し、`git status` を確認して変更があれば `git stash`
3. **起点ブランチの更新**: `git checkout "$dev_base"` → `git pull`
4. **worktree クリーンアップ**: `git worktree list` で当該 PR に対応する作業ツリーを探し、**「削除前の同意取得」に従ってパスと未コミット変更の有無を提示し、同意を得てから** `git worktree remove <path>` で削除する。探す先は 2 つある

   | 置き場所 | 作られ方 | 名前 |
   |---|---|---|
   | `<主ディレクトリ>/.worktrees/<ブランチ名>` | 開発用。`/ndf:worktree` が作る | ブランチ名がそのままパスになる |
   | システムの一時ディレクトリ配下 | レビュー用。`cross-review` / `cross-refactoring` が作る | `pr<PR番号>` |

   開発用はブランチ名で、レビュー用は PR 番号で探す。レビュー用は削除すると中の `.cross_review/` も一緒に消える。空になった `.worktrees/` の中間ディレクトリ（`feature/` など）も削除する
5. **ブランチ削除**: 削除するブランチ名を提示して同意を得てから `git branch -d <feature-branch>`
6. **マージ済みブランチの整理**: 下記の手順で残存ブランチをまとめて削除
7. **閉じ忘れた issue を閉じる**: 下記「閉じ忘れた issue を閉じる」の手順で、マージした PR の本文が指す issue のうち **まだ OPEN のもの** を閉じる
8. **復元**: 手順 2 で stash していれば、**退避元のブランチへ戻してから**復元する
   - 退避元のブランチが残っている場合: `git checkout <退避元のブランチ>` → `git stash pop`
   - 退避元のブランチを手順 5 / 6 で削除した場合: **`git stash pop` を実行しない**。手順 3 以降は起点ブランチに居るため、そのまま pop すると無関係な変更が起点ブランチの作業ツリーへ展開される。stash は残したまま `git stash list` の該当エントリを作業完了報告に記載し、復元先ブランチの作成か破棄かをユーザーに判断してもらう

**注意**: 冪等性保証・エラー時中断・削除済み無視

## 閉じ忘れた issue を閉じる

**GitHub の自動クローズは、既定ブランチへマージしたときにだけ働く。** 起点ブランチが
既定ブランチでないリポジトリでは、本文に `Closes #<番号>` と書いてもマージで閉じない。
完了した課題が課題の一覧に残り、次に着手する担当が完了済みの課題を拾う。

閉じ忘れた issue は、マージ済みのブランチや作業ツリーと同じく**残ったままだと次の担当が
拾ってしまう残骸**である。後片付けの工程で拾う。

```bash
# $SCRIPTS の決め方は development-workflow の references/scripts-lookup.md にある。
# この Skill のスクリプトは、その 1 つ上（プラグインの根）から辿る。
CLOSING="$SCRIPTS/../skills/merged/scripts/closing-issues.sh"

# 1. 本文から閉じる語が指す先を取り出す（<所有者>/<リポジトリ> と <番号> をタブ区切りで出す）
gh pr view <PR番号> --json body -q .body | bash "$CLOSING"

# 2. まだ OPEN のものだけに絞る
gh issue view <番号> --repo <所有者>/<リポジトリ> --json state -q .state    # OPEN / CLOSED

# 3. 一覧を提示して同意を得てから閉じる
gh issue close <番号> --repo <所有者>/<リポジトリ> --comment "PR #<PR番号> でマージしました"
```

- **閉じる語は番号ごとに要る。** `Fixes #12, #13` は 12 だけが対象で、`#13` は閉じる語を
  伴わない参照である（[公式ドキュメント](https://docs.github.com/articles/closing-issues-using-keywords)が
  "you must use the keyword before each issue you reference" と定めている）
- 大文字と小文字は区別しない。`close` / `closed` / `fix` / `fixed` / `resolve` / `resolved` も対象
- **閉じる前に対象の番号とタイトルを一覧で示し、同意を得る**（「削除前の同意取得」と同じ理由。
  閉じる操作は取り消しにくい）
- **対象は OPEN のものに限る。** 既定ブランチへマージした場合は GitHub が先に閉じている。
  この手順を二重に実行しても結果は変わらない
- 閉じる語が 1 つも無ければ何もしない。スクリプトは何も出さずに終了コード 0 で終わる
- **閉じる先は、リポジトリまで含めて取り出す。** 開発の対象が別のリポジトリのとき、そこへ
  起票した issue は番号だけでは指せない。`Fixes <所有者>/<リポジトリ>#<番号>` と issue の
  URL の 2 つも読み、番号だけの形はこれまでどおり実行したリポジトリの issue を指す
- **`gh issue close` は `owner/repo#番号` を受け取らない。** 位置引数は `{<number> | <url>}`
  で、リポジトリを渡す口は `--repo` だけである。取り出した 2 つの値をそのまま
  `gh issue close <番号> --repo <所有者>/<リポジトリ>` の形へ渡す

## マージ済みブランチの整理

残存するマージ済みブランチをまとめて削除する。**単独で実行可能**な節であり、
「クリーンアップの手順」の手順 1（PR のマージ確認）を前提にしない。
OPEN な PR が残っている状態でも、ブランチ整理だけを目的に実行してよい。

```bash
# 起点はローカルに無いことがある。この節は単独でも実行するため、ここで取得して
# からリモート追跡ブランチを判定の先にする
git fetch origin "$dev_base"             # 1. 起点を取得
git branch --merged "origin/$dev_base"   # 2. マージ済みブランチを列挙
git branch -d <branch>                   # 3. ローカル削除
git push origin --delete <branch>        # 4. リモートにも残っていれば削除
```

- 起点ブランチと現在のブランチは必ず除外する
- **手順 3 の前に削除対象のローカルブランチを一覧で提示し、同意を得てから削除する**
- **手順 4 のリモート削除は共有ブランチに影響するため、ローカル削除とは分けて対象を提示し、
  改めて同意を得てから実行する**（「削除前の同意取得」を参照）

## 起点ブランチの取り込み

作業中のブランチへ最新の起点ブランチを取り込む場合はこちらを使う。PR のマージ有無は前提条件にしない。

1. **ブランチ確認**: `git branch --show-current`。起点ブランチ自身なら `git pull` のみ実行して終了
2. **作業ツリー確認**: 未コミット変更があれば `git stash` で退避
3. **最新取得**: `git fetch origin "$dev_base"`
4. **マージ実行**: `git merge "origin/$dev_base" --no-edit`
   - コンフリクト時は `git diff --name-only --diff-filter=U` で一覧を表示し、**自動解決はしない**。ユーザーに報告し、確認後に作業継続
5. **後処理**: stash していれば `git stash pop`。コンフリクトがなければ `git push` で反映し、マージ済みコミット数と変更ファイル数を報告

## 通過工程を報告に載せる

**記憶から書かない。** 工程を通ったかどうかは会話の中にしか残らず、セッションが変わると
引き継がれない。スクリプトの出力をそのまま完了報告へ貼る。

```bash
bash "$SCRIPTS/../skills/development-workflow/scripts/stage-check.sh" report <issue番号>
```

- 記録が無ければ 1 行だけ返る。**すべての工程を欠落として並べない**
- 記録の無い必須の工程があれば、その名前と、記録するコマンドが出力に載る
- 実施済みであれば記録してから先へ進む。実施していなければ、その工程へ戻る
- 控えの読み方と、記録が無いときの扱いは
  [references/stage-completeness.md](../development-workflow/references/stage-completeness.md) にある

## 作業完了報告（必須）

- 実行サマリー（PR タイトル、マージコミット、削除したブランチ、現在のブランチ）
- **通過工程の報告**（前節の `stage-check.sh report` の出力をそのまま貼る）
- **閉じた issue の番号**。閉じなかったものがあれば理由（既に CLOSED だった、同意が得られなかった）
- 起点ブランチの状態
- 復元していない stash が残っている場合はその旨と `git stash list` の該当エントリ
- **このマージがまとまりの最後だったかどうか**。最後なら次の工程は配布（`/ndf:release`）である。
  判断できないときは、その旨を書いて運用者の回答を待つ
- PR URL

## 次の工程

**後片付けが済んでも、変更は利用者へ届いていない。** マージは取り込みであって配布ではない。

まとまり（1 度にマージする Pull Request の集合）の最後のマージを行った場合は、続けて
`/ndf:release` で版を上げる。まだ残りがある場合は、最後のマージを行う側へ渡す。単独の変更では
まとまりが 1 件になるため、この後片付けの直後が配布の時期にあたる。

**まとまりの範囲が分からないときは、運用者に確認してから決める。** どの Pull Request まで
含むかは、この Skill が読み取れる情報の外にあることがある。確認せずに最後だと決めると、
残りがマージされる前に版が上がる。

この Skill は版を上げない。担い手と時期は `release` が持つ。

この工程に入ったら `/ndf:progress-tracking <issue番号> "後片付け"` を呼ぶ（記録の手順はその Skill が持つ）。

## 関連

- `/ndf:release` — この工程の後に行う配布（版を上げて公開する）
- `/ndf:cherry-pick-pr` — 環境ブランチへの cherry-pick PR 作成と、複数ブランチへ同じ修正を適用する原則
