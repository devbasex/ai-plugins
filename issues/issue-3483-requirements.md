# #3483: cross-refactoring: 止めた後に PR の head が進んでも、古い head の作業場所と耐久の記録を続けて push で止まる

正は課題の本文（#3483）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 種類

不具合

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（と要求を書いた AI）が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は、再開の照合が引数と状態ファイルの有無だけを見て、レビュー worktree の起点と PR の今の head を比べていないこと | 起票者の本文の「起票者の推定」。コードでは、耐久の記録が `refactor.py init` を 1 つの実行の回で 1 回だけ打つ（`drive.py` の I19）ため、中断した実行を続けると init の中の同期（`setup.py` の `_sync_work_worktree`）が走らない | 前提 1（コードを読んだ範囲では成り立つ。再現の確認は実装の工程で行う） |
| S2 | head が違うときは、止まらずに古いレビュー worktree と状態を退避して今の head から作り直す | 期待する結果の「止まるか、今の head から作り直す」の 2 つから、要求を書いた AI が選んだ。止める方は、利用者に手での退避を求め続け、検査のプランが止まる影響が残る（Value 1「ワークフロー上の課題で利用者に操作を求めない」）。退避は消さずに置くため戻せる | 受け入れ条件 2〜4 |
| S3 | 「起点が違う」の判定は、PR の今の head がレビュー worktree の HEAD に含まれない（祖先でも同じでもない）こととする | 実行が自分で push した後に止まった場合、PR の head は起点より進むが、レビュー worktree の HEAD はそれを含む。この場合まで作り直すと、公開した改善項目の続きを捨てる | 受け入れ条件 1・5 |
| S4 | ndf-next の cross-refactoring の再開（`plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` の `_resume_mismatch`）にも同じ照合を入れる | 起票者の本文の「環境」が ndf-next の照合は head の SHA を比べないと書いている。作り直しの同等版は不具合を引き継がない方針 | 受け入れ条件 7 |
| S5 | 最終ゲートの cross-review の止まり（終了コード 23）から続ける回は、照合の対象から外す | cross-review の修正は PR へ push するため、続ける時点で PR の head がレビュー worktree の HEAD より進むのは正しい状態である | 前提 3・受け入れ条件 6 |

## 事象

cross-refactoring を途中で止めた後、対象の Pull Request の head が進んでから同じ PR で起動し直すと、止める前の head で作ったレビュー worktree（`/tmp/ndf-worktrees/<repo>/rf<PR>/{claude,codex,kiro,work}`）と耐久の記録（`<git の共通ディレクトリ>/ndf/state/dbos/refactor-<鍵>.sqlite`）をそのまま続けます。古いコードに対して提案と実装を進め、最後の push が non-fast-forward で断られて止まります（`refactor.py final-gate が終了コード 4 で止まった`、`adopted 0 / unconfirmed 23`）。

## 期待する結果

再開の前に、レビュー worktree の起点と PR の今の head を比べる。違えば古い状態を続けずに、古いレビュー worktree と状態を退避して、今の head から作り直す。

## 再現手順

1. スプリント PR #3480（head 08ead1510）で検査のプランを流し、refactor の始まりで止める（kill）
2. PR のブランチへ別の PR #3482 をマージする（head 2561d6dd9）
3. `supervise.py queue --resume <done> --from 3-check=collect` で検査を流し直す
4. refactor のレビュー worktree は 08ead1510 のまま（`git worktree list`）。提案 24 件・実装 23 件のコミットの後、`git push origin HEAD:sprint/lr-rf-p3b` が non-fast-forward で断られる

## 環境

ndf 10.27.64（`plugins/ndf/skills/cross-refactoring/scripts/drive.py`）。2026-11-15 01:27 JST。ndf-next 側の再開の照合（4113b0114）は引数と対象ブランチを比べるが、head の SHA は比べない。

## 影響

止めた検査を流し直すたびに、古い head への提案と実装で 15〜20 分と token を使い、検査のプランが止まる。回避は古いレビュー worktree と耐久の記録を手で退けてからやり直すこと。

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。コマンドの引数・結果 JSON の形・終了コードの表は同じ。作り直したときは標準エラーへ 1 行と、結果 JSON の `items` へ退避の 1 件を足す（既存の開始の片付けの結果と同じ形） |
| データ | 状態ファイルの形は変えない。耐久の記録は新しい実行の回を 1 つ足す |
| 既存の振る舞い | PR の head が進んだ後に中断した実行を続ける回だけが変わる。head が同じ回、自分の push で進んだ回、最終ゲートの止まりから続ける回は変わらない |

## 由来

並列の計画 p6 のレーン lr-rf、P3 #3097 のスプリント PR #3480 の検査。

Refs #3097

## 前提

- 前提 1: 再開で古いレビュー worktree が続くのは、中断した耐久ワークフローを続けると記録済みの `init`（レビュー worktree を origin の head へ早送りする処理を含む）が流し直されないためである。実装の工程で、失敗するテストを先に書いて確かめる
- 前提 2: レビュー worktree の HEAD に載っていて PR へ push していないコミットは、古い head に対して作ったものなので、作り直しの後の実行では使わない。退避したレビュー worktree から人が取り出せれば足りる
- 前提 3: 最終ゲートの cross-review の止まり（終了コード 23）から続ける回は、実行の続きであって中断からの再開ではない。照合の対象に入れない
- 前提 4: PR の今の head は `gh pr view <PR> --json headRefOid` で取る（既存の `loop_drive.pr_head` と同じ）。取れなければ照合ができないため、続けずに止まる

## 対象範囲

含む:
- ndf の cross-refactoring の駆動（`drive.py`）が、中断した実行を続ける前にレビュー worktree の HEAD と PR の今の head を比べること
- head が違うときに、古いレビュー worktree・状態・耐久の記録の実行の回を退避または終わらせ、今の head から新しい実行の回を始めること
- ndf-next の cross-refactoring の再開の照合に、同じ比較と同じ扱いを足すこと
- 上の振る舞いを確かめるテスト

含まない:
- cross-review の駆動の再開（同じ形の照合が要るかは、この課題の設計で共通の層へ寄せるかを決めるときに確かめる。要るなら別の課題にする）
- 中断していない実行・完了の記録を返す回（`keep_finished_output` の head の比較）の変更
- 退避したレビュー worktree の自動の削除（後片付けの既存の手順に任せる）
- 検査のプラン（`supervise.py`）の側の再開の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 実行を中断した | 利用者か上位の工程がプロセスを止めた | — | init が状態ファイルとレビュー worktree を作った後（作る前の中断は init が流し直され、既存の早送りが効く） |
| E2 | PR の head が進んだ | 別の PR のマージ・人の push | — | E1 の後 |
| E3 | 同じコマンドを打ち直した | 利用者か上位の工程 | — | E1 |
| E4 | レビュー worktree の HEAD と PR の今の head を比べた | E3 で中断した実行の回が見つかった | head が取れない → レビュー worktree と記録を変えずに止まる（前提 4） | E3。最終ゲートの止まりから続ける回では起きない（前提 3） |
| E5 | 古いレビュー worktree と状態を退避した | E4 で PR の今の head がレビュー worktree の HEAD に含まれないと分かった | 退避に失敗 → 新しい実行を始めずに止まり、退避できなかった場所を出す | E4 |
| E6 | 古い実行の回を終わらせた | E5 | 終わらせられない → 新しい実行を始めずに止まる | E5 |
| E7 | 今の head から新しい実行の回を始めた | E6 | 以降は既存の init の失敗の経路 | E6 |
| E8 | 改善項目を PR へ push した | 最終ゲート | 既存の経路（non-fast-forward で止まる） | E7。レビュー worktree の起点が PR の今の head である |

## 用語

| 用語 | 意味 |
| --- | --- |
| レビュー worktree | cross-refactoring が PR ごとに作る git の worktree（書き込み用の `work` と、担当ごとの読み取り用）。状態ファイルは `work/.cross_refactoring/` に置く。退避は、消さずに別の名前の場所へ移すことを指す |
| 耐久の記録 | 駆動の進みを持つ記録（DBOS の SQLite）。同じコマンドを打ち直すと、記録のある手順を流し直さずに続ける |
| 実行の回 | 耐久の記録の中の 1 回の耐久ワークフロー。中断した回は続け、終わった回の次は新しい回を始める |
| 古い起点 | PR の今の head が、レビュー worktree の HEAD と同じでも、その祖先でもないこと |

## 受け入れ条件

- [ ] 1. 中断した実行を続けるとき、駆動は提案・実装の担当の CLI を 1 つも起動する前に、レビュー worktree `work` の HEAD と PR の今の head を比べる
- [ ] 2. PR の今の head が `work` の HEAD に含まれない（`git merge-base --is-ancestor <PR の head> <work の HEAD>` が偽）とき、古い実行の回を続けず、今の head を起点にした新しい実行の回で init から流す。新しい回の `work` の HEAD は PR の今の head と一致する
- [ ] 3. 2 のとき、古いレビュー worktree（`work` と担当ごとの読み取り用）と状態ファイルは消さずに退避し、退避した先のパスを標準エラーへ 1 行と、結果 JSON の `items` へ 1 件出す。古い `work` の HEAD のコミットは、退避した先から `git -C <退避した先> rev-parse HEAD` で引ける
- [ ] 4. 2 のとき、古い実行の回は耐久の記録の上で終わった状態になり、次に同じコマンドを打っても古い回を続けない
- [ ] 5. PR の今の head が `work` の HEAD と同じか、その祖先（実行が自分で push した後に止まった場合）のときは、今と同じく古い実行の回を続ける。レビュー worktree と状態ファイルは動かさない
- [ ] 6. 最終ゲートの cross-review の止まり（終了コード 23）から続ける回は、PR の head が進んでいても作り直さない（今と同じ振る舞い）
- [ ] 7. PR の今の head を取れないときは、レビュー worktree・状態ファイル・耐久の記録を変えずに中断（結果の `status` は stopped、`metrics.exit` は 4）で終わり、`summary` に head を取れなかったことを書く
- [ ] 8. ndf-next の cross-refactoring でも、未完了の状態を続ける前に同じ比較を行い、1・2・3・5・7 と同じ扱いになる（退避と新しく始める仕組みは ndf-next のレビュー worktree と状態ファイルの置き場に合わせる）
- [ ] 9. 再現手順の形（中断 → PR の head を進める → 打ち直す）を偽の PR と一時のリポジトリで組んだテストが、修正の前に落ち、修正の後に通る（ndf と ndf-next のそれぞれ）
- [ ] 10. 既存の cross-refactoring のテスト（ndf の `plugins/ndf/skills/cross-refactoring/scripts/tests/` と `plugins/ndf/scripts/tests/` のうち cross-refactoring のもの、ndf-next の `plugins/ndf-next/tests/domains/crossrefactor/`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 照合で止まるのは head が取れないときだけ。古い起点を理由に検査のプランを止めない |
| 性能・拡張性 | 照合で増える外部コマンドは、中断した実行を続ける回につき `gh pr view` 1 回と `git merge-base` 1 回まで |
| 運用・保守性 | 退避したレビュー worktree は既存の後片付け（`run_state` の開始の片付けと `/ndf:merged`）の対象になる名前に置く。照合の規則は ndf と ndf-next で同じ文で説明できる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/cross-refactoring/scripts/tests plugins/ndf/scripts/tests -q -n 4 -k "refactor"` と、`plugins/ndf-next` で `uv run --frozen pytest tests/domains/crossrefactor -q` |
| 静的解析・型検査 | プロジェクトの既存の検査（CI の lint のジョブ）に従う |
| 手動確認 | リリース後テストで、再現手順の 1〜3 をスプリント PR で打ち、`git worktree list` の `work` の HEAD が PR の head と一致すること、push が non-fast-forward で断られないことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。ndf の変更は `plugins/ndf/`、ndf-next の変更は `plugins/ndf-next/` に置く。安定版の経路（実験版の置き場に置かない） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。cross-review と cross-refactoring で同じ役割の関数を分けない（共通の照合は `scripts/lib/loop_drive.py` のような共通の層に置けるかを設計で決める） |
| テスト戦略 | 照合と退避の判断は単体のテストで、中断 → head を進める → 打ち直すの流れは一時の git リポジトリと偽の `gh` で組んだ結合のテストで確かめる。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の cross-refactoring のテストの実行、SKILL.md の「再開」の説明の更新（振る舞いが変わる分だけ） |
| 確認してから行う | 結果 JSON・終了コードの表の変更（設計承認ゲートで見る） |
| 行わない | 退避したレビュー worktree・耐久の記録の削除、`-D` / `--force` が要る git の操作、cross-review の駆動の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 照合を ndf の `loop_drive`（cross-review と共通の層）に置くか、cross-refactoring の駆動に置くか | 設計の工程 | 設計 PR |
| 退避の名前と、古い実行の回を終わらせる耐久の記録の操作（取り消しか、終わりの記録か） | 設計の工程 | 設計 PR |

## 依頼（原文）

> 再開の前に、作業場所の起点と PR の今の head を比べる。違えば古い状態を続けずに止まるか、今の head から作り直す。

（書き手: 起票者、日付: 2026-11-15 JST。課題の「期待する結果」の節。書き手が利用者でないため、利用者の指示の原文として扱わない）

> #3483。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-14）
