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

リリース後テストを省くのは、マージ前に実施できなかった受け入れ条件が無い場合である
（`standard` のみ）。この場合も振り返りは行う。レビューの工程を通っており、起票し損ねた
ものが無いかを確かめる場が要るためである。

要否を決めるのは `development-workflow` である。この Skill は判定結果を受け取って実行する。
呼ばれた時点で、この工程が要ると判定されている。

## 手順

**先に、記録を残すリポジトリを決める。** 記録を置くのは、その変更を行ったリポジトリである。
起点の issue と、変更を配布した Pull Request がある場所を指す。以降の `gh` は、すべて
このリポジトリへ向ける。

```bash
RECORD_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
```

**起票先とは別のものである。** 起票先は課題の性質が決めるため、配布元のリポジトリになる
ことがある（[out-of-scope の判断表](../out-of-scope/references/issue-target.md)）。記録の
投稿先は性質で変わらない。範囲外の課題を配布元へ回した変更でも、記録はこちらに残る。

**`--repo` を省かない。** この工程は `merged` の後に来るため、作業ツリーが消えている。
`gh` は現在の作業ディレクトリからリポジトリを決めるので、省くと起点の issue や配布した
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
| 計画ファイルの「やらないこと」 | 途中で足した項目 |

取りこぼしが見つかったら、この時点で `/ndf:out-of-scope` を呼んで起票する。**発見から
時間が空いているため、どのファイルのどの行で、なぜ範囲外と判断したのかを先に確かめる。**

### 2. 観点ごとに事実を集める

推測ではなく、記録に残っているものから集める。

| 観点 | 見る場所 | 集めるもの |
| --- | --- | --- |
| 受け入れ条件 | 仕様と計画ファイル | 途中で変えた条件と、その理由 |
| 手戻り | レビューのラウンド数、修正のコミット | 何回差し戻したか、何が原因だったか |
| 見落とし | リリース後テストの結果 | マージ前に踏めなかった経路 |
| 範囲 | 計画ファイルと起票した課題 | 範囲を広げた判断、外した判断 |
| 工程 | 実際に通った工程 | 飛ばした工程と、その結果 |

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
| 複数の issue（まとまり） | そのまとまりを配布した Pull Request へのコメント | 対象のすべての issue の本文末尾へ 1 行 |
| 起点の issue を持たない変更 | その変更の Pull Request へのコメント | 追加の 1 行は要らない |

**閉じた issue にもコメントは投稿できる。** GitHub が拒むのは locked のときだけである。
投稿が失敗したときは、別の場所へ回さずに止めて利用者に伝える。

#### Pull Request の番号を特定する

起点が 1 件の issue なら、番号はそのまま使える。**残る 2 つの場合だけ番号を引く。**
この工程は `merged` の後に来るため、ブランチも作業ツリーも残っていない。番号はマージ先の
先頭のコミットから引く。

| 場合 | 起点にするコミット |
| --- | --- |
| 起点の issue を持たない変更 | その変更をマージした先のブランチ（起点。`.ndf/worktree.json` の `base_branch`）の先頭 |
| まとまり | そのまとまりを配布した先（正式版のチャネルのブランチ）の先頭 |

**起点のブランチは対象リポジトリが決める。** 字面で書かず、`merged` / `deploy` /
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

**まとまりを対象にする場合は、`$dev_base` ではなくそのまとまりを配布した先を使う。**
`$dev_base` は開発の起点であり、配布した先とは限らない。開発の起点と配布の先が別の
ブランチであるリポジトリで `$dev_base` のまま引くと、配布の Pull Request ではなく起点の
先頭に関連付いた別の Pull Request を選び、誤った番号へ記録を投稿する。

```bash
# 起点の issue を持たない変更 — 開発の起点をそのまま使う
record_base=$dev_base
```

```bash
# まとまり — 配布した先を使う。**配布した先は対象リポジトリが決める。** 開発の起点を
# そのまま配布に使っているリポジトリでは `$dev_base` と同じ値になり、正式版のチャネルを
# 分けているリポジトリでは別の値になる。字面で書かず、既定ブランチ（origin の HEAD が
# 指す先）で確かめる。取れないときは推測せず番号を利用者に聞く
record_base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
[ -n "$record_base" ] || {
  printf 'NOTE: まとまりを配布した先のブランチを判別できません。番号を利用者に聞いてください\n' >&2
  exit 1
}
```

決めた `$record_base` で番号を引く。

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

## 次に変えること

| 変えること | 落とし先 | 状態 |
| --- | --- | --- |
| ... | `<Skill 名>` の手順 | この変更で反映済み / #NNN として起票 |

## 途中で起票した課題

| 番号 | 何を見つけたか | 見つけた場面 |
| --- | --- | --- |
| #NNN | ... | 実装中 / レビュー中 |
```

```bash
gh issue comment <issue番号> --repo "$RECORD_REPO" --body-file <記録のファイル>   # 起点が 1 件の issue
gh pr comment <PR番号> --repo "$RECORD_REPO" --body-file <記録のファイル>         # まとまり / 起点の issue を持たない変更
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

まとまりでは、対象のすべての issue へ同じ URL の 1 行を足す。起点の issue を持たない変更では
この手順が要らない。記録は Pull Request 自身に付いている。

**設計判断の理由と実測の結果を残す。** Skill の挙動そのものは各 `SKILL.md` が正であり、
ここに書き写さない。書くのは、そこに書かない理由と、判断の材料になった実測である。

## 書かないこと

| 書かないもの | 理由 |
| --- | --- |
| 個人の働き方への評価 | 対象は進め方であって人ではない（`markdown-writing` のルール 5） |
| 成果物の良し悪し | レビューの工程が扱う |
| 経緯の時系列そのもの | git の履歴と Pull Request に残っている |

進行を盤面へ記録する場合は、[references/projects-tracking.md](../development-workflow/references/projects-tracking.md) の「`$SCRIPTS` を決める」でパスを解決してから
次を実行する（`.ndf/projects.json` が無いリポジトリでは何も起きない）。この工程で終わるため `status` も `Done` にする。

```bash
bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "振り返り"
bash "$SCRIPTS/projects-sync.sh" <issue番号> status "Done"
```

## 関連

- `/ndf:release-verification` — この工程の前に行うリリース後テスト
- `/ndf:out-of-scope` — 取りこぼしを見つけたときの起票
- `/ndf:plan-to-spec` — 決まった仕様の永続化（振り返りとは別の出力物）
