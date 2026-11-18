# リリースコマンドを走らせる

**リポジトリに固有のリリースの手順は、リポジトリのリリースの設定 `.ndf/release.json` が
リリースコマンドとして持つ。** `release` は手順 3 で、設定されたコマンドを 1 行のコマンドで走らせるだけである。
リリースの設定が無いリポジトリでは何も起きない。

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

`release` の手順 3 で、バージョンと変更履歴を上げた後、リリースの Pull Request を作る前に次を打つ。

```bash
python3 "$SCRIPTS/release-steps.py" run --root . --stage production --version 2.4.0
rc=$?
echo "exit=$rc"
(exit "$rc")   # このブロックの終了コードをコマンドの実行の値へ戻す
```

コマンドは `python3 tools/snapshot.py --released 2.4.0` を根で実行する。変えたのが `docs/metrics/` の
中だけなら 0 で終わり、`guide: docs/metrics/README.md` を出す。エージェントはその手引きを読んで
判断の要る部分だけを書き足し、コマンドが書いたファイルと一緒にリリースの Pull Request へ入れる。

`$SCRIPTS` の決め方は [scripts-lookup.md](../../development-workflow/references/scripts-lookup.md) にある。

## リリース種別の値

| `--stage` | いつ渡すか | 走るコマンドの `stage` |
| --- | --- | --- |
| `production` | 本番リリース（正式版として公開する・本番へ反映する） | `production` / `any` |
| `verification` | 開発版のデプロイ（開発版として公開する・検証環境へ反映する） | `verification` / `any` |

## 設定の項目

形は [../schemas/release.schema.json](../schemas/release.schema.json) が定める。

| 項目 | 必須 | 何を決めるか |
| --- | --- | --- |
| `version` | 必須 | 設定の形のバージョン。`1` だけを読む |
| `steps` | 必須 | コマンドの並び。書いた順に実行し、最初に落ちたコマンドで止める |
| `steps[].name` | 必須 | 出力と完了報告に出す名前 |
| `steps[].stage` | 必須 | `production` / `verification` / `any` |
| `steps[].command` | 必須 | 引数の配列。シェルを通さない。`{version}` を配るバージョンで置き換える（置き換えるのはこの 1 つだけ） |
| `steps[].writes` | 必須 | 書いてよいパスの前置き（根からの相対）。`[]` は何も書かないコマンド |
| `steps[].guide` | 任意 | エージェントが判断する部分の手引き |
| `steps[].timeout_seconds` | 任意 | コマンド 1 つの時間の上限。既定 600、1 以上 |

**`writes` の外を変えたコマンドは失敗にする。** コマンドは任意のコマンドで、リリースの Pull Request に何が
混ざるかを本文から読めない。変えたかどうかは、コマンドの前後で `git status` に出たパスの内容を比べて
決める。コマンドの前から変わっていたパスも、コマンドが中身を変えれば（元へ戻しても）数える。

## 終了コード

`publish`・`cd-result`・`cd-guidance`・`record` の終了コードは本文の各節にある。

| 終了コード | 意味 | どうするか |
| --- | --- | --- |
| 0 | 設定が無い（何も出力しない）・リリース種別に合うコマンドが無い・すべてのコマンドが通った | `guide:` の行があれば、その手引きに従う |
| 1 | コマンドが 0 以外で終わった・時間切れ・書いてよい場所の外を変えた | 出力を読んで直す。直すまでリリースの Pull Request を作らない |
| 2 | `check` だけが返す。設定が無い | 設定の有無で分岐する側が使う |
| 3 | 設定が読めない（壊れた JSON・未対応の `version`・必須の項目が無い・値が不正） | 標準エラーに出た項目を直す |

**1 と 3 を 0 へ畳まない。** 走らなかったコマンドを、通ったと報告しない。

## 走らせる前に確かめる

```bash
python3 "$SCRIPTS/release-steps.py" check --root .                                  # 0 / 2（無い）/ 3
python3 "$SCRIPTS/release-steps.py" run --root . --stage production --version 2.4.0 --dry-run
```

`--dry-run` は実行せず、走らせるコマンドと、バージョンを置き換えた後のコマンドを出す。

## Claude Code のプラグインの形のサブコマンド

設定したコマンドとは別に、Claude Code のプラグインの形ではリリースの手順そのものをスクリプトが行う。
どれも結果 JSON を出し、`status` と終了コードで読む（`ok` = 0 で次へ、`gate` = 10 で承認を得る、
`stopped` = 1 / 2 / 3 なら `summary` を読んで直すか報告して止まる）。

```bash
# 手順 2: 公開前の承認資料のうち機械で作れる部分を書き出す
python3 "$SCRIPTS/release-steps.py" approval-facts --version <バージョン> --prs <PR番号>... [--prev-tag <タグ>] [--approval <承認資料>] --root .
# 手順 3: 版数と変更履歴を上げる
python3 "$SCRIPTS/release-steps.py" bump      --plugin <名前> --to <バージョン> --root .
python3 "$SCRIPTS/release-steps.py" changelog --version <バージョン> --prs <PR番号>... --root .
python3 "$SCRIPTS/release-steps.py" notes     --version <バージョン> --prs <PR番号>... --root .
# 手順 2 の承認資料の欄を埋める（開発版のデプロイの後）
python3 "$SCRIPTS/release-steps.py" notes     --version <バージョン> --prs <PR番号>... --approval <承認資料> \
  --verified claude,codex,kiro --ref <ベースブランチ> --root .
# バージョンを上げる他のプラグインとバージョン種別の候補を承認資料へ書く（変えるなら --set <名前>=MAJOR|MINOR|PATCH で書き直す）
python3 "$SCRIPTS/release-steps.py" changed-plugins --prs <PR番号>... --approval <承認資料> [--since <タグ>] --root .
# 本番: 承認資料の表のバージョン種別で上げるバージョンを返す（items の name と to を bump --plugin <name> --to <to> へ渡す）
python3 "$SCRIPTS/release-steps.py" changed-plugins --decided <承認資料> [--since <タグ>] --root .
# 承認を得たら、承認資料へ承認の記録を書く（<SHA> は提示した資料を書いた approval-facts の metrics.approved_sha）
python3 "$SCRIPTS/release-steps.py" approve --approval <承認資料> --approved-sha <SHA> --by user --root .
# 手順 4: 公開する（bump と changelog の変更をコミットしてから）
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel dev --root .   # 開発版のデプロイ
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel prod --approved-sha <SHA> --root .  # 本番リリース（承認を得てから）
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel prod --approval <承認資料> --root .  # 本番のリリースプランの形
# 選んで集めるリリース方式（ブランチ戦略の設定の release_strategy: release_branch）: 本番チャネルから組んだリリースブランチで出す
python3 "$SCRIPTS/release-steps.py" release-branch --version <バージョン> --units <スプリントブランチ>... [--production <本番チャネル>] --root .
# リリースブランチの PR のレビュー（ブランチ戦略の設定の reviews.release.diff が none でないとき。差分が空なら終了コード 3）
python3 "$SCRIPTS/release-steps.py" review-open --version <バージョン> --diff pr|resolution [--production <本番チャネル>] --root .
python3 "$SCRIPTS/release-steps.py" review-close --version <バージョン> --root .
python3 "$SCRIPTS/release-steps.py" approval-facts --version <バージョン> --prs --head release/v<バージョン> --approval <承認資料> --root .
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel prod --approval <承認資料> --release-strategy release_branch --root .
# CD のある場合（release-case の metrics.case が cd-no-review / cd-review / cd-admin）の本番
python3 "$SCRIPTS/release-steps.py" release-case --root .   # リリースの場合を出す
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel prod --root .   # 承認の前: 本番の PR を作り CI を待って gate（10）
python3 "$SCRIPTS/release-steps.py" approval-post --pr <本番の PR> --approval <承認資料> --root .   # 承認資料を本番の PR へ置く
python3 "$SCRIPTS/release-steps.py" approve --pr <本番の PR> --approved-sha <HEAD> --by user --root .   # cd-no-review・cd-admin: 承認の証跡を書く
python3 "$SCRIPTS/release-steps.py" release --version <バージョン> --channel prod --root .   # 承認の後: --admin なしでマージし CD の結果を読む
```

- 選んで集めるリリース方式: `release-branch` は `release/v<バージョン>` を `origin/<本番チャネル>` から切り（origin に既にあればその先頭へ足す。
  強制 push はしない）、`--units` のブランチだけを `--no-ff` でマージして push する。衝突すれば `stopped`（1）。`approval-facts --head`
  は承認に出す先頭をそのブランチにし、「含む PR」はリリースブランチへマージした単位の中の PR になる。`release --release-strategy release_branch`
  はベースブランチへの開発版の PR を出さず、`release/v<バージョン>` → 本番チャネルの PR を承認と検証してマージし、タグと GitHub Release を
  作った後に `release/v<バージョン>-sync` → ベースブランチの PR で本番の中身をベースブランチへ戻す。手順の全体は `development-workflow` の
  `references/production-merge.md` の「選んで集めるリリース方式のリリース」にある
- リリースブランチの PR のレビュー（ブランチ戦略の設定の `reviews.release`。`worktree` の `references/ndf-settings.md` の
  「レビューの単位」）: 開発版のデプロイのプランが `release-branch` と `approval-facts` の間で打つ。
  - `review-open` は、範囲（同じバージョンの前のレビュー済みの先頭、無ければ本番チャネルから、リリースブランチの先頭まで）の差分を
    一時の PR（衝突の解消のレビューの PR。head は `review/release/v<バージョン>`、マージ先は `review/release/v<バージョン>-base`）にして、
    `metrics.review_pr` に番号を出す。`--diff resolution` は範囲の first-parent のマージの再マージの差分
    （`git log --remerge-diff`）だけを見せ、衝突したマージが無ければ PR を作らずに 3 で終える。`--diff pr` は範囲の差分全体を見せる。
    逆に当てられない・PR を作れないなら 1
  - 始めに、開いている衝突の解消のレビューの PR をバージョンに依らず調べ、直しをリリースブランチへ移し終えたものを閉じて一時の
    ブランチを消す。同じバージョンで範囲の終わりがリリースブランチの先頭と同じ、移していない直しを持つ PR は使い直す。それ以外の
    移していない直しを持つ PR か、比べるリリースブランチを取得できない PR があれば、何も閉じずに 1 で止める
  - `review-close` は、cross-review が見せるブランチへ積んだ直しのうち、まだリリースブランチへ移していないものを
    cherry-pick して送り、レビュー済みの先頭を PR の本文へ書いて閉じ、一時のブランチを消す。移せなければリリースブランチを
    動かさず、PR とブランチを残して 1 で止める（人が原因を直して `review-close` から打ち直す）。レビューの後にリリースブランチへ
    マージが足されていれば、閉じた後に 1 で止める（`review-open` から打ち直すと、足したマージだけを範囲に取る）
  - 本番のリリースのプランのクリーンアップは、そのバージョンの一時のブランチが残っていれば消す

- `approval-facts`: `gate` なら `presentation_path` の承認資料の「`配る中身`」と「`開発版のデプロイで確かめたこと`」を
  `notes --approval` で埋めて利用者へ示し、承認（`AskUserQuestion` の `metadata.source` は `ndf-gate:2`）を得てから `next` のコマンドを打つ。`stopped`
  （3 = 前のタグを決められない）なら `--prev-tag` を渡して打ち直す。`metrics.approved_sha` と承認資料の
  「`承認したコミット`」が承認の対象（打った時点の `origin/<ベースブランチ>` の 40 桁）で、`next` の
  `--approved-sha` も同じ値である。資料を書き直すと承認の記録は消える。「`含む PR`」は、前のタグから
  承認したコミットまでにベースブランチへ merge commit か squash merge で入った PR（head が `release/` の版上げの PR と、
  件名の `(#N)` が課題を指す番号を除く）と `--prs` の和を、番号の昇順に並べる。`--prs` には範囲の外の PR
  （マージ前の PR など）だけを渡せば足りる。範囲のログか squash の head を読めなければ、資料を書かずに
  `stopped`（2 = git のログ・1 = GitHub）で止まる。
  `## 指示書の変更` のセクションには、前のタグから承認したコミットまでの first-parent のログで指示書（`.ndf/instructions.json`
  の `files`。設定が無ければ `AGENTS.md` と `CLAUDE.md`）に触れたコミットを、指示書ごとに `短い SHA（#PR）` と
  差分を見るコマンド（`git diff <前のタグ> <承認したコミット> -- <ファイル>`）で並べる（触れていなければ「- 無し」。
  個数は `metrics.instructions`）。`--approval` に前の承認資料を渡すと、`approval-facts` が書くセクション（`## 1.`・
  `## 2.`・`## 指示書の変更`・`## 同意を求めること`・`## 戻し方`）だけを作り直し、それ以外の `## ` のセクション（手で
  足したセクション・`## 未検証・残る危険` など）を見出しも中身も変えずに、前の資料で直前にあった `approval-facts` のセクションの
  後へ戻す（そのセクションが無くなれば `## 同意を求めること` の前。残した見出しは `metrics.kept`）。前の資料が読めない・
  H1 が無い・同じ `## ` の見出しが 2 つあるときは、資料を書かずに `stopped`（2）で止まる。資料が無いパスは
  新しく作る。開発版のリリースプランは `presentation_to` と同じパスを渡す
- `approve`: 承認資料の「`承認したコミット`」の行の直後へ「`承認の記録`」（SHA・承認した者・時刻）を書く。
  `--approved-sha` が資料の今の値と違えば（提示の後に書き直された）書かずに `stopped`（1）で止まるので、
  資料を提示し直して承認を取り直す。形の誤りは 2、資料・欄が無いと 3
- `bump`: `items[]` に手で直す箇所が載っていれば直す。plugin の README には触れない
- `changelog`: CHANGELOG.md のバージョンのセクションへ見出しと PR のタイトルを並べるだけで、本文は書かない。plugin の README は
  書かない。未マージの PR は載せず、番号を
  `metrics.unmerged` へ出す。すべて未マージなら `stopped`（3）で止まるので、マージしてから打ち直すか `--prs` を直す
- `notes`: 各 PR の本文の `## 利用者向けの変化` の箇条（1 行目の末尾に `（#番号）`）で、CHANGELOG.md のバージョンのセクションを
  組み直す。plugin の README は読まず書かない（README は CHANGELOG.md を参照する固定の「`## 更新情報`」のセクションだけを
  持つ）。字下げした子の箇条は独立した項目にせず、親の項目の下に 1 ステップの入れ子（`  - `）で残す。セクションが無い・「無し」の
  PR は題名で代える（`metrics.fallback` が件数）。`--approval` を渡すと CHANGELOG は変えず、承認資料の 2 つの欄と、
  PR の本文の `## 未検証・残る危険` を集めたセクションと `## 移行の手順` のセクション（0 件なら「- 無し」）を書く。
  `## 未検証・残る危険` のセクションが既にあれば行を消さず、PR の箇条のうち無いものだけを足す（足したら「- PR の本文に
  記載が無い」の行を外す）。PR の本文の
  `## 移行の手順` の箇条は、CHANGELOG.md のバージョンのセクションでは `### 移行の手順` の下へ `（#番号）` つきで並ぶ（0 件なら
  見出しを作らない）。`--approval` のとき、欄を書く PR は承認資料の「`含む PR`」の欄の PR で、`--prs` は
  欄に足さない。`--prs` に「`含む PR`」に無い番号があれば欄を書かずに `stopped`（3）で止まるので、
  `approval-facts` で承認資料を作り直す。未マージの PR は載せず、番号を `metrics.unmerged` へ出す。
  `stopped`（3）は changelog や approval-facts を先に打つ。渡した PR がすべて未マージのときも 3 で止まるので、
  マージしてから打ち直すか `--prs` を直す。
  後の PR の本文の `## 置き換える箇条` が指す前の PR の箇条は、CHANGELOG.md のバージョンのセクションにも「`配る中身`」にも
  載せない（前の PR の本文は書き換えない）。書き方は下の「置き換える箇条」。置き換えごとに `items[]` へ
  `kind: replacement`・`name: #<後>→#<前>`・`result`（`removed` / `not_found`。形に合わない行は `unreadable`）を
  出し、除いた箇条の数を `metrics.replaced` へ出す。見つからない置き換えは何も除かず、終了コードは 0 のまま
- `changed-plugins`: 前のタグからの差分にある、設定の `release.plugin` 以外のプラグインを `items[]`（`from`・`to`・
  `level`・`basis`）へ返す。前のタグからバージョンが変わったものは `metrics.already` へ入れ、上げ直さない。`--prs` は PR の
  本文の GitHub の closing keywords（PR 本文に `Closes #123` のように書くと、マージ時にその課題を自動で閉じる機能）が指す課題の `## 影響` の「公開インタフェース」の行に互換なしのフラグ（`互換なし` か `互換の経路は持たない`）
  があるか、PR の本文の `## 移行の手順` がプラグインの名前に触れれば、そのパスに触れた PR のプラグインの候補を
  MAJOR、無ければ PATCH にする（MINOR の候補は出さない）。材料を読めない PR は根拠の欄に書く。`--approval` は承認
  資料の `## バージョンを上げる他のプラグイン` のセクション（0 件なら「- 無し」）と同意の行を書き、`--set` はリリース承認ゲートで決めた
  バージョン種別へ行を書き直す（表に無い名前・知らないバージョン種別は 2）。`--decided` は表のバージョン種別で `to` を決め、表が無い・
  バージョン種別を読めない・バージョンや集合が差分と合わないときは PATCH へ倒さずに 1 で止まる。他のプラグインが 0 件なら表を読まない
- `release`: `dev` は `release/v<バージョン>` →ベースブランチの Pull Request を作り、チェックを待ってマージのコミットでマージする。
  マージが版数を持つ箇所だけで衝突すれば、`release/v<バージョン>-sync` に `origin/<ベースブランチ>` をマージして本番のバージョンの側を取り、
  `release/v<バージョン>-sync` →ベースブランチの Pull Request でマージする（`release/v<バージョン>` そのものへはベースブランチをマージしない）。
  版数の外の箇所が衝突すれば解かずに 1 で止まる（`release_lib/version_merge.py`）。`metrics.release_pr` は検証に使う
  `release/v<バージョン>` の Pull Request、`metrics.sync_pr` は `-sync` でマージしたときのその Pull Request である。
  `prod` は続けて `release/v<バージョン>` を head にした本番チャネル宛の Pull Request をマージし、タグと GitHub Release を作る。
  `release.deliveries` の行が `publishes_release: true` なら、この 2 つは CD が作り、`release` は作らない（`metrics.tag` は `null`、`metrics.publisher` は `cd`）。
  本番チャネル宛の Pull Request がマージ済みなら（途中で止まったリリースの再開）、比較とマージを飛ばし、タグと GitHub Release のうち
  無いものだけを作る（`items[]` の `result: already_merged`・`exists`）。`items[]`
  （マージした Pull Request と、`release` が作ったときのタグ・GitHub Release）がリリース完了の確認で照会した結果である。
  `prod` は最後に「退避先の回収」の候補を挙げ、`items[]` の `kind: trash`（`result: candidate`）と
  `metrics.sweep_candidates` に載せる。消すのは人の承認（`ndf-ask:redline:C4`）を得た後である（下のセクション）。
  どちらの Pull Request でも pytest と runtime smoke は省かれて成功（skipping / pass）を返す
  （[バージョンとリリース](../../../../../docs/versioning-and-distribution.md#正式版を出す)）。待ちはそれを通ったものとして扱う
- `release --channel prod` の承認したコミット: `--approved-sha <SHA>` か `--approval <承認資料>` のどちらかが要る
  （どちらも無い・形の誤りは 2、資料・欄が無いと 3。どれも何もマージしない）。`--approval` は承認の記録の SHA が
  承認したコミットと同じときだけ受け取り、違えば何もマージせずに `gate`（10・`result: not_approved`）で止まる。
  デプロイの PR をマージした後、承認したコミットから `origin/release/v<バージョン>` の HEAD（比べた HEAD）までに `release/v<バージョン>` の
  Pull Request の外のコミットか中身があれば、本番チャネルの PR・タグ・GitHub Release を作らずに `gate`（10）で止まる。
  承認の後にベースブランチが進んでいても、ベースブランチは比べる相手に入らないため止まらず、進んだ分は次のデプロイで出る。ただし差分がすべて許可リスト
  （`docs/`・`issues/`。`release_lib/approval.py` の `UNGATED`）の下に収まる外のコミット（設計文書・用語集だけの PR など）は止めずに通し、
  `items[]` に `result: outside_unshipped` で、件数を `metrics.tolerated` に残す。本番チャネルへは比べた HEAD だけを
  マージし（`merged-steps.py merge-when-green --expect-head`）、CI 待ちの間に `release/v<バージョン>` の HEAD が進んだときは比べ直す。進んだ分が
  リリース対象に触れなければ比べ直した HEAD で入れ直し（3 回まで）、触れれば `gate` で止まる。
  `--channel dev` には渡さない（2）

  `gate` の読み方（git を照会し直さずに、結果 JSON だけで読める）:

  | 欄 | 中身 |
  | --- | --- |
  | `metrics.reason` | `outside_commits`（承認の外のコミットがある）・`tree_differs`（中身が違う）・`not_ancestor` / `unknown_commit`（承認したコミットが HEAD の祖先でない・無い）・`undecidable`（判定できない）・`head_moved`・`not_approved` |
  | `metrics.approved_sha` / `metrics.compared_head` | 承認したコミット / 比べた HEAD |
  | `items[]` の `result: unapproved` | 承認の外のコミット（`name` が SHA、`subject`、分かれば `pr`） |

  承認の外の変更を含めて `approval-facts` で承認資料を作り直し、承認を取り直して `next` のとおり打ち直す

### CD のある場合の本番

`release-case` が出す場合（`metrics.case`）が `cd-no-review`・`cd-review`・`cd-admin` のとき、`release --channel prod` は次の形で進む。
場合の意味は [SKILL.md](../SKILL.md) の「リリースの場合」にある。

- `release --channel prod`: `--approved-sha` も `--approval` も受けない（渡すと 3。承認の SSOT は本番の PR の上にある）。
  本番の PR が無ければ、開発版の PR をマージし、`release/v<バージョン>` を戻して本番の PR を作る。承認が HEAD と一致しなければ、
  `ndf/release-approval` を除くチェックが通るのを待ってから `gate`（10）で止まる（本番の PR は開いたまま。`metrics.main_pr`・
  `metrics.approved_sha` は HEAD・`metrics.reason` は検証の理由）。一致すれば、`cd-no-review` は HEAD の `ndf/release-approval` が
  `success` になるのを待ち、HEAD に固定して `merge-when-green --no-admin` でマージし、CD の結果を読む。`cd-admin` は利用者の証跡
  （担い手が `user`）だけで照らし、同じ待ちの後に `--no-admin` を付けずに（管理者の迂回で）マージして、本番の PR へ迂回の記録
  （`ndf:admin-merge` のマーカーのコメント）を置く。本番の PR の行の `admin` が迂回の有無で、記録の行は `record: admin_merge`。記録を
  書けなければ本番の完了と分けて `stopped`（1）で報告し、打ち直すとマージ済みからの再開で記録だけを書く。マージ済みなら公開の後から続ける。
  選んで集めるリリース方式も同じ検証を使い、許可リストの許容と HEAD の入れ直しを使わない。承認の後に HEAD が動けば `gate`（10）で止まる
- `approval-post`: 承認資料を本番の PR へ承認資料のコメント（証跡に数えないマーカーつき）で置く。同じ HEAD の承認資料のコメントがあれば
  置かずに 0。承認資料が無ければ 3
- `approve --pr`: `cd-no-review` と `cd-admin` の承認の証跡を本番の PR へ書く（`cd-admin` は `--by user` の証跡だけを承認に数える）。承認したコミットは `--approved-sha` か、`--facts-from`（リリースのワークフローの
  状態ディレクトリの `facts` の出力。`--by user` なら `--answer-from` の利用者の承認の記録も要る）。PR の HEAD が承認したコミットと違えば
  書かずに `gate`（10）。同じ人が同じコミットの証跡を既に置いていれば置き直さない
- `approval-check`: 証跡のチェックの workflow（`.github/workflows/release-approval.yml`）が打つ。本番チャネル宛ての PR の証跡を判定し、
  HEAD へ commit status `ndf/release-approval` を付ける。承認権限（admin か maintain）を持つ人の最新の、作成の後に編集されていない
  証跡が HEAD を指すときだけ `success` で、説明に理由を 1 行で出す（例: `証跡 0123abc ≠ HEAD 89abcde`）。同じ HEAD の開いた PR が
  ほかにあれば落とす。読めなければ `error` を付けて 1
- `cd-review`: 証跡を書かず、承認権限として有効な人（共同作業者の権限が admin か write）の最新の承認のレビューのコミットを HEAD と比べる
- `release --channel dev`: ブランチ戦略の設定の `channels` の `dev` の行に `workflow` があれば、開発版の PR を作るか使い回し、開いていればその
  workflow を `gh workflow run` で起動し、実行が終わるまで待つ（上限は `release-steps.dev-workflow`。75 で止まる）。workflow はマージ先と
  バージョンの接尾辞を確かめ、CI の必須チェックが通るのを待って `merge-when-green --no-admin` でマージする。設定が無ければ手元でマージする

### 置き換える箇条

同じバージョンの後の PR が前の PR の振る舞いを変えたときは、後の PR の本文に次のセクションを書き、前の PR の「利用者向けの変化」の
箇条を CHANGELOG.md のバージョンのセクションと承認資料の「`配る中身`」から外す。マージ済みの前の PR の本文は直さない。

```markdown
## 置き換える箇条

- #1825: cross-refactoring の init は、対象の Pull Request が Draft でない場合に処理を止める
```

- 1 行に 1 つ、`- #<前の PR の番号>: <前の PR の「利用者向けの変化」の箇条の 1 行目>`。CHANGELOG の案から写してよい
- 比べるときは両側の末尾の `（#N…）` / `(#N…)` を外し、空白の続きを 1 つにする。文が一致した箇条だけを外す
- 指す PR は同じバージョンの PR（`notes` へ渡す PR）に限る。前の PR の `## 未検証・残る危険` と `## 移行の手順` は外さない

## デプロイの後にリリース記録を置く

**どのリリースの形でも、リリースを実施した後に、実施後の値で `段階: 本番` のリリース記録を置く。**
手順 3 で本文に置いたブロックはデプロイの前に書いたもので、本番が承認待ちなら `段階: 検証（…）` のままである。
読む側は Pull Request の本文とコメントのうち最後の `## リリース記録` を読むため、デプロイの後の記録が無いと
本番まで出たスプリントの課題が閉じられない。

| 形 | 置き方 |
| --- | --- |
| Claude Code のプラグイン | `release-steps.py record --version <バージョン> --prs <PR番号>...` が本番のリリースの Pull Request へコメントで書く |
| リリースの Pull Request のマージで届くもの | `release-steps.py record --promote --head <ベースブランチ> --base <本番チャネル> --prs <PR番号>...` がリリースの Pull Request へコメントで書く。`バージョン:` はマージのコミットである |
| 本番チャネルへの直接のマージで CD が届けるもの | `release-steps.py record --merged --base <本番チャネル> --prs <PR番号>...` が最後の `--prs` の Pull Request へコメントで書く。`バージョン:` はマージのコミットである |
| それ以外 | SKILL.md の「出力物」と同じ 3 行の `## リリース記録` を、デプロイした値で記録の Pull Request へコメントで足す |

スプリントのプランでデプロイするときは、プランの `record` のステップがこれを打つ。

**CD の結果は `cd-result` が読み、`record --cd-result <ファイル>` が記録へ書く。**

```bash
python3 "$SCRIPTS/release-steps.py" cd-result --base <本番チャネル> [--head <ブランチ>] --out <ファイル> --root .
```

| 値 | 意味 | 続き |
| --- | --- | --- |
| `success`（成功） | CD の状態を読んで成功を確かめた | リリース後テストへ進む |
| `failure`（失敗） | CD が成功以外で終わった | デプロイの工程は完了にならず、スプリントは閉じられない |
| `undetermined`（未確定） | 読む手段が定義されていないか、待ちの上限に当たった | 未確定と書いてリリース後テストへ進む。リリース後テストが本番の状態を直接確かめ、その記録が無ければスプリントは閉じられない |
| `none` | 本番チャネルへ自動で届く行が無い | 記録に CD の行を足さない |

値は `metrics.cd_result` に出す。`cd-result` の終了コードは 4 つの値のどれでも 0 である。
`--head` があれば最後にマージした `--head` → `--base` の Pull Request のコミットを、無ければ本番チャネルの HEAD を読む。
`record` は値が `failure` なら `段階: 反映失敗（<理由>）` を書いた後に終了コード 5 で止まる。読む側は最後の記録を使うため、CD を直して
`cd-result` と `record` を打ち直すと、成功の記録で閉じられる。

## 退避先の回収

**本番へ出したら、そのバージョンに入ったブランチの退避先を回収の候補として挙げ、人の承認を得てから消す。**
`merged` が worktree を外すときに退避したファイル（`<共通の git ディレクトリ>/ndf/worktree-trash/`）は、
戻す必要が出るのが本番の前だけだからである。ただし退避先は Git にも本番のコミットにも無い利用者のファイル
（手で直した `.env`・未追跡のメモ）を含み、消すと戻せない。**本番の承認はこの削除の承認を兼ねない**
（共通原則の C3・C4）。候補（退避先のパスと `du -sh <退避先>` の容量）を人へ示し、`AskUserQuestion`（`metadata.source` は `ndf-ask:redline:C4`）で承認を得たときだけ、承認した退避先の名前を
`--yes --only <名前>...` で渡して消す。名前の外の退避先（候補を挙げた後に増えたもの）は消さない。
承認が無ければ退避先は残す。Claude Code のプラグインの形では `release --channel prod` が候補を挙げ、`next` に
候補の名前を並べた消す 1 行を載せる。それ以外の形では、本番へ出たコミット（タグ・本番チャネルのブランチ）を渡して打つ。
開発版のデプロイでは打たない。

```bash
python3 "$SCRIPTS/merged-steps.py" sweep-trash --ref <本番に出たコミット>        # 候補を挙げるだけ（消さない）
python3 "$SCRIPTS/merged-steps.py" sweep-trash --ref <本番に出たコミット> --yes --only <承認した退避先>...  # 承認した名前だけを消す
```

候補の件数（`metrics.sweep_candidates`）・消した退避先（`metrics.swept_trash`）・ジャーナルが無いため残した件数
（`metrics.unledgered_trash`）を完了報告に書く。
退避先とジャーナルの形は `merged` の SKILL.md にある。

## 設定したリリースの形で組むサブコマンド

ndf-next の `ndf release-steps` の `materials`・`assemble`・`publish` は、設定 `.ndf/project.json` の `branch_strategy.release`
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
