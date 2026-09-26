---
name: retrospective
description: "Record what to change in how the work was done, and pick up findings that were never filed. Use when a change has been released and verified（振り返り・進め方の見直し・起票の取りこぼしを拾う）."
allowed-tools:
  - Bash(gh *)
  - Bash(git *)
  - Read
  - Write
  - Grep
---

# 振り返り

**進め方のうち次に変えることを決めて残し、起票の取りこぼしを拾う。**

対象はこの変更の進め方と、途中で起票した課題の一覧である。成果物の良し悪しは
`pr-review` と `cross-review` が扱う。ここで扱うのは、どう進めたかである。

## いつ行うか

開始条件は、リリース後テストを行ったかどうかで変わる。

| リリース後テスト | 開始できる時点 |
| --- | --- |
| 行った | その結果が出た後。実環境で何が起きたかまで分かった時点で、マージ前の判断を見直せる |
| 行わない | マージ後の後片付け（`merged`）が終わった時点 |

リリース後テストを省くのは、マージ前に実施できなかった受け入れ条件が無い場合
（`standard`）と、反映の結果を実行した経路とは別の経路で確かめられない場合
（`operation`）である。

**振り返りの要否はモードで違う。** `standard` は必ず行う。コードレビューの工程を通っており、
起票し損ねたものが無いかを確かめる場が要るためである。**`operation` は実行の手順そのものを
変えたときだけ行う**（設定値を 1 つ変えただけなら通さない。進め方を見直す材料が出ない）。

要否を決めるのは `development-workflow` である。この Skill は判定結果を受け取って実行する。
呼ばれた時点で、この工程が要ると判定されている。

## 手順

**先に、記録を残すリポジトリを決める。** 記録を置くのは、その変更を行ったリポジトリである。
起点の issue と、変更をリリースした Pull Request がある場所を指す。以降の `gh` は、すべて
このリポジトリへ向ける。

```bash
RECORD_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
```

**起票先とは別のものである。** 起票先は課題の性質が決めるため、上流リポジトリになる
ことがある（[out-of-scope の判断表](../out-of-scope/references/issue-target.md)）。記録の
投稿先は性質で変わらない。範囲外の課題を配布元へ回した変更でも、記録はこちらに残る。

**`--repo` を省かない。** この工程は `merged` の後に来るため、worktree が消えている。
`gh` は現在の作業ディレクトリからリポジトリを決めるので、省くと起点の issue やリリースした
Pull Request と違う場所へ記録が残り得る。`gh repo view` が別のリポジトリを返す位置に
いるときは、推測せずに名前を利用者に確かめる。

### 1. 起票の取りこぼしを拾う

**この工程で新しく起票を集めない。** 起票は見つけたその場で `/ndf:out-of-scope` が行う。
ここで行うのは、残っていないものを探すことである。

```bash
# その変更から出た課題の一覧（本文とコメントの両方を対象にする）
gh issue list --repo "$RECORD_REPO" --state all --search "<由来>"   # 例: "PR #177" / "issue #175"
```

`<由来>` は `out-of-scope` が起票のときに書いたものと同じ形にする。Pull Request を作る前に
見つけた課題は起点の issue の番号で残るため、`PR #<番号>` だけで探すと漏れる。**起点の
issue と Pull Request の両方で検索する。**

起票先が 2 つのリポジトリへ分かれた変更では、`--repo` を配布元へ替えてもう一度検索する。
配布元へ回した課題は、開発している側のリポジトリの検索には出ない。**替えるのは検索の
`--repo` だけで、記録の投稿先は変わらない。**

次の 3 か所と突き合わせる。番号が無いものが取りこぼしである。

| 突き合わせる場所 | 何を探すか |
| --- | --- |
| `quality-gates` の完了報告 | 「範囲外と判断したもの」に挙げた項目 |
| レビューの指摘 | 範囲外として resolve した指摘 |
| 実装計画のファイルの「やらないこと」 | 途中で足した項目 |

取りこぼしが見つかったら、この時点で `/ndf:out-of-scope` を呼んで起票する。**発見から
時間が空いているため、どのファイルのどの行で、なぜ範囲外と判断したのかを先に確かめる。**

### 2. 観点ごとに事実を集める

推測ではなく、記録に残っているものから集める。

| 観点 | 見る場所 | 集めるもの |
| --- | --- | --- |
| 受け入れ条件 | 仕様と実装計画のファイル | 途中で変えた条件と、その理由 |
| 手戻り | レビューのラウンド数、修正のコミット | 何回差し戻したか、何が原因だったか |
| 見落とし | リリース後テストの結果 | マージ前に踏めなかった経路 |
| 範囲 | 実装計画のファイルと起票した課題 | 範囲を広げた判断、外した判断 |
| 工程 | 実際に通った工程 | 飛ばした工程と、その結果 |
| context window | `/ndf:skill-stats --agents --session <conductor のセッション>` | 層ごとの固定費と実作業、束ねる候補・割る候補・worker を使いすぎの目印 |
| 用語集 | `python3 "$SCRIPTS/glossary.py" diff --base <起点> --head <終点>` | その変更で足された語・廃止された語・意味の変わった語（コンテキストごと） |

**用語集の変化は、その変更の起点と終点の 2 つの版で `glossary.py diff` を打って取る。** 起点と終点は
「Pull Request の番号を特定する」で決めた Pull Request の base と merge のコミットである。用語集の設定の無い
プロジェクトでは `items` が空になり、投稿に何も載せない。

**context window の値は測って取る。** 記憶や体感で書かない。ミッションを複数のセッションで
通したときは `--session` を繰り返して 1 つの表にする。**目印の判定は記録ごとの比で行われる**
ため、固定費の水準が違うセッションを束ねても判定の意味は変わらない。中央値と合計の列は
分布の目安として読む。

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/skill-stats/scripts/skill-stats.py" \
  --agents --session "$CLAUDE_CODE_SESSION_ID"
```

### 3. 次に変えることを決める

**1 つ以上決める。** 変えないと判断したなら、変えない理由を書く。何も書かない状態を
残さない。

変えることは、次の 3 つのどれかへ落とす。落とせないものは「変えること」になっていない。

| 落とし先 | 例 |
| --- | --- |
| Skill の手順の変更 | 工程の追加、手順の並べ替え、確認の追加 |
| プロジェクトの取り決めの変更 | `AGENTS.md` / `CLAUDE.md` の記述 |
| 次の変更で試すこと | 起票して残す |

起票して残す先は、[out-of-scope の判断表](../out-of-scope/references/issue-target.md)が決める。

### 4. 記録を残す

**記録の本体はコメント 1 件である。** 同じ内容を複数の場所へ投稿しない。後から直したときに
片方が古くなる。

#### 投稿先を決める

| 起点 | 記録の本体を置く場所 | 辿る経路 |
| --- | --- | --- |
| 1 件の issue | その issue へのコメント | その issue の本文末尾へ 1 行 |
| 複数の issue（ミッション） | そのミッションをリリースした Pull Request へのコメント | 対象のすべての issue の本文末尾へ 1 行 |
| 起点の issue を持たない変更 | その変更の Pull Request へのコメント | 追加の 1 行は要らない |

**閉じた issue にもコメントは投稿できる。** GitHub が拒むのは locked のときだけである。
投稿が失敗したときは、別の場所へ回さずに止めて利用者に伝える。

#### Pull Request の番号を特定する

起点が 1 件の issue なら、番号はそのまま使える。**残る 2 つの場合だけ番号を引く。**
この工程は `merged` の後に来るため、ブランチもworktree も残っていない。番号はマージ先の
先頭のコミットから引く。

**番号は 3 つの手順で引く。各手順は直前の手順の出力を受け取る。**

| 手順 | 入力 | 出力 | 止まる条件 |
| --- | --- | --- | --- |
| 1. 開発の起点を解決する | `.ndf/worktree.json` の `base_branch`、origin | `$dev_base` | 設定したブランチが origin にもローカルにも無い |
| 2. 記録対象の基準ブランチを決める | 下表の場合、`$dev_base`（起点の issue を持たない変更だけが使う） | `$record_base` | ミッションをリリースした先のブランチを判別できない |
| 3. 基準コミットから Pull Request を引く | `$record_base`、`$RECORD_REPO` | マージ済みの Pull Request 1 件の番号 | マージ済みへ絞った結果が 1 件でない |

**手順 1: 開発の起点を解決する**

**ベースブランチは対象リポジトリが決める。** 字面で書かず、`merged` / `deploy` /
`pr-review` / `cherry-pick-pr` と同じ解決を使う。

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

**手順 2: 記録対象の基準ブランチを決める**

| 場合 | 起点にするコミット |
| --- | --- |
| 起点の issue を持たない変更 | その変更をマージした先のブランチ（起点。`.ndf/worktree.json` の `base_branch`）の先頭 |
| ミッション | そのミッションをリリースした先（正式版のチャネルのブランチ）の先頭 |

**ミッションを対象にする場合は、そのミッションをリリースした先を使う。**
`$dev_base` は開発の起点であり、リリースした先とは限らない。開発の起点とリリースの先が別の
ブランチであるリポジトリで `$dev_base` のまま引くと、起点の先頭に
関連付いた別の Pull Request を選び、誤った番号へ記録を投稿する。

```bash
# 起点の issue を持たない変更 — 開発の起点をそのまま使う
record_base=$dev_base
```

```bash
# ミッション — リリースした先を使う。**リリースした先は対象リポジトリが決める。** 開発の起点を
# そのままリリースに使っているリポジトリでは `$dev_base` と同じ値になり、正式版のチャネルを
# 分けているリポジトリでは別の値になる。字面で書かず、既定ブランチ（origin の HEAD が
# 指す先）で確かめる。取れないときは推測せず番号を利用者に聞く
record_base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
[ -n "$record_base" ] || {
  printf 'NOTE: ミッションを配布した先のブランチを判別できません。番号を利用者に聞いてください\n' >&2
  exit 1
}
```

**手順 3: 基準コミットから Pull Request を引く**

手順 2 で決めた `$record_base` の先頭のコミットで番号を引く。

```bash
gh api "/repos/$RECORD_REPO/commits/$(git rev-parse "origin/$record_base")/pulls" \
  --jq '.[] | select(.merged_at) | "#\(.number) \(.base.ref) <- \(.head.ref)"'
```

**マージ済みを表す `merged_at` を持つものへ絞る。** そのコミットを含む未マージの枝の Pull Request も返るため、
絞らないと 2 件以上になる。絞った結果が 1 件でないときは、推測で投稿せず番号を利用者に聞く。

#### 投稿する

書式は `markdown-writing` に従う。本文の雛形は次のとおりである。

```markdown
## 振り返り（<YYYY-MM-DD>）

**対象**: [issue #NNN](...) / [PR #NNN](...)

（この変更で何を作ったか。1〜2 段落）

## 何が起きたか

（観点ごとに集めた事実。手戻りの回数、見落とした経路など）

## context window の大きさ

| 層 | フェーズ | モデル | 件数 | 固定費の中央値 | 実作業の中央値 | 実作業 < 固定費 | 最大充填の最大 | 目印 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |

| 層 | 件数 | 固定費の合計 | 実作業の合計 | 総消費 |
| --- | ---: | ---: | ---: | ---: |

| フェーズ | supervisor | supervisor の実作業 | worker の件数 | supervisor と worker の固定費の合計 | 目印 |
| --- | ---: | ---: | ---: | ---: | --- |

（`束ねる候補` / `割る候補` / `worker を使いすぎ` の目印が付いた行ごとに、束ねる・割る・
worker を減らす・そのままのどれにするかと、その理由を 1 行）

## 用語集の変化

（`glossary.py diff` の結果。0 件なら節ごと書かない。コンテキストごとに 1 行）

| コンテキスト | 足した語 | 廃止した語 | 意味を変えた語 |
| --- | --- | --- | --- |

## 次に変えること

| 変えること | 落とし先 | 状態 |
| --- | --- | --- |
| ... | `<Skill 名>` の手順 | この変更で反映済み / #NNN として起票 |

## 途中で起票した課題

| 番号 | 何を見つけたか | 見つけた場面 |
| --- | --- | --- |
| #NNN | ... | 実装中 / レビュー中 |
```

**context window の表は貼るだけにする。** 載せてよいのは測定が出す値（数値・層・フェーズ・
作業種別・モデル名・終わり方・フェーズの中の連番）と、そこから導いた目印と判断の理由の文
だけである。**プロンプト・応答の本文・ファイルのパス・サブエージェントの識別子は投稿しない**
（#159）。測定の出力はこの列だけを持つため、加工せずに貼れば足りる。

```bash
gh issue comment <issue番号> --repo "$RECORD_REPO" --body-file <記録のファイル>   # 起点が 1 件の issue
gh pr comment <PR番号> --repo "$RECORD_REPO" --body-file <記録のファイル>         # ミッション / 起点の issue を持たない変更
```

#### 辿る経路を作る

対象の issue の本文末尾へ、投稿したコメントの URL を 1 行足す。

```text
振り返り: https://github.com/<所有者>/<リポジトリ>/issues/<番号>#issuecomment-<識別子>
```

**`gh issue edit --body` は本文を全文で書き直す。** いまの本文を読み出してから足す。

```bash
gh issue view <issue番号> --repo "$RECORD_REPO" --json body --jq .body > /tmp/issue-body.md
printf '\n振り返り: %s\n' "<コメントの URL>" >> /tmp/issue-body.md
gh issue edit <issue番号> --repo "$RECORD_REPO" --body-file /tmp/issue-body.md
```

ミッションでは、対象のすべての issue へ同じ URL の 1 行を足す。起点の issue を持たない変更では
この手順が要らない。記録は Pull Request 自身に付いている。

**設計判断の理由と実測の結果を残す。** Skill の挙動そのものは各 `SKILL.md` が正であり、
ここに書き写さない。書くのは、そこに書かない理由と、判断の材料になった実測である。

## 書かないこと

| 書かないもの | 理由 |
| --- | --- |
| 個人の働き方への評価 | 対象は進め方であって人ではない（`markdown-writing` のルール 5） |
| 成果物の良し悪し | コードレビューの工程が扱う |
| 経緯の時系列そのもの | git の履歴と Pull Request に残っている |

この工程に入ったら進捗記録 `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "振り返り"` を 1 行打つ（issue の本文とボードの両方に残る。`$SCRIPTS` の決め方は `development-workflow` の `references/scripts-lookup.md`、3 層では起動指示の「進捗記録」を使う）。 **入口のこの記録ではボードの `Status` を書かない。** 先に `Done` にすると、ボードの `Auto-close issue` が課題を閉じ、reopen の手段が報告から落ちる。

## ミッションを閉じる

**振り返りを通る変更では、この工程がその実行の最終工程である。** 記録を投稿した後に、
`progress-tracking` の「ミッションを閉じる」を行う。**手順はそこが正本で、ここには写さない。**
ボードの `Status` を `Done` にするのも、課題を閉じるのも、その手順の中で行う。

## 蓄積した課題を棚卸しする

**「ミッションを閉じる」の後に `/ndf:issue-upkeep` を呼ぶ。** 順序を逆にすると、`issue-upkeep` の
手順 1 が読む「このミッションで閉じた課題」がまだ閉じていない。振り返りが拾うのは、この変更から出た
取りこぼしである。**変更をまたいで溜まった課題そのもの**は対象にしていない。

対象が 0 件ならその Skill 自身が飛ばす。

**振り返りはクラスタの発見を担わない。** クラスタは、同じ修正レイヤーを指す課題の集まりで
ある。振り返りは 1 回の変更を見るため、変更をまたいで溜まった課題どうしの関係が見えない。
見つけるのは `issue-upkeep` の「ルートコーズ」である。

## 関連

- `/ndf:release-verification` — この工程の前に行うリリース後テスト
- `/ndf:out-of-scope` — 取りこぼしを見つけたときの起票
- `/ndf:plan-to-spec` — 決まった仕様の永続化（振り返りとは別の出力物）
- `/ndf:progress-tracking` — 「ミッションを閉じる」の正本
- `/ndf:issue-upkeep` — 蓄積した課題の棚卸し（この工程の最後に呼ぶ）
