# 盤面への記録

盤面（GitHub Projects）へ書くときの、宛先の決め方・アイテムの追加・問い合わせの減らし方。
**リポジトリに `.ndf/projects.json` があるときだけ動く。**

盤面の設定（宣言ファイルの形・要るフィールド・工程との対応）は
[references/projects-tracking.md](../../development-workflow/references/projects-tracking.md)
にある。

## 宛先を決める

**先に当たったものを採る。**

| 順 | 経路 | 決め方 |
| --- | --- | --- |
| 1 | 宣言 | `.ndf/projects.json` の `owner` と `number` |
| 2 | その issue が載っている盤面 | GraphQL の `issue.projectItems.nodes.project` |
| 3 | リポジトリにリンクされた盤面 | GraphQL の `repository.projectsV2` |
| 4 | 所有者の盤面が 1 つだけ | `gh project list --owner <owner>` |
| 5 | 見つからない | 盤面へは書かず、issue の側だけに残す |

**2 の取得には GraphQL を使う。** `gh issue view --json projectItems` は盤面のタイトルと
`Status` しか返さず、`owner` と `number` を持たない。

```console
$ gh api graphql -f query='{ repository(owner:"acme", name:"demo") {
    issue(number:113) { projectItems(first:10) { nodes { project {
      number title owner { __typename ... on Organization { login } ... on User { login } } } } } } } }'
```

**2〜4 で決まった宛先は宣言へ書き戻す。** 次回からは 1 の経路で決まり、問い合わせが要らなく
なる。書き戻しは作業ツリーの中で行い、その変更の Pull Request に載せる。

## 同じ盤面を何度も読まない

**`gh project item-list --limit 1000` は GraphQL で、取得の点数が REST とは別の上限を持つ**
（#271）。2026-09-04 の実測では、10 件の課題へ 2 つのキーを書こうとした時点で
`API rate limit exceeded` に達し、以後の記録がすべて捨てられた（終了コードは 0 のまま、
出力も無い）。**書き込んだつもりで何も残らない状態が、上限に達したことによっても起きる。**

| 呼び出し | 1 回あたりの取得 | 10 件 × 2 キーで |
| --- | --- | --- |
| `project view` | 盤面 1 つ | 20 回 |
| `project item-list --limit 1000` | **アイテム 1000 件** | 20 回 |
| `project field-list` | フィールド 17 個 | 20 回 |

解決した識別子（盤面とアイテム）を課題ごとに控え、次回からは `item-edit` だけを呼ぶ。
**アイテムの識別子は issue と盤面の組で決まり、工程が進んでも変わらない。**

控えの位置は `git rev-parse --git-common-dir` が返すディレクトリの下（`<共通の git
ディレクトリ>/ndf/`）である。**作業ツリーでは `.git` がファイルである**ため、`.git/ndf/` を
作ろうとすると失敗する。共通の git ディレクトリなら、作業ツリーを消しても控えが残り、同じ
リポジトリの複数の作業ツリーで 1 つの控えを共有できる。

## アイテムが無いとき

**追加を試みる。** アイテムの追加は 1 つの issue を 1 つの盤面へ載せる操作で、取り消しも
1 コマンドである。載っていない課題は進行が一切記録されない。

```console
$ gh project item-add 1 --owner devbasex --url https://github.com/devbasex/ai-plugins/issues/372
PVTI_lADOEIyqkc4Bh-5Mzg5eaBA
```

**組織が所有する盤面で成功する**（2026-09-04 の実測）。盤面の所有者の種別は
`gh project view --format json` の `.owner.type` が返す。

**`unknown owner type` は、所有者の種別が原因とは限らない。** GraphQL の上限に達した状態で
`item-list` を呼ぶと、同じ文言が返る。

```console
$ gh api graphql -f query='{viewer{login}}'
{"errors":[{"type":"RATE_LIMIT","code":"graphql_rate_limit", ...}]}

$ gh project item-list 1 --owner devbasex --limit 3 --format json
unknown owner type
```

`gh` は所有者の種別を GraphQL で問い合わせてから盤面の操作を組み立てる。問い合わせが失敗
すると、種別が決まらなかったこととして同じ文言を出す。**この文言を見たときは種別ではなく
上限を先に疑う。**

## 盤面そのものが無いとき

**作成は自動で走らせず、承認を得てから行う。** 盤面は組織から見える資産で、作った後に誰が
いつ消すかが決まらない。承認が得られなければ、記録は issue の側だけに残して工程を進める。

| 操作 | コマンド |
| --- | --- |
| 盤面を作る | `gh project create --owner <owner> --title <題名>` |
| 単一選択のフィールドを作る | `gh project field-create <番号> --owner <owner> --name 進行 --data-type SINGLE_SELECT --single-select-options "<工程名をカンマ区切り>"` |
| 文字列のフィールドを作る | `gh project field-create <番号> --owner <owner> --name 作業ツリー --data-type TEXT` |

`stage` の選択肢は工程表の 15 行、`mode` は 3 つのモード名を渡す。いずれも
`development-workflow` の工程表が基準である。

**作成とフィールドの追加には `project` スコープが要る。** 無ければ何もせず、従来どおり工程を
進める。

## 既知の制約

### 日本語のフィールド名は読み返しに使えない

`gh project item-list --format json` が返す JSON は、フィールド名を小文字化する際に
**日本語のフィールド名の先頭 1 文字を壊す**（`進行` が `���行` になる）。値は壊れない。

このため、**盤面から現在の値を読み返す用途にこの出力を使わない**。フィールドの名前と識別子は
`gh project field-list` から取る（そちらは壊れない）。書き込みだけを行う経路はこの制約を
踏まない。
