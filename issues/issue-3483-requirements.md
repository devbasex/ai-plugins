# #3483: cross-refactoring: 止めた後に PR の head が進んでも、古い head の作業場所と耐久の記録を続けて push で止まる

正は課題の本文（#3483）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 種類

不具合

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（と要求を書いた AI）が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は、再開の照合が引数と対象ブランチだけを見て、作業場所の `work` の HEAD と PR の今の head を比べていないこと | 起票者の本文の「起票者の推定」と「環境」。ndf-next のコードでは、`drive._open` が `_resume_mismatch` で引数と対象ブランチだけを比べ、`workspace.prepare` は在る worktree を作り直さない（`worktree.resumable`）ため、`work` は前の head のまま続く | 前提 1（コードを読んだ範囲では成り立つ。再現の確認は実装の工程で行う） |
| S2 | head が違うときは、止まらずに古い作業場所と状態ファイルを退避して今の head から作り直す | 期待する結果の「止まるか、今の head から作り直す」の 2 つから、要求を書いた AI が選んだ。止める方は、利用者に手での退避を求め続け、検査のプランが止まる影響が残る（Value 1「ワークフロー上の課題で利用者に操作を求めない」）。退避は消さずに置くため戻せる | 受け入れ条件 2〜4 |
| S3 | 「起点が違う」の判定は、PR の今の head が `work` の HEAD に含まれない（祖先でも同じでもない）こととする | 実行が自分で push した後に止まった場合、PR の head は起点より進むが、`work` の HEAD はそれを含む。この場合まで作り直すと、公開した改善項目の続きを捨てる | 受け入れ条件 1・5 |
| S4 | 最終ゲートの記録がある回から続ける回は、照合の対象から外す | 最終ゲートは送る前に `work` を origin の先頭へ合わせ（`worktree.sync_worktree`）、修正担当の直しを PR へ送る。続ける時点で PR の head が `work` の HEAD より進むのは正しい状態である | 前提 3・受け入れ条件 6 |

## 事象

cross-refactoring を途中で止めた後、対象の Pull Request の head が進んでから同じ PR で起動し直すと、止める前の head で作った作業場所と状態をそのまま続けます。古いコードに対して提案と実装を進め、最後の push が non-fast-forward で断られて止まります。

観測したのは今の ndf（10.27.64）で、レビュー worktree（`/tmp/ndf-worktrees/<repo>/rf<PR>/{claude,codex,kiro,work}`）と耐久の記録（`<git の共通ディレクトリ>/ndf/state/dbos/refactor-<鍵>.sqlite`）が続き、`refactor.py final-gate が終了コード 4 で止まった`（`adopted 0 / unconfirmed 23`）で終わりました。ndf-next の cross-refactoring（`plugins/ndf-next/src/ndf/domains/crossrefactor/`）も、未完了の状態ファイル（`<置き場>/rf<PR>/state.json`）を続けるときに `work` の HEAD と PR の head を比べないため、同じ形で古い起点のまま続きます。

## 期待する結果

ndf-next の cross-refactoring で、未完了の状態を続ける前に、作業場所の `work` の HEAD と PR の今の head を比べる。違えば古い状態を続けずに、古い作業場所と状態ファイルを退避して、今の head から作り直す。

## 再現手順

1. スプリント PR #3480（head 08ead1510）で検査のプランを流し、refactor の始まりで止める（kill）
2. PR のブランチへ別の PR #3482 をマージする（head 2561d6dd9）
3. `supervise.py queue --resume <done> --from 3-check=collect` で検査を流し直す
4. refactor のレビュー worktree は 08ead1510 のまま（`git worktree list`）。提案 24 件・実装 23 件のコミットの後、`git push origin HEAD:sprint/lr-rf-p3b` が non-fast-forward で断られる

ndf-next では、`ndf refactor run` を実行の途中で止め、PR のブランチを進めてから同じ引数で打ち直すと、`work` が止める前の head のまま続く（コードを読んだ範囲。前提 1）。

## 環境

観測は ndf 10.27.64（`plugins/ndf/skills/cross-refactoring/scripts/drive.py`）。2026-11-15 01:27 JST。ndf-next 側の再開の照合（`plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` の `_resume_mismatch`。4113b0114）は引数と対象ブランチを比べるが、head の SHA は比べない。

## 影響

止めた検査を流し直すたびに、古い head への提案と実装で 15〜20 分と token を使い、検査のプランが止まる。回避は古い作業場所と状態を手で退けてからやり直すこと。

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。`ndf refactor run` の引数・結果 JSON の形・終了コードの表（`plugins/ndf-next/contracts/facts/exit-codes.json`）・止まった理由の表（`plugins/ndf-next/contracts/causes.json`）は同じ。作り直したときは標準エラーへ 1 行と、結果 JSON の `items` へ退避の 1 件を足す |
| データ | 状態ファイル（`state.json`）の形は変えない。作り直したときは、元の置き場に空の状態から新しい状態ファイルを作る |
| 既存の振る舞い | PR の head が進んだ後に未完了の状態を続ける回だけが変わる。head が同じ回、自分の push で進んだ回、最終ゲートの記録がある回は変わらない |

## 由来

並列の計画 p6 のレーン lr-rf、P3 #3097 のスプリント PR #3480 の検査。

Refs #3097

## 前提

- 前提 1: ndf-next で古い `work` が続くのは、`workspace.prepare` が在る worktree を作り直さず（`worktree.resumable`）、取った PR の head（`worktree.fetch_head`）へ合わせないためである。実装の工程で、失敗するテストを先に書いて確かめる
- 前提 2: `work` の HEAD に載っていて PR へ push していないコミットは、古い head に対して作ったものなので、作り直しの後の実行では使わない。退避した作業場所から人が取り出せれば足りる
- 前提 3: 状態ファイルに最終ゲートの記録（`final`）がある回は、最終ゲートが自分で `work` を origin の先頭へ合わせる（`worktree.sync_worktree`）。照合の対象に入れない
- 前提 4: PR の今の head は、ndf-next の既存の GitHub のアダプタ（`plugins/ndf-next/src/ndf/adapters/github.py` の `GitHub.pull` の `head_sha`）で取る。取れなければ照合ができないため、続けずに止まる

## 対象範囲

含む:
- ndf-next の cross-refactoring の駆動（`plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py`）が、未完了の状態を続ける前に `work` の HEAD と PR の今の head を比べること
- head が違うときに、古い作業場所（`work`・`read/<ランタイム>`・`logs`）と状態ファイルを退避し、空の状態で今の head から作業場所を作り直すこと（`plugins/ndf-next/src/ndf/domains/crossrefactor/` の中）
- 上の振る舞いを確かめるテスト（`plugins/ndf-next/tests/domains/crossrefactor/`）

含まない:
- 今の ndf（`plugins/ndf/`）の cross-refactoring の駆動（`drive.py`・`durable.py`・`run_state.py`・SKILL.md）の変更。今は #1142 の作り直し（`plugins/ndf-next/`）へ注力し、11.0.0 で `plugins/ndf/` と入れ替える方針のため、直す先は ndf-next の側だけにする。今の ndf での回避は、古いレビュー worktree と耐久の記録を手で退けてから打ち直すこと
- 今の ndf との後方互換（状態ファイル・退避の名前を今の ndf と揃えること）
- cross-review の駆動の再開（同じ形の照合が要るかは、要るなら別の課題にする）
- 完了の記録を返す回（`_reused` の head の比較）の変更
- 退避した作業場所の、別の PR の実行からの片付け（同じ PR の作業場所の片付けだけを扱う）
- 検査のプラン（`supervise.py`）の側の再開の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 実行を中断した | 利用者か上位の工程がプロセスを止めた | — | 作業場所の用意（`workspace.prepare`）が `work` を作り、状態ファイルに途中の記録が入った後（入る前の中断は、空の状態から始め直す） |
| E2 | PR の head が進んだ | 別の PR のマージ・人の push | — | E1 の後 |
| E3 | 同じコマンドを打ち直した | 利用者か上位の工程 | — | E1 |
| E4 | `work` の HEAD と PR の今の head を比べた | E3 で未完了の状態ファイルが見つかった | head が取れない → 作業場所と状態ファイルを変えずに止まる（前提 4） | E3。最終ゲートの記録がある回では起きない（前提 3） |
| E5 | 古い作業場所と状態ファイルを退避した | E4 で PR の今の head が `work` の HEAD に含まれないと分かった | 退避に失敗 → 新しく始めずに止まり、退避できなかった場所を出す | E4 |
| E6 | 今の head から作業場所を作り直し、空の状態で始めた | E5 | 以降は既存の `workspace.prepare` の失敗の経路 | E5 |
| E7 | 改善項目を PR へ push した | 最終ゲート | 既存の経路 | E6。`work` の起点が PR の今の head である |

## 用語

| 用語 | 意味 |
| --- | --- |
| 作業場所 | ndf-next の cross-refactoring が PR ごとに作る置き場 `<置き場>/rf<PR>`。書き込み用の `work`・担当ごとの読み取り用 `read/<ランタイム>`・`logs`・状態ファイル `state.json` を持つ。退避は、消さずに別の名前の場所へ移すことを指す |
| 状態ファイル | 実行の進みを持つ `rf<PR>/state.json`。完了の記録（`done`）が無ければ、同じコマンドを打ち直すと記録のある手順を流し直さずに続ける |
| 古い起点 | PR の今の head が、`work` の HEAD と同じでも、その祖先でもないこと |

## 受け入れ条件

- [ ] 1. 未完了の状態ファイルを続けるとき、駆動は作業場所の用意（`workspace.prepare`）と担当の CLI の起動より前に、`work` の HEAD と PR の今の head を比べる
- [ ] 2. PR の今の head が `work` の HEAD に含まれない（`git merge-base --is-ancestor <PR の head> <work の HEAD>` が 0 でない）とき、古い状態を続けず、空の状態で今の head から作業場所を作り直して始める。新しい `work` の HEAD は PR の今の head と一致する
- [ ] 3. 2 のとき、古い作業場所（`work`・`read/<ランタイム>`・`logs`）と状態ファイルは消さずに退避し、退避した先のパスを標準エラーへ 1 行と、結果 JSON の `items` へ 1 件出す。古い `work` の HEAD のコミットは、`git -C <退避した先>/work rev-parse HEAD` で引ける
- [ ] 4. 2 のとき、元の置き場 `rf<PR>` に古い状態ファイルは残らず、次に同じコマンドを打っても古い状態を続けない
- [ ] 5. PR の今の head が `work` の HEAD と同じか、その祖先（実行が自分で push した後に止まった場合）のときは、今と同じく古い状態を続ける。作業場所と状態ファイルは動かさない
- [ ] 6. 状態ファイルに最終ゲートの記録（`final`）がある回は、PR の head が進んでいても作り直さない（今と同じ振る舞い）
- [ ] 7. PR の今の head を取れないときは、作業場所・状態ファイルを変えずに止まる。結果の `status` は stopped、止まった理由の種類は `transient`、終了コードは `infra_wait`（75）で、`summary` に head を取れなかったことを書く
- [ ] 8. 再現手順の形（中断 → PR の head を進める → 打ち直す）を偽の PR と一時のリポジトリで組んだテストが、修正の前に落ち、修正の後に通る
- [ ] 9. 既存の ndf-next の cross-refactoring のテスト（`plugins/ndf-next/tests/domains/crossrefactor/`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 照合で止まるのは head が取れないときだけ。古い起点を理由に検査のプランを止めない |
| 性能・拡張性 | 照合で増える外部への問い合わせは、未完了の状態を続ける回につき GitHub の PR の読み取り 1 回と `git merge-base` 1 回まで |
| 運用・保守性 | 退避した作業場所は、同じ PR の作業場所の片付け（`workspace.release`）の対象になる名前に置く |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `plugins/ndf-next` で `uv run --frozen pytest tests/domains/crossrefactor -q` |
| 静的解析・型検査 | プロジェクトの既存の検査（CI の lint のジョブ）に従う |
| 手動確認 | ndf-next の入れ替えの後のリリース後テストで、再現手順の形（中断 → PR の head を進める → 打ち直す）をスプリント PR で打ち、`git worktree list` の `work` の HEAD が PR の head と一致すること、push が non-fast-forward で断られないことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。変更は `plugins/ndf-next/` だけに置く。安定版の経路（実験版の置き場に置かない） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。判定は純粋な関数（`*_decide.py`）に、外との接続は殻（`drive.py`・`workspace.py`）に置く ndf-next の分け方に従う |
| テスト戦略 | 照合と退避の判断は単体のテストで、中断 → head を進める → 打ち直すの流れは一時の git リポジトリと偽の GitHub で組んだテストで確かめる。`.md` の文言を照合するテストは書かない。時間で区切った負荷試験は入れない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の ndf-next の cross-refactoring のテストの実行、作業場所の説明（`workspace.py` の冒頭の説明）の更新（振る舞いが変わる分だけ） |
| 確認してから行う | 結果 JSON・終了コードの表・止まった理由の表の変更（設計承認ゲートで見る） |
| 行わない | 今の ndf（`plugins/ndf/`）の変更、退避した作業場所の作業の残る worktree の削除、`-D` / `--force` が要る git の操作、cross-review の駆動の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 退避の名前と、退避が途中で止まったときの続け方 | 設計の工程 | 設計 PR |

## 依頼（原文）

> 再開の前に、作業場所の起点と PR の今の head を比べる。違えば古い状態を続けずに止まるか、今の head から作り直す。

（書き手: 起票者、日付: 2026-11-15 JST。課題の「期待する結果」の節。書き手が利用者でないため、利用者の指示の原文として扱わない）

> #3483。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-14）

> 今は #1142 の作り直し（`plugins/ndf-next/`、11.0.0 で `plugins/ndf/` と入れ替える）に注力している。直す先は原則 ndf-next の側で、今の `plugins/ndf/` は即時修正を除き変えない前提で設計する。後方互換は意識しない。時間で区切った負荷試験を受け入れ条件に入れない。#3483 は ndf-next の側（`plugins/ndf-next/src/ndf/domains/crossrefactor/`）で直す設計にする。

（書き手: 並列の計画のオーガナイザー（AI）、日付: 2026-11-14。書き手が利用者でないため、利用者の指示の原文として扱わない）
