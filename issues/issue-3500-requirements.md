## 何が起きたか

2026-11-14 に PR #3491 で cross-refactoring を流したとき、検証の失敗を直すコミット（項目 I-011、修正担当は codex）に、cross-refactoring 自身の作業ファイル 2 件（`.cross_refactoring/codex-fix-rf3491-progress.log` と `-result.json`）が入った。2 件はコミットの時点で `.gitignore` の無視の対象だった。cross-refactoring の最終ゲートは通り、続く cross-review の round 1 で kiro が見つけて、別のコミット（3dd9fe9b6）で外した。

2026-11-22 に 11 系の今のコード（main 5c2c1e62d）で経路を確かめ直した。同じことは今も起こりうる（「今の姿」）。

## 種類

不具合（作業ファイルを差分に入れないという約束が、今のコードの説明（`workspace.py`・`launch_decide.py` の docstring）にあるため）

## 今の姿

コードを書く工程（テストの追加・実装・修正・最終ゲートの直し・フレーキーなテストの直し。工程の表 `plugins/ndf/contracts/domains/loop-phases.json` で coding が真の 5 工程）では、修正担当（NDF 11.x では claude。`participants.FIXER`）が書き込み用の作業ディレクトリで動く。修正担当は結果ファイルと進捗のファイルを、そのディレクトリの `.cross_refactoring/` に書く（`plugins/ndf/src/ndf/domains/crossrefactor/launch_decide.py` の `result_location`）。オーケストレーターは起動の後に結果ファイルを回収先へ移すが、進捗のファイルは作業ディレクトリに残る（`launch.py` の `_collect_file`）。

約束は「`.cross_refactoring/` を git の除外へ足し、差分に入れない」である（`workspace.py` の `_exclude_results` が共通の git ディレクトリの `info/exclude` へ `.cross_refactoring/` を足す）。除外は `git add` の既定の振る舞いを変えるだけで、追跡に入るのを止めはしない。2026-11-22 の実測（git 2.53.0）は次のとおりである。

| 修正担当の操作 | `.cross_refactoring/` の下のファイル |
| --- | --- |
| `git add -A` | 入らない |
| `git add .cross_refactoring/<ファイル>` | 入らない（終了コード 1 で「無視の対象」と出る） |
| `git add -f .cross_refactoring/<ファイル>` | **入る** |
| 一度入った後の `git add -A`・`git commit -a` | **入り続ける**（追跡済みのファイルに除外は効かない） |

オーケストレーターがコミットの中身を見るのは 2 か所だけで、どちらも作業ファイルを見分けない。

- フレーキーなテストの直し（`flaky_decide.violations`）: テスト以外のファイルの変更を違反に数える。作業ファイルが入ると、正しい直しも取り消される
- 最終ゲートの直し（`final_gate_decide.verify_final_fix`）: 対象範囲の外の変更を `refused` で止め、人へ渡す。作業ファイルが入ると、直しそのものが正しくても止まる

テストの追加・実装・修正の 3 工程のコミットは何も検査されない。修正の手順書（`plugins/ndf/skills/cross-refactoring/prompts/fix.md`）は「`Item-Id` の欠けたコミットは打ち消される」と書くが、`Item-Id` を読むコードは 11 系に無い（`git grep -n "Item-Id" -- plugins/ndf/src` は 0 件）。

## 事象

観測の時点（NDF 10.27.64）のコミット:

```text
c9436c319 Fix: 検証の失敗を直す — plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py#prod_version
 .cross_refactoring/codex-fix-rf3491-progress.log  | 5 +++++
 .cross_refactoring/codex-fix-rf3491-result.json   | 1 +
 plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py         | 7 +++++++
 plugins/ndf-next/tests/domains/loop/test_loop_sprint_decide.py | 5 +++++
```

コミットの時点の `.gitignore` の 49 行目は `.cross_refactoring/` だった（`git show c9436c319:.gitignore`）。

11 系の作業場所の形での再現（2026-11-22、git 2.53.0。linked worktree の共通の `info/exclude` に `.cross_refactoring/` を足した状態）:

```text
$ git add -f .cross_refactoring/claude-fix-I-001-progress.log && git commit -m "B ..."
$ git show --stat --format= HEAD
 .cross_refactoring/claude-fix-I-001-progress.log | 1 +
 a.py                                             | 1 +
```

## 期待する結果

コードを書く工程の担当が作業ファイルを `git add -f` で入れても、オーケストレーターが公開する前のコミットから `.cross_refactoring/` の下のパスが無くなる。担当の正しい変更とコミットのメッセージ（トレーラーを含む）はそのまま残る。

## 再現手順

再現は観測の 1 回（PR #3491 の cross-refactoring、項目 I-011、2026-11-14）。同じ形の再発は見ていない。11 系の作業場所の形では、上の「事象」の 2 つ目のとおり手で再現できる。

1. cross-refactoring を流し、コードを書く工程の担当を起動する
2. 担当が作業ディレクトリで `git add -f .cross_refactoring/<作業ファイル>` を含むコミットを作る
3. オーケストレーターはそのコミットを検査せずに残す。修正の工程なら検証を通って公開され、最終ゲートの直しなら範囲外として止まる

## 環境

- 観測: NDF 10.27.64、修正担当は codex（Impl-Model: default）、対象は devbasex/ai-plugins の PR #3491
- 確かめ直し: main 5c2c1e62d（ndf 11.0.16 の後）、修正担当は claude、git 2.53.0、Linux

## なぜ困るか

作業ファイルが Pull Request の差分に入り、レビューで見つからなければ開発の本流へ残る。見つかっても、外すために修正のラウンドを 1 回使う（#3491 のとき）。最終ゲートの直しで起きると、正しい直しが範囲外として止まり人へ渡る。フレーキーなテストの直しで起きると、正しい直しが取り消される。

## 目的

- cross-refactoring が作るコミットに、cross-refactoring 自身の作業ファイルが残らない。担当が除外を越えて入れても、オーケストレーターの側で外れる

## 対象範囲

含む:
- コードを書く 5 工程（テストの追加・実装・修正・最終ゲートの直し・フレーキーなテストの直し）の担当が、起動の間に作ったコミット
- `.cross_refactoring/`（作業ディレクトリの根の結果ファイルと進捗のファイルの置き場）の下のパス

含まない:
- 手順書の `Item-Id` の検査がコードに無いこと（別の課題として起票する。この課題の原因と別の欠けで、直し方も別である）
- 作業ファイル以外の、無視の対象のファイル（生成物・`.env` など）。cross-refactoring が自分で作るのは `.cross_refactoring/` だけで、ほかの無視の対象を外すかはリポジトリごとの判断になる
- cross-review の作業ファイル。cross-review は状態の保存先をレビューする worktree の外に置き、中なら `precondition` で止める（`plugins/ndf/skills/cross-review/SKILL.md` の `--state-dir`）ため、同じ経路が無い
- 起動の前から HEAD にあったコミット（公開済みか、前の起動で検査を通ったもの）

## 前提

- 前提 1: コードを書く工程の担当は、起動の間にマージのコミットを作らない（手順書がマージを指示せず、ベースブランチのマージは起動の外のプランのステップが行う）

## 受け入れ条件

- [ ] 1. コードを書く 5 工程のどれでも、担当の起動の後に、起動の前の HEAD から今の HEAD までのコミットのどれも `.cross_refactoring/` の下のパスを含まない。担当が `git add -f` で作業ファイルを入れたときも同じ
- [ ] 2. 作業ファイルを除いたコミットは、除く前と同じメッセージ（`Item-Id`・`Impl-Runtime`・`Impl-Model`・`Flaky-Fix` のトレーラーを含む）・作者・作業ファイル以外の変更を持ち、並びも同じである。作業ファイルだけを変えたコミットは無くなる
- [ ] 3. 作業ファイルを除いても、作業ディレクトリの未コミットの変更と、`.cross_refactoring/` の下に残ったファイルは消えない
- [ ] 4. 作業ファイルを除いたときは、状態ファイルに工程・起動の名前・除いた元のコミットが残り、標準エラーへ 1 行出る
- [ ] 5. 最終ゲートの直しで担当が作業ファイルを入れても、作業ファイルを理由に範囲外（`refused`）として止まらない。フレーキーなテストの直しで担当が作業ファイルを入れても、作業ファイルを理由にテスト以外の変更（`non_test`）の違反にならない
- [ ] 6. 担当が作業ファイルを入れなかった起動では、コミットを作り直さない（起動の後の HEAD の SHA が変わらない）
- [ ] 7. 起動の前の HEAD とそれより前のコミットは作り直さない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`ndf refactor run` の引数と結果 JSON の形は同じ。状態ファイルにキーが 1 つ増える） |
| 重さ | 作業ファイルが Pull Request に入り、レビューで見つからなければ本流に残る。最終ゲートの直しでは正しい直しが止まり人へ渡る。回避は、レビューで見つけて手で外すことだけ |
| データ | 状態ファイル（`state.json`）に、作業ファイルを除いた記録のキーが増える。移行は無い |
| 既存の振る舞い | 作業ファイルを入れたコミットの SHA が変わる（公開の前で、起動の前の HEAD より後のものだけ） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests -q -n 4`（CI のジョブでも同じ範囲を打つ） |
| 静的解析・型検査 | `PATH=/work/ai-plugins/.venv/bin:$PATH ndf check`（CI の検査と同じ） |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 修正担当が `git add -f` など無視の規則を越える追加をした | コミットの時点の `.gitignore` に `.cross_refactoring/` があり、通常の `git add` では入らないこと。担当が打ったコマンドの記録は見ていない | 前提として採る。2026-11-22 の実測で、除外の下で入る経路は `git add -f`（と、一度入った後の追跡）だけと確かめた（「今の姿」の表） |
| S2 | 直す場所は、修正担当への指示（`prompts/fix.md`）より、オーケストレーターのコミットの検査が適する | `prompts/fix.md` の「コミットの規約」が、規約から外れたコミットを打ち消す仕組みを持つこと | 採る（受け入れ条件 1・5）。ただし推した出所の「打ち消す仕組み」は 11 系のコードに無い（「今の姿」）。検査は 5 工程の起動に共通の場所へ置く |
| S3 | 入ったときは、トレーラーの欠けと同じようにオーケストレーターがそのコミットを差し戻す | 期待する結果の 2 行目 | 採らない。差し戻すと正しい変更のために担当をもう 1 回起動することになる。作業ファイルだけを除けば担当の変更を失わない（受け入れ条件 2） |
| S4 | 11 系でも同じことが起きる | 2026-11-19 の棚卸しの「11 系での確かめ」 | 確かめた（「事象」の 2 つ目）。課題は閉じない |

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

