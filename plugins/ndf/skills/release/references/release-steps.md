# リリースの手順とサブコマンド

**リリースの書く操作（リリースブランチの組み立て・マージ・リリース記録・タグと GitHub Release）は、利用者のリポジトリの
workflow が `ndf release-steps` で打つ。** 会話のセッションが打つのは、承認資料を組む・見せる・回答を書く 3 つと、
リリースの後の確かめだけである。workflow の置き方と、会話のセッションとの分け方は [release-workflow.md](release-workflow.md) にある。

この文書は 4 つを持つ。リポジトリに固有のリリースの手順（`.ndf/release.json`）の書き方・サブコマンドの読み方・
リリース記録・退避先の回収である。

## 例: 正式版 2.4.0 を出すリポジトリ

正式版を出すたびに、リリースの Pull Request へ計測の記録を載せたいリポジトリがあるとする。

```json
{
  "version": 1,
  "steps": [
    {
      "name": "計測の記録",
      "stage": "production",
      "command": ["python3", "tools/snapshot.py", "--released", "{version}"],
      "writes": ["docs/metrics/"],
      "guide": "docs/metrics/README.md",
      "timeout_seconds": 600
    }
  ]
}
```

workflow の `assemble`（`ndf release-steps assemble --channel production`）が、版数と CHANGELOG を書いた後に
`python3 tools/snapshot.py --released 2.4.0` をリポジトリの根で実行し、書いたファイルを `release/v2.4.0` のコミットへ入れて
リリースの Pull Request を作る。**会話のセッションでは手順を走らせない。**

`assemble` は `guide` を読まない。リリースの Pull Request ができたら、エージェントが `docs/metrics/README.md` を読み、
判断の要る部分だけを `release/v2.4.0` へ書き足してコミットする。書き足すと HEAD が動くため、承認資料の提示（`present`）の前に行う。

## 段階の値

| `stage` | 走る時点 |
| --- | --- |
| `production` | 本番の組み立て（`assemble --channel production`） |
| `dev` | 開発版の組み立て（`assemble --channel dev`） |
| `verify` | リリース後のテスト。`ndf release-steps` は流さない。リリース後テストで、記録の前に手で打つ |

## 設定の項目

形は [../schemas/release.schema.json](../schemas/release.schema.json) が定める。

| 項目 | 必須 | 何を決めるか |
| --- | --- | --- |
| `version` | 必須 | 設定の形のバージョン。`1` だけを読む |
| `steps` | 任意 | コマンドの並び。書いた順に実行し、最初に落ちたコマンドで止める。無ければ手順なし |
| `steps[].name` | 必須 | 出力と完了報告に出す名前 |
| `steps[].stage` | 必須 | `production` / `dev` / `verify` |
| `steps[].command` | 必須 | 引数の配列。シェルを通さない。`{version}` を配るバージョンで置き換える（置き換えるのはこの 1 つだけ） |
| `steps[].writes` | 任意 | コマンドが書くパスの前置き（根からの相対）。`[]` は何も書かないコマンド |
| `steps[].guide` | 任意 | エージェントが判断する部分の手引き |
| `steps[].timeout_seconds` | 任意 | コマンド 1 つの時間の上限。既定 600、1 以上 |
| `undistributed` | 任意 | リリース対象に関わらないファイルの形。既定（テストのファイルの表）へ足す |
| `bump_undistributed` | 任意 | リリース対象に関わらない変化だけの単位もバージョンを上げるか（既定は偽） |
| `changelog` | 任意 | CHANGELOG のファイル（既定は根の `CHANGELOG.md`） |

**`writes` の外を変えたら送らない。** `assemble` はコミットの前に、変わったファイルがバージョンの保存先・CHANGELOG・
`writes` の中かを確かめ、外が変わっていればリリースブランチを送らずに止まる。コマンドの成否は終了コードだけで決まる。

## 結果の読み方

どのサブコマンドも結果 JSON（[result.schema.json](../../../contracts/schemas/result.schema.json)）を標準出力の最後の 1 行に出す。
`status` と終了コードの区分、`cause.kind`（停止理由）の意味は [終了コードと停止理由](../../../docs/contracts/exit-codes.md) にある。
止まったら `summary` と `next` を読む。

| 止まり方 | 起きること | どうするか |
| --- | --- | --- |
| `check_failed`（1） | 設定の手順のコマンドが 0 以外で終わった・時間切れ | `summary` の手順の名前と標準エラーの末尾を読んで直す。直すまでリリースの Pull Request を作らない |
| `precondition`（3） | 設定が読めない（壊れた JSON・未対応の `version`・必須の項目が無い・値が不正）・`writes` の外を変えた | `summary` の項目を直す |
| `refused`（1） | 本番ブランチ宛ての別のリリースの PR が開いている・回答の時点で HEAD が動いた・タグが別のコミットを指す | `summary` を報告して止まる |

**止まったものを通ったと報告しない。** 走らなかったコマンドを、通ったと書かない。

## サブコマンド

| 場 | サブコマンド | すること |
| --- | --- | --- |
| 会話のセッション | `materials` | ブランチを作らずに承認資料を組む（含む PR・上げるバージョン・指示書の変更・届き方） |
| 会話のセッション | `present` | 承認資料を、提示した HEAD の SHA を添えてリリースの PR へコメントする。資料の対象のコミットが今の HEAD でなければ提示しない |
| 会話のセッション | `answer` | 回答を証跡のコメントに書き、PR を読み直して今の HEAD への判定として届いたかを確かめる |
| 会話のセッション | `records` | PR のリリース記録とリリース後テストの記録を読む |
| 会話のセッション | `verification-record` | リリース後テストの記録をリリース記録と同じ PR へ書く（`release-verification`） |
| workflow | `assemble` | リリースブランチを組み立て（版上げ・CHANGELOG・設定の手順）、リリースの PR を作る |
| workflow | `approval-check` | 証跡を読んで判定し、PR の HEAD へ commit status `ndf/release-approval` を付ける |
| workflow | `merge` | 証跡と必須のチェックの後に、ブランチ戦略の設定の `merge` の主体と方式で入れる。要ればベースブランチへ戻す PR も |
| workflow | `record` | 本番ブランチへのマージの後に、含めた PR の各々へリリース記録を書く |
| workflow | `publish` | 本番ブランチへ入ったコミットのバージョンで、タグと GitHub Release のうち足りないものを作る |

```bash
ndf release-steps materials --base <ベースブランチ> --production <本番ブランチ> --channel production --out <承認資料>
ndf release-steps present --pr <リリースの PR> --material <承認資料>   # outputs.head が提示した HEAD
ndf release-steps answer --pr <リリースの PR> --answer approved --presented <提示した HEAD> --by user   # 承認を得てから
ndf release-steps records --pr <リリース記録の PR>
```

**`answer` は承認を得てから打つ。** `--by` は回答した担い手で、利用者が `user`、MVV 判定で通したときが `mvv` である
（`pace: fast` と `pace: auto` だけ。条件は [pace.md](../../development-workflow/references/pace.md)）。管理者の迂回でマージする設定では、
`user` の証跡だけを数える。却下は `--answer rejected` で書く。

**承認の後に HEAD が動けば、マージされない。** `merge` は証跡を読み直し、今の HEAD が承認されていることを確かめてから入れる。
承認の後にリリースブランチへコミットを足したら、`materials` から組み直して `present` で見せ直し、承認を取り直す。

workflow を置かないリポジトリ（ほかの CI・手元）では、workflow の 4 つを同じ並びで手で打つ（[release-workflow.md](release-workflow.md) の
「ほかの CI から呼ぶ」）。

### CHANGELOG の本文

**`assemble` が CHANGELOG のバージョンのセクションに書くのは、見出しと含む PR の題名だけである。** 利用者向けの本文は、
リリースの PR のブランチで LLM が組み直してコミットする（承認資料の提示の前に）。

- 各 PR の本文の `## 利用者向けの変化` の箇条を、1 行目の末尾に `（#番号）` を付けて並べる。字下げした子の箇条は親の項目の下に
  1 ステップの入れ子（`  - `）で残す。セクションが無い・「無し」の PR は題名のまま残す
- PR の本文の `## 移行の手順` の箇条は、バージョンのセクションの `### 移行の手順` の下へ `（#番号）` つきで並べる（0 件なら見出しを作らない）
- 後の PR が `## 置き換える箇条` で指した前の PR の箇条は載せない（下の「置き換える箇条」）
- plugin の README は書き換えない

### 置き換える箇条

同じバージョンの後の PR が前の PR の振る舞いを変えたときは、後の PR の本文に次のセクションを書き、前の PR の「利用者向けの変化」の
箇条を CHANGELOG.md のバージョンのセクションから外す（上の「CHANGELOG の本文」で組み直すとき）。マージ済みの前の PR の本文は直さない。

```markdown
## 置き換える箇条

- #1825: cross-refactoring の init は、対象の Pull Request が Draft でない場合に処理を止める
```

- 1 行に 1 つ、`- #<前の PR の番号>: <前の PR の「利用者向けの変化」の箇条の 1 行目>`。CHANGELOG の案から写してよい
- 比べるときは両側の末尾の `（#N…）` / `(#N…)` を外し、空白の続きを 1 つにする。文が一致した箇条だけを外す
- 指す PR は同じバージョンに含む PR に限る。前の PR の `## 未検証・残る危険` と `## 移行の手順` は外さない

## デプロイの後にリリース記録を置く

**どのリリースの形でも、リリースを実施した後に、実施後の値で `段階: 本番` のリリース記録を置く。**
読む側は Pull Request の本文とコメントのうち最後のリリース記録を読むため、デプロイの後の記録が無いと
本番まで出たスプリントの課題が閉じられない。

| 形 | 置き方 |
| --- | --- |
| workflow でリリースするもの | 本番ブランチへのマージの後に、workflow が `ndf release-steps record --pr <リリースの PR> --production <本番ブランチ> [--production-sha <本番へ入れたコミット>]` を打ち、含めた PR の各々へ書く。同じバージョンの記録が既にある PR には書かない |
| それ以外 | SKILL.md の「出力物」と同じ 3 行の `## リリース記録` を、デプロイした値で記録の Pull Request へコメントで足す |

書いた記録は `ndf release-steps records --pr <PR>` で読む。`record` は書く token の書き手が読み手に信用されるか（write 以上の権限）を
先に確かめ、信用されなければ 1 件も書かずに `precondition`（3）で止まる。`github.token` で書いた記録は読まれない。

**CD の結果は、CD の workflow の実行の結果で読む。** `release.deliveries` の行の `result.workflow` が名指す workflow の、本番ブランチへの
マージのコミットの実行を見る。

```bash
gh run list --workflow <workflow のファイル名> --branch <本番ブランチ> --commit <マージのコミット> --json databaseId,status,conclusion,url
```

| 値 | 意味 | 続き |
| --- | --- | --- |
| 成功 | 実行が `completed` で `conclusion` が `success` | リリース後テストへ進む |
| 失敗 | 実行が `success` 以外で終わった | `段階: 反映失敗（<理由>）` を書いて止まる。デプロイの工程は完了にならず、スプリントは閉じられない |
| 未確定 | 実行が見つからない・待ちの上限に当たった | 未確定と書いてリリース後テストへ進む。リリース後テストが本番の状態を直接確かめ、その記録が無ければスプリントは閉じられない |
| 無し | 本番チャネルへ自動で届く行が無い | 記録に CD の行を足さない |

待ちの上限と照会の間隔は [completion-check.md](completion-check.md) に従う。読む側は最後の記録を使うため、CD を直して
記録を書き直すと、成功の記録で閉じられる。

## 退避先の回収

**本番へ出したら、そのバージョンに入ったブランチの退避先を回収の候補として挙げ、人の承認を得てから消す。**
`merged` が worktree を外すときに退避したファイル（`<共通の git ディレクトリ>/ndf/worktree-trash/`）は、
戻す必要が出るのが本番の前だけだからである。ただし退避先は Git にも本番のコミットにも無い利用者のファイル
（手で直した `.env`・未追跡のメモ）を含み、消すと戻せない。**本番の承認はこの削除の承認を兼ねない**
（共通原則の C3・C4）。候補（退避先のパスと `du -sh <退避先>` の容量）を人へ示し、`AskUserQuestion`（`metadata.source` は `ndf-ask:redline:C4`）で承認を得たときだけ、承認した退避先の名前を
`--yes --only <名前>...` で渡して消す。名前の外の退避先（候補を挙げた後に増えたもの）は消さない。
承認が無ければ退避先は残す。本番へ出たコミット（タグ・本番チャネルのブランチ）を渡して打つ。開発版のデプロイでは打たない。

```bash
ndf worktree trash --ref <本番に出たコミット>        # 候補を挙げるだけ（消さない）。next に候補の名前を並べた消す 1 行が載る
ndf worktree trash --ref <本番に出たコミット> --yes --only <承認した退避先>...  # 承認した名前だけを消す
```

候補の件数（`metrics.candidates`）・消した退避先の件数（`metrics.removed`）を完了報告に書く。
退避先とジャーナルの形は `merged` の SKILL.md にある。

## 設定したリリースの形で組むサブコマンド

`ndf release-steps` の `materials`・`assemble`・`publish` は、設定 `.ndf/project.json` の `branch_strategy.release`
（リリースの形・バージョンの保存先・名前の書式）とリリース方式で動く。プラグインの登録の無いリポジトリでも組む。
workflow からの起動は [release-workflow.md](release-workflow.md) にある。

例: Flutter のリポジトリ（`release_branch`・ベースブランチ `dev`・本番ブランチ `prod`・保存先 `pubspec`・タグ `v{version}`）で、スプリント `sprint/a` と `sprint/b` を集めて
承認資料を組む。

```bash
ndf release-steps materials --base dev --production prod --channel production --units sprint/a sprint/b --out materials.md
```

承認資料には届き方の行と、`r: 1.2.2 → 1.2.3（範囲の起点 <SHA>）` の形のバージョンの行が出る。

| 引数 | 渡すとき |
| --- | --- |
| `--units <ブランチ>...` | リリース方式が `release_branch`（`materials`・`assemble`）。本番ブランチの HEAD へ順に `--no-ff` でマージした先頭を範囲の終点にする |
| `--pr <番号>` | 承認の対象が `production_pr`（`materials`）。その PR を範囲にする。`assemble` はバージョンアップの PR を作らない |
| `--plugin <単位>` | バージョンの単位が複数あるとき（保存先 `plugin`）、リリースブランチの名前に使う単位 |

範囲の起点はリリース方式で決まる。`merge_base_branch` は本番ブランチの HEAD、`trunk` の `release_pr` と `release_branch` は
バージョンの単位ごとの前のリリースの起点（タグ・今のバージョンが保存先に入ったコミット・本番ブランチの最初のコミットの順）である。

| 設定のキー（`branch_strategy.release`） | 既定 | 例 |
| --- | --- | --- |
| `tag` | 無し（必須。`null` はタグを作らない） | `v{version}`・`{plugin}--v{version}` |
| `branch` | `release/v{version}` | `rel-{version}` |
| `changelog_heading` | `## [{plugin} {version}] - {date}` | `## [{version}] - {date}`（Keep a Changelog） |

リリース対象外のファイルの既定は、テストのファイルの表（`contracts/domains/test-files.json`）のすべての行である。
`.ndf/release.json` の `undistributed` に書いた形は、この既定へ足される。

停止理由（結果 JSON の `cause.kind`）:

| 種類 | 起きること |
| --- | --- |
| `precondition` | 扱わないリリースの形（リリースの形の表 `contracts/domains/release-forms.json` で `supported` が偽）・保存先の種類が無い・保存先のバージョンが読めない・`--units` か `--pr` が無い・スプリントの衝突・リリースブランチが既にある |
| `not_applicable` | バージョンを上げる変化が無い・リリース方式がバージョンアップの PR を作らない（`assemble`）・`tag` が `null`（`publish`） |
| `refused` | 本番ブランチ宛ての別のリリースの PR が開いている・同じ名前のタグが別のコミットを指している（`publish`） |
