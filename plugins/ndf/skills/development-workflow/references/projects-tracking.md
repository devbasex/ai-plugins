# 進行を盤面へ記録する

工程の進行を GitHub Projects の盤面へ残す。セッションが変わっても、いまどの工程にいるか・
どのモードで判定したか・対応する作業ツリーと計画ファイルがどれかを引き継げる。

**この仕組みは任意である。** リポジトリに `.ndf/projects.json` が無ければ、呼び出しは何も
出力せず終了コード 0 で終わる。盤面が使えない環境でも工程はそのまま通る。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 盤面 | GitHub Projects v2 のプロジェクト 1 つ |
| アイテム | 盤面に載る 1 行。1 つの issue に対応する |
| 宣言 | リポジトリの `.ndf/projects.json`。これが無ければ何も動かない |

## 宣言ファイル

```json
{
  "version": 1,
  "owner": "devbasex",
  "number": 1
}
```

| キー | 内容 |
| --- | --- |
| `version` | 宣言の形式。現在は `1` のみ。違う値は読まない |
| `owner` | 盤面を持つ組織または利用者のログイン名 |
| `number` | 盤面の番号（`https://github.com/orgs/<owner>/projects/<number>`） |
| `fields` | 省略可。盤面のフィールド名を差し替える（後述） |

`owner` と `number` のどちらかが欠けていると、宣言は無効として扱う。盤面を特定できない
まま推測で書き込むと、別の盤面を更新しかねない。

**宣言はコミットする。** リポジトリの設定であり、同じ運用を他の開発者にも適用する。

## 盤面に要るフィールド

| キー | 既定の名前 | 種類 | 値 |
| --- | --- | --- | --- |
| `stage` | `進行` | 単一選択 | 工程表の行名（下の対応表） |
| `mode` | `モード` | 単一選択 | `light` / `operation` / `legacy-refactor` / `standard` |
| `worktree` | `作業ツリー` | 文字列 | `.worktrees/<ブランチ名>` |
| `plan` | `計画ファイル` | 文字列 | `issues/<ファイル名>` |
| `status` | `Status` | 単一選択 | `Todo` / `In Progress` / `Done`（GitHub の既定） |

`Linked pull requests` と `Repository` は GitHub が最初から持つ。1 つの issue から複数の
Pull Request が出る場合も、既定の `Linked pull requests` に並ぶため対応付けを新しく作らない。

名前を変えたい場合は宣言で差し替える。

```json
{ "version": 1, "owner": "devbasex", "number": 1,
  "fields": { "stage": "Stage", "plan": "Plan file" } }
```

## 工程と値の対応

**値は工程表の行名と一致させる。** 綴りの違う値を書き込むと、盤面の側に工程表に無い値が増える。
スクリプトは一覧に無い値を弾く。

| 工程表の行 | 記録する Skill | `stage` の値 |
| --- | --- | --- |
| 要求と受け入れ条件 | `requirements-design` | `要求と受け入れ条件` |
| 作業場所の用意 | `worktree` | `作業場所の用意` |
| 設計 | `design` | `設計` |
| ドキュメント再構成 | `document-restructuring` | `ドキュメント再構成` |
| ドキュメントレビュー | `design` が `pr` / `cross-review` / `merged` を呼ぶ | `ドキュメントレビュー` |
| 計画 | `implementation-plan` | `計画` |
| 実装 | `tdd-cycle` | `実装` |
| 構造改善 | `refactoring` / `cross-refactoring` | `構造改善` |
| 実装レビュー | `cross-review` / `pr-review` | `実装レビュー` |
| 完了判定 | `quality-gates` | `完了判定` |
| Pull Request | `pr` | `Pull Request` |
| 確定仕様化 | `plan-to-spec` | `確定仕様化` |
| 後片付け | `merged` | `後片付け` |
| 配布 | `release` | `配布` |
| リリース後テスト | `release-verification` | `リリース後テスト` |
| 振り返り | `retrospective` | `振り返り` |

## 工程名が変わったとき

**旧い名前を新しい名前として読む表は持たない。** 読み替えの表を置くと、それをいつ消すかを
決める工程が新たに要る。消す時期を決められないまま残ると、名前が 2 通り通用する状態が続く。

**記録済みの値は移行で 1 度だけ書き換える。** 改名は同じものの名前が変わっただけであるため、
書き換えても「そのとき何を通ったか」は失われない。

| 書き換える先 | 手段 |
| --- | --- |
| 盤面の単一選択 | 利用者が値を足し、既存のアイテムを付け替える |
| 課題の本文の `## 進行` の節 | 節の中の工程の一覧だけを差し替える。**節の外は書き換えない** |
| 控え（実行のたびに作り直される） | 放置してよい |

**書き換えた後は、現行の名前だけが通用する。** 一覧に無い値が来たら、名前の揺れではなく
記録そのものの誤りとして扱う。

## 呼び方

```bash
bash "$SCRIPTS/projects-sync.sh" <issue番号> <キー> "<値>"
```

**値は引用符で囲む。** スクリプトは引数をちょうど 3 つ受け取る。引用を落とすと、空白を含む値が
シェルの側で分割され、4 つ目の引数になる。上の対応表の `Pull Request` がこれにあたり、
引用せずに呼ぶと引数の検査で終了コード 2 になり、盤面には何も書き込まれない。空白を含まない値も
同じ形で書き、呼び方を 1 つに揃える。

```bash
bash "$SCRIPTS/projects-sync.sh" 186 stage "実装レビュー"
bash "$SCRIPTS/projects-sync.sh" 186 stage "Pull Request"
bash "$SCRIPTS/projects-sync.sh" 186 mode "standard"
bash "$SCRIPTS/projects-sync.sh" 186 worktree ".worktrees/fix/issue-186"
bash "$SCRIPTS/projects-sync.sh" 186 plan "issues/issue-186.md"
```

### `$SCRIPTS` を決める

手順は [scripts-lookup.md](scripts-lookup.md) にある。**盤面の記録だけが使う値ではない**
ため、独立した参照に置く（`worktree` は 3 本のスクリプトを呼ぶ）。

## 対象のアイテムの選び方

盤面は組織単位で持てる。`devbasex` の盤面には ai-plugins / devbase / devbase-samples の
issue が同居する。**issue 番号はリポジトリごとに独立している**ため、`ai-plugins#186` と
`devbase#186` は別の課題でありながら同じ番号を持つ。番号だけで選ぶと、並び順によっては
別のリポジトリのアイテムの工程・モード・パスを書き換える。

そこで `projects-sync.sh` は、番号に加えて**アイテムの所属リポジトリも照合する**。いま開いて
いるリポジトリを `gh repo view --json nameWithOwner -q .nameWithOwner` で取り、アイテムが持つ
`.content.repository` と突き合わせる。どちらも `<owner>/<repo>` の形である。

```console
$ gh project item-list 1 --owner devbasex --format json | jq -r '.items[0].content.repository'
devbasex/ai-plugins
```

一致するアイテムが無いときは、何も出力せず終了コード 0 で終わる。リポジトリ名を取得できない
ときも同じである。**別のリポジトリのアイテムを書き換えるより、何もしないほうが安全である。**

## 何もしない条件

次のいずれでも、何も出力せず終了コード 0 で終わる。

- `.ndf/projects.json` が無い
- `gh` または `jq` が無い
- 盤面への問い合わせや更新が失敗した（権限不足を含む。`project` スコープが要る）
- いま開いているリポジトリの名前を取得できない（上の「対象のアイテムの選び方」）
- 盤面に対象のアイテムが無い（取得が上限で切れた場合を除く。下の「既知の制約」）

**呼び出し側の誤りだけは 2 を返す。** 知らないキー・工程表に無い値・引数の不足がこれにあたる。
引用を落として空白を含む値が分割された場合も、引数の数が合わなくなるためここへ入る。
黙って進むと、綴りの違う値が盤面へ入るか、書き込んだつもりの値が入らない。

## 既知の制約

### アイテムの取得は 1000 件で切れる

`projects-sync.sh` は `gh project item-list --limit 1000` で盤面を読む。閉じたアイテムも
残るため、長く使う盤面では総数がこの上限を超えうる。超えると、盤面に載っている issue でも
対象が見つからない。

見つからない理由は 2 つあり、盤面へ登録していない場合と、取得が上限で切れた場合である。
前者は正常な状態なので黙って抜ける。**盤面の総数が上限を超えるときだけ、標準エラーへ知らせる。**
総数は `gh project item-list --format json` の `.totalCount` が持ち、`--limit` の値に左右されない。
取得した件数を上限と比べると、ちょうど上限と同じ件数の盤面で、切れていないのに知らせてしまう。

```console
NOTE: 盤面のアイテムは 1200 件あり、取得の上限 1000 を超えています。#186 が見つからないのは取り漏れの可能性があります
```

**終了コードは 0 のままである。** 進行管理が理由で工程を止めない。上限に達する盤面では、
`--limit` の値を上げるか、盤面を分けることになる。

### 日本語のフィールド名は読み返しに使えない

`gh project item-list --format json` が返す JSON は、フィールド名を小文字化する際に
**日本語のフィールド名の先頭 1 文字を壊す**（`進行` が `���行` になる）。値は壊れない。

```console
$ gh project item-list 1 --owner devbasex --format json | jq '.items[0] | keys'
[ "content", "id", "labels", "repository", "status", "title", "___ード", "___行" ]
```

このため、**盤面から現在の値を読み返す用途にこの出力を使わない**。フィールドの名前と
識別子は `gh project field-list` から取る（そちらは壊れない）。書き込みだけを行う
`projects-sync.sh` はこの制約を踏まない。
