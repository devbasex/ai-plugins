---
name: merged
description: "Delete merged branches and worktrees, stopping only where git refuses, then update the base branch. Use when a PR was merged（マージ後の後片付け・ブランチを整理・worktreeを削除）."
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
マージの確認（`merged-steps.py cleanup`）を前提条件にしてはならない。「マージ済みブランチの整理」と
「起点ブランチの取り込み」はいずれも **単独で実行可能** で、PR のマージ状態に依存しない。

## 止まる条件

**後片付けは止めずに通す。** worktree・ローカルブランチ・Pull Request の head のリモート
ブランチの削除は、どれも事後に戻せる。**実行前確認は置かず、消した対象と戻し方を作業完了
報告へ載せる**（要否の基準は
[AUTHORING.md](../AUTHORING.md) の「実行前確認の要否を決める 3 つの問い」）。

**止める合図は git の拒否だけである。** `-D` の代わりになる判定をこの Skill の側に作らない。

| 対象 | 止めずに行う | 止まる（一覧で示し、同意の無い対象を消さない） | 触らない（報告に「対象外」） |
| --- | --- | --- | --- |
| 作業ツリー | `git worktree remove <path>` が 0 で終わる。拒まれたら未追跡・無視されたファイルを退避して外す（スクリプトが行う） | 退避か削除が 0 以外で終わる（未完了。同意の対象にしない） | 主ディレクトリ |
| ローカルブランチ | `git branch -d <name>` が 0 で終わる | 同じコマンドが 0 以外で終わる | 起点・本番のチャネル・現在のブランチ |
| リモートブランチ | マージ済み Pull Request の head で、同じリポジトリにあり、先端が `headRefOid` と一致する | — | 起点・本番のチャネル・fork の head、先端が `headRefOid` と違う、対応する Pull Request が見つからない |
| 課題 | **閉じない**（ミッションの課題の OPEN の一覧を報告に載せるだけ） | — | — |

- **起点・本番のチャネル・現在のブランチは、同意を求めずに対象から外す。** 尋ねる場面を
  作ること自体が誤りで、1 回の誤答で開発の本流が消える。起点は「起点ブランチを決める」の
  `$dev_base`、本番のチャネルは `.ndf/worktree.json` の `production_branch`（宣言が無ければ
  既定ブランチ）である
- **リモートブランチを消すのは、先端が Pull Request の `headRefOid` と一致するときだけ。**
  マージの後に積まれたコミットは Restore branch で戻らず、消した後に戻す手段が無い。
  引けない・先端が違うときは、同意を得ても戻せないため消さずに対象外として報告する
- **squash / rebase でマージしたブランチは `git branch -d` が拒む**（リモートのブランチが
  消えた後は「起点へマージされていない」と判定されるため）。**`-D` へ落とさない。**
  止まる対象として一覧に載せ、判断の材料（下表）を添える

止まったときの一覧（git の出力・先端のハッシュ・戻し方）は、スクリプトが提示物
（結果 JSON の `presentation_path`）として書き出す。

**止まる対象は最後に 1 回だけ示す。** 見つけるたびに止まると、止まる回数が拒否の件数だけ
戻る。同意を得た対象だけを `git branch -D` で消し、残りは報告に載せる。**このコマンドを、
この同意の外で実行しない。** 作業ツリーの `--force` は、退避を終えた後にスクリプトだけが使う。

**「未完了」（作業ツリーを外せなかった・`kept`）は止まる対象と別の結果で、同意の対象に含めない。** `git worktree remove --force` を同意の一覧に載せない。原因を取り除いた後に `/ndf:merged <PR番号>` をもう一度実行する。

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

**マージの確認・作業ツリーの削除・ローカルブランチの削除・主ディレクトリの取り込みはスクリプトが行う。**
引数が無ければ、自身が作成した最新の PR の番号を渡す。

```bash
python3 "$SCRIPTS/merged-steps.py" cleanup <PR番号>... --root <主ディレクトリ>
```

出力は 1 行の JSON（形は `$SCRIPTS/lib/README.md`）。`status` を見て次を決める。

| `status`（終了コード） | 次にすること |
| --- | --- |
| `ok`（0） | 「ミッションの課題を報告する」→ 必要なら「マージ済みブランチの整理」→ 作業完了報告 |
| `gate`（10） | `git branch -d` が拒んだブランチがある。`presentation_path` の提示物を示して同意を取り、同意を得た対象にだけ `next` のコマンド（`git branch -D`）を打つ |
| `stopped`（1 / 2 / 3） | `summary` と `items[].reason` を報告して止まる |

- `items[]` の `result` は `removed`（作業ツリーを外した）/ `deleted`（ブランチを消した。`restore` が戻し方）/ `absent`（ローカルに無い。削除済みとして扱う）/ `kept`（MERGED でない・主ディレクトリが checkout している など。`reason` を報告へ載せる）/ `stopped` / `pulled` を取る
- 作業ツリーの未追跡・無視されたファイルは `<共通の git ディレクトリ>/ndf/worktree-trash/` へ退避してから外す。退避先は `reason` に載る。**退避先は自動では消さない**（容量 `du -sh <退避先>` を報告に載せ、消すのは利用者）
- レビュー用の作業ツリー（システムの一時ディレクトリ配下の `pr<PR番号>`）もスクリプトが外す。cross-review / cross-refactoring の実行の要約は作業ツリーの外にあり、消えない

### マージから行うとき

**マージの承認を得た後に限り、** CI の待ちとマージを同じスクリプトで行える。承認の関門
（`development-workflow` の Pull Request のマージの承認）はこのコマンドの前に置き、この
コマンドを承認の代わりにしない。

```bash
python3 "$SCRIPTS/merged-steps.py" merge-when-green <PR番号> --root <主ディレクトリ> \
  [--method merge|squash|rebase] [--interval 10] [--recheck 5] [--no-checks-after 60] [--timeout 3600] [--stale-after 300] [--no-cleanup]
```

- CI のチェックが全部通るまで待つ。push で先頭のコミットが変わると待ち直す（`items` に `rewait` が載る）
- 同じ先頭のコミットで pending を見た後に全部が通れば、その周でマージする。pending を見ずに通って
  いたときだけ `--recheck` 秒後に 1 度確かめ直す。rollup が空のうちはマージせず、`--no-checks-after`
  秒を過ぎても空なら CI の無いリポジトリとしてマージする（`items` に `no_checks` が載る）
- 失敗したチェックが 1 つでもあれば、マージせずに `stopped`（1）で止まる。`items[].name` が失敗したチェック
- 通れば `gh pr merge --admin` でマージし、続けて上の `cleanup` と同じ後片付けを行う。
  `status` の読み方は上の表と同じ
- 上限の時間を過ぎてもチェックが終わらなければ `stopped`（1）で止まる。`next` のコマンドで打ち直す
- 実行が終わった（`completed`）のにチェックが pending のまま `--stale-after` 秒続けば、GitHub 側で
  取り残されたチェックとみなし、そのジョブを `gh run rerun <run> --job <job>` で **1 度だけ**再実行する
  （`items` に `{"kind": "check", "result": "rerun", "run": ..., "job": ...}` が載る）。
  再実行した同じチェックが再び取り残されたら `stopped`（1）で止まる（`items` の `result` は `stuck`）
- ジョブが `queued` のままランナーを待つ間は、待ちの 1 周ごとに stderr へ
  `merge-when-green: CI のランナー待ち（待ち行列 N 件、待ち M 件）` を出す。最後に見た待ち行列の件数は
  `metrics.queued_runs` に残る。supervise.py の run のステップで動かすと、この行が `progress.jsonl` の
  `alive` の行の `last_output` に載る

**待ちが遅れたときの一次の調査は `probe` で行う。** supervise.py の `merge` と `release` のステップが、経過が
想定を超えたときに打つ（ステップの `probe`）。

```bash
python3 "$SCRIPTS/merged-steps.py" probe (--pr <PR番号> | --head <ブランチ>...) [--act] --root <主ディレクトリ>
```

| `metrics.class` | 何を見たか | `metrics.action` |
| --- | --- | --- |
| `failed` | 失敗の結論のチェックがある | `fix` |
| `stale` | 実行が completed なのにチェックが pending でジョブの結論が無く、実行の `attempt` が 1 | `--act` なら `gh run rerun <run> --job <job>` を打って `remedied`。打てない・`--act` が無ければ `judge` |
| `stale_again` | 同じ形で `attempt` が 2 以上（既に再実行した） | `judge` |
| `settled` | 実行が completed でジョブに結論があり、表示だけが pending | `wait` |
| `queued` | ジョブがランナーを待っている | `wait` |
| `running` | 実行中のジョブがある | `wait` |
| `passed` | すべて通っている | `judge` |
| `none` | 開いた PR が無い・読めない | `judge` |

- 分類は表の上ほど強い。複数の PR は最も上の分類で全体を表す。`metrics` に `prs`・`queued_runs` も持つ
- 終了コードは 0 = 調べた / 2 = 引数が読めない。書き込みは `--act` の再実行だけで、マージ・push をしない

## ミッションの課題を報告する

**この Skill は課題を閉じない。** ミッションの課題が閉じるのは、ミッションの終わりの工程を
通った時点である（手順は `progress-tracking` の「ミッションを閉じる」が持つ）。**マージした
時点で閉じると、配布で問題が出ても課題の一覧に出ない。**

後片付けで行うのは、**ミッションの課題のうち OPEN のものを控えて報告へ載せること**だけである。

```bash
# $SCRIPTS の決め方は development-workflow の references/scripts-lookup.md にある。
# 閉じる語の読み取りは、どの Skill にも属さない共通層（$SCRIPTS/lib/）にある。
# **gate も同じ実体を読む**ため、Skill の下に複製は無い。
CLOSING="$SCRIPTS/lib/closing-issues.sh"

# 1. 本文から閉じる語が指す先を取り出す（<所有者>/<リポジトリ> と <番号> をタブ区切りで出す）
gh pr view <PR番号> --json body -q .body | bash "$CLOSING"

# 2. まだ OPEN のものだけに絞り、報告へ載せる
gh issue view <番号> --repo <所有者>/<リポジトリ> --json state -q .state    # OPEN / CLOSED
```

- 閉じる語が 1 つも無ければ何もしない。スクリプトは何も出さずに終了コード 0 で終わる
- **この一覧は、後片付けの進行の記録を書く先でもある**（`progress-tracking` の「工程の単位と
  記録する課題」。後片付けは Pull Request 単位で、マージした側が本文の閉じる語が指す課題
  すべてへ書く）
- 既定ブランチへマージしたリポジトリでは GitHub が先に閉じている。その場合はこの一覧が
  空になるだけで、扱いは変わらない

## マージ済みブランチの整理

残存するマージ済みブランチをまとめて削除する。**単独で実行可能**な節であり、
「クリーンアップの手順」（PR のマージ確認）を前提にしない。
OPEN な PR が残っている状態でも、ブランチ整理だけを目的に実行してよい。

```bash
# 起点はローカルに無いことがある。この節は単独でも実行するため、ここで取得して
# からリモート追跡ブランチを判定の先にする
git fetch origin "$dev_base"             # 1. 起点を取得
git branch --merged "origin/$dev_base"   # 2. マージ済みブランチを列挙
git branch -d <branch>                   # 3. ローカル削除（拒まれたら止まる対象へ積む）
# 4. リモートにも残っていれば、対応する Pull Request を引いて先端を突き合わせる
gh pr list --state merged --head <branch> --json number,headRefOid,headRepositoryOwner
git ls-remote origin "refs/heads/<branch>"
git push origin --delete <branch>        # 先端が headRefOid と一致したときだけ
```

- **起点ブランチ・本番のチャネル・現在のブランチは必ず除外する**（同意を求めずに外す）
- 手順 3 は「止まる条件」の表のとおり、0 で終われば消し、拒まれたら止まる対象へ積む
- **手順 4 で消すのは、同じリポジトリにあるマージ済み Pull Request の head で、先端が
  `headRefOid` と一致するものだけである。** Pull Request を引けないブランチと、先端が違う
  ブランチは消さずに対象外として報告する。マージの後に積まれたコミットは Restore branch で
  戻らず、消した後に戻す手段が無いためである。**同意を得ても消さない**（戻せないため、
  同意を求める一覧にも載せない）。対象外にした行には、先端のハッシュと `headRefOid`、
  マージ後に積まれたコミット（`git log --oneline <headRefOid>..origin/<名前>`）を添える
- fork の head（`headRepositoryOwner` がこのリポジトリの所有者と違う）も対象外である

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
- 通過記録の読み方と、記録が無いときの扱いは
  [references/stage-completeness.md](../development-workflow/references/stage-completeness.md) にある

## 作業完了報告（必須）

**止めずに行った削除は、すべてここに戻し方つきで残す。** 報告に載らない自動の削除を作らない。

| 項目 | 形 |
| --- | --- |
| 消したローカルブランチ | `<名前>`（`<削除時のハッシュ>`）— 戻すなら `git branch <名前> <ハッシュ>` |
| 消したリモートブランチ | `origin/<名前>` — 戻すなら `<Pull Request の URL>` の Restore branch |
| 消した作業ツリー | `<パス>`。無視されたファイルを退避したなら、退避先のパスと容量（`du -sh <退避先>`）— 戻すなら `mv <退避先>/<退避した相対パス> <作業ツリーを作り直した先>/<退避した相対パス>`、まとめて戻すなら `cp -a <退避先>/. <作業ツリーを作り直した先>/` |
| 対象外 | 名前と理由（起点 / 本番のチャネル / 現在のブランチ / fork / 先端が `headRefOid` と違う / 対応する Pull Request が無い）。先端が違う・Pull Request が無いリモートブランチには、先端のハッシュと `headRefOid`、マージ後に積まれたコミット（`git log --oneline <headRefOid>..origin/<名前>`）を添える |
| 止まった対象 | 「止まる条件」の「止まったときの一覧」と、同意の結果 |
| 未完了 | 退避が失敗した作業ツリーのパス、退避先・退避済みのパス・失敗したパスと `mv` の出力（退避済みのものは退避先に残す。戻すなら同じ `mv` の逆）、「原因を取り除いた後に `/ndf:merged <PR番号>` をもう一度実行する」 |
| ミッションの課題 | このマージの閉じる語が指す課題のうち OPEN のもの。「閉じるのはミッションの終わりの工程（`progress-tracking` の「ミッションを閉じる」）」と添える |
| ミッションの最後か | 最後 / 残りあり / **判断できない** |

あわせて次も載せる。

- 実行サマリー（PR タイトル、マージコミット、現在のブランチ）
- **通過工程の報告**（前節の `stage-check.sh report` の出力をそのまま貼る）
- 起点ブランチの状態
- 復元していない stash が残っている場合はその旨と `git stash list` の該当エントリ
- PR URL

**ミッションの最後かを判断できなくても、運用者の回答を待たない。** 判断できないことを報告へ
書いて終わる。この Skill は版を上げないため、ここで待っても得るものが無い。ミッションの範囲の
確認は `release` の開始条件（ミッションのすべての Pull Request がマージされている）が持つ。

## 次の工程

**後片付けが済んでも、変更は利用者へ届いていない。** マージは取り込みであって配布ではない。

ミッション（1 度にマージする Pull Request の集合）の最後のマージを行った場合は、続けて
`/ndf:release` で版を上げる。まだ残りがある場合は、最後のマージを行う側へ渡す。単独の変更では
ミッションが 1 件になるため、この後片付けの直後が配布の時期にあたる。

**ミッションの範囲が分からないときは、判断できないと報告して終わる。** どの Pull Request まで
含むかは、この Skill が読み取れる情報の外にあることがある。**ここで待たない。** 版を上げるのは
`release` であり、確認が要るのは配布を始める時点である。待つと、配布をしない後片付けでも止まる。

この Skill は版を上げない。担い手と時期は `release` が持つ。

**このマージに人手の承認が要ったかどうかは、届く先が本番の系かどうかで決まる。** 開発版や
検証環境のチャネルへ入れるマージは取り消せるため、承認を求めない。**本番の系へ届く操作
だけが関門である**（設計 Pull Request のマージは、チャネルによらず承認が要る別の関門である）。

**本番の系へ届く操作は 2 つの形を取る。** 配布（`release`）と、運用モードの実行
（`operation` の実装）である。マージが関わるのは前者で、規則は `/ndf:release` が持つ。
どのブランチが本番のチャネルかはリポジトリが宣言する（`.ndf/worktree.json` の
`production_branch`。宣言が無ければ既定ブランチ）。

この工程に入ったら記録のコマンド `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "後片付け"` を 1 行打つ（issue の本文とボードの両方に残る。`$SCRIPTS` の決め方は `development-workflow` の `references/scripts-lookup.md`、3 層では起動指示の「記録のコマンド」を使う）。

## 関連

- `/ndf:release` — この工程の後に行う配布（版を上げて公開する）
- `/ndf:cherry-pick-pr` — 環境ブランチへの cherry-pick PR 作成と、複数ブランチへ同じ修正を適用する原則
