## 何が起きたか

**きっかけ（事例 1）**: 2026-11-14 に PR #3491 で cross-refactoring を流したとき、検証の失敗を直すコミット（項目 I-011、修正担当は codex）に、cross-refactoring 自身の作業ファイル 2 件（`.cross_refactoring/codex-fix-rf3491-progress.log` と `-result.json`）が入った。2 件はコミットの時点で `.gitignore` の無視の対象だった（`git show c9436c319:.gitignore` の 49 行目）。cross-refactoring の最終ゲートは通り、続く cross-review の round 1 で kiro が見つけて、別のコミット（3dd9fe9b6）で外した。

```text
c9436c319 Fix: 検証の失敗を直す — plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py#prod_version
 .cross_refactoring/codex-fix-rf3491-progress.log  | 5 +++++
 .cross_refactoring/codex-fix-rf3491-result.json   | 1 +
```

2026-11-22 に 11.x の今のコード（main 5c2c1e62d、git 2.53.0）で確かめ直すと、同じことは今も起こりうる。除外の下でも `git add -f` で入り、一度入ると以後のコミットにも入り続ける。

**主題**: 調べると、NDF がリポジトリの中に作る追跡しないフォルダとファイルの無視の仕方が、置き場ごとにばらばらだった（「今の姿」）。2026-11-23 の設計承認ゲートで、利用者がこの課題を、NDF が作るリポジトリ管理外のファイルの ignore の仕方をそろえる上位の課題にした。

## 種類

改善（無視の仕方の約束を 1 つにそろえる。cross-refactoring の作業ファイルの件は不具合で、その事例として扱う）

## 今の姿

NDF がリポジトリの中に作る、git で追跡しない置き場は次の 3 つから引ける。1 つ目は置き場の一覧の設定ファイル `plugins/ndf/contracts/domains/locations.json` で、NDF が使う保存先ごとに、パスと、そのパスをどこから数えるか（今の worktree の根か、メインディレクトリの根か）と、git で追跡するかを持つ。2 つ目は工程が書くファイルの一覧 `plugins/ndf/contracts/domains/ndf-files.json` で、NDF の工程が書くファイルごとに置き場と追跡するかを持つ。3 つ目は cross-refactoring の作業ファイルの置き場で、一覧には載っていない。無視の仕方は置き場ごとに違う。

| 置き場 | 今の無視の仕方 |
| --- | --- |
| `.cross_refactoring`（cross-refactoring の作業ファイル） | cross-refactoring が共通の git ディレクトリの `info/exclude` に足す。追跡済みのファイルには効かない |
| `.worktrees` | worktree の Skill の手順 2-2 のシェルが根の `.gitignore` に足し、`ndf worktree create` が無ければ止まる |
| `.ndf/tmp`・`.ndf/handoff`・`.ndf/sprints`・`.ndf/parallel`・`.ndf/backlog/state` | NDF は何もしない。利用者の根の `.gitignore` 次第（ai-plugins では手で書いた `.ndf/.gitignore` の行と根の `tmp/` が当たる） |
| `.ndf/loop` | 置き場の一覧にはあるが、今のコードは作業ツリーでなく git の共通ディレクトリの中（`<共通ディレクトリ>/ndf/state`）に書く。無視は要らない |
| `.ndf/worktree.local.json`（個人設定） | `ndf worktree init` が `.ndf/.gitignore` を無いときだけ作って 1 行書く |

除外は追跡済みのファイルに効かない（gitignore の文書「Files already tracked by Git are not affected」）。除外の下でも `git add -f` で入る。2026-11-22 の実測は次のとおりである。

| 修正担当の操作 | `.cross_refactoring/` の下のファイル |
| --- | --- |
| `git add -A` | 入らない |
| `git add -f .cross_refactoring/<ファイル>` | **入る** |
| 一度入った後の `git add -A`・`git commit -a` | **入り続ける** |

## 何を足すか・変えるか

| | 今 | 変えた後 |
| --- | --- | --- |
| NDF が作るフォルダの無視 | 置き場ごとに `info/exclude`・根の `.gitignore`・何もしない、が混ざる | 作る側が作るときに、中へ `*` だけの `.gitignore` を置く |
| `.ndf/` の中の追跡しないファイルの無視 | `.ndf/.gitignore` を無いときだけ作る | 作る側が `.ndf/.gitignore` へ行を書く（既にあれば足さない） |
| 根の `.gitignore` | `.worktrees/` の登録を求める | NDF は書かず、求めない |
| cross-refactoring の作業ファイル（事例 1） | 一度入ると追跡され続ける | 担当の起動の前に作り直し、追跡されていれば索引から外す |

## 今の利用者への影響と互換

- `ndf worktree create` は、根の `.gitignore` に `.worktrees/` が無くても止まらなくなる
- 利用者のリポジトリに出る差分は、`.ndf/.gitignore` に行が無いときのその 1 行だけになる。フォルダの中の `.gitignore` は自身も無視されるので差分に出ない
- 利用者が根の `.gitignore` に書いた既存の行は消さない

## なぜ困るか

無視の仕方がばらばらだと、置き場ごとに漏れ方が違い、利用者のリポジトリの設定次第で NDF の記録や作業ファイルが Pull Request の差分に入る。事例 1 では作業ファイルが Pull Request に入り、修正のラウンドを 1 回使った。最終ゲートの直しで起きると正しい直しが範囲外として止まり、フレーキーなテストの直しで起きると正しい直しが取り消される。

## 目的

- NDF が作る追跡しないフォルダとファイルの無視を、次の 2 つの仕組みにそろえる。根の `.gitignore` と `ndf worktree init`・`check` の登録に頼らない
  1. フォルダは、作る側が作るときに中へ `*` だけの `.gitignore` を置く
  2. `.ndf/` の中のファイルは、作る側が作るときに `.ndf/.gitignore` へ行を書く（既にあれば足さない）
- 事例 1: cross-refactoring の作業ファイルが追跡されたまま次のコミットへ持ち越されることを、担当の起動の前の片付けで止める

## 対象範囲

含む:
- 置き場の一覧の、追跡しないフォルダ（`.worktrees`・`.ndf/tmp`・`.ndf/handoff`・`.ndf/sprints`・`.ndf/parallel`・`.ndf/backlog/state`）と `.cross_refactoring` を、NDF のコードが置き場の単位（worktree ごとに 1 つか、メインディレクトリに 1 つ）のパスに作り、作るときに中へ `*` だけの `.gitignore` を置くこと
- 置き場の一覧の起点を実際の置き場に合わせること（`.worktrees` と `.ndf/handoff` は実際にはメインディレクトリに 1 つなのに、一覧では worktree ごとになっている。`.ndf/loop` は一覧ではメインディレクトリだが、実際は git の内部のディレクトリに書く）
- `.ndf/` の中の追跡しないファイル（`.ndf/worktree.local.json`）の行を、NDF が `.ndf/.gitignore` に書くこと
- cross-refactoring が `info/exclude` へ書くのをやめ、`ndf worktree create` が根の `.gitignore` の登録を求めるのをやめること
- 事例 1: 担当の起動の前に、担当が動くディレクトリの `.cross_refactoring/` を作り直すこと（前の作業ファイルを消し、追跡されていれば索引からも外す）

含まない:
- 担当が `git add -f` で入れた作業ファイルを、その起動のコミットから除くこと（コミットの作り直しはしない。最後の起動で入れたものは、次の起動が無ければ残る）
- 根の `.gitignore` を NDF が書くこと。`ndf worktree init`・`check` が登録の欠けを検査すること
- 利用者が根の `.gitignore` に書いた既存の行を消すこと
- 手順書の `Item-Id` の検査がコードに無いこと（#4195 に起票した）
- NDF が作らない、無視の対象のファイル（生成物・`.env` など）
- cross-review の作業ファイル。cross-review は状態の保存先をレビューする worktree の外に置き、中なら `precondition` で止める（`plugins/ndf/skills/cross-review/SKILL.md` の `--state-dir`）ため、同じ経路が無い

## 前提

- 前提 1: 前の起動の進捗のファイルは、次の起動の前に消してよい（結果ファイルは起動の直後に回収先へ移してあり、進捗のファイルは無進捗の監視が起動の間だけ測る）

## 受け入れ条件

- [ ] 1. 置き場の一覧の追跡しないフォルダと `.cross_refactoring` のどれも、NDF のコードがその置き場の単位のパス（worktree 単位なら今の worktree の根、メインディレクトリ単位ならメインディレクトリ）に作り、作るときに中へ `*` だけの `.gitignore` が置かれる。メインディレクトリと linked worktree の両方の `git status` に、置き場の中のファイルが出ない。既にある `.gitignore` は書き換えない
- [ ] 2. `.ndf/` の中の追跡しないファイル（メインディレクトリに 1 つの `.ndf/worktree.local.json`）は、NDF がその置き場を用意するときに、メインディレクトリの `.ndf/.gitignore` に行がある（無ければ足し、既にあれば足さず、既存の行は消さない）
- [ ] 3. 根の `.gitignore` と `info/exclude` に登録が無いリポジトリでも、上の置き場の中のファイル（中の `.gitignore` 自身を含む）は `git add -A` で入らない。NDF は根の `.gitignore` を書かず、登録の欠けで止まらない（`ndf worktree create` を含む）
- [ ] 4. `.worktrees/` の中の `.gitignore` は、作った worktree の中のファイルの無視に影響しない（worktree の中で足したファイルは `git add -A` で入る）
- [ ] 5. 事例 1: どの工程でも、担当を起動する直前に、担当が動くディレクトリの `.cross_refactoring/` には前の起動の作業ファイルが無く、`*` だけの `.gitignore` がある。前に追跡されていたファイルは索引から外れていて（`git ls-files .cross_refactoring` が空）、担当の次のコミットで追跡から外れる
- [ ] 6. 事例 1 の片付けは `.cross_refactoring/` だけに効き、作業ディレクトリのほかのファイル・未コミットの変更・コミットは変わらない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（互換あり）。`ndf worktree create` は `.worktrees/` の登録が無くても止まらない |
| データ | 変わらない（状態ファイルの形は同じ） |
| 既存の振る舞い | NDF が作るフォルダの中に `.gitignore` が 1 つずつ増える（無視されるので差分に出ない）。利用者のリポジトリに出る差分は `.ndf/.gitignore` の行（無いときだけ）。事例 1 では、担当の起動の前に前の起動の進捗のファイルが消え、作業ファイルが追跡されていたときは次のコミットに削除が入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests -q -n 4`（CI のジョブでも同じ範囲を打つ） |
| 静的解析・型検査 | `PATH=/work/ai-plugins/.venv/bin:$PATH ndf check`（CI の検査と同じ） |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 修正担当が `git add -f` など無視の規則を越える追加をした | コミットの時点の `.gitignore` に `.cross_refactoring/` があり、通常の `git add` では入らないこと。担当が打ったコマンドの記録は見ていない | 前提として採る。2026-11-22 の実測で、除外の下で入る経路は `git add -f`（と、一度入った後の追跡）だけと確かめた（「今の姿」の表） |
| S2 | 直す場所は、修正担当への指示（`prompts/fix.md`）より、オーケストレーターのコミットの検査が適する | 推した出所: `prompts/fix.md` の「コミットの規約」が、規約から外れたコミットを打ち消す仕組みを持つこと | 採らない。2026-11-23 の設計承認ゲートで、利用者がコミットの検査と作り直しをやめ、起動の前の片付けだけにすると決めた。推した出所の「打ち消す仕組み」も 11.x のコードに無い（「今の姿」） |
| S3 | 入ったときは、トレーラーの欠けと同じようにオーケストレーターがそのコミットを差し戻す | 期待する結果の 2 行目 | 採らない。同じ設計承認ゲートの決定による（起動の前の片付けだけにする） |
| S4 | 11 系でも同じことが起きる | 2026-11-19 の棚卸しの「11 系での確かめ」 | 確かめた（「何が起きたか」の 11.x での再現）。課題は閉じない |
| S5 | 課題の主題を、NDF が作る追跡しないフォルダとファイル全体の無視の仕方へ広げる | 2026-11-23 の設計承認ゲートの回答と、オーガナイザーが変えた題 | 採る（利用者の指示。「目的」「受け入れ条件」） |

## リクエスト（原文）

<details>
<summary>起票の時点の本文（2026-11-19 の棚卸しで直した版）</summary>

> 2026-11-19 の棚卸しで、古くなった数値・パス・関連課題の状態を 11 系の今に合わせて直した。
>
> ## 事象
>
> cross-refactoring の検証の失敗を直すコミット（修正担当は codex）に、`.gitignore` の対象である作業ログ 2 件が入った。
>
> ```text
> c9436c319 Fix: 検証の失敗を直す — plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py#prod_version
>  .cross_refactoring/codex-fix-rf3491-progress.log  | 5 +++++
>  .cross_refactoring/codex-fix-rf3491-result.json   | 1 +
>  plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py         | 7 +++++++
>  plugins/ndf-next/tests/domains/loop/test_loop_sprint_decide.py | 5 +++++
> ```
>
> コミットの時点の `.gitignore` の 49 行目は `.cross_refactoring/` で、2 件は無視の対象だった（`git show c9436c319:.gitignore`）。
> cross-refactoring の最終ゲートは passed で通り、続く cross-review の round 1 で kiro が指摘して、別のコミット（3dd9fe9b6）で外した。
>
> ## 期待する結果
>
> cross-refactoring の修正担当が作るコミットに、cross-refactoring 自身の作業ファイル（`.cross_refactoring/` の下）が入らない。
> 入ったときは、トレーラーの欠けと同じようにオーケストレーターがそのコミットを差し戻す。
>
> ## 再現手順
>
> 再現は 1 回（PR #3491 の cross-refactoring、項目 I-011、2026-11-14）。同じ形の再発は見ていない。
>
> 1. スプリントの検査のプランで cross-refactoring を流す
> 2. 適用した項目が範囲テストを落とし、修正（`prompts/fix.md`）を codex が担当する
> 3. 修正のコミットに `.cross_refactoring/codex-fix-rf<PR>-progress.log` と `-result.json` が入る
>
> ## 環境
>
> - NDF 10.27.64（観測の時点）。11 系の同じ指示は `plugins/ndf/skills/cross-refactoring/prompts/fix.md`
> - 修正担当: codex（Impl-Model: default）
> - 対象: devbasex/ai-plugins の PR #3491
>
> ## 影響
>
> 作業ログが Pull Request の差分に入り、レビューで見つからなければ開発の本流へ残る（Value 6「使うほど散らからない」）。
> このときはレビューが拾い、修正の 1 ラウンドを使った。
>
> ## 起票者の推定
>
> - 修正担当が `git add -f` など無視の規則を越える追加をした（推した出所: コミットの時点の `.gitignore` に `.cross_refactoring/` があり、通常の `git add` では入らないこと。担当が打ったコマンドの記録は見ていない）
> - 直す場所は、修正担当への指示（`prompts/fix.md`）より、オーケストレーターのコミットの検査（Item-Id・トレーラーを見ている所）が適する（推した出所: `prompts/fix.md` の「コミットの規約」が、規約から外れたコミットを打ち消す仕組みを持つこと）
>
> ## 由来
>
> PR #3491 の cross-review round 1 の kiro の指摘（`.cross_refactoring/codex-fix-rf3491-result.json`）。スプリント lr-fix-a-m1（#3300）の振り返りで、指摘は直したが原因の課題が残っていないことを拾った。#3300 の範囲は `plugins/ndf-next/src/ndf/domains/loop/` とそのテストで、cross-refactoring のコミットの検査は範囲外である。根拠: Value 6・Value 7（MVV 版 7）
>
> ## 11 系での確かめ（2026-11-19、main 2e43af7bc、ndf 11.0.8）
>
> 観測の時点の `plugins/ndf-next/` は、入れ替え（#3304）で `plugins/ndf/` になった。
>
> - 作業ログの置き場は今も `.cross_refactoring/` で、差分から外すのは git の除外へ 1 行足すことだけである（`plugins/ndf/src/ndf/domains/crossrefactor/workspace.py` の 58〜60 行、`launch_decide.py` の `IGNORE_ENTRY`）
> - 修正のコミットの中身から、無視の対象のパスを見つけて差し戻す検査は見当たらない。コミットの検査はトレーラーの欠け（`flaky_decide.py` の 66〜68 行）だけを見る
> - 担当が `git add -f` で除外を越えれば、11 系でも同じことが起きる見込み（未確認。再現は観測の 1 回だけ）

</details>

（書き手: 起票者（AI。スプリント lr-fix-a-m1（#3300）の振り返りで起票し、2026-11-19 の棚卸しで直した）、日付: 2026-11-14 起票・2026-11-19 更新。書き手が利用者でない文は、利用者の指示の原文として扱わない）

> フェーズ: 設計。課題: #3500。文書を書く前に、今の cross-refactoring のコードで、作業ログがどの経路でコミットへ入るかを実物で確かめる。課題の本文が古ければ（11 系の作り直しで経路が変わっていれば）、今の経路で起きるかを確かめ、起きなければ要求仕様にその結果を書いて、閉じる案を承認資料に載せる

（書き手: AI の起動指示（conductor、計画 p12 のレーン lcrrate）、日付: 2026-11-22）

> 設計承認ゲートの回答: 「直してから」。「#3500 は難しく考えすぎ」。作業（担当の起動）の前に `.cross_refactoring/` があれば削除し（追跡済みなら追跡からも外す）、`.gitignore` に入れる。それだけでよい。コミットの作り直しと失敗したときの備えはすべてやめる。`git add -f` の経路が残ることは決定の記録に 1 行で書く

（書き手: 利用者（conductor が設計承認ゲートの回答として伝えた。引用の文は conductor の要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（2 回目）: info/exclude の形は「マージしない」。方針は「作業の前に .cross_refactoring/ があれば削除し（追跡済みなら追跡からも外す）、.gitignore に入れる」のまま。`.gitignore` への登録は `/ndf:development-workflow` の初期化（`ndf worktree check`・worktree の setup.py が `.ndf/.gitignore` を作る所・create.py の `.worktrees/` の確かめ）へ、`.cross_refactoring/` と `.ndf` の一時フォルダ（`.ndf/tmp` など要るもの）の登録を寄せる

（書き手: 利用者（conductor とオーガナイザーが設計承認ゲートの回答として伝えた。引用の文は conductor の要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（3 回目）: 「直してから」。「.cross_refactoring/ を作成する際、必ず同時に .cross_refactoring/.gitignore を作るのではだめなのですか？」

（書き手: 利用者（conductor が設計承認ゲートの回答として伝えた）、日付: 2026-11-23）

> 補足（3 回目の回答の続き）: `.ndf/` の中のファイルは `.ndf/.gitignore` に書けばよい。NDF がそのファイルを作るときに `.ndf/.gitignore` へ行を書く形にし、根の `.gitignore` と `ndf worktree init` の登録に頼らない。登録の仕組みは、フォルダは作る側が中へ `*` だけの `.gitignore` を置く、`.ndf/` の中のファイルは作る側が `.ndf/.gitignore` へ行を書く、の 2 つだけにする

（書き手: 利用者（オーガナイザーと conductor が伝えた。引用の文は要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（4 回目）: 「直してから」。「無視するフォルダが worktree 単位なのかルートディレクトリ単位なのかが不明。例えば .cross_refactoring は worktree 単位、.ndf/handoff はルートディレクトリで 1 つのはず」

（書き手: 利用者（conductor が伝えた）、日付: 2026-11-23）

