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

`release` の手順 3 で、版と変更履歴を上げた後、リリースの Pull Request を作る前に次を打つ。

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
| `verification` | 検証リリース（開発版として公開する・検証環境へ反映する） | `verification` / `any` |

## 設定の項目

形は [../schemas/release.schema.json](../schemas/release.schema.json) が定める。

| 項目 | 必須 | 何を決めるか |
| --- | --- | --- |
| `version` | 必須 | 設定の形の版。`1` だけを読む |
| `steps` | 必須 | コマンドの並び。書いた順に実行し、最初に落ちたコマンドで止める |
| `steps[].name` | 必須 | 出力と完了報告に出す名前 |
| `steps[].stage` | 必須 | `production` / `verification` / `any` |
| `steps[].command` | 必須 | 引数の配列。シェルを通さない。`{version}` を配る版で置き換える（置き換えるのはこの 1 つだけ） |
| `steps[].writes` | 必須 | 書いてよいパスの前置き（根からの相対）。`[]` は何も書かないコマンド |
| `steps[].guide` | 任意 | エージェントが判断する部分の手引き |
| `steps[].timeout_seconds` | 任意 | コマンド 1 つの時間の上限。既定 600、1 以上 |

**`writes` の外を変えたコマンドは失敗にする。** コマンドは任意のコマンドで、リリースの Pull Request に何が
混ざるかを本文から読めない。変えたかどうかは、コマンドの前後で `git status` に出たパスの内容を比べて
決める。コマンドの前から変わっていたパスも、コマンドが中身を変えれば（元へ戻しても）数える。

## 終了コード

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

`--dry-run` は実行せず、走らせるコマンドと、版を置き換えた後のコマンドを出す。

## Claude Code のプラグインの形のサブコマンド

設定したコマンドとは別に、Claude Code のプラグインの形ではリリースの手順そのものをスクリプトが行う。
どれも結果 JSON を出し、`status` と終了コードで読む（`ok` = 0 で次へ、`gate` = 10 で承認を得る、
`stopped` = 1 / 2 / 3 なら `summary` を読んで直すか報告して止まる）。

```bash
# 手順 2: 公開前の承認資料のうち機械で作れる部分を書き出す
python3 "$SCRIPTS/release-steps.py" approval-facts --version <版> --prs <PR番号>... [--prev-tag <タグ>] --root .
# 手順 3: 版数と変更履歴を上げる
python3 "$SCRIPTS/release-steps.py" bump      --plugin <名前> --to <版> --root .
python3 "$SCRIPTS/release-steps.py" changelog --version <版> --prs <PR番号>... --root .
python3 "$SCRIPTS/release-steps.py" notes     --version <版> --prs <PR番号>... --root .
# 手順 2 の承認資料の欄を埋める（検証リリースの後）
python3 "$SCRIPTS/release-steps.py" notes     --version <版> --prs <PR番号>... --approval <承認資料> \
  --verified claude,codex,kiro --ref develop --root .
# 版を上げる他のプラグインと上げ幅の候補を承認資料へ書く（変えるなら --set <名前>=MAJOR|MINOR|PATCH で書き直す）
python3 "$SCRIPTS/release-steps.py" changed-plugins --prs <PR番号>... --approval <承認資料> [--since <タグ>] --root .
# 本番: 承認資料の表の上げ幅で上げる版を返す（items の name と to を bump --plugin <name> --to <to> へ渡す）
python3 "$SCRIPTS/release-steps.py" changed-plugins --decided <承認資料> [--since <タグ>] --root .
# 承認を得たら、承認資料へ承認の記録を書く（<SHA> は提示した資料を書いた approval-facts の metrics.approved_sha）
python3 "$SCRIPTS/release-steps.py" approve --approval <承認資料> --approved-sha <SHA> --by user --root .
# 手順 4: 公開する（bump と changelog の変更をコミットしてから）
python3 "$SCRIPTS/release-steps.py" release --version <版> --channel dev --root .   # 検証リリース
python3 "$SCRIPTS/release-steps.py" release --version <版> --channel prod --approved-sha <SHA> --root .  # 本番リリース（承認を得てから）
python3 "$SCRIPTS/release-steps.py" release --version <版> --channel prod --approval <承認資料> --root .  # 本番のリリースプランの形
```

- `approval-facts`: `gate` なら `presentation_path` の承認資料の「`配る中身`」と「`検証への配布で確かめたこと`」を
  `notes --approval` で埋めて利用者へ示し、承認を得てから `next` のコマンドを打つ。`stopped`
  （3 = 前のタグを決められない）なら `--prev-tag` を渡して打ち直す。`metrics.approved_sha` と承認資料の
  「`承認したコミット`」が承認の対象（打った時点の `origin/<ベースブランチ>` の 40 桁）で、`next` の
  `--approved-sha` も同じ値である。資料を書き直すと承認の記録は消える
- `approve`: 承認資料の「`承認したコミット`」の行の直後へ「`承認の記録`」（SHA・承認した者・時刻）を書く。
  `--approved-sha` が資料の今の値と違えば（提示の後に書き直された）書かずに `stopped`（1）で止まるので、
  資料を提示し直して承認を取り直す。形の誤りは 2、資料・欄が無いと 3
- `bump`: `items[]` に手で直す箇所が載っていれば直す
- `changelog`: 見出しと PR のタイトルを並べるだけで、本文は書かない。未マージの PR は載せず、番号を
  `metrics.unmerged` へ出す。すべて未マージなら `stopped`（3）で止まるので、マージしてから打ち直すか `--prs` を直す
- `notes`: 各 PR の本文の `## 利用者向けの変化` の箇条（末尾に `（#番号）`）で、CHANGELOG.md の版の節と
  plugin の README の `## v<版> へ更新するとき` の節を組み直す。節が無い・「無し」の PR は題名で代える
  （`metrics.fallback` が件数）。`--approval` を渡すと CHANGELOG と README は変えず、承認資料の 2 つの欄と、
  PR の本文の `## 未検証・残る危険` を集めた節と `## 移行の手順` の節（0 件なら「- 無し」）を書く。PR の本文の
  `## 移行の手順` の箇条は、CHANGELOG.md の版の節と README の更新の節では `### 移行の手順` の下へ `（#番号）` つきで
  並ぶ（0 件なら見出しを作らない）。未マージの PR は載せず、番号を `metrics.unmerged` へ出す。
  `stopped`（3）は changelog や approval-facts を先に打つ。渡した PR がすべて未マージのときも 3 で止まるので、
  マージしてから打ち直すか `--prs` を直す
- `changed-plugins`: 前のタグからの差分にある、宣言の `release.plugin` 以外のプラグインを `items[]`（`from`・`to`・
  `level`・`basis`）へ返す。前のタグから版が変わったものは `metrics.already` へ入れ、上げ直さない。`--prs` は PR の
  本文の閉じる語が指す課題の `## 影響` の「公開インタフェース」の行に互換なしの印（`互換なし` か `互換の経路は持たない`）
  があるか、PR の本文の `## 移行の手順` がプラグインの名前に触れれば、そのパスに触れた PR のプラグインの候補を
  MAJOR、無ければ PATCH にする（MINOR の候補は出さない）。材料を読めない PR は根拠の欄に書く。`--approval` は承認
  資料の `## 版を上げる他のプラグイン` の節（0 件なら「- 無し」）と同意の行を書き、`--set` は承認ゲート 2 で決めた
  上げ幅へ行を書き直す（表に無い名前・知らない上げ幅は 2）。`--decided` は表の上げ幅で `to` を決め、表が無い・
  上げ幅を読めない・版や集合が差分と合わないときは PATCH へ倒さずに 1 で止まる。他のプラグインが 0 件なら表を読まない
- `release`: `dev` は `release/v<版>` → `develop` の Pull Request を作り、チェックを待ってマージする。
  `prod` は続けて `develop` → `main` をマージし、タグと GitHub Release を作る。`items[]`
  （マージした Pull Request・タグ・GitHub Release）がリリース完了の確認で照会した結果である。
  `prod` は最後に「退避先の回収」の候補を挙げ、`items[]` の `kind: trash`（`result: candidate`）と
  `metrics.sweep_candidates` に載せる。消すのは人の承認を得た後である（下の節）。
  どちらの Pull Request でも pytest と runtime smoke は省かれて成功（skipping / pass）を返す
  （[版と配布](../../../../../docs/versioning-and-distribution.md#正式版を出す)）。待ちはそれを通ったものとして扱う
- `release --channel prod` の承認したコミット: `--approved-sha <SHA>` か `--approval <承認資料>` のどちらかが要る
  （どちらも無い・形の誤りは 2、資料・欄が無いと 3。どれも何もマージしない）。`--approval` は承認の記録の SHA が
  承認したコミットと同じときだけ受け取り、違えば何もマージせずに `gate`（10・`result: not_approved`）で止まる。
  配布の PR をマージした後、`origin/<ベースブランチ>` の先端（比べた先端）までに配布の PR の外のコミットか中身が
  あれば、本番チャネルの PR・タグ・GitHub Release を作らずに `gate`（10）で止まる。本番チャネルへは比べた先端だけを
  マージし（`merged-steps.py merge-when-green --expect-head`）、CI 待ちの間に先端が進んでも `gate` で止まる。
  `--channel dev` には渡さない（2）

  `gate` の読み方（git を照会し直さずに、結果 JSON だけで読める）:

  | 欄 | 中身 |
  | --- | --- |
  | `metrics.reason` | `outside_commits`（承認の外のコミットがある）・`tree_differs`（中身が違う）・`not_ancestor` / `unknown_commit`（承認したコミットが先端の祖先でない・無い）・`undecidable`（判定できない）・`head_moved`・`not_approved` |
  | `metrics.approved_sha` / `metrics.compared_head` | 承認したコミット / 比べた先端 |
  | `items[]` の `result: unapproved` | 承認の外のコミット（`name` が SHA、`subject`、分かれば `pr`） |

  承認の外の変更を含めて `approval-facts` で承認資料を作り直し、承認を取り直して `next` のとおり打ち直す

## 退避先の回収

**本番へ出したら、その版に入ったブランチの退避先を回収の候補として挙げ、人の承認を得てから消す。**
`merged` が worktree を外すときに退避したファイル（`<共通の git ディレクトリ>/ndf/worktree-trash/`）は、
戻す必要が出るのが本番の前だけだからである。ただし退避先は Git にも本番のコミットにも無い利用者のファイル
（手で直した `.env`・未追跡のメモ）を含み、消すと戻せない。**本番の承認はこの削除の承認を兼ねない**
（共通原則の C3・C4）。候補（退避先のパスと `du -sh <退避先>` の容量）を人へ示し、承認を得たときだけ、承認した退避先の名前を
`--yes --only <名前>...` で渡して消す。名前の外の退避先（候補を挙げた後に増えたもの）は消さない。
承認が無ければ退避先は残す。Claude Code のプラグインの形では `release --channel prod` が候補を挙げ、`next` に
候補の名前を並べた消す 1 行を載せる。それ以外の形では、本番へ出たコミット（タグ・本番チャネルのブランチ）を渡して打つ。
検証リリースでは打たない。

```bash
python3 "$SCRIPTS/merged-steps.py" sweep-trash --ref <本番に出たコミット>        # 候補を挙げるだけ（消さない）
python3 "$SCRIPTS/merged-steps.py" sweep-trash --ref <本番に出たコミット> --yes --only <承認した退避先>...  # 承認した名前だけを消す
```

候補の件数（`metrics.sweep_candidates`）・消した退避先（`metrics.swept_trash`）・台帳が無いため残した件数
（`metrics.unledgered_trash`）を完了報告に書く。
退避先と台帳の形は `merged` の SKILL.md にある。
