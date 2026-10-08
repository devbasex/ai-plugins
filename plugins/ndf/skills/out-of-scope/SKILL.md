---
name: out-of-scope
description: "Capture a finding outside the current scope as an issue at the moment it is found. Use when a defect or improvement appears outside what this change fixes（範囲外の課題・その場で起票・対象外と判断した指摘）."
allowed-tools:
  - Bash(python3 *)
  - Bash(gh *)
  - Read
  - Grep
---

# 範囲外の課題を、見つけたその場で起票する

作業の途中で見つけた**この変更の範囲に含まれない課題**を、発見の瞬間に issue へ残す。工程の外に置き、どの工程からも
呼ぶ。決まった手順は `issue-file.py` が行い、1 行の JSON と終了コードで返す（`$SCRIPTS` は `development-workflow` の
`references/scripts-lookup.md`）。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 範囲外の課題 | この変更の受け入れ条件にも、直す対象にも含まれない課題 |
| 由来 | その課題を見つけた元。`PR #<番号>` か `issue #<番号>`。Pull Request がまだ無ければ起点の issue |

## いつ呼ぶか

| 呼び出し元 | 呼ぶ場面 |
| --- | --- |
| `tdd-cycle` / `refactoring` | 対象・範囲の外で不具合や直したい兆候に気づいたとき |
| `cross-review` / `pr-review` / `fix` | 指摘を範囲外と判断したとき |
| `quality-gates` | 完了報告の「範囲外と判断したもの」を書くとき |
| `problem-solving` | 根本原因がこの変更の範囲の外にあったとき |

## 手順

### 1. 範囲かを照合する

**基準を新しく作らない。** 受け入れ条件（`requirements-design` の仕様、`issues/` 配下）と、やらないこと
（`implementation-plan` の実装計画の「目的と非目的」）に照らす。どちらも無ければ依頼文が範囲を決める。

### 2. 3 択で決める

**不具合が即時修正の 4 条件（`development-workflow` の SKILL.md の「即時修正」）をすべて満たすなら、起票せずに
即時修正する。** この 3 択は、満たさないものと不具合でないものに使う。**迷ったら起票する側へ倒す。** 後で閉じるほうが、
拾い直すより安い。

**判断の基準は `python3 "$SCRIPTS/project-mvv.py" context` で読む。** 根拠にした項目の番号と MVV の版（無ければ
「MVV なし」）を、本文の「なぜこの変更の範囲外なのか」か起票しない理由に書く（`development-workflow` の
`references/project-mvv.md`）。

| 判断 | 選ぶ条件 | 残すもの |
| --- | --- | --- |
| 起票する | 範囲外だが、直さないと誰かが困る | issue と、作業メモへの 1 行 |
| 範囲内へ入れる | この変更で直す対象と同じ原因・同じ形で、分けると片方だけが残る | 範囲を広げた事実と理由を実装計画のファイルへ 1 行 |
| 起票しない | 変更前と同じ挙動で、直す価値が現時点で無い | 理由を 1 行（作業メモか完了報告） |

### 3. 起票先を決める

**「起票する」を選んだときだけ行う。**

```bash
python3 "$SCRIPTS/issue-file.py" resolve-target
```

0 なら `metrics.upstream` と `metrics.target` から [references/issue-target.md](references/issue-target.md) の判断表で
起票先を選ぶ。20 なら推測せず `items` の候補を示して利用者に聞く。

### 4. 重複を確かめる

検索は 1 回でよい。`items` に同じ課題があれば起票せず、`note` でそちらへ由来を 1 行足す。

```bash
python3 "$SCRIPTS/issue-file.py" dup --repo <起票先> --query "<課題を表す語 2〜3 個>"
python3 "$SCRIPTS/issue-file.py" note --repo <起票先> --number <番号> --origin "<由来>"
```

打つ先が開発対象リポジトリと同じなら `note` はそのまま 1 行を打つ。**別のリポジトリ（開発対象を読めないときも含む）
なら、1 回目は承認資料（打つ先・番号・1 行）を書いて 10 で止まる**（他のリポジトリへの公開）。利用者に示して同意を
取り、同じ引数に `--approved <metrics.digest>` を足して打ち直すと 1 行が打たれる（示した後に変えると 1 で打たれない）。
人に問えない起動では打ち直さず、承認資料を添えて人へ戻す。

### 5. 起票する

本文は 4 つの節と空の「由来」の見出しで書く（書式は `markdown-writing`）。**由来は `create` が入れ、5 つの見出しが
そろい空でないかを確かめる**（欠ければ 1。欠けた見出しが `items` に返る）。

```markdown
## 何を見つけたか
（再現する形で。入力・状態・観測した結果）

## どこで見つけたか
（ファイルと行、または Pull Request とレビューの指摘）

## なぜこの変更の範囲外なのか
（受け入れ条件・やらないことのどれに照らして外れるか。根拠: <MVV の項目>（MVV 版 <N> か MVV なし））

## 直さないと何が起きるか
（影響と、その範囲）

## 由来
```

```bash
python3 "$SCRIPTS/issue-file.py" create --repo <起票先> --title "<何が起きるか>" \
  --body-file <本文のファイル> --origin "<由来>" [--label <名前>]...
```

**1 回目は承認資料（起票先・題・本文・ラベル）を書いて 10 で止まる。** 利用者に示して同意を取り、同じ引数に
`--approved <metrics.digest>` を足して打ち直すと課題ができる（示した後に変えると 1 で作られない）。人に問えない起動
では起動指示に従うが、**`metrics.other_repo` が真なら打ち直さず、承認資料を添えて人へ戻す**（他のリポジトリへの公開）。

両方にまたがる課題の順序は [references/issue-target.md](references/issue-target.md) にある。

### 6. 由来を残す

起票した番号を元の場所へ戻す。**番号を書かずに閉じない。** 実装・リファクタリング・原因の調査で見つけたものは元の
Pull Request の本文か実装計画の「やらないこと」へ（Pull Request が無ければ起点の issue へ）、レビューの指摘は返信に
番号を書いてから resolve する（無いと無視と区別できない）。

```bash
gh pr comment <PR番号> --body "範囲外と判断し、#<起票した番号> として残した。"
```

## 起票した課題の辿り方

由来は**本文とコメントの参照だけ**で辿り、label は増やさない。`retrospective` は
`issue-file.py by-origin --origin "<由来>" ... --with-upstream` で、その変更から出た課題の一覧を作る。

## この手順が扱わないこと

| 扱わないもの | 担当 |
| --- | --- |
| この変更の範囲に含まれる不具合 | `tdd-cycle`（再現テストから直す） |
| 手を付ける範囲そのものの決め方 | `refactoring` |
| 受け入れ条件の作り方 | `requirements-design` |
| 起票の取りこぼしを拾うこと | `retrospective` |
| 蓄積した既存の課題への判断（ここの 3 択は発見の瞬間だけに効く） | `backlog-refinement`（「既存の Skill との境界」） |
